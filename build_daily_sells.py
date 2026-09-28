# -*- coding: utf-8 -*-
"""Build daily_sells_snapshot.json + daily_sell_charts/<date>.json from quant_fresh's
real QMT sell fills. Read-only toward all trading data."""
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
import util
from visual import build_daily_buys
from visual import build_qmt_fills as qf


VISUAL_ROOT = Path(__file__).resolve().parent
OUTPUT_PATH = VISUAL_ROOT / "daily_sells_snapshot.json"
CHART_DIR = VISUAL_ROOT / "daily_sell_charts"
STRATEGY_RULES = {
    "s1": "次日+9%止盈，10:00强制卖出",
    "s2": "次日+9%止盈，10:00强制卖出",
    "s3": "次日+9%止盈，10:00强制卖出",
    "s4": "+9%止盈，持有3日到期卖出",
    "s5": "+9%止盈，持有3日到期卖出",
    "s6": "+9%止盈，持有5日到期卖出",
    "s7": "+9%止盈，持有5日到期卖出",
    "s8": "+12%止盈，持有3日到期卖出",
    "s9": "+12%止盈，持有5日到期卖出",
    "s10": "+12%止盈，持有3日到期卖出",
    "s11": "+12%止盈，持有5日到期卖出",
    "s12": "+9%止盈，持有3日到期卖出",
    "s13": "+9%止盈，持有5日到期卖出",
}
ACTION_LABELS = {
    "t": "达到+9%目标止盈",
    "f": "次日10:00强制卖出",
    "e": "持有到期卖出",
    "take_profit": "达到+9%目标，预估止盈卖出",
    "next_day_force": "次日10:00预估强制卖出",
    "expiry": "持有到期预估卖出",
}


def actual_action_label(strategy_id: str, action: str) -> str:
    if action == "t":
        cfg = config.STRATEGY_CONFIGS.get(strategy_id) or {}
        rate = safe_float(cfg.get("take_profit_rate")) * 100
        return f"达到+{rate:g}%目标止盈" if rate > 0 else "达到目标止盈"
    if action == "e":
        cfg = config.STRATEGY_CONFIGS.get(strategy_id) or {}
        if str(cfg.get("holding_mode") or "") == "next_day":
            return "次日10:00强制卖出"
        return "持有到期卖出"
    return ACTION_LABELS.get(action, "真实卖出委托")


def safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# Fallback fee model, only used when a fill row carries no QMT commission.
FEE_CONFIG = {
    "commission_rate": 0.0003,
    "minimum_commission": 5.0,
    "transfer_fee_rate": 0.00001,
    "stamp_duty_rate": 0.0005,
    "source": "paper_broker.py defaults",
}

def estimated_trade_fees(buy_amount: float, sell_amount: float) -> Dict[str, float]:
    commission_rate = safe_float(FEE_CONFIG["commission_rate"])
    minimum_commission = safe_float(FEE_CONFIG["minimum_commission"])
    transfer_fee_rate = safe_float(FEE_CONFIG["transfer_fee_rate"])
    stamp_duty_rate = safe_float(FEE_CONFIG["stamp_duty_rate"])
    buy_commission = max(minimum_commission, buy_amount * commission_rate) if buy_amount > 0 else 0.0
    sell_commission = max(minimum_commission, sell_amount * commission_rate) if sell_amount > 0 else 0.0
    buy_transfer_fee = buy_amount * transfer_fee_rate
    sell_transfer_fee = sell_amount * transfer_fee_rate
    stamp_duty = sell_amount * stamp_duty_rate
    buy_fee = buy_commission + buy_transfer_fee
    sell_fee = sell_commission + sell_transfer_fee + stamp_duty
    return {
        "buy_commission": round(buy_commission, 4),
        "buy_transfer_fee": round(buy_transfer_fee, 4),
        "sell_commission": round(sell_commission, 4),
        "sell_transfer_fee": round(sell_transfer_fee, 4),
        "stamp_duty": round(stamp_duty, 4),
        "buy_fee": round(buy_fee, 2),
        "sell_fee": round(sell_fee, 2),
        "fees": round(buy_fee + sell_fee, 2),
    }


