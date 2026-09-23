# -*- coding: utf-8 -*-
import argparse
import datetime as dt
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xtquant import xtdata

import multi_strategy
import qmt_broker
import strategy
import util
from visual import build_daily_buys


LOT_SIZE = 100


def score_two_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = []
    for row in payload.get("scores", []) if isinstance(payload.get("scores"), list) else []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stock_code") or "")
        scores = row.get("scores", {})
        stds = row.get("score_stds", {})
        mean = scores.get("2") if isinstance(scores, dict) else None
        std = stds.get("2") if isinstance(stds, dict) else None
        try:
            mean_value = float(mean)
            std_value = float(std or 0.0)
        except (TypeError, ValueError):
            continue
        if code.endswith((".SH", ".SZ")) and math.isfinite(mean_value) and math.isfinite(std_value):
            result.append({"stock_code": code, "mean": mean_value, "std": std_value})
    return result


def frame_with_stamp(raw: Any, period: str) -> pd.DataFrame:
    if raw is None or getattr(raw, "empty", True):
        return pd.DataFrame()
    frame = raw.copy()
    texts = frame.index.astype(str)
    if period == "1m":
        frame["stamp"] = pd.to_datetime(texts.str[:14], format="%Y%m%d%H%M%S", errors="coerce")
    else:
        frame["stamp"] = pd.to_datetime(texts.str[:8], format="%Y%m%d", errors="coerce")
    return frame.dropna(subset=["stamp"]).sort_values("stamp")


def historical_market(codes: Sequence[str], trade_date: str, decision_time: str = "145300") -> Dict[str, Dict[str, Any]]:
    trade_day = dt.datetime.strptime(trade_date, "%Y%m%d").date()
    daily_start = (trade_day - dt.timedelta(days=15)).strftime("%Y%m%d") + "000000"
    minute_start = f"{trade_date}093000"
    minute_end = f"{trade_date}{decision_time}"
    result: Dict[str, Dict[str, Any]] = {}
    for index, code in enumerate(codes, start=1):
        try:
            xtdata.download_history_data(code, "1m", minute_start, minute_end)
            xtdata.download_history_data(code, "1d", daily_start, f"{trade_date}150000")
            minute_map = xtdata.get_market_data_ex(
                [], [code], period="1m", start_time=minute_start, end_time=minute_end, count=-1,
                dividend_type="none", fill_data=False,
            )
            daily_map = xtdata.get_market_data_ex(
                [], [code], period="1d", start_time=daily_start, end_time=f"{trade_date}150000", count=-1,
                dividend_type="none", fill_data=False,
            )
            minute = frame_with_stamp(minute_map.get(code) if isinstance(minute_map, dict) else None, "1m")
            daily = frame_with_stamp(daily_map.get(code) if isinstance(daily_map, dict) else None, "1d")
            minute = minute[minute["stamp"].dt.strftime("%Y%m%d") == trade_date] if not minute.empty else minute
            previous = daily[daily["stamp"].dt.strftime("%Y%m%d") < trade_date] if not daily.empty else daily
            current_daily = daily[daily["stamp"].dt.strftime("%Y%m%d") == trade_date] if not daily.empty else daily
            price = float(minute.iloc[-1].get("close") or 0.0) if not minute.empty else 0.0
            open_price = float(minute.iloc[0].get("open") or minute.iloc[0].get("close") or 0.0) if not minute.empty else 0.0
            high_price = float(minute["high"].max() or 0.0) if not minute.empty and "high" in minute else price
            low_price = float(minute["low"].min() or 0.0) if not minute.empty and "low" in minute else price
            volume = float(minute["volume"].sum() or 0.0) if not minute.empty and "volume" in minute else 0.0
            real_minute = (
                bool(minute["_real_minute"].astype(bool).any())
                if not minute.empty and "_real_minute" in minute else False
            )
            previous_close = float(previous.iloc[-1].get("close") or 0.0) if not previous.empty else 0.0
            # 停牌与"没数据"必须分开:日线窗口有 bar 而当日分钟为空 -> 真停牌;
            # 日线也一根没有 -> 该代码不在导出宇宙,是数据缺失(no_data),不是停牌
            # (688392.SH 0819 有量却被标停牌的教训)。
            result[code] = {
                "price": price,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "volume": volume,
                "previous_close": previous_close,
                "minute_rows": 0 if minute.empty else int(len(minute)),
                "daily_rows": 0 if daily.empty else int(len(daily)),
                "current_daily_rows": 0 if current_daily.empty else int(len(current_daily)),
                "real_minute": real_minute,
                "paused": minute.empty and not daily.empty,
                "no_data": minute.empty and daily.empty,
            }
        except Exception as exc:
            result[code] = {"price": 0.0, "previous_close": 0.0, "paused": False,
                            "no_data": True, "error": str(exc)}
        print(
            f"historical market {index}/{len(codes)} {code} "
            f"price={result[code]['price']:.3f} prev={result[code]['previous_close']:.3f}"
        )
    return result


