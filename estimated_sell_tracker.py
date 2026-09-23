# -*- coding: utf-8 -*-
"""Track hypothetical sells for historical estimated buys without creating QMT orders."""
import argparse
import datetime as dt
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from xtquant import xtdata

import util


ROOT = Path(__file__).resolve().parent
ESTIMATE_PATH = ROOT / "visual" / "historical_buy_estimates.json"
BUY_SNAPSHOT_PATH = ROOT / "visual" / "daily_buys_snapshot.json"
STATE_PATH = ROOT / "virtual_qmt_data" / "estimated_sell_state.json"
PUBLIC_PATH = ROOT / "visual" / "estimated_sell_snapshot.json"
LOCK_PATH = ROOT / "virtual_qmt_data" / "estimated_sell_tracker.lock"
LOG_PATH = ROOT / "virtual_qmt_data" / "estimated_sell_tracker.log"
TAKE_PROFIT_RATE = 0.09
CAPITAL_BUFFER_RATIO = 0.995
FIXED_BUY_AMOUNT = 100000.0
LOT_SIZE = 100
NEXT_DAY_FORCE_TIME = dt.time(10, 0, 0)
EXPIRY_SELL_TIME = dt.time(14, 55, 0)
STRATEGIES = {
    "s1": {"name": "策略1", "allocation": "equal", "holding_mode": "next_day", "take_profit_rate": 0.09},
    "s2": {"name": "策略2", "allocation": "equal", "holding_mode": "next_day", "take_profit_rate": 0.09},
    "s3": {"name": "策略3", "allocation": "equal", "holding_mode": "next_day", "take_profit_rate": 0.09},
    "s4": {"name": "策略4", "allocation": "fixed", "holding_mode": "three_days", "holding_days": 3, "take_profit_rate": 0.09},
    "s5": {"name": "策略5", "allocation": "fixed", "holding_mode": "three_days", "holding_days": 3, "take_profit_rate": 0.09},
    "s6": {"name": "策略6", "allocation": "fixed", "holding_mode": "five_days", "holding_days": 5, "take_profit_rate": 0.09},
    "s7": {"name": "策略7", "allocation": "fixed", "holding_mode": "five_days", "holding_days": 5, "take_profit_rate": 0.09},
    "s8": {"name": "策略8", "allocation": "fixed", "holding_mode": "three_days", "holding_days": 3, "take_profit_rate": 0.12},
    "s9": {"name": "策略9", "allocation": "fixed", "holding_mode": "five_days", "holding_days": 5, "take_profit_rate": 0.12},
    "s10": {"name": "策略10", "allocation": "fixed", "holding_mode": "three_days", "holding_days": 3, "take_profit_rate": 0.12},
    "s11": {"name": "策略11", "allocation": "fixed", "holding_mode": "five_days", "holding_days": 5, "take_profit_rate": 0.12},
    "s12": {"name": "策略12", "allocation": "fixed", "holding_mode": "three_days", "holding_days": 3, "take_profit_rate": 0.09},
    "s13": {"name": "策略13", "allocation": "fixed", "holding_mode": "five_days", "holding_days": 5, "take_profit_rate": 0.09},
}


