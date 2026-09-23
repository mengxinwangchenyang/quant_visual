# -*- coding: utf-8 -*-
import argparse
import datetime as dt
import math
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from xtquant import xtconstant, xtdata
from xtquant.xttrader import XtQuantTrader
from xtquant.xttype import StockAccount

import qmt_broker
import util


STATE_KEY = "multi_strategy"
STATE_VERSION = 2
ORDER_PREFIX = "m"
BUY_CUTOFF_TIME = "15:00:00"
NEAR_LIMIT_RATIO = 0.995
BUY_LIMIT_RATIO = 1.005
SELL_LIMIT_RATIO = 0.995
TAKE_PROFIT_RATE = 0.09
FIXED_BUY_AMOUNT = 100000.0
CAPITAL_BUFFER_RATIO = 0.995

ACTIVE_PLAN_STATUSES = {
    "buy_submitted",
    "buy_partial",
    "open",
    "sell_submitted",
    "sell_partial",
    "sell_cancel_pending",
}
TERMINAL_ORDER_STATUSES = {
    xtconstant.ORDER_SUCCEEDED,
    xtconstant.ORDER_CANCELED,
    xtconstant.ORDER_PARTSUCC_CANCEL,
    xtconstant.ORDER_PART_CANCEL,
    xtconstant.ORDER_JUNK,
}

STRATEGY_CONFIGS: Dict[str, Dict[str, Any]] = {
    "s1": {
        "name": "策略1",
        "initial_cash": 500000.0,
        "allocation": "equal",
        "holding_mode": "next_day",
        "take_profit_rate": 0.09,
    },
    "s2": {
        "name": "策略2",
        "initial_cash": 500000.0,
        "allocation": "equal",
        "holding_mode": "next_day",
        "take_profit_rate": 0.09,
    },
    "s3": {
        "name": "策略3",
        "initial_cash": 500000.0,
        "allocation": "equal",
        "holding_mode": "next_day",
        "take_profit_rate": 0.09,
    },
    "s4": {
        "name": "策略4",
        "initial_cash": 200000.0,
        "allocation": "fixed",
        "holding_mode": "three_days",
        "holding_days": 3,
        "take_profit_rate": 0.09,
    },
    "s5": {
        "name": "策略5",
        "initial_cash": 500000.0,
        "allocation": "fixed",
        "holding_mode": "three_days",
        "holding_days": 3,
        "take_profit_rate": 0.09,
    },
    "s6": {
        "name": "策略6",
        "initial_cash": 200000.0,
        "allocation": "fixed",
        "holding_mode": "five_days",
        "holding_days": 5,
        "take_profit_rate": 0.09,
    },
    "s7": {
        "name": "策略7",
        "initial_cash": 200000.0,
        "allocation": "fixed",
        "holding_mode": "five_days",
        "holding_days": 5,
        "take_profit_rate": 0.09,
    },
    "s8": {
        "name": "策略8",
        "initial_cash": 500000.0,
        "allocation": "fixed",
        "holding_mode": "three_days",
        "holding_days": 3,
        "take_profit_rate": 0.12,
    },
    "s9": {
        "name": "策略9",
        "initial_cash": 500000.0,
        "allocation": "fixed",
        "holding_mode": "five_days",
        "holding_days": 5,
        "take_profit_rate": 0.12,
    },
    "s10": {
        "name": "策略10",
        "initial_cash": 1000000.0,
        "allocation": "fixed",
        "holding_mode": "three_days",
        "holding_days": 3,
        "take_profit_rate": 0.12,
    },
    "s11": {
        "name": "策略11",
        "initial_cash": 1000000.0,
        "allocation": "fixed",
        "holding_mode": "five_days",
        "holding_days": 5,
        "take_profit_rate": 0.12,
    },
    "s12": {
        "name": "策略12",
        "initial_cash": 1000000.0,
        "allocation": "fixed",
        "holding_mode": "three_days",
        "holding_days": 3,
        "take_profit_rate": 0.09,
    },
    "s13": {
        "name": "策略13",
        "initial_cash": 1000000.0,
        "allocation": "fixed",
        "holding_mode": "five_days",
        "holding_days": 5,
        "take_profit_rate": 0.09,
    },
}
DEFAULT_TAKE_PROFIT_RATE = TAKE_PROFIT_RATE


def take_profit_rate(strategy_id: str) -> float:
    config = STRATEGY_CONFIGS.get(strategy_id) or {}
    return float(config.get("take_profit_rate", DEFAULT_TAKE_PROFIT_RATE))
_INSTRUMENT_DETAIL_CACHE: Dict[str, Dict[str, Any]] = {}


def ensure_state(state: Dict[str, Any]) -> Dict[str, Any]:
    root = state.setdefault(STATE_KEY, {})
    root["version"] = STATE_VERSION
    root.setdefault("created_at", dt.datetime.now().isoformat(timespec="seconds"))
    root.setdefault("predictions", {})
    root.setdefault("buy_attempts", {})
    root.setdefault("plans", {})
    root.setdefault("processed_trades", {})
    accounts = root.setdefault("accounts", {})
    for strategy_id, config in STRATEGY_CONFIGS.items():
        account = accounts.setdefault(strategy_id, {})
        account.setdefault("strategy_id", strategy_id)
        account.setdefault("name", config["name"])
        account.setdefault("initial_cash", float(config["initial_cash"]))
        account.setdefault("cash", float(config["initial_cash"]))
        account.setdefault("realized_pnl", 0.0)
    return root