def implied_limit_from_trade_price(price: float, limit_rate: float) -> Tuple[float, float]:
    """Infer previous close/up-limit only when a trade price itself looks limit-up."""
    if price <= 0 or limit_rate <= 0:
        return 0.0, 0.0
    previous = qmt_broker.round_stock_price(price / (1.0 + limit_rate))
    if previous <= 0:
        return 0.0, 0.0
    up_price = qmt_broker.round_stock_price(previous * (1.0 + limit_rate))
    if up_price > 0 and price >= up_price * multi_strategy.NEAR_LIMIT_RATIO:
        return previous, up_price
    return 0.0, 0.0


def implied_limit_from_open_and_price(open_price: float, price: float, limit_rate: float) -> Tuple[float, float]:
    if open_price <= 0 or price <= 0 or limit_rate <= 0:
        return 0.0, 0.0
    up_price = qmt_broker.round_stock_price(open_price * (1.0 + limit_rate))
    if up_price > 0 and price >= up_price * multi_strategy.NEAR_LIMIT_RATIO:
        return open_price, up_price
    return 0.0, 0.0


def evaluate_strategy(
    strategy_id: str,
    codes: Sequence[str],
    trade_date: str,
    market: Dict[str, Dict[str, Any]],
    cash_before: float,
) -> Dict[str, Any]:
    trade_day = dt.datetime.strptime(trade_date, "%Y%m%d").date()
    decisions: List[Dict[str, Any]] = []
    filters: Dict[str, int] = {}
    strict_listing = strategy_id in (
        "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "s12", "s13",
    )
    for rank, code in enumerate(codes, start=1):
        quote = market.get(code, {})
        price = float(quote.get("price") or 0.0)
        previous_close = float(quote.get("previous_close") or 0.0)
        detail = multi_strategy.instrument_detail(code)
        name = str(detail.get("InstrumentName") or detail.get("ProductName") or "").upper()
        limit_rate = 0.05 if "ST" in name else qmt_broker.code_limit_rate(code)
        up_price = qmt_broker.round_stock_price(previous_close * (1.0 + limit_rate)) if previous_close > 0 else 0.0
        limit_check_source = "daily_previous_close" if previous_close > 0 else ""
        if up_price <= 0:
            for ref_price, source in (
                (float(quote.get("open") or 0.0), "qmt_minute_open_implied"),
                (price, "qmt_minute_close_implied"),
                (float(quote.get("high") or 0.0), "qmt_minute_high_implied"),
            ):
                implied_previous, implied_up = implied_limit_from_trade_price(ref_price, limit_rate)
                if implied_up > 0:
                    previous_close = implied_previous
                    up_price = implied_up
                    limit_check_source = source
                    break
        elif quote.get("real_minute") and price < up_price * multi_strategy.NEAR_LIMIT_RATIO:
            # Some exported daily bars carry an unadjusted previous close, while
            # the intraday limit uses the adjusted auction/open reference.
            implied_previous, implied_up = implied_limit_from_open_and_price(
                float(quote.get("open") or 0.0), price, limit_rate
            )
            if implied_up > 0:
                previous_close = implied_previous
                up_price = implied_up
                limit_check_source = "qmt_minute_open_as_previous_close_implied"
        reason = "eligible"
        if quote.get("no_data"):
            reason = "no_data"
        elif quote.get("paused"):
            reason = "paused"
        elif price <= 0 or up_price <= 0:
            reason = "invalid_price"
        elif price >= up_price * multi_strategy.NEAR_LIMIT_RATIO:
            reason = "near_limit"
        elif "ST" in name or "退" in name or (not name and util.is_st_stock(code)):
            reason = "st"
        elif multi_strategy.is_delisted(detail):
            reason = "delisted"
        elif strict_listing:
            # 与 multi_strategy 同源:OpenDate 有效走天数快路径;缺失(老股常见 OpenDate=0)
            # 落日线首根 bar 反推,仅能证明是次新才拒,判不了/非次新一律放行(不再误杀老股)。
            listing_date = multi_strategy.parse_listing_date(detail)
            if listing_date is not None:
                if (trade_day - listing_date).days < multi_strategy.NEW_STOCK_DAYS:
                    reason = "new_stock"
            elif multi_strategy.listed_under_threshold(code, trade_date) is True:
                reason = "new_stock"
        if reason != "eligible":
            filters[reason] = filters.get(reason, 0) + 1
        decisions.append({
            "rank": rank,
            "code": code,
            "price": price,
            "previous_close": previous_close,
            "change_pct": ((price / previous_close) - 1.0) * 100.0 if price > 0 and previous_close > 0 else 0.0,
            "up_price": up_price,
            "limit_check_source": limit_check_source,
            "paused": bool(quote.get("paused")),
            "minute_rows": int(quote.get("minute_rows") or 0),
            "daily_rows": int(quote.get("daily_rows") or 0),
            "real_minute": bool(quote.get("real_minute")),
            "eligible": reason == "eligible",
            "reason": reason,
            "limit_price": min(qmt_broker.round_stock_price(price * multi_strategy.BUY_LIMIT_RATIO), up_price) if price > 0 and up_price > 0 else 0.0,
        })

    eligible = [row for row in decisions if row["reason"] == "eligible"]
    estimated_buys = set()
    config = multi_strategy.strategy_config(strategy_id)
    if config["allocation"] == "equal" and eligible and cash_before > 0:
        distributable = cash_before * multi_strategy.CAPITAL_BUFFER_RATIO
        affordable = list(eligible)
        while affordable:
            per_stock = distributable / len(affordable)
            next_affordable = [row for row in affordable if row["price"] * LOT_SIZE <= per_stock]
            if len(next_affordable) == len(affordable):
                break
            affordable = next_affordable
        estimated_buys = {row["code"] for row in affordable}
    elif config["allocation"] == "fixed":
        remaining = cash_before
        for row in eligible:
            if remaining + 0.000001 < multi_strategy.FIXED_BUY_AMOUNT:
                break
            if qmt_broker.calc_order_volume(multi_strategy.FIXED_BUY_AMOUNT, row["price"], LOT_SIZE) <= 0:
                continue
            estimated_buys.add(row["code"])
            remaining -= multi_strategy.FIXED_BUY_AMOUNT

    for row in eligible:
        if row["code"] in estimated_buys:
            row["reason"] = "estimated_buy"
            row["reason_label"] = "预计可以买入"
        else:
            row["eligible"] = False
            row["reason"] = "insufficient_cash"
            row["reason_label"] = "通过风控，但预计资金不足"
            filters["insufficient_cash"] = filters.get("insufficient_cash", 0) + 1
    for row in decisions:
        if "reason_label" not in row:
            row["reason_label"] = build_daily_buys.REASON_LABELS.get(row["reason"], row["reason"])
    return {
        "time": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}T14:53:00",
        "candidate_count": len(codes),
        "accepted_count": len(eligible),
        "estimated_buy_count": len(estimated_buys),
        "submitted_count": 0,
        "cash_before": cash_before,
        "filters": filters,
        "candidates": decisions,
    }