def fee_model_label(config: Dict[str, Any]) -> str:
    commission_wan = safe_float(config.get("commission_rate")) * 10000
    transfer_wan = safe_float(config.get("transfer_fee_rate")) * 10000
    stamp_wan = safe_float(config.get("stamp_duty_rate")) * 10000
    return (
        f"仓库费率：佣金万{commission_wan:g}（最低{safe_float(config.get('minimum_commission')):g}元）"
        f" · 过户费万{transfer_wan:g} · 卖出印花税万{stamp_wan:g}"
    )


def load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def iter_dict_values(value: Any) -> Iterable[Dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    return (item for item in value.values() if isinstance(item, dict))


def timestamp_date(value: Any) -> str:
    try:
        timestamp = float(value or 0.0)
        return dt.datetime.fromtimestamp(timestamp).strftime("%Y%m%d") if timestamp > 0 else ""
    except (TypeError, ValueError, OSError):
        return ""


def timestamp_time(value: Any) -> str:
    try:
        timestamp = float(value or 0.0)
        return dt.datetime.fromtimestamp(timestamp).strftime("%H:%M:%S") if timestamp > 0 else ""
    except (TypeError, ValueError, OSError):
        return ""


def trading_days_elapsed(buy_date: str, sell_date: str) -> int:
    try:
        start = dt.datetime.strptime(buy_date, "%Y%m%d").date()
        end = dt.datetime.strptime(sell_date, "%Y%m%d").date()
    except (TypeError, ValueError):
        return 0
    count = 0
    cursor = start + dt.timedelta(days=1)
    while cursor <= end:
        if util.is_trading_day(cursor, use_api=False):
            count += 1
        cursor += dt.timedelta(days=1)
    return count


def actual_rows(plans: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for plan in iter_dict_values(plans):
        sold_shares = safe_int(plan.get("sold_shares") or plan.get("sell_volume"))
        order_id = safe_int(plan.get("sell_order_id"))
        order_date = str(plan.get("sell_order_date") or "")
        trade_date = timestamp_date(plan.get("sell_trade_time"))
        sell_date = trade_date or order_date
        if len(sell_date) != 8 or (sold_shares <= 0 and order_id <= 0):
            continue
        buy_price = safe_float(plan.get("buy_price") or plan.get("buy_reference_price"))
        sell_price = safe_float(plan.get("sell_price") or plan.get("sell_limit_price"))
        volume = sold_shares or max(0, safe_int(plan.get("shares")) - safe_int(plan.get("sold_shares")))
        sell_amount = safe_float(plan.get("sell_amount")) or sell_price * sold_shares
        buy_amount = buy_price * sold_shares
        recorded_buy_fee = safe_float(plan.get("buy_commission"))
        recorded_sell_fee = safe_float(plan.get("sell_commission"))
        if recorded_buy_fee > 0 or recorded_sell_fee > 0:
            buy_fee = recorded_buy_fee
            sell_fee = recorded_sell_fee
            fee_source = "actual"
            fee_source_label = "QMT实际费用"
            fee_details = [
                {"label": "QMT买入实际手续费", "amount": round(buy_fee, 4)},
                {"label": "QMT卖出实际手续费", "amount": round(sell_fee, 4)},
            ]
        else:
            fee_values = estimated_trade_fees(buy_amount, sell_amount)
            buy_fee = fee_values["buy_fee"]
            sell_fee = fee_values["sell_fee"]
            fee_source = "estimated"
            fee_source_label = "按仓库配置估算"
            fee_details = [
                {"label": "买入佣金", "amount": fee_values["buy_commission"]},
                {"label": "买入过户费", "amount": fee_values["buy_transfer_fee"]},
                {"label": "卖出佣金", "amount": fee_values["sell_commission"]},
                {"label": "卖出过户费", "amount": fee_values["sell_transfer_fee"]},
                {"label": "卖出印花税", "amount": fee_values["stamp_duty"]},
            ]
        fees = buy_fee + sell_fee
        pnl = sell_amount - buy_amount - fees if sold_shares > 0 else 0.0
        capital_cost = buy_amount + buy_fee
        action = str(plan.get("sell_action") or "")
        rows.append({
            "record_id": str(plan.get("sell_remark") or f"{plan.get('strategy_id')}:{plan.get('buy_date')}:{plan.get('code')}"),
            "data_type": "actual",
            "data_type_label": "真实卖出",
            "strategy_id": str(plan.get("strategy_id") or ""),
            "strategy_name": str(plan.get("strategy_name") or ""),
            "code": str(plan.get("code") or ""),
            "name": str(plan.get("name") or ""),
            "buy_date": str(plan.get("buy_date") or ""),
            "buy_price": buy_price,
            "buy_amount": round(buy_amount, 2),
            "target_price": safe_float(plan.get("target_price")),
            "sell_date": sell_date,
            "sell_time": timestamp_time(plan.get("sell_trade_time")) or timestamp_time(plan.get("sell_submit_time")),
            "sell_price": sell_price,
            "sell_volume": volume,
            "sell_amount": round(sell_amount, 2),
            "buy_fee": round(buy_fee, 2),
            "sell_fee": round(sell_fee, 2),
            "fees": round(fees, 2),
            "fee_details": fee_details,
            "pnl": round(pnl, 2),
            "return_rate": pnl / capital_cost if capital_cost > 0 else 0.0,
            "reason": action,
            "reason_label": actual_action_label(str(plan.get("strategy_id") or ""), action),
            "order_id": order_id,
            "status": str(plan.get("status") or ""),
            "status_label": "真实卖出已成交" if sold_shares > 0 else "真实卖出委托中",
            "fees_included": True,
            "fee_source": fee_source,
            "fee_source_label": fee_source_label,
        })
    return rows


def strategy_summary(strategy_id: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    selected = [row for row in rows if row.get("strategy_id") == strategy_id]
    actual = [row for row in selected if row.get("data_type") == "actual"]
    estimated = [row for row in selected if row.get("data_type") == "estimated"]
    actual_filled = [row for row in actual if safe_int(row.get("sell_volume")) > 0]
    buy_amount = sum(safe_float(row.get("buy_amount")) for row in selected)
    buy_fee = sum(safe_float(row.get("buy_fee")) for row in selected)
    pnl = sum(safe_float(row.get("pnl")) for row in selected)
    return {
        "id": strategy_id,
        "name": config.STRATEGY_CONFIGS[strategy_id]["name"],
        "rule": STRATEGY_RULES[strategy_id],
        "record_count": len(selected),
        "actual_order_count": sum(1 for row in actual if safe_int(row.get("order_id")) > 0),
        "actual_filled_count": len(actual_filled),
        "estimated_sold_count": len(estimated),
        "sell_amount": round(sum(safe_float(row.get("sell_amount")) for row in selected), 2),
        "buy_amount": round(buy_amount, 2),
        "fees": round(sum(safe_float(row.get("fees")) for row in selected), 2),
        "pnl": round(pnl, 2),
        "return_rate": pnl / (buy_amount + buy_fee) if buy_amount + buy_fee > 0 else 0.0,
    }


def build_day(trade_date: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    day_rows = sorted(
        [dict(row) for row in rows if row.get("sell_date") == trade_date],
        key=lambda row: (str(row.get("sell_time") or ""), str(row.get("strategy_id") or ""), str(row.get("code") or "")),
    )
    for row in day_rows:
        row["chart_key"] = f"{trade_date}:{row.get('code')}"
    strategies = [strategy_summary(strategy_id, day_rows) for strategy_id in config.STRATEGY_CONFIGS]
    buy_amount = sum(safe_float(row.get("buy_amount")) for row in day_rows)
    buy_fee = sum(safe_float(row.get("buy_fee")) for row in day_rows)
    pnl = sum(safe_float(row.get("pnl")) for row in day_rows)
    return {
        "date": trade_date,
        "chart_path": f"./daily_sell_charts/{trade_date}.json",
        "record_count": len(day_rows),
        "unique_stock_count": len({str(row.get("code") or "") for row in day_rows if row.get("code")}),
        "actual_order_count": sum(row["actual_order_count"] for row in strategies),
        "actual_filled_count": sum(row["actual_filled_count"] for row in strategies),
        "estimated_sold_count": sum(row["estimated_sold_count"] for row in strategies),
        "sell_amount": round(sum(safe_float(row.get("sell_amount")) for row in day_rows), 2),
        "buy_amount": round(buy_amount, 2),
        "fees": round(sum(safe_float(row.get("fees")) for row in day_rows), 2),
        "pnl": round(pnl, 2),
        "return_rate": pnl / (buy_amount + buy_fee) if buy_amount + buy_fee > 0 else 0.0,
        "strategies": strategies,
        "records": day_rows,
    }


def sell_chart_trading_days(buy_date: str, sell_date: str) -> List[str]:
    try:
        buy = dt.datetime.strptime(buy_date, "%Y%m%d").date()
        sell = dt.datetime.strptime(sell_date, "%Y%m%d").date()
    except (TypeError, ValueError):
        return build_daily_buys.recent_trading_days(sell_date, 3)
    if buy > sell:
        return build_daily_buys.recent_trading_days(sell_date, 3)
    start = dt.datetime.strptime(build_daily_buys.recent_trading_days(buy_date, 4)[0], "%Y%m%d").date()
    result = []
    current = start
    while current <= sell:
        if util.is_trading_day(current, use_api=False):
            result.append(current.strftime("%Y%m%d"))
        current += dt.timedelta(days=1)
    return result


def build_sell_chart_snapshot(trade_date: str, rows: List[Dict[str, Any]], names: Dict[str, str]) -> Dict[str, Any]:
    rows_by_code: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        code = str(row.get("code") or "")
        if code:
            rows_by_code.setdefault(code, []).append(row)
    periods: Dict[str, List[str]] = {}
    for code, code_rows in rows_by_code.items():
        buy_dates = sorted(
            str(row.get("buy_date") or "")
            for row in code_rows
            if len(str(row.get("buy_date") or "")) == 8 and str(row.get("buy_date") or "") <= trade_date
        )
        periods[code] = sell_chart_trading_days(buy_dates[0], trade_date) if buy_dates else build_daily_buys.recent_trading_days(trade_date, 3)
    return build_daily_buys.build_chart_snapshot_for_periods(trade_date, periods, names)


def build_snapshot(plans: Dict[str, Any], extra_dates: Optional[List[str]] = None) -> Dict[str, Any]:
    rows = actual_rows(plans)
    for row in rows:
        row["holding_trading_days"] = trading_days_elapsed(str(row.get("buy_date") or ""), str(row.get("sell_date") or ""))
    date_set = {str(row.get("sell_date")) for row in rows if len(str(row.get("sell_date") or "")) == 8}
    # 把真实成交日(>=cutoff)并进来:即使当日无卖出也保留一个"零卖出"页面,而不是整天消失。
    for d in (extra_dates or []):
        if len(str(d)) == 8:
            date_set.add(str(d))
    # 交易日补洞:区间内任何交易日都渲染页面(当日零卖出则给空页)。周末/节假日不生成。
    if date_set:
        try:
            cursor = dt.datetime.strptime(min(date_set), "%Y%m%d").date()
            hi = dt.datetime.strptime(max(date_set), "%Y%m%d").date()
            while cursor <= hi:
                if util.is_trading_day(cursor, use_api=False):
                    date_set.add(cursor.strftime("%Y%m%d"))
                cursor += dt.timedelta(days=1)
        except ValueError:
            pass
    dates = sorted(date_set, reverse=True)
    if not dates:
        dates = [dt.date.today().strftime("%Y%m%d")]
    return {
        "schema_version": 2,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "schedule": "周一至周五 15:30",
        "latest_date": dates[0],
        "real_state_status": "valid",
        "warning": "",
        "fee_model": {
            **FEE_CONFIG,
            "label": fee_model_label(FEE_CONFIG),
        },
        "days": [build_day(trade_date, rows) for trade_date in dates[:40]],
    }


def main() -> int:
    plans = qf.real_sell_injection()
    extra_dates = qf.real_trade_dates()  # 真实成交日 -> 即使零卖出也保留页面
    snapshot = build_snapshot(plans, extra_dates)
    latest = snapshot["days"][0]
    codes = sorted({str(row.get("code") or "") for row in latest["records"] if row.get("code")})
    names = {str(row.get("code")): str(row.get("name") or "") for row in latest["records"]}
    names.update(build_daily_buys.resolve_stock_names([code for code in codes if not names.get(code)]))
    for row in latest["records"]:
        if not row.get("name"):
            row["name"] = names.get(str(row.get("code") or ""), "")
    target = build_daily_buys.write_json(snapshot, OUTPUT_PATH)
    chart_target = build_daily_buys.write_json(
        build_sell_chart_snapshot(latest["date"], latest["records"], names),
        CHART_DIR / f"{latest['date']}.json",
    )
    print(f"daily sell snapshot updated: {target}")
    print(f"daily sell charts updated: {chart_target}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