def strategy_config(strategy_id: str) -> Dict[str, Any]:
    return STRATEGY_CONFIGS[strategy_id]


def plan_key(strategy_id: str, buy_date: str, code: str) -> str:
    return f"{strategy_id}:{buy_date}:{code}"


def iter_plans(root: Dict[str, Any], strategy_id: str = "") -> Iterable[Tuple[str, Dict[str, Any]]]:
    for key, plan in root.setdefault("plans", {}).items():
        if not isinstance(plan, dict):
            continue
        if strategy_id and str(plan.get("strategy_id") or "") != strategy_id:
            continue
        yield key, plan


def order_remark(strategy_id: str, action: str, buy_date: str, code: str) -> str:
    number = str(code).split(".", 1)[0]
    return f"{ORDER_PREFIX}{strategy_id[1:]}{action}{buy_date}_{number}"


def parse_order_remark(value: Any) -> Dict[str, str]:
    match = re.fullmatch(r"m(1[0-3]|[1-9])([btfe])(\d{8})_(\d{6})", str(value or ""))
    if not match:
        return {}
    return {
        "strategy_id": f"s{match.group(1)}",
        "action": match.group(2),
        "buy_date": match.group(3),
        "number": match.group(4),
    }


def is_multi_remark(value: Any) -> bool:
    return bool(parse_order_remark(value))


def newest_order_for_remark(orders: Iterable[Any], remark: str) -> Any:
    matches = [item for item in orders if str(getattr(item, "order_remark", "") or "") == remark]
    if not matches:
        return None
    active = [item for item in matches if qmt_broker.order_is_active(item)]
    candidates = active or matches
    return max(candidates, key=lambda item: int(getattr(item, "order_time", 0) or 0))


def compact_prediction_items(items: Sequence[Dict[str, Any]], score_reader: Any) -> List[Dict[str, Any]]:
    compact: List[Dict[str, Any]] = []
    for item in items:
        code = str(item.get("stock_code") or "")
        mean = score_reader(item, "mean")
        std = score_reader(item, "std")
        if not code or mean is None:
            continue
        compact.append({"stock_code": code, "mean": float(mean), "std": float(std or 0.0)})
    return compact


def prune_prediction_cache(root: Dict[str, Any], keep: int = 10) -> None:
    predictions = root.setdefault("predictions", {})
    for key in sorted(predictions)[:-max(1, keep)]:
        predictions.pop(key, None)


def active_buy_reserve(root: Dict[str, Any], strategy_id: str) -> float:
    reserve = 0.0
    for _key, plan in iter_plans(root, strategy_id):
        if plan.get("buy_order_active"):
            unfilled = max(0, int(plan.get("shares") or 0) - int(plan.get("filled_shares") or 0))
            shares = max(1, int(plan.get("shares") or 0))
            reserve += float(plan.get("buy_reserved") or 0.0) * unfilled / shares
    return reserve


def available_cash(root: Dict[str, Any], strategy_id: str) -> float:
    account = root["accounts"][strategy_id]
    return max(0.0, float(account.get("cash") or 0.0) - active_buy_reserve(root, strategy_id))


def trading_days_elapsed(buy_date: str, current_date: str) -> int:
    start = dt.datetime.strptime(buy_date, "%Y%m%d").date()
    end = dt.datetime.strptime(current_date, "%Y%m%d").date()
    if end <= start:
        return 0
    count = 0
    cursor = start + dt.timedelta(days=1)
    while cursor <= end:
        if util.is_trading_day(cursor, use_api=False):
            count += 1
        cursor += dt.timedelta(days=1)
    return count


def next_trading_day(buy_date: str) -> str:
    cursor = dt.datetime.strptime(buy_date, "%Y%m%d").date() + dt.timedelta(days=1)
    while not util.is_trading_day(cursor, use_api=False):
        cursor += dt.timedelta(days=1)
    return cursor.strftime("%Y%m%d")


def instrument_detail(code: str) -> Dict[str, Any]:
    if code in _INSTRUMENT_DETAIL_CACHE:
        return _INSTRUMENT_DETAIL_CACHE[code]
    try:
        detail = xtdata.get_instrument_detail(code) or {}
        result = detail if isinstance(detail, dict) else {}
        if result:
            _INSTRUMENT_DETAIL_CACHE[code] = result
        return result
    except Exception as exc:
        util.log(f"[multi][filter] 合约详情失败 code={code} error={exc}")
        return {}


_DAILY_VOLUME_CACHE: Dict[Tuple[str, str], bool] = {}


