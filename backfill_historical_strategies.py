# -*- coding: utf-8 -*-
"""Replay seven strategies from archived predictions without submitting QMT orders."""
import argparse
import datetime as dt
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xtquant import xtdata

import estimated_sell_tracker
import multi_strategy
import qmt_broker
import strategy
import util
from visual import build_daily_buys, build_daily_sells, import_missed_prediction


DATA_ROOT = PROJECT_ROOT / "virtual_qmt_data"
ARCHIVE_ROOT = DATA_ROOT / "prediction_archives"
ESTIMATE_PATH = PROJECT_ROOT / "visual" / "historical_buy_estimates.json"
SELL_STATE_PATH = DATA_ROOT / "estimated_sell_state.json"
SELL_PUBLIC_PATH = PROJECT_ROOT / "visual" / "estimated_sell_snapshot.json"
BUY_CHART_ROOT = PROJECT_ROOT / "visual" / "daily_buy_charts"
SELL_CHART_ROOT = PROJECT_ROOT / "visual" / "daily_sell_charts"
LOT_SIZE = 100


def trading_dates(start_date: str, end_date: str) -> List[str]:
    start = dt.datetime.strptime(start_date, "%Y%m%d").date()
    end = dt.datetime.strptime(end_date, "%Y%m%d").date()
    result = []
    cursor = start
    while cursor <= end:
        if util.is_trading_day(cursor, use_api=False):
            result.append(cursor.strftime("%Y%m%d"))
        cursor += dt.timedelta(days=1)
    return result


def score_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    result = payload.get("result")
    return result if isinstance(result, dict) and isinstance(result.get("scores"), list) else payload


def prediction_source(trade_date: str, source_dir: Optional[Path] = None) -> Path:
    if source_dir is not None:
        root = Path(source_dir).resolve()
        candidates = [
            root / f"predict_sync_{trade_date}_latest.json",
            root / f"{trade_date}_scores.json",
            root / trade_date / "scores.json",
        ]
        for path in candidates:
            if path.exists():
                return path
        raise FileNotFoundError(f"{trade_date} 在 {root} 下没有可用的 server_return 分数文件")
    scheduled = DATA_ROOT / f"predict_sync_{trade_date}_latest.json"
    if scheduled.exists():
        return scheduled
    matches = sorted(ARCHIVE_ROOT.glob(f"{trade_date}_target_*_scores.json"))
    if matches:
        return matches[-1]
    raise FileNotFoundError(f"{trade_date} 没有可用的全市场预测文件")


def load_prediction(
    trade_date: str, source_dir: Optional[Path] = None
) -> Tuple[Path, Dict[str, Any], List[Dict[str, Any]], str]:
    source = prediction_source(trade_date, source_dir=source_dir)
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    scores = score_payload(payload)
    items = import_missed_prediction.score_two_items(scores)
    if not items:
        raise RuntimeError(f"{source} 没有有效Score2")
    context_date = str((scores.get("live") or {}).get("live_context_date") or "")
    if context_date and context_date != trade_date:
        raise RuntimeError(f"{source} 上下文日期为{context_date}，不是{trade_date}")
    target_date = str((payload.get("request") or {}).get("date") or "")
    if not target_date:
        parts = source.stem.split("_target_", 1)
        target_date = parts[1].split("_", 1)[0] if len(parts) == 2 else ""
    return source, payload, items, target_date