def log(message: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {message}"
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as file_obj:
        file_obj.write(line + "\n")


def load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    return util.load_json(str(path), default)


def trading_days_elapsed(buy_date: str, current_date: str) -> int:
    start = dt.datetime.strptime(buy_date, "%Y%m%d").date()
    end = dt.datetime.strptime(current_date, "%Y%m%d").date()
    count = 0
    cursor = start + dt.timedelta(days=1)
    while cursor <= end:
        if util.is_trading_day(cursor, use_api=False):
            count += 1
        cursor += dt.timedelta(days=1)
    return count


def next_trading_day(value: str) -> str:
    cursor = dt.datetime.strptime(value, "%Y%m%d").date() + dt.timedelta(days=1)
    while not util.is_trading_day(cursor, use_api=False):
        cursor += dt.timedelta(days=1)
    return cursor.strftime("%Y%m%d")


def order_volume(amount: float, price: float) -> int:
    if amount <= 0 or price <= 0:
        return 0
    return max(0, int(math.floor(amount / price / LOT_SIZE)) * LOT_SIZE)


def stock_names(source_date: str) -> Dict[str, str]:
    snapshot = load_json(BUY_SNAPSHOT_PATH, {})
    day = next((item for item in snapshot.get("days", []) if item.get("date") == source_date), {})
    names: Dict[str, str] = {}
    for strategy in day.get("strategies", []) if isinstance(day, dict) else []:
        for item in strategy.get("candidates", []) if isinstance(strategy, dict) else []:
            code = str(item.get("code") or "")
            name = str(item.get("name") or "")
            if code and name:
                names[code] = name
    return names


def initialize_state(source_date: str = "all", rebuild: bool = False) -> Dict[str, Any]:
    archive = load_json(ESTIMATE_PATH, {})
    archive_days = archive.get("days", {}) if isinstance(archive.get("days"), dict) else {}
    selected_dates = sorted(archive_days) if source_date in ("", "all") else [source_date]
    selected_dates = [value for value in selected_dates if isinstance(archive_days.get(value), dict)]
    if not selected_dates:
        raise RuntimeError(f"没有找到 {source_date or 'all'} 的历史买入估算")
    existing = {} if rebuild else load_json(STATE_PATH, {})
    if source_date not in ("", "all") and existing.get("source_trade_date") not in (None, "", source_date):
        raise RuntimeError(f"预估卖出状态属于其他日期: {existing.get('source_trade_date')}")
    state: Dict[str, Any] = existing or {
        "schema_version": 2,
        "mode": "estimated_sell_only",
        "source_trade_dates": selected_dates,
        "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "positions": {},
        "events": [],
    }
    state["schema_version"] = 2
    stored_dates = state.get("source_trade_dates", [])
    known_dates = set(stored_dates if isinstance(stored_dates, list) else [])
    legacy_date = str(state.get("source_trade_date") or "")
    if legacy_date and legacy_date != "multiple":
        known_dates.add(legacy_date)
    known_dates.update(selected_dates)
    state["source_trade_dates"] = sorted(known_dates)
    state.pop("source_trade_date", None)
    state["safety"] = {
        "label": "预估卖出，不提交真实委托",
        "qmt_trader_created": False,
        "orders_submitted": 0,
    }
    positions = state.setdefault("positions", {})
    for trade_date in selected_dates:
        day = archive_days[trade_date]
        names = stock_names(trade_date)
        strategies = day.get("strategies", {}) if isinstance(day.get("strategies"), dict) else {}
        for strategy_id, config in STRATEGIES.items():
            estimate = strategies.get(strategy_id, {}) if isinstance(strategies.get(strategy_id), dict) else {}
            candidates = [
                row for row in estimate.get("candidates", [])
                if isinstance(row, dict) and row.get("reason") == "estimated_buy"
            ]
            cash = float(estimate.get("cash_before") or 0.0)
            allocation = cash * CAPITAL_BUFFER_RATIO / len(candidates) if config["allocation"] == "equal" and candidates else FIXED_BUY_AMOUNT
            for row in candidates:
                code = str(row.get("code") or "")
                buy_price = float(row.get("price") or 0.0)
                shares = int(row.get("shares") or 0) or order_volume(allocation, buy_price)
                if not code or buy_price <= 0 or shares <= 0:
                    continue
                key = f"{strategy_id}:{trade_date}:{code}"
                if key in positions:
                    continue
                positions[key] = {
                    "position_key": key,
                    "strategy_id": strategy_id,
                    "strategy_name": config["name"],
                    "code": code,
                    "name": names.get(code, ""),
                    "buy_date": trade_date,
                    "buy_time": "14:53:00",
                    "buy_price": buy_price,
                    "shares": shares,
                    "buy_amount": round(float(row.get("buy_amount") or buy_price * shares), 2),
                    "target_price": round(buy_price * (1.0 + float(config.get("take_profit_rate", TAKE_PROFIT_RATE))), 2),
                    "holding_mode": config["holding_mode"],
                    "holding_days": int(config.get("holding_days") or 1),
                    "status": "tracking",
                    "status_label": "等待预估卖出",
                    "latest_price": 0.0,
                    "max_price_seen": 0.0,
                }
    for position in positions.values():
        if position.get("status") == "estimated_sold" and position.get("estimated_sell_reason") == "next_day_force":
            old_time = str(position.get("estimated_sell_time") or "")
            old_date = str(position.get("estimated_sell_date") or "")
            if old_time and old_time != "10:00:00" and not position.get("estimated_sell_calculated_at"):
                position["estimated_sell_calculated_at"] = f"{old_date} {old_time}".strip()
            position["estimated_sell_time"] = "10:00:00"
    return state


def market_quotes(codes: Sequence[str]) -> Dict[str, Dict[str, float]]:
    raw = xtdata.get_full_tick(list(codes)) or {}
    result: Dict[str, Dict[str, float]] = {}
    for code in codes:
        tick = raw.get(code, {}) if isinstance(raw, dict) else {}
        if not isinstance(tick, dict):
            tick = {}
        last_price = float(tick.get("lastPrice") or 0.0)
        high_price = float(tick.get("high") or tick.get("highPrice") or last_price or 0.0)
        result[code] = {"last_price": last_price, "high_price": max(last_price, high_price)}
    return result


def historical_high(code: str, trade_date: str, end_time: str) -> float:
    try:
        raw = xtdata.get_market_data_ex(
            [], [code], period="1m", start_time=f"{trade_date}093000", end_time=f"{trade_date}{end_time}",
            count=-1, dividend_type="none", fill_data=False,
        )
        frame = raw.get(code) if isinstance(raw, dict) else None
        if frame is None or getattr(frame, "empty", True) or "high" not in frame:
            return 0.0
        return float(frame["high"].max() or 0.0)
    except Exception:
        return 0.0


def historical_close(code: str, trade_date: str, end_time: str) -> float:
    try:
        raw = xtdata.get_market_data_ex(
            [], [code], period="1m", start_time=f"{trade_date}095800", end_time=f"{trade_date}{end_time}",
            count=-1, dividend_type="none", fill_data=False,
        )
        frame = raw.get(code) if isinstance(raw, dict) else None
        if frame is None or getattr(frame, "empty", True) or "close" not in frame:
            return 0.0
        return float(frame["close"].iloc[-1] or 0.0)
    except Exception:
        return 0.0


def sell_signal(
    position: Dict[str, Any],
    trade_date: str,
    now_time: dt.time,
    last_price: float,
    day_high: float,
    pre_force_high: float = 0.0,
    force_price: float = 0.0,
) -> Optional[Dict[str, Any]]:
    buy_date = str(position.get("buy_date") or "")
    if position.get("status") != "tracking" or not buy_date or trade_date <= buy_date or last_price <= 0:
        return None
    target = float(position.get("target_price") or math.inf)
    mode = str(position.get("holding_mode") or "")
    if mode == "next_day":
        sell_date = next_trading_day(buy_date)
        if trade_date < sell_date:
            return None
        if trade_date == sell_date and max(pre_force_high, float(position.get("max_price_seen") or 0.0)) >= target:
            return {"reason": "take_profit", "reason_label": "预估止盈卖出（达到买入点+9%）", "price": target}
        if trade_date > sell_date or now_time >= NEXT_DAY_FORCE_TIME:
            price = force_price or last_price
            return {"reason": "next_day_force", "reason_label": "预估次日10:00强制卖出", "price": price, "effective_time": "10:00:00"}
        return None
    if max(day_high, float(position.get("max_price_seen") or 0.0)) >= target:
        return {"reason": "take_profit", "reason_label": "预估止盈卖出（达到买入点+9%）", "price": target}
    holding_days = int(position.get("holding_days") or 3)
    if trading_days_elapsed(buy_date, trade_date) >= holding_days and now_time >= EXPIRY_SELL_TIME:
        return {"reason": "expiry", "reason_label": f"预估持有{holding_days}日到期卖出", "price": last_price}
    return None


def close_estimated_position(position: Dict[str, Any], signal: Dict[str, Any], now: dt.datetime) -> None:
    sell_price = float(signal["price"])
    shares = int(position["shares"])
    buy_amount = float(position["buy_amount"])
    sell_amount = sell_price * shares
    position.update({
        "status": "estimated_sold",
        "status_label": "已预估卖出",
        "estimated_sell": True,
        "estimated_sell_date": now.strftime("%Y%m%d"),
        "estimated_sell_time": str(signal.get("effective_time") or now.strftime("%H:%M:%S")),
        "estimated_sell_calculated_at": now.strftime("%Y%m%d %H:%M:%S"),
        "estimated_sell_price": round(sell_price, 4),
        "estimated_sell_amount": round(sell_amount, 2),
        "estimated_sell_reason": signal["reason"],
        "estimated_sell_reason_label": signal["reason_label"],
        "estimated_pnl": round(sell_amount - buy_amount, 2),
        "estimated_return_rate": (sell_price / float(position["buy_price"]) - 1.0) if position["buy_price"] else 0.0,
    })


def build_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    positions = list(state.get("positions", {}).values())
    strategies: Dict[str, Dict[str, Any]] = {}
    for strategy_id, config in STRATEGIES.items():
        rows = [row for row in positions if row.get("strategy_id") == strategy_id]
        sold = [row for row in rows if row.get("status") == "estimated_sold"]
        tracking = [row for row in rows if row.get("status") == "tracking"]
        realized = sum(float(row.get("estimated_pnl") or 0.0) for row in sold)
        market_pnl = sum((float(row.get("latest_price") or 0.0) - float(row.get("buy_price") or 0.0)) * int(row.get("shares") or 0) for row in tracking)
        strategies[strategy_id] = {
            "strategy_id": strategy_id,
            "strategy_name": config["name"],
            "position_count": len(rows),
            "tracking_count": len(tracking),
            "estimated_sold_count": len(sold),
            "estimated_realized_pnl": round(realized, 2),
            "estimated_floating_pnl": round(market_pnl, 2),
            "status_label": "无预计买入持仓" if not rows else ("预估卖出完成" if not tracking else "预估卖出跟踪中"),
        }
    sold_all = [row for row in positions if row.get("status") == "estimated_sold"]
    tracking_all = [row for row in positions if row.get("status") == "tracking"]
    return {
        "label": "预估卖出统计（非真实委托）",
        "position_count": len(positions),
        "tracking_count": len(tracking_all),
        "estimated_sold_count": len(sold_all),
        "orders_submitted": 0,
        "estimated_realized_pnl": round(sum(float(row.get("estimated_pnl") or 0.0) for row in sold_all), 2),
        "estimated_floating_pnl": round(sum(item["estimated_floating_pnl"] for item in strategies.values()), 2),
        "strategies": strategies,
    }


def publish(state: Dict[str, Any]) -> None:
    state["updated_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["summary"] = build_summary(state)
    util.save_json(str(STATE_PATH), state)
    util.save_json(str(PUBLIC_PATH), state)


def refresh(state: Dict[str, Any], now: dt.datetime) -> int:
    positions = [row for row in state.get("positions", {}).values() if row.get("status") == "tracking"]
    if not positions:
        publish(state)
        return 0
    trade_date = now.strftime("%Y%m%d")
    quotes = market_quotes(sorted({str(row["code"]) for row in positions}))
    sold = 0
    for position in positions:
        quote = quotes.get(str(position["code"]), {})
        last_price = float(quote.get("last_price") or 0.0)
        day_high = float(quote.get("high_price") or last_price)
        if last_price <= 0:
            position["quote_status"] = "暂无实时价格"
            continue
        position["latest_price"] = last_price
        position["latest_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
        pre_force_high = 0.0
        force_price = 0.0
        if position.get("holding_mode") == "next_day" and now.time() >= NEXT_DAY_FORCE_TIME:
            pre_force_high = historical_high(str(position["code"]), trade_date, "095959")
            force_price = historical_close(str(position["code"]), trade_date, "100000")
            position["max_price_seen"] = max(float(position.get("max_price_seen") or 0.0), pre_force_high)
        else:
            position["max_price_seen"] = max(float(position.get("max_price_seen") or 0.0), day_high)
        signal = sell_signal(position, trade_date, now.time(), last_price, day_high, pre_force_high, force_price)
        if not signal:
            continue
        close_estimated_position(position, signal, now)
        state.setdefault("events", []).append({
            "event": "estimated_sell",
            "label": "预估卖出，不是实际委托",
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "strategy_id": position["strategy_id"],
            "code": position["code"],
            "price": position["estimated_sell_price"],
            "reason": position["estimated_sell_reason"],
        })
        sold += 1
        log(f"[estimated-sell] strategy={position['strategy_id']} code={position['code']} price={position['estimated_sell_price']:.2f} reason={position['estimated_sell_reason']}")
    publish(state)
    return sold


def connect_until_ready(stop_time: dt.time, retry_seconds: float) -> bool:
    while True:
        try:
            util.connect_market_data()
            return True
        except Exception as exc:
            now = dt.datetime.now()
            log(f"[connect] miniQMT行情未就绪，{retry_seconds:.0f}秒后重试 error={exc}")
            if now.time() >= stop_time:
                return False
            time.sleep(max(1.0, retry_seconds))


def run(args: argparse.Namespace) -> int:
    lock = util.single_instance_lock(str(LOCK_PATH))
    if not lock.acquire():
        log("[exit] 已有预估卖出进程运行")
        return 0
    try:
        util.load_env()
        state = initialize_state(args.source_date, rebuild=bool(args.rebuild))
        publish(state)
        log(f"[start] source_date={args.source_date} positions={len(state['positions'])} mode=estimated_sell_only orders=0")
        if not any(row.get("status") == "tracking" for row in state.get("positions", {}).values()):
            state["process_status"] = "completed"
            publish(state)
            log("[complete] 没有待跟踪虚拟持仓，真实委托=0")
            return 0
        if not util.is_trading_day(dt.date.today(), use_api=False):
            state["process_status"] = "waiting_next_trading_day"
            publish(state)
            log("[wait] 今天不是交易日，状态保留到下一次自启动，真实委托=0")
            return 0
        if not connect_until_ready(util.parse_hms(args.stop_time), float(args.connect_retry_seconds)):
            state["process_status"] = "waiting_for_qmt"
            publish(state)
            log("[stop] miniQMT在停止时间前未就绪，状态保留到下次自启动，真实委托=0")
            return 1
        while True:
            now = dt.datetime.now()
            if util.is_trading_day(now.date(), use_api=False):
                refresh(state, now)
            if not any(row.get("status") == "tracking" for row in state.get("positions", {}).values()):
                state["process_status"] = "completed"
                publish(state)
                log("[complete] 所有虚拟持仓已完成预估卖出，真实委托=0")
                return 0
            if now.time() >= util.parse_hms(args.stop_time):
                state["process_status"] = "stopped_for_day"
                publish(state)
                log("[stop] 到达停止时间，未完成持仓保留到下次启动，真实委托=0")
                return 0
            state["process_status"] = "running"
            time.sleep(float(args.interval))
    finally:
        lock.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="历史预计买入的独立预估卖出跟踪器（绝不提交委托）")
    parser.add_argument("--source-date", default="all")
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--stop-time", default="15:30:00")
    parser.add_argument("--connect-retry-seconds", type=float, default=60.0)
    parser.add_argument("--rebuild", action="store_true", help="根据当前历史买入估算重新建立只读预估卖出状态")
    args = parser.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        log(f"[error] {exc}")
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