def import_prediction(source: Path, trade_date: str, target_date: str, output: Path) -> Dict[str, Any]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    items = score_two_items(payload)
    if not items:
        raise RuntimeError("预测文件中没有有效的 score 2")
    context_date = str((payload.get("live") or {}).get("live_context_date") or "")
    if context_date and context_date != trade_date:
        raise RuntimeError(f"预测上下文日期 {context_date} 与交易日 {trade_date} 不一致")
    util.connect_market_data()
    selections = strategy.select_strategies(items)
    codes = list(dict.fromkeys(code for values in selections.values() for code in values))
    market = historical_market(codes, trade_date)
    state = util.load_state()
    root = multi_strategy.ensure_state(state)
    strategies = {
        strategy_id: evaluate_strategy(
            strategy_id,
            selections.get(strategy_id, []),
            trade_date,
            market,
            float(root["accounts"][strategy_id].get("cash") or 0.0),
        )
        for strategy_id in multi_strategy.STRATEGY_CONFIGS
    }
    archive = build_daily_buys.load_historical_estimates(output)
    days = archive.setdefault("days", {})
    try:
        source_name = str(source.relative_to(PROJECT_ROOT))
    except ValueError:
        source_name = str(source)
    days[trade_date] = {
        "trade_date": trade_date,
        "target_date": target_date,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": source_name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_label": "预测服务故障，按14:53历史行情估算",
        "score_id": 2,
        "scored_stock_count": len(items),
        "strategies": strategies,
    }
    build_daily_buys.write_json(archive, output)
    return days[trade_date]


def main() -> int:
    parser = argparse.ArgumentParser(description="导入漏跑预测并生成只读历史买入估算")
    parser.add_argument("--source", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--output", default=str(build_daily_buys.ESTIMATE_PATH))
    args = parser.parse_args()
    result = import_prediction(Path(args.source).resolve(), args.trade_date, args.target_date, Path(args.output).resolve())
    print(json.dumps({
        "trade_date": result["trade_date"],
        "target_date": result["target_date"],
        "strategies": {
            key: {
                "candidates": value["candidate_count"],
                "risk_passed": value["accepted_count"],
                "estimated_buys": value["estimated_buy_count"],
                "filters": value["filters"],
            }
            for key, value in result["strategies"].items()
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