def minute_frame(code: str, trade_date: str, cache: Dict[Tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    key = (code, trade_date)
    if key in cache:
        return cache[key]
    start = f"{trade_date}093000"
    end = f"{trade_date}150000"
    try:
        xtdata.download_history_data(code, "1m", start, end)
        raw = xtdata.get_market_data_ex(
            [], [code], period="1m", start_time=start, end_time=end, count=-1,
            dividend_type="none", fill_data=False,
        )
        frame = import_missed_prediction.frame_with_stamp(raw.get(code) if isinstance(raw, dict) else None, "1m")
        if not frame.empty:
            frame = frame[frame["stamp"].dt.strftime("%Y%m%d") == trade_date]
    except Exception as exc:
        print(f"sell market unavailable {trade_date} {code}: {exc}")
        frame = pd.DataFrame()
    cache[key] = frame
    return frame


def first_target_hit(frame: pd.DataFrame, target: float, end_time: str = "145500") -> Optional[pd.Series]:
    if frame.empty or "high" not in frame:
        return None
    cutoff = dt.datetime.strptime(end_time, "%H%M%S").time()
    eligible = frame[(frame["stamp"].dt.time <= cutoff) & (frame["high"].astype(float) >= target)]
    return eligible.iloc[0] if not eligible.empty else None


def close_at(frame: pd.DataFrame, end_time: str) -> Optional[pd.Series]:
    if frame.empty or "close" not in frame:
        return None
    cutoff = dt.datetime.strptime(end_time, "%H%M%S").time()
    eligible = frame[frame["stamp"].dt.time <= cutoff]
    return eligible.iloc[-1] if not eligible.empty else None


def _format_pct(rate: float) -> str:
    value = round(float(rate) * 100, 4)
    if value == int(value):
        return str(int(value))
    return ("%g" % value)


def historical_sell_signal(
    position: Dict[str, Any], trade_date: str, frame: pd.DataFrame
) -> Optional[Dict[str, Any]]:
    buy_date = str(position.get("buy_date") or "")
    if trade_date <= buy_date or frame.empty:
        return None
    tp_pct = _format_pct(multi_strategy.take_profit_rate(str(position.get("strategy_id") or "")))
    tp_label = "预估止盈卖出（达到买入点+{}%）".format(tp_pct)
    target = float(position.get("target_price") or math.inf)
    mode = str(position.get("holding_mode") or "")
    if mode == "next_day":
        sell_date = estimated_sell_tracker.next_trading_day(buy_date)
        if trade_date < sell_date:
            return None
        hit = first_target_hit(frame, target, "095959") if trade_date == sell_date else None
        if hit is not None:
            return {
                "reason": "take_profit", "reason_label": tp_label,
                "price": target, "time": hit["stamp"].strftime("%H:%M:%S"),
            }
        row = close_at(frame, "100000")
        if row is not None:
            return {
                "reason": "next_day_force", "reason_label": "预估次日10:00强制卖出",
                "price": float(row.get("close") or 0.0), "time": "10:00:00",
            }
        return None
    hit = first_target_hit(frame, target)
    if hit is not None:
        return {
            "reason": "take_profit", "reason_label": "预估止盈卖出（达到买入点+9%）",
            "price": target, "time": hit["stamp"].strftime("%H:%M:%S"),
        }
    holding_days = int(position.get("holding_days") or 3)
    if estimated_sell_tracker.trading_days_elapsed(buy_date, trade_date) < holding_days:
        return None
    row = close_at(frame, "145500")
    if row is None:
        return None
    return {
        "reason": "expiry", "reason_label": f"预估持有{holding_days}日到期卖出",
        "price": float(row.get("close") or 0.0), "time": "14:55:00",
    }


def instrument_name(code: str) -> str:
    detail = multi_strategy.instrument_detail(code)
    return str(detail.get("InstrumentName") or detail.get("ProductName") or "")


def close_position(
    position: Dict[str, Any], signal: Dict[str, Any], trade_date: str, cash: Dict[str, float]
) -> Dict[str, Any]:
    sell_price = float(signal["price"])
    shares = int(position["shares"])
    buy_amount = float(position["buy_amount"])
    sell_amount = sell_price * shares
    fees = build_daily_sells.estimated_trade_fees(buy_amount, sell_amount)
    strategy_id = str(position["strategy_id"])
    cash[strategy_id] += sell_amount - float(fees["sell_fee"])
    position.update({
        "status": "estimated_sold",
        "status_label": "已预估卖出",
        "estimated_sell": True,
        "estimated_sell_date": trade_date,
        "estimated_sell_time": str(signal["time"]),
        "estimated_sell_calculated_at": dt.datetime.now().strftime("%Y%m%d %H:%M:%S"),
        "estimated_sell_price": round(sell_price, 4),
        "estimated_sell_amount": round(sell_amount, 2),
        "estimated_sell_reason": str(signal["reason"]),
        "estimated_sell_reason_label": str(signal["reason_label"]),
        "estimated_pnl": round(sell_amount - buy_amount - float(fees["fees"]), 2),
        "estimated_return_rate": (
            (sell_amount - buy_amount - float(fees["fees"])) / (buy_amount + float(fees["buy_fee"]))
            if buy_amount > 0 else 0.0
        ),
    })
    return {
        "event": "estimated_sell", "label": "预估卖出，不是实际委托",
        "time": f"{trade_date} {signal['time']}", "strategy_id": strategy_id,
        "code": position["code"], "price": position["estimated_sell_price"],
        "reason": position["estimated_sell_reason"],
    }


def add_buys(
    trade_date: str,
    strategy_id: str,
    estimate: Dict[str, Any],
    positions: Dict[str, Dict[str, Any]],
    cash: Dict[str, float],
) -> None:
    candidates = [row for row in estimate.get("candidates", []) if row.get("reason") == "estimated_buy"]
    config = multi_strategy.strategy_config(strategy_id)
    if not candidates:
        return
    allocation = (
        float(estimate["cash_before"]) * multi_strategy.CAPITAL_BUFFER_RATIO / len(candidates)
        if config["allocation"] == "equal" else multi_strategy.FIXED_BUY_AMOUNT
    )
    for row in candidates:
        price = float(row.get("price") or 0.0)
        shares = qmt_broker.calc_order_volume(allocation, price, LOT_SIZE)
        if shares <= 0:
            row["reason"] = "insufficient_cash"
            row["reason_label"] = "通过风控，但预计资金不足"
            row["eligible"] = False
            continue
        buy_amount = price * shares
        fees = build_daily_sells.estimated_trade_fees(buy_amount, 0.0)
        total_cost = buy_amount + float(fees["buy_fee"])
        if total_cost > cash[strategy_id] + 0.000001:
            row["reason"] = "insufficient_cash"
            row["reason_label"] = "通过风控，但预计资金不足"
            row["eligible"] = False
            continue
        cash[strategy_id] -= total_cost
        row["shares"] = shares
        row["buy_amount"] = round(buy_amount, 2)
        row["buy_fee"] = float(fees["buy_fee"])
        code = str(row["code"])
        key = f"{strategy_id}:{trade_date}:{code}"
        positions[key] = {
            "position_key": key,
            "strategy_id": strategy_id,
            "strategy_name": str(config["name"]),
            "code": code,
            "name": instrument_name(code),
            "buy_date": trade_date,
            "buy_time": "14:53:00",
            "buy_price": price,
            "shares": shares,
            "buy_amount": round(buy_amount, 2),
            "buy_fee": float(fees["buy_fee"]),
            "target_price": round(price * (1.0 + multi_strategy.take_profit_rate(strategy_id)), 2),
            "holding_mode": str(config["holding_mode"]),
            "holding_days": int(config.get("holding_days") or 1),
            "status": "tracking",
            "status_label": "等待预估卖出",
            "latest_price": 0.0,
            "max_price_seen": 0.0,
        }
    estimate["estimated_buy_count"] = sum(
        1 for row in estimate.get("candidates", []) if row.get("reason") == "estimated_buy" and row.get("shares")
    )


def replay(start_date: str, buy_end_date: str,
           sell_end_date: Optional[str] = None,
           prediction_source_dir: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """回算 13 策略预估。买入生成止于 buy_end_date(冻结点,之后交给真实盘),卖出评估可
    跨到 sell_end_date(最新有日线数据的交易日)——这样多日持有策略在到期日 > buy_end 时
    仍能被了结,且随 sell_end 随数据到达前移而逐日结清 tracking 尾巴。"""
    sell_end = sell_end_date or buy_end_date
    if sell_end < buy_end_date:
        sell_end = buy_end_date
    dates = trading_dates(start_date, sell_end)
    # 只对买入日加载预估源;纯卖出日(buy_end 之后)没有预估文件,不加载。
    source_root = Path(prediction_source_dir).resolve() if prediction_source_dir else None
    sources = {
        trade_date: (
            load_prediction(trade_date, source_dir=source_root)
            if source_root is not None else load_prediction(trade_date)
        )
        for trade_date in dates
        if trade_date <= buy_end_date
    }
    util.connect_market_data()
    cash = {
        strategy_id: float(config["initial_cash"])
        for strategy_id, config in multi_strategy.STRATEGY_CONFIGS.items()
    }
    positions: Dict[str, Dict[str, Any]] = {}
    events: List[Dict[str, Any]] = []
    days: Dict[str, Dict[str, Any]] = {}
    daily_accounts: Dict[str, Dict[str, Any]] = {}
    minute_cache: Dict[Tuple[str, str], pd.DataFrame] = {}
    market_quote_count = 0
    market_nonzero_count = 0

    for trade_date in dates:
        tracking = [row for row in positions.values() if row.get("status") == "tracking"]
        print(f"replay sells {trade_date} tracking={len(tracking)}")
        for position in tracking:
            frame = minute_frame(str(position["code"]), trade_date, minute_cache)
            signal = historical_sell_signal(position, trade_date, frame)
            if signal and float(signal.get("price") or 0.0) > 0:
                events.append(close_position(position, signal, trade_date, cash))

        daily_accounts[trade_date] = {
            "trade_date": trade_date,
            "cash_after_sells": {
                strategy_id: round(cash[strategy_id], 2)
                for strategy_id in multi_strategy.STRATEGY_CONFIGS
            },
            "cash_before_buy": None,
            "cash_after_buy": None,
        }

        # 买入生成只在买入窗口内(buy_end 之后是纯卖出日,不产生新买入、不记 days)。
        if trade_date > buy_end_date:
            continue

        source, _payload, items, target_date = sources[trade_date]
        selections = strategy.select_strategies(items)
        codes = list(dict.fromkeys(code for selected in selections.values() for code in selected))
        print(f"replay buys {trade_date} scored={len(items)} candidates={len(codes)}")
        market = import_missed_prediction.historical_market(codes, trade_date)
        market_quote_count += len(market)
        market_nonzero_count += sum(
            1 for quote in market.values()
            if isinstance(quote, dict) and float(quote.get("price") or 0.0) > 0
        )
        day_strategies: Dict[str, Any] = {}
        daily_accounts[trade_date]["cash_before_buy"] = {
            strategy_id: round(cash[strategy_id], 2)
            for strategy_id in multi_strategy.STRATEGY_CONFIGS
        }
        for strategy_id in multi_strategy.STRATEGY_CONFIGS:
            cash_before = cash[strategy_id]
            estimate = import_missed_prediction.evaluate_strategy(
                strategy_id, selections.get(strategy_id, []), trade_date, market, cash_before,
            )
            add_buys(trade_date, strategy_id, estimate, positions, cash)
            estimate["cash_after"] = round(cash[strategy_id], 2)
            day_strategies[strategy_id] = estimate
        daily_accounts[trade_date]["cash_after_buy"] = {
            strategy_id: round(cash[strategy_id], 2)
            for strategy_id in multi_strategy.STRATEGY_CONFIGS
        }
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
            "source_label": "13策略历史回算，仅预估，未向虚拟盘提交委托",
            "score_id": 2,
            "scored_stock_count": len(items),
            "strategies": day_strategies,
        }

    estimate_archive = {
        "schema_version": 2,
        "mode": "historical_estimate_only",
        "estimate_start_date": start_date,
        "estimate_end_date": buy_end_date,
        "sell_eval_end_date": sell_end,
        "real_order_start_date": estimated_sell_tracker.next_trading_day(buy_end_date),
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "days": days,
    }
    sell_state = {
        "schema_version": 2,
        "mode": "server_return_full_replay",
        "cash_source_mode": "server_return_full_replay",
        "source_trade_dates": dates,
        "estimate_start_date": start_date,
        "estimate_end_date": buy_end_date,
        "sell_eval_end_date": sell_end,
        "real_order_start_date": estimate_archive["real_order_start_date"],
        "prediction_source_dir": str(source_root) if source_root else "",
        "market_data_ok": market_nonzero_count > 0,
        "market_data_counts": {
            "quotes": market_quote_count,
            "nonzero_price": market_nonzero_count,
        },
        "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "positions": positions,
        "events": events,
        "daily_accounts": daily_accounts,
        "accounts": {
            strategy_id: {
                "strategy_id": strategy_id,
                "initial_cash": float(config["initial_cash"]),
                "cash": round(cash[strategy_id], 2),
            }
            for strategy_id, config in multi_strategy.STRATEGY_CONFIGS.items()
        },
        "safety": {
            "label": "预估卖出，不提交真实委托",
            "qmt_trader_created": False,
            "orders_submitted": 0,
        },
    }
    sell_state["summary"] = estimated_sell_tracker.build_summary(sell_state)
    sell_state["updated_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sell_state["process_status"] = (
        "tracking" if any(row.get("status") == "tracking" for row in positions.values()) else "completed"
    )
    return estimate_archive, sell_state


def build_historical_sell_charts(sell_state: Dict[str, Any], known_names: Optional[Dict[str, str]] = None) -> None:
    names = dict(known_names or {})
    names.update({
        str(row.get("code") or ""): str(row.get("name") or "")
        for row in sell_state.get("positions", {}).values()
        if isinstance(row, dict) and row.get("code")
    })
    sold_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in (sell_state.get("positions", {}) or {}).values():
        if not isinstance(row, dict) or row.get("status") != "estimated_sold":
            continue
        sell_date = str(row.get("estimated_sell_date") or "")
        code = str(row.get("code") or "")
        if len(sell_date) == 8 and code:
            sold_by_date.setdefault(sell_date, []).append(row)
    for sell_date, rows in sorted(sold_by_date.items()):
        codes = sorted({str(row.get("code") or "") for row in rows if row.get("code")})
        for code in codes:
            if not names.get(code):
                names[code] = instrument_name(code)
        snapshot = build_daily_sells.build_sell_chart_snapshot(sell_date, rows, names)
        build_daily_buys.write_json(snapshot, SELL_CHART_ROOT / f"{sell_date}.json")
        print(f"historical sell chart {sell_date} codes={len(codes)}")


def build_historical_charts(archive: Dict[str, Any], sell_state: Dict[str, Any]) -> None:
    names = {
        str(row.get("code") or ""): str(row.get("name") or "")
        for row in sell_state.get("positions", {}).values()
        if isinstance(row, dict) and row.get("code")
    }
    for trade_date, day in sorted((archive.get("days", {}) or {}).items()):
        codes = sorted({
            str(row.get("code") or "")
            for estimate in (day.get("strategies", {}) or {}).values()
            if isinstance(estimate, dict)
            for row in estimate.get("candidates", [])
            if isinstance(row, dict) and row.get("code")
        })
        for code in codes:
            if not names.get(code):
                names[code] = instrument_name(code)
        snapshot = build_daily_buys.build_chart_snapshot(trade_date, codes, names)
        build_daily_buys.write_json(snapshot, BUY_CHART_ROOT / f"{trade_date}.json")
        print(f"historical buy chart {trade_date} codes={len(codes)}")

    build_historical_sell_charts(sell_state, names)


def main() -> int:
    parser = argparse.ArgumentParser(description="按日期顺序回算七策略历史预估买入与卖出")
    parser.add_argument("--start-date", default="20260721")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--sell-end-date", dest="sell_end_date", default="")
    parser.add_argument("--prediction-source-dir", dest="prediction_source_dir", default="")
    parser.add_argument("--estimate-output", default=str(ESTIMATE_PATH))
    parser.add_argument("--sell-state-output", default=str(SELL_STATE_PATH))
    parser.add_argument("--sell-public-output", default=str(SELL_PUBLIC_PATH))
    parser.add_argument("--charts-only", action="store_true")
    parser.add_argument("--skip-charts", action="store_true")
    args = parser.parse_args()
    estimate_output = Path(args.estimate_output).resolve()
    sell_state_output = Path(args.sell_state_output).resolve()
    if args.charts_only:
        archive = json.loads(estimate_output.read_text(encoding="utf-8-sig"))
        sell_state = json.loads(sell_state_output.read_text(encoding="utf-8-sig"))
        util.connect_market_data()
    else:
        archive, sell_state = replay(
            args.start_date,
            args.end_date,
            args.sell_end_date or None,
            prediction_source_dir=args.prediction_source_dir or None,
        )
        build_daily_buys.write_json(archive, estimate_output)
        build_daily_buys.write_json(sell_state, sell_state_output)
        build_daily_buys.write_json(sell_state, Path(args.sell_public_output).resolve())
    if not args.skip_charts:
        build_historical_charts(archive, sell_state)
    print(json.dumps({
        "estimate_dates": len(archive["days"]),
        "positions": len(sell_state["positions"]),
        "estimated_sells": sum(1 for row in sell_state["positions"].values() if row.get("status") == "estimated_sold"),
        "tracking": sum(1 for row in sell_state["positions"].values() if row.get("status") == "tracking"),
        "orders_submitted": 0,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