def has_trading_volume(code: str, trade_date: str) -> bool:
    """判断个股在 trade_date 当日是否有成交量。

    以当日日线成交量为准（与行情图口径一致），替代实时 IsTrading 元数据，
    用于停牌判定：当日无成交量视为停牌。行情查询失败时按“有成交量”处理，
    避免因数据缺失误判停牌。
    """
    cache_key = (code, trade_date)
    if cache_key in _DAILY_VOLUME_CACHE:
        return _DAILY_VOLUME_CACHE[cache_key]
    result = True
    try:
        start = f"{trade_date}000000"
        end = f"{trade_date}150000"
        xtdata.download_history_data(code, "1d", start, end)
        data = xtdata.get_market_data_ex(
            ["volume"],
            [code],
            period="1d",
            start_time=start,
            end_time=end,
            count=-1,
            dividend_type="none",
            fill_data=False,
        )
        frame = data.get(code) if isinstance(data, dict) else None
        if frame is None or getattr(frame, "empty", True):
            result = False
        else:
            volumes = frame.get("volume")
            total = float(volumes.sum()) if volumes is not None else 0.0
            result = total > 0
    except Exception as exc:
        util.log(f"[multi][filter] 成交量查询失败 code={code} date={trade_date} error={exc}")
        result = True
    _DAILY_VOLUME_CACHE[cache_key] = result
    return result


def parse_listing_date(detail: Dict[str, Any]) -> Optional[dt.date]:
    for key in ("OpenDate", "ListDate", "list_date", "IPODate"):
        raw = str(detail.get(key) or "").strip().replace("-", "").replace("/", "")
        match = re.search(r"(\d{8})", raw)
        if match:
            try:
                return dt.datetime.strptime(match.group(1), "%Y%m%d").date()
            except ValueError:
                continue
    return None


def is_delisted(detail: Dict[str, Any]) -> bool:
    for key in ("list_status", "ListStatus", "InstrumentStatus"):
        if str(detail.get(key) or "").strip().upper() == "D":
            return True
    return False


NEW_STOCK_DAYS = 60          # 次新阈值:上市不足 60 自然日则排除(strict_listing_filter)。
_LISTING_LOOKBACK_DAYS = 200  # 反推上市点时的日线回看跨度(足够覆盖 60 天问题并留余量)。
_BAR_DATES_CACHE: Dict[str, List[str]] = {}


def _daily_bar_dates(code: str, start_date: str) -> List[str]:
    """升序返回该票 [start_date, 今] 内可用的日线交易日(8 位)。

    离线 shim 下读导出窗口内的日线,实盘下走真实 xtdata。按 code 缓存(同一进程内
    多交易日复用,回看窗重叠、首根 bar 分类稳定)。查询失败/无数据返回空列表。"""
    cache = _BAR_DATES_CACHE.get(code)
    if cache is not None:
        return cache
    dates: List[str] = []
    try:
        xtdata.download_history_data(code, "1d", start_date + "000000", "")
        data = xtdata.get_market_data_ex(
            ["close"], [code], period="1d",
            start_time=start_date + "000000", end_time="", count=-1,
            dividend_type="none", fill_data=False,
        )
        frame = data.get(code) if isinstance(data, dict) else None
        if frame is not None and not getattr(frame, "empty", True):
            dates = sorted(str(d)[:8] for d in list(frame.index) if len(str(d)) >= 8)
    except Exception as exc:
        util.log(f"[multi][filter] 日线回看失败 code={code} error={exc}")
    _BAR_DATES_CACHE[code] = dates
    return dates


def listed_under_threshold(code: str, trade_date: str) -> Optional[bool]:
    """该票在 trade_date 是否上市不足 NEW_STOCK_DAYS 天 —— 用日线首根 bar 反推,
    免疫 QMT get_instrument_detail 对老股返回 OpenDate=0 的数据毛病。

    返回:True=次新(应排除); False=非次新(放行); None=数据不足以判定(调用方默认放行)。

    原理:个股上市前不可能有日线。cutoff = trade_date - 60 天。
      - 首根 bar <= cutoff  → 60 天前已在交易 → 非次新(False)。
      - 首根 bar > cutoff 且 > 数据窗口起点 → 首根即真实上市点、且晚于 cutoff → 次新(True)。
      - 首根 bar > cutoff 但正好撞窗口起点(被截断,无法证明更早无 bar) → 判不了(None)。
    数据窗口起点 = max(查询起点, 离线 shim 导出 start);实盘无 shim 则取查询起点。"""
    try:
        td = dt.datetime.strptime(trade_date, "%Y%m%d").date()
    except ValueError:
        return None
    cutoff = (td - dt.timedelta(days=NEW_STOCK_DAYS)).strftime("%Y%m%d")
    query_start = (td - dt.timedelta(days=_LISTING_LOOKBACK_DAYS)).strftime("%Y%m%d")
    floor = query_start
    meta = getattr(xtdata, "meta", None)
    if isinstance(meta, dict) and meta.get("start"):
        floor = max(floor, str(meta["start"])[:8])
    dates = _daily_bar_dates(code, query_start)
    if not dates:
        return None
    first = dates[0]
    if first <= cutoff:
        return False
    if first > floor:
        return True
    return None


def filter_buy_candidates(
    strategy_id: str,
    codes: Sequence[str],
    trade_date: str,
    prices_override: Optional[Dict[str, float]] = None,
    up_prices_override: Optional[Dict[str, float]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    accepted, stats, _decisions = evaluate_buy_candidates(
        strategy_id,
        codes,
        trade_date,
        prices_override,
        up_prices_override,
    )
    return accepted, stats


def evaluate_buy_candidates(
    strategy_id: str,
    codes: Sequence[str],
    trade_date: str,
    prices_override: Optional[Dict[str, float]] = None,
    up_prices_override: Optional[Dict[str, float]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], List[Dict[str, Any]]]:
    prices = prices_override if prices_override is not None else qmt_broker.get_last_prices(codes)
    up_prices = up_prices_override if up_prices_override is not None else qmt_broker.get_buy_limit_prices(codes, prices)
    accepted: List[Dict[str, Any]] = []
    decisions: List[Dict[str, Any]] = []
    stats: Dict[str, int] = {
        "invalid_price": 0,
        "near_limit": 0,
        "paused": 0,
        "st": 0,
        "delisted": 0,
        "listing_date": 0,
        "new_stock": 0,
    }
    strict_listing_filter = strategy_id in (
        "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "s12", "s13",
    )
    today = dt.datetime.strptime(trade_date, "%Y%m%d").date()
    for rank, code in enumerate(codes, start=1):
        price = float(prices.get(code) or 0.0)
        up_price = float(up_prices.get(code) or 0.0)
        decision = {"rank": rank, "code": code, "price": price, "up_price": up_price, "eligible": False, "reason": ""}
        if price <= 0 or up_price <= 0:
            stats["invalid_price"] += 1
            decision["reason"] = "invalid_price"
            decisions.append(decision)
            continue
        if price >= up_price * NEAR_LIMIT_RATIO:
            stats["near_limit"] += 1
            decision["reason"] = "near_limit"
            decisions.append(decision)
            continue
        detail = instrument_detail(code)
        name = str(detail.get("InstrumentName") or detail.get("ProductName") or "").upper()
        if "ST" in name or "退" in name or (not name and util.is_st_stock(code)):
            stats["st"] += 1
            decision["reason"] = "st"
            decisions.append(decision)
            continue
        if not has_trading_volume(code, trade_date):
            stats["paused"] += 1
            decision["reason"] = "paused"
            decisions.append(decision)
            continue
        if is_delisted(detail):
            stats["delisted"] += 1
            decision["reason"] = "delisted"
            decisions.append(decision)
            continue
        if strict_listing_filter:
            listing_date = parse_listing_date(detail)
            if listing_date is not None:
                # 合约详情有有效上市日 -> 直接按天数判(快路径)。
                if (today - listing_date).days < NEW_STOCK_DAYS:
                    stats["new_stock"] += 1
                    decision["reason"] = "new_stock"
                    decisions.append(decision)
                    continue
            else:
                # OpenDate 缺失/为 0(老股常见毛病):用日线首根 bar 反推,不再一律拒。
                # 仅当能证明确为次新才排除;判不了(None)/非次新(False)都放行。
                if listed_under_threshold(code, trade_date) is True:
                    stats["new_stock"] += 1
                    decision["reason"] = "new_stock"
                    decisions.append(decision)
                    continue
        decision["eligible"] = True
        decision["reason"] = "eligible"
        decisions.append(decision)
        accepted.append(
            {
                "code": code,
                "price": price,
                "up_price": up_price,
                "limit_price": min(qmt_broker.round_stock_price(price * BUY_LIMIT_RATIO), up_price),
            }
        )
    return accepted, stats, decisions


def _existing_plan(root: Dict[str, Any], strategy_id: str, trade_date: str, code: str) -> Optional[Dict[str, Any]]:
    plan = root.setdefault("plans", {}).get(plan_key(strategy_id, trade_date, code))
    return plan if isinstance(plan, dict) else None


def _submit_buy(
    trader: XtQuantTrader,
    account: StockAccount,
    root: Dict[str, Any],
    strategy_id: str,
    trade_date: str,
    item: Dict[str, Any],
    target_amount: float,
    args: argparse.Namespace,
) -> bool:
    code = str(item["code"])
    if _existing_plan(root, strategy_id, trade_date, code):
        return False
    price = float(item["price"])
    limit_price = float(item["limit_price"])
    volume = qmt_broker.calc_order_volume(target_amount, price, int(args.lot_size))
    if volume <= 0:
        return False
    strategy_cash = available_cash(root, strategy_id)
    reserve = volume * limit_price
    fixed_allocation = strategy_config(strategy_id)["allocation"] == "fixed"
    cash_reserve = min(reserve, target_amount) if fixed_allocation else reserve
    if not fixed_allocation and reserve > strategy_cash + 0.000001:
        volume = qmt_broker.calc_order_volume(strategy_cash, limit_price, int(args.lot_size))
        reserve = volume * limit_price
        cash_reserve = reserve
    if volume <= 0:
        return False
    remark = order_remark(strategy_id, "b", trade_date, code)
    order_id = qmt_broker.submit_stock_order(
        trader,
        account,
        code,
        "buy",
        volume,
        "limit",
        remark,
        bool(args.execute),
        limit_price,
    )
    if order_id <= 0 and args.execute:
        return False
    key = plan_key(strategy_id, trade_date, code)
    root["plans"][key] = {
        "strategy_id": strategy_id,
        "strategy_name": strategy_config(strategy_id)["name"],
        "code": code,
        "buy_date": trade_date,
        "shares": volume,
        "filled_shares": 0,
        "sold_shares": 0,
        "buy_reference_price": price,
        "buy_limit_price": limit_price,
        "buy_order_id": order_id,
        "buy_remark": remark,
        "buy_order_active": bool(args.execute),
        "buy_reserved": cash_reserve,
        "buy_submit_time": time.time(),
        "target_price": round(price * (1.0 + take_profit_rate(strategy_id)), 4),
        "status": "buy_submitted" if args.execute else "preview",
    }
    return True


def buy_for_strategy(
    trader: XtQuantTrader,
    account: StockAccount,
    state: Dict[str, Any],
    strategy_id: str,
    trade_date: str,
    ranked_codes: Sequence[str],
    args: argparse.Namespace,
    market_snapshot: Optional[Tuple[Dict[str, float], Dict[str, float]]] = None,
) -> Dict[str, Any]:
    root = ensure_state(state)
    attempt_key = f"{trade_date}:{strategy_id}"
    if attempt_key in root["buy_attempts"]:
        return {"strategy_id": strategy_id, "skipped": "already_attempted"}
    prices = market_snapshot[0] if market_snapshot else None
    up_prices = market_snapshot[1] if market_snapshot else None
    accepted, filtered, decisions = evaluate_buy_candidates(strategy_id, ranked_codes, trade_date, prices, up_prices)
    cash_before = available_cash(root, strategy_id)
    submitted = 0
    submitted_codes = set()
    insufficient_cash_codes = set()
    order_failed_codes = set()
    config = strategy_config(strategy_id)
    if accepted and cash_before > 0:
        if config["allocation"] == "equal":
            distributable = cash_before * CAPITAL_BUFFER_RATIO
            affordable = list(accepted)
            while affordable:
                per_stock = distributable / len(affordable)
                next_affordable = [
                    item for item in affordable
                    if float(item["price"]) * int(args.lot_size) <= per_stock
                ]
                if len(next_affordable) == len(affordable):
                    break
                affordable = next_affordable
            per_stock = distributable / len(affordable) if affordable else 0.0
            affordable_codes = {str(item["code"]) for item in affordable}
            insufficient_cash_codes.update(str(item["code"]) for item in accepted if str(item["code"]) not in affordable_codes)
            for item in affordable:
                if _submit_buy(trader, account, root, strategy_id, trade_date, item, per_stock, args):
                    submitted += 1
                    submitted_codes.add(str(item["code"]))
                else:
                    order_failed_codes.add(str(item["code"]))
        else:
            for index, item in enumerate(accepted):
                if available_cash(root, strategy_id) < FIXED_BUY_AMOUNT:
                    insufficient_cash_codes.update(str(row["code"]) for row in accepted[index:])
                    break
                if _submit_buy(trader, account, root, strategy_id, trade_date, item, FIXED_BUY_AMOUNT, args):
                    submitted += 1
                    submitted_codes.add(str(item["code"]))
                else:
                    order_failed_codes.add(str(item["code"]))
    elif accepted:
        insufficient_cash_codes.update(str(item["code"]) for item in accepted)
    for decision in decisions:
        code = str(decision["code"])
        if code in submitted_codes:
            decision["reason"] = "submitted"
        elif code in insufficient_cash_codes:
            decision["reason"] = "insufficient_cash"
        elif code in order_failed_codes or decision.get("eligible"):
            decision["reason"] = "order_failed"
    root["buy_attempts"][attempt_key] = {
        "time": dt.datetime.now().isoformat(timespec="seconds"),
        "candidate_count": len(ranked_codes),
        "accepted_count": len(accepted),
        "submitted_count": submitted,
        "cash_before": cash_before,
        "filters": filtered,
        "candidates": decisions,
    }
    util.save_state(state)
    util.log(
        f"[multi][buy] strategy={strategy_id} ranked={len(ranked_codes)} "
        f"accepted={len(accepted)} submitted={submitted} cash={cash_before:.2f} filters={filtered}"
    )
    return root["buy_attempts"][attempt_key]


def buy_all_strategies(
    trader: XtQuantTrader,
    account: StockAccount,
    state: Dict[str, Any],
    trade_date: str,
    selections: Dict[str, Sequence[str]],
    args: argparse.Namespace,
) -> None:
    all_codes = list(dict.fromkeys(code for codes in selections.values() for code in codes))
    prices = qmt_broker.get_last_prices(all_codes)
    up_prices = qmt_broker.get_buy_limit_prices(all_codes, prices)
    market_snapshot = (prices, up_prices)
    for strategy_id in STRATEGY_CONFIGS:
        buy_for_strategy(
            trader,
            account,
            state,
            strategy_id,
            trade_date,
            selections.get(strategy_id, []),
            args,
            market_snapshot,
        )


def _trade_identity(trade: Any) -> str:
    traded_id = str(getattr(trade, "traded_id", "") or getattr(trade, "trade_id", "") or "")
    if traded_id:
        return f"id:{traded_id}"
    return "fallback:{0}:{1}:{2}:{3}:{4}".format(
        int(getattr(trade, "order_id", 0) or 0),
        int(getattr(trade, "traded_time", 0) or 0),
        float(getattr(trade, "traded_price", 0.0) or 0.0),
        int(getattr(trade, "traded_volume", 0) or 0),
        str(getattr(trade, "order_remark", "") or ""),
    )


def _find_plan(root: Dict[str, Any], parsed: Dict[str, str], code: str) -> Optional[Dict[str, Any]]:
    key = plan_key(parsed["strategy_id"], parsed["buy_date"], code)
    plan = root.setdefault("plans", {}).get(key)
    return plan if isinstance(plan, dict) else None


def sync_trade_fills(
    trader: XtQuantTrader,
    account: StockAccount,
    state: Dict[str, Any],
) -> None:
    root = ensure_state(state)
    changed = False
    for trade in trader.query_stock_trades(account) or []:
        remark = str(getattr(trade, "order_remark", "") or "")
        parsed = parse_order_remark(remark)
        if not parsed:
            continue
        identity = _trade_identity(trade)
        if identity in root["processed_trades"]:
            continue
        code = str(getattr(trade, "stock_code", "") or "")
        plan = _find_plan(root, parsed, code)
        price = float(getattr(trade, "traded_price", 0.0) or 0.0)
        volume = int(getattr(trade, "traded_volume", 0) or 0)
        if plan is None or price <= 0 or volume <= 0:
            continue
        account_state = root["accounts"][parsed["strategy_id"]]
        amount = price * volume
        commission = float(getattr(trade, "commission", 0.0) or 0.0)
        order_type = int(getattr(trade, "order_type", 0) or 0)
        if order_type == xtconstant.STOCK_BUY:
            old_volume = int(plan.get("filled_shares") or 0)
            old_amount = float(plan.get("buy_amount") or 0.0)
            new_volume = old_volume + volume
            new_amount = old_amount + amount
            plan["filled_shares"] = new_volume
            plan["buy_amount"] = new_amount
            plan["buy_commission"] = float(plan.get("buy_commission") or 0.0) + commission
            plan["buy_price"] = new_amount / new_volume
            plan["target_price"] = round(plan["buy_price"] * (1.0 + take_profit_rate(parsed["strategy_id"])), 4)
            plan["status"] = "open" if new_volume >= int(plan.get("shares") or 0) else "buy_partial"
            if new_volume >= int(plan.get("shares") or 0):
                plan["buy_order_active"] = False
            account_state["cash"] = float(account_state.get("cash") or 0.0) - amount - commission
            account_state["realized_pnl"] = float(account_state.get("realized_pnl") or 0.0) - commission
        elif order_type == xtconstant.STOCK_SELL:
            old_volume = int(plan.get("sold_shares") or 0)
            old_amount = float(plan.get("sell_amount") or 0.0)
            new_volume = old_volume + volume
            new_amount = old_amount + amount
            plan["sold_shares"] = new_volume
            plan["sell_amount"] = new_amount
            plan["sell_commission"] = float(plan.get("sell_commission") or 0.0) + commission
            plan["sell_price"] = new_amount / new_volume
            remaining = max(0, int(plan.get("filled_shares") or 0) - new_volume)
            plan["status"] = "closed" if remaining <= 0 else "sell_partial"
            if remaining <= 0:
                plan["sell_order_active"] = False
            account_state["cash"] = float(account_state.get("cash") or 0.0) + amount - commission
            realized = amount - float(plan.get("buy_price") or 0.0) * volume - commission
            account_state["realized_pnl"] = float(account_state.get("realized_pnl") or 0.0) + realized
        else:
            continue
        root["processed_trades"][identity] = {
            "strategy_id": parsed["strategy_id"],
            "code": code,
            "order_id": int(getattr(trade, "order_id", 0) or 0),
            "price": price,
            "volume": volume,
            "commission": commission,
            "side": "buy" if order_type == xtconstant.STOCK_BUY else "sell",
        }
        changed = True
    if changed:
        util.save_state(state)


def reconcile_orders(
    trader: XtQuantTrader,
    account: StockAccount,
    state: Dict[str, Any],
    trade_date: str,
) -> None:
    root = ensure_state(state)
    orders = qmt_broker.query_orders_list(trader, account)
    by_id = {qmt_broker.order_id_value(order): order for order in orders if qmt_broker.order_id_value(order) > 0}
    remarks = {
        str(getattr(order, "order_remark", "") or "")
        for order in orders
        if is_multi_remark(getattr(order, "order_remark", ""))
    }
    by_remark = {remark: newest_order_for_remark(orders, remark) for remark in remarks}
    changed = False
    for _key, plan in iter_plans(root):
        buy_order_id = int(plan.get("buy_order_id") or 0)
        if plan.get("buy_order_active"):
            order = by_id.get(buy_order_id) or by_remark.get(str(plan.get("buy_remark") or ""))
            if order is not None and qmt_broker.order_id_value(order) != buy_order_id:
                plan["buy_order_id"] = qmt_broker.order_id_value(order)
                changed = True
            if order is not None and qmt_broker.order_status(order) in TERMINAL_ORDER_STATUSES:
                plan["buy_order_active"] = False
                if int(plan.get("filled_shares") or 0) <= 0:
                    plan["status"] = "buy_unfilled"
                changed = True
            elif str(plan.get("buy_date") or "") < trade_date and order is None:
                plan["buy_order_active"] = False
                if int(plan.get("filled_shares") or 0) <= 0:
                    plan["status"] = "buy_unfilled"
                changed = True
            elif order is None and time.time() - float(plan.get("buy_submit_time") or 0.0) >= qmt_broker.SELL_RETRY_WAIT_SECONDS:
                plan["buy_order_active"] = False
                if int(plan.get("filled_shares") or 0) <= 0:
                    plan["status"] = "buy_unfilled"
                changed = True
        sell_order_id = int(plan.get("sell_order_id") or 0)
        if plan.get("sell_order_active"):
            order = by_id.get(sell_order_id) or by_remark.get(str(plan.get("sell_remark") or ""))
            if order is not None and qmt_broker.order_id_value(order) != sell_order_id:
                plan["sell_order_id"] = qmt_broker.order_id_value(order)
                changed = True
            if order is not None and qmt_broker.order_status(order) in TERMINAL_ORDER_STATUSES:
                plan["sell_order_active"] = False
                changed = True
            elif str(plan.get("sell_order_date") or "") < trade_date and order is None:
                plan["sell_order_active"] = False
                changed = True
            elif order is None and time.time() - float(plan.get("sell_submit_time") or 0.0) >= qmt_broker.SELL_RETRY_WAIT_SECONDS:
                plan["sell_order_active"] = False
                changed = True
    if changed:
        util.save_state(state)


def _remaining_shares(plan: Dict[str, Any]) -> int:
    return max(0, int(plan.get("filled_shares") or 0) - int(plan.get("sold_shares") or 0))


def reconcile_positions(root: Dict[str, Any], positions: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    internal: Dict[str, int] = {}
    for _key, plan in iter_plans(root):
        remaining = _remaining_shares(plan)
        if remaining > 0:
            code = str(plan.get("code") or "")
            internal[code] = internal.get(code, 0) + remaining
    issues: Dict[str, Dict[str, int]] = {}
    for code, expected in internal.items():
        actual = int(getattr(positions.get(code), "volume", 0) or 0)
        if expected > actual:
            issues[code] = {"internal": expected, "actual": actual}
    root["last_reconciliation"] = {
        "time": dt.datetime.now().isoformat(timespec="seconds"),
        "issues": issues,
    }
    if issues:
        util.log(f"[multi][reconcile] 内部持仓超过QMT实际持仓，暂停相关卖单 issues={issues}")
    return issues


def _sell_limit(code: str, last_price: float, aggressive: bool) -> float:
    if not aggressive:
        return 0.0
    down = qmt_broker.get_force_sell_limit_prices([code], {code: last_price}).get(code, 0.0)
    if down <= 0:
        return 0.0
    return max(qmt_broker.round_stock_price(last_price * SELL_LIMIT_RATIO), float(down))


def _submit_sell(
    trader: XtQuantTrader,
    account: StockAccount,
    plan: Dict[str, Any],
    trade_date: str,
    volume: int,
    action: str,
    limit_price: float,
    args: argparse.Namespace,
) -> bool:
    strategy_id = str(plan["strategy_id"])
    code = str(plan["code"])
    remark = order_remark(strategy_id, action, str(plan["buy_date"]), code)
    order_id = qmt_broker.submit_stock_order(
        trader,
        account,
        code,
        "sell",
        volume,
        "limit",
        remark,
        bool(args.execute),
        limit_price,
    )
    if order_id <= 0 and args.execute:
        return False
    plan["sell_order_id"] = order_id
    plan["sell_remark"] = remark
    plan["sell_order_date"] = trade_date
    plan["sell_order_active"] = bool(args.execute)
    plan["sell_action"] = action
    plan["sell_limit_price"] = limit_price
    plan["status"] = "sell_submitted" if args.execute else "preview_sell"
    plan["sell_submit_time"] = time.time()
    return True


def _cancel_target_order(
    trader: XtQuantTrader,
    account: StockAccount,
    plan: Dict[str, Any],
    orders_by_id: Dict[int, Any],
) -> bool:
    order_id = int(plan.get("sell_order_id") or 0)
    order = orders_by_id.get(order_id)
    if order is None:
        sell_remark = str(plan.get("sell_remark") or "")
        order = newest_order_for_remark(orders_by_id.values(), sell_remark)
    if order is None or not qmt_broker.order_is_active(order):
        plan["sell_order_active"] = False
        return False
    order_id = qmt_broker.order_id_value(order)
    plan["sell_order_id"] = order_id
    if qmt_broker.order_status(order) == xtconstant.ORDER_REPORTED_CANCEL:
        return True
    last_cancel = float(plan.get("cancel_submit_time") or 0.0)
    if time.time() - last_cancel < qmt_broker.CANCEL_RETRY_WAIT_SECONDS:
        return True
    submitted = qmt_broker.cancel_stock_order(
        trader,
        account,
        str(plan["code"]),
        order_id,
        "多策略十点强制卖出前撤销止盈单",
        qmt_broker.order_market_value(order),
        qmt_broker.order_sysid_value(order),
        False,
    )
    if submitted:
        plan["cancel_submit_time"] = time.time()
        plan["status"] = "sell_cancel_pending"
    return submitted


def manage_sells(
    trader: XtQuantTrader,
    account: StockAccount,
    state: Dict[str, Any],
    trade_date: str,
    args: argparse.Namespace,
) -> None:
    root = ensure_state(state)
    reconcile_orders(trader, account, state, trade_date)
    plans = [(key, plan) for key, plan in iter_plans(root) if _remaining_shares(plan) > 0]
    if not plans:
        return
    codes = sorted({str(plan["code"]) for _key, plan in plans})
    prices = qmt_broker.get_last_prices(codes)
    positions = qmt_broker.query_positions_map(trader, account)
    reconciliation_issues = reconcile_positions(root, positions)
    available_by_code = {
        code: int(getattr(positions.get(code), "can_use_volume", 0) or 0) for code in codes
    }
    orders = qmt_broker.query_orders_list(trader, account)
    orders_by_id = {qmt_broker.order_id_value(order): order for order in orders if qmt_broker.order_id_value(order) > 0}
    now = dt.datetime.now().time()
    changed = False
    for _key, plan in sorted(plans, key=lambda row: (str(row[1].get("buy_date") or ""), row[0])):
        buy_date = str(plan.get("buy_date") or "")
        if not buy_date or trade_date <= buy_date:
            continue
        code = str(plan["code"])
        if code in reconciliation_issues:
            continue
        remaining = _remaining_shares(plan)
        if remaining <= 0:
            continue
        strategy_id = str(plan["strategy_id"])
        mode = strategy_config(strategy_id)["holding_mode"]
        last_price = float(prices.get(code) or 0.0)
        if last_price <= 0:
            continue
        detail = instrument_detail(code)
        if "IsTrading" in detail and not bool(detail.get("IsTrading")):
            continue
        active_sell = bool(plan.get("sell_order_active"))
        action = ""
        limit_price = 0.0
        if mode == "next_day":
            sell_date = next_trading_day(buy_date)
            if trade_date < sell_date:
                continue
            force = trade_date > sell_date or now >= util.parse_hms(args.sell_market_time)
            if active_sell and force and str(plan.get("sell_action") or "") == "t":
                _cancel_target_order(trader, account, plan, orders_by_id)
                changed = True
                continue
            if active_sell:
                continue
            if force:
                action = "f"
                limit_price = float(
                    qmt_broker.get_force_sell_limit_prices([code], {code: last_price}).get(code, 0.0)
                )
            else:
                action = "t"
                limit_price = qmt_broker.round_stock_price(float(plan.get("target_price") or 0.0))
        else:
            if active_sell:
                continue
            reached_target = last_price >= float(plan.get("target_price") or math.inf)
            holding_days = int(strategy_config(strategy_id).get("holding_days") or 3)
            expired = trading_days_elapsed(buy_date, trade_date) >= holding_days and now >= util.parse_hms(args.expiry_sell_time)
            if reached_target:
                action = "t"
            elif expired:
                action = "e"
            else:
                continue
            limit_price = _sell_limit(code, last_price, True)
        if not action or limit_price <= 0:
            continue
        can_use = int(available_by_code.get(code) or 0)
        sell_volume = min(remaining, can_use)
        if sell_volume <= 0:
            continue
        if _submit_sell(trader, account, plan, trade_date, sell_volume, action, limit_price, args):
            available_by_code[code] = can_use - sell_volume
            changed = True
            util.log(
                f"[multi][sell] strategy={strategy_id} action={action} code={code} "
                f"volume={sell_volume} price={limit_price}"
            )
    if changed:
        util.save_state(state)


def strategy_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    root = ensure_state(state)
    result: Dict[str, Any] = {}
    for strategy_id, config in STRATEGY_CONFIGS.items():
        account = root["accounts"][strategy_id]
        open_plans = [plan for _key, plan in iter_plans(root, strategy_id) if _remaining_shares(plan) > 0]
        result[strategy_id] = {
            "name": config["name"],
            "initial_cash": float(account.get("initial_cash") or 0.0),
            "cash": float(account.get("cash") or 0.0),
            "available_cash": available_cash(root, strategy_id),
            "realized_pnl": float(account.get("realized_pnl") or 0.0),
            "open_plan_count": len(open_plans),
        }
    return result
