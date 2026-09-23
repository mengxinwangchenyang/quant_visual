# -*- coding: utf-8 -*-
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xtquant import xtdata

from visual import xtdata_offline_shim

import multi_strategy
import qmt_broker
import strategy
import strategy_cash_ledger
import util


VISUAL_ROOT = Path(__file__).resolve().parent
QMT_LOCAL = PROJECT_ROOT / "qmt_local"
CSI1000_FILE = QMT_LOCAL / "qmt_csi1000.json"
OUTPUT_PATH = VISUAL_ROOT / "daily_buys_snapshot.json"
CHART_DIR = VISUAL_ROOT / "daily_buy_charts"
ESTIMATE_PATH = VISUAL_ROOT / "historical_buy_estimates.json"
CASH_LEDGER_PATH = PROJECT_ROOT / "virtual_qmt_data" / "strategy_cash_ledger.json"
STRATEGY_META = {
    "s1": {"signal": "14:53按Score2全市场前0.8%（向下取整）与中证1000取交集后买入，当日可用资金均分买入（初始资金50万）；次日+9%限价止盈，10:00强制卖出", "capital": "50万均分"},
    "s2": {"signal": "14:53按Score2全市场前1.17%（向下取整）与中证1000取交集后买入，当日可用资金均分买入（初始资金50万）；次日+9%限价止盈，10:00强制卖出", "capital": "50万均分"},
    "s3": {"signal": "14:53按Score2全市场前1.08%（向下取整）与中证1000取交集后买入，当日可用资金均分买入（初始资金50万）；次日+9%限价止盈，10:00强制卖出", "capital": "50万均分"},
    "s4": {"signal": "14:53按Score2全市场前0.5%（向下取整）买入，每笔固定买入10万元（初始资金20万）；+9%止盈，否则第3个后续交易日14:55卖出", "capital": "20万，每票10万"},
    "s5": {"signal": "14:53按Score2全市场前0.8%（向下取整）买入，每笔固定买入10万元（初始资金50万）；+9%止盈，否则第3个后续交易日14:55卖出", "capital": "50万，每票10万"},
    "s6": {"signal": "14:53按Score2全市场前0.5%（向下取整）买入，每笔固定买入10万元（初始资金20万）；+9%止盈，否则第5个后续交易日14:55卖出", "capital": "20万，每票10万，持有5日"},
    "s7": {"signal": "14:53按Score2全市场前0.4%（向下取整）买入，每笔固定买入10万元（初始资金20万）；+9%止盈，否则第5个后续交易日14:55卖出", "capital": "20万，每票10万，持有5日"},
    "s8": {"signal": "14:53按Score2全市场前0.8%（向下取整）买入，每笔固定买入10万元（初始资金50万）；+12%止盈，否则第3个后续交易日14:55卖出", "capital": "50万，每票10万"},
    "s9": {"signal": "14:53按Score2全市场前0.8%（向下取整）买入，每笔固定买入10万元（初始资金50万）；+12%止盈，否则第5个后续交易日14:55卖出", "capital": "50万，每票10万，持有5日"},
    "s10": {"signal": "14:53按Score2全市场前0.8%（向下取整）买入，每笔固定买入10万元（初始资金100万）；+12%止盈，否则第3个后续交易日14:55卖出", "capital": "100万，每票10万"},
    "s11": {"signal": "14:53按Score2全市场前0.8%（向下取整）买入，每笔固定买入10万元（初始资金100万）；+12%止盈，否则第5个后续交易日14:55卖出", "capital": "100万，每票10万，持有5日"},
    "s12": {"signal": "14:53按Score2全市场前0.8%（向下取整）买入，每笔固定买入10万元（初始资金100万）；+9%止盈，否则第3个后续交易日14:55卖出", "capital": "100万，每票10万"},
    "s13": {"signal": "14:53按Score2全市场前0.8%（向下取整）买入，每笔固定买入10万元（初始资金100万）；+9%止盈，否则第5个后续交易日14:55卖出", "capital": "100万，每票10万，持有5日"},
}
FILTER_LABELS = {
    "invalid_price": "价格无效",
    "near_limit": "涨停附近",
    "paused": "停牌",
    "st": "ST/退市风险",
    "delisted": "已退市",
    "listing_date": "上市日期缺失",
    "new_stock": "次新股",
    "insufficient_cash": "预计资金不足",
}
REASON_LABELS = {
    "invalid_price": "价格或涨停价无效",
    "near_limit": "涨停附近，按规则不买",
    "paused": "停牌，无法买入",
    "no_data": "行情未导出，无法评估",
    "not_submitted": "通道断线，委托未到柜台",
    "st": "ST或退市风险，按规则不买",
    "delisted": "已退市，无法买入",
    "listing_date": "上市日期缺失",
    "new_stock": "上市不足60天",
    "insufficient_cash": "策略可用资金不足",
    "order_failed": "miniQMT委托提交失败",
    "submitted": "已提交买入委托",
    "estimated_buy": "预计可以买入",
    "eligible": "通过风控，未形成委托",
    "pending": "已生成委托，未成交",
    "unknown": "历史原因未记录",
}


def safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def format_timestamp(value: Any) -> str:
    try:
        timestamp = float(value or 0.0)
        return dt.datetime.fromtimestamp(timestamp).strftime("%H:%M:%S") if timestamp > 0 else ""
    except (TypeError, ValueError, OSError):
        return ""


def iter_dict_values(value: Any) -> Iterable[Dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    return (item for item in value.values() if isinstance(item, dict))


def _strategy_initial_cash(strategy_id: str) -> float:
    config = multi_strategy.STRATEGY_CONFIGS.get(strategy_id) or {}
    return safe_float(config.get("initial_cash"))


def _cash_map(value: Any) -> Dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: Dict[str, float] = {}
    for strategy_id in multi_strategy.STRATEGY_CONFIGS:
        cash = optional_float(value.get(strategy_id))
        if cash is not None:
            result[strategy_id] = cash
    return result


def _daily_account_items(value: Any) -> List[Tuple[str, Dict[str, Any]]]:
    if isinstance(value, dict):
        items = value.values()
    elif isinstance(value, list):
        items = value
    else:
        return []
    result: List[Tuple[str, Dict[str, Any]]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        trade_date = str(item.get("trade_date") or "")[:8]
        if len(trade_date) == 8 and trade_date.isdigit():
            result.append((trade_date, item))
    return sorted(result, key=lambda row: row[0])


def load_strategy_cash_timeline(path: Path = CASH_LEDGER_PATH) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Build per-day cash checkpoints from the canonical cash ledger."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    daily_items = _daily_account_items(payload.get("daily_accounts"))
    if not daily_items:
        return {}

    source = str(payload.get("cash_source_mode") or path.name)
    initial_cash = {strategy_id: _strategy_initial_cash(strategy_id) for strategy_id in multi_strategy.STRATEGY_CONFIGS}
    prior_after_buy = dict(initial_cash)
    timeline: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for trade_date, account in daily_items:
        cash_before_sell = _cash_map(account.get("cash_before_sell"))
        cash_after_sells = _cash_map(account.get("cash_after_sells"))
        cash_before_buy = _cash_map(account.get("cash_before_buy"))
        cash_after_buy = _cash_map(account.get("cash_after_buy"))
        next_prior: Dict[str, float] = {}
        for strategy_id in multi_strategy.STRATEGY_CONFIGS:
            default_before_sell = prior_after_buy.get(strategy_id, initial_cash.get(strategy_id, 0.0))
            before_sell = cash_before_sell.get(strategy_id, default_before_sell)
            after_sell = cash_after_sells.get(strategy_id, before_sell)
            before_buy = cash_before_buy.get(strategy_id, after_sell)
            after_buy_inferred = strategy_id not in cash_after_buy
            after_buy = cash_after_buy.get(strategy_id, before_buy)
            timeline[(trade_date, strategy_id)] = {
                "cash_before_sell": before_sell,
                "cash_after_sell": after_sell,
                "cash_before_buy": before_buy,
                "cash_after_buy": after_buy,
                "cash_source": source,
                "cash_after_buy_inferred": after_buy_inferred,
            }
            next_prior[strategy_id] = after_buy
        prior_after_buy = next_prior
    return timeline


def strategy_cash_timeline_source(cash_timeline: Optional[Dict[Tuple[str, str], Dict[str, Any]]]) -> str:
    for row in (cash_timeline or {}).values():
        source = str(row.get("cash_source") or "")
        if source:
            return source
    return ""




def _chart_xtdata():
    """Prefer the local offline shim so chart building does not depend on a live
    xtquant service. If the shim cannot be initialized, fall back to the real
    xtdata object."""
    try:
        return xtdata_offline_shim.OfflineXtData()
    except Exception:
        return xtdata


def collect_trade_dates(
    root: Dict[str, Any],
    estimates: Optional[Dict[str, Any]] = None,
    cash_timeline: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None,
) -> List[str]:
    dates = set()
    for key in root.get("buy_attempts", {}) if isinstance(root.get("buy_attempts"), dict) else {}:
        text = str(key).split(":", 1)[0]
        if len(text) == 8 and text.isdigit():
            dates.add(text)
    for plan in iter_dict_values(root.get("plans", {})):
        text = str(plan.get("buy_date") or "")[:8]
        if len(text) == 8 and text.isdigit():
            dates.add(text)
    estimate_days = (estimates or {}).get("days", {})
    if isinstance(estimate_days, dict):
        for value in estimate_days:
            text = str(value)
            if len(text) == 8 and text.isdigit():
                dates.add(text)
    for key in (cash_timeline or {}):
        trade_date = str(key[0]) if isinstance(key, tuple) and key else ""
        if len(trade_date) == 8 and trade_date.isdigit():
            dates.add(trade_date)
    if not dates:
        dates.add(dt.date.today().strftime("%Y%m%d"))
    return sorted(dates, reverse=True)[:40]


def resolve_stock_names(codes: Sequence[str]) -> Dict[str, str]:
    names: Dict[str, str] = {}
    api = _chart_xtdata()
    for code in codes:
        try:
            detail = api.get_instrument_detail(code) or {}
        except Exception:
            detail = {}
        if isinstance(detail, dict):
            name = str(detail.get("InstrumentName") or detail.get("ProductName") or "").strip()
            if name:
                names[code] = name
    return names


def plan_row(plan: Dict[str, Any], names: Dict[str, str]) -> Dict[str, Any]:
    code = str(plan.get("code") or "")
    shares = safe_int(plan.get("shares"))
    filled_shares = safe_int(plan.get("filled_shares"))
    buy_price = safe_float(plan.get("buy_price")) or safe_float(plan.get("buy_reference_price"))
    buy_amount = safe_float(plan.get("buy_amount")) or filled_shares * buy_price
    return {
        "strategy_id": str(plan.get("strategy_id") or ""),
        "strategy_name": str(plan.get("strategy_name") or ""),
        "code": code,
        "name": names.get(code, ""),
        "submit_time": format_timestamp(plan.get("buy_submit_time")),
        "order_id": safe_int(plan.get("buy_order_id")),
        "remark": str(plan.get("buy_remark") or ""),
        "status": str(plan.get("status") or ""),
        "shares": shares,
        "filled_shares": filled_shares,
        "buy_price": buy_price,
        "buy_limit_price": safe_float(plan.get("buy_limit_price")),
        "buy_amount": buy_amount,
        "near_limit": bool(plan.get("near_limit_fill")),
    }


def real_cutoff() -> str:
    """真实层起点(=估算档 real_order_start_date,现 0820)。cutoff 之前的日子统一走预估
    叙事:旧 m 时代的 buy_attempts/plans(如 0814 的 m2b/m3b 真实成交)不得压制预估层,
    否则买入页显真实、卖出页显预估,两页对不上(用户 0820 反馈 s2/s3 0814买/0817卖不一致)。"""
    try:
        from visual import build_qmt_fills as qf
    except Exception:  # noqa: BLE001
        import build_qmt_fills as qf
    try:
        return qf.order_cutoff()
    except Exception:  # noqa: BLE001
        return "20260820"


def legacy_codes_for_historical_attempt(state: Dict[str, Any], trade_date: str) -> set:
    codes = set()
    plans = state.get("position_plans", {}) if isinstance(state, dict) else {}
    for plan in iter_dict_values(plans):
        if str(plan.get("sell_date") or "") == trade_date and safe_int(plan.get("filled_shares")) > 0:
            code = str(plan.get("code") or "")
            if code:
                codes.add(code)
    return codes


def prediction_items_for_date(root: Dict[str, Any], trade_date: str) -> List[Dict[str, Any]]:
    predictions = root.get("predictions", {}) if isinstance(root.get("predictions"), dict) else {}
    direct = predictions.get(f"{trade_date}:{trade_date}")
    if isinstance(direct, list):
        return direct
    for key, value in predictions.items():
        if str(key).startswith(f"{trade_date}:") and isinstance(value, list):
            return value
    return []


def qmt_csi1000_pool() -> set:
    try:
        doc = json.loads(CSI1000_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        return set()
    return set(str(code).strip() for code in (doc.get("codes") or []) if str(code).strip())


def select_strategies_with_qmt_csi(items: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    pool = qmt_csi1000_pool()
    if not pool:
        return strategy.select_strategies(items)
    original = strategy.resolve_target_pools
    try:
        strategy.resolve_target_pools = lambda: {"csi1000": pool, "kcb": set()}
        return strategy.select_strategies(items)
    finally:
        strategy.resolve_target_pools = original


def plan_lookup(root: Dict[str, Any]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    result = {}
    for plan in iter_dict_values(root.get("plans", {})):
        key = (str(plan.get("buy_date") or ""), str(plan.get("strategy_id") or ""), str(plan.get("code") or ""))
        result[key] = plan
    return result


def backfill_candidate_map(
    state: Dict[str, Any],
    dates: Sequence[str],
    estimates: Optional[Dict[str, Any]] = None,
) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    root = state.get(multi_strategy.STATE_KEY, {}) if isinstance(state, dict) else {}
    root = root if isinstance(root, dict) else {}
    attempts = root.get("buy_attempts", {}) if isinstance(root.get("buy_attempts"), dict) else {}
    plan_by_key = plan_lookup(root)
    result: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    selection_cache: Dict[str, Dict[str, List[str]]] = {}
    latest_date = dates[0] if dates else ""
    estimate_days = (estimates or {}).get("days", {})
    estimate_days = estimate_days if isinstance(estimate_days, dict) else {}
    cutoff = real_cutoff()
    for trade_date in dates:
        estimate_day = estimate_days.get(trade_date, {})
        estimate_strategies = estimate_day.get("strategies", {}) if isinstance(estimate_day, dict) else {}
        estimate_strategies = estimate_strategies if isinstance(estimate_strategies, dict) else {}
        # cutoff 前的旧 attempt 整体忽略(纯预估叙事),cutoff 起才认 attempt。
        day_attempts = attempts if trade_date >= cutoff else {}
        missing_ids = [
            strategy_id
            for strategy_id in multi_strategy.STRATEGY_CONFIGS
            if strategy_id not in estimate_strategies
            and bool(day_attempts.get(f"{trade_date}:{strategy_id}"))
            and not isinstance((day_attempts.get(f"{trade_date}:{strategy_id}") or {}).get("candidates"), list)
        ]
        if missing_ids:
            items = prediction_items_for_date(root, trade_date)
            selection_cache[trade_date] = select_strategies_with_qmt_csi(items) if items else {}
        selections = selection_cache.get(trade_date, {})
        all_selected = list(dict.fromkeys(code for strategy_id in missing_ids for code in selections.get(strategy_id, [])))
        prices = qmt_broker.get_last_prices(all_selected) if trade_date == latest_date and all_selected else {}
        up_prices = qmt_broker.get_buy_limit_prices(all_selected, prices) if prices else {}
        historical_skip = legacy_codes_for_historical_attempt(state, trade_date)
        for strategy_id in multi_strategy.STRATEGY_CONFIGS:
            attempt = day_attempts.get(f"{trade_date}:{strategy_id}", {})
            attempt = attempt if isinstance(attempt, dict) else {}
            estimate = estimate_strategies.get(strategy_id, {})
            estimate = estimate if isinstance(estimate, dict) else {}
            stored = attempt.get("candidates")
            if estimate and not attempt:
                decisions = [dict(item) for item in estimate.get("candidates", []) if isinstance(item, dict)]
                for decision in decisions:
                    decision["reason_source"] = "historical_estimate"
                    decision["reason_source_label"] = str(estimate_day.get("source_label") or "14:53历史行情估算")
                    decision["estimated"] = True
            elif isinstance(stored, list):
                decisions = [dict(item) for item in stored if isinstance(item, dict)]
                for decision in decisions:
                    decision["reason_source"] = "recorded"
            elif attempt:
                codes = list(selections.get(strategy_id, []))
                expected_count = safe_int(attempt.get("candidate_count"))
                filtered_codes = [code for code in codes if code not in historical_skip]
                if expected_count and len(filtered_codes) == expected_count:
                    codes = filtered_codes
                elif expected_count and len(codes) > expected_count:
                    codes = codes[:expected_count]
                if trade_date == latest_date and codes:
                    _accepted, _stats, decisions = multi_strategy.evaluate_buy_candidates(
                        strategy_id,
                        codes,
                        trade_date,
                        prices,
                        up_prices,
                    )
                else:
                    decisions = [
                        {"rank": rank, "code": code, "price": 0.0, "up_price": 0.0, "eligible": False, "reason": "unknown"}
                        for rank, code in enumerate(codes, start=1)
                    ]
                for decision in decisions:
                    decision["reason_source"] = "reconstructed"
            else:
                decisions = []
            for decision in decisions:
                code = str(decision.get("code") or "")
                plan = plan_by_key.get((trade_date, strategy_id, code)) if trade_date >= cutoff else None
                if plan:
                    decision["reason"] = "submitted"
                    decision["eligible"] = True
                    decision["order_status"] = str(plan.get("status") or "")
                    decision["order_id"] = safe_int(plan.get("buy_order_id"))
                    decision["shares"] = safe_int(plan.get("shares"))
                    decision["filled_shares"] = safe_int(plan.get("filled_shares"))
                reason = str(decision.get("reason") or "unknown")
                decision["reason"] = reason
                if not decision.get("reason_label"):
                    decision["reason_label"] = REASON_LABELS.get(reason, reason)
                decision["chart_key"] = f"{trade_date}:{code}"
            result[(trade_date, strategy_id)] = decisions
    return result


def build_day(
    root: Dict[str, Any],
    trade_date: str,
    names: Dict[str, str],
    candidate_map: Optional[Dict[Tuple[str, str], List[Dict[str, Any]]]] = None,
    estimates: Optional[Dict[str, Any]] = None,
    cash_timeline: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    attempts = root.get("buy_attempts", {}) if isinstance(root.get("buy_attempts"), dict) else {}
    cutoff = real_cutoff()
    # cutoff 前统一预估叙事:旧真实 plans/attempt 不入页(与卖出页的预估口径保持一致)。
    plans = [
        plan_row(plan, names)
        for plan in iter_dict_values(root.get("plans", {}))
        if str(plan.get("buy_date") or "") == trade_date and trade_date >= cutoff
    ]
    strategies = []
    estimate_days = (estimates or {}).get("days", {})
    estimate_day = estimate_days.get(trade_date, {}) if isinstance(estimate_days, dict) else {}
    estimate_strategies = estimate_day.get("strategies", {}) if isinstance(estimate_day, dict) else {}
    estimate_strategies = estimate_strategies if isinstance(estimate_strategies, dict) else {}
    for strategy_id, config in multi_strategy.STRATEGY_CONFIGS.items():
        attempt = attempts.get(f"{trade_date}:{strategy_id}", {}) if trade_date >= cutoff else {}
        attempt = attempt if isinstance(attempt, dict) else {}
        estimate = estimate_strategies.get(strategy_id, {}) if not attempt else {}
        estimate = estimate if isinstance(estimate, dict) else {}
        summary_source = attempt or estimate
        strategy_plans = [row for row in plans if row["strategy_id"] == strategy_id]
        candidates = [dict(item) for item in (candidate_map or {}).get((trade_date, strategy_id), [])]
        for item in candidates:
            item["name"] = names.get(str(item.get("code") or ""), "")
        filters = [
            {"key": key, "label": FILTER_LABELS.get(key, key), "count": safe_int(count)}
            for key, count in (summary_source.get("filters", {}) or {}).items()
            if safe_int(count) > 0
        ]
        filled = [row for row in strategy_plans if row["filled_shares"] > 0]
        cash_row = (cash_timeline or {}).get((trade_date, strategy_id), {})
        cash_before_sell = optional_float(cash_row.get("cash_before_sell"))
        cash_after_sell = optional_float(cash_row.get("cash_after_sell"))
        cash_before_buy = optional_float(cash_row.get("cash_before_buy"))
        cash_after_buy = optional_float(cash_row.get("cash_after_buy"))
        if cash_before_buy is None:
            cash_before_buy = optional_float(summary_source.get("cash_before"))
        if cash_after_buy is None:
            cash_after_buy = optional_float(summary_source.get("cash_after"))
        cash_before = cash_before_buy if cash_before_buy is not None else optional_float(summary_source.get("cash_before"))
        strategies.append(
            {
                "id": strategy_id,
                "name": str(config["name"]),
                "signal": STRATEGY_META[strategy_id]["signal"],
                "capital": STRATEGY_META[strategy_id]["capital"],
                "attempted": bool(attempt),
                "estimated": bool(estimate),
                "attempt_time": str(summary_source.get("time") or "")[11:19],
                "candidate_count": safe_int(summary_source.get("candidate_count")),
                "accepted_count": safe_int(summary_source.get("accepted_count")),
                "estimated_buy_count": safe_int(summary_source.get("estimated_buy_count")),
                "submitted_count": safe_int(attempt.get("submitted_count")),
                "pending_count": safe_int(summary_source.get("pending_count")),
                "est_buyable_count": safe_int(summary_source.get("est_buyable_count")),
                "filled_count": len(filled),
                "filled_amount": sum(row["buy_amount"] for row in filled),
                "cash_before": cash_before,
                "cash_before_sell": cash_before_sell,
                "cash_after_sell": cash_after_sell,
                "cash_before_buy": cash_before_buy,
                "cash_after_buy": cash_after_buy,
                "cash_source": str(cash_row.get("cash_source") or ""),
                "cash_after_buy_inferred": bool(cash_row.get("cash_after_buy_inferred")),
                "filters": filters,
                "candidates": candidates,
                "buys": strategy_plans,
            }
        )
    return {
        "date": trade_date,
        "chart_path": f"./daily_buy_charts/{trade_date}.json",
        "completed_strategy_count": sum(1 for row in strategies if row["attempted"]),
        "estimated_strategy_count": sum(1 for row in strategies if row["estimated"]),
        "estimate_source_label": str(estimate_day.get("source_label") or "") if isinstance(estimate_day, dict) else "",
        "candidate_count": sum(row["candidate_count"] for row in strategies),
        "accepted_count": sum(row["accepted_count"] for row in strategies),
        "submitted_count": sum(row["submitted_count"] for row in strategies),
        "pending_count": sum(row["pending_count"] for row in strategies),
        "est_buyable_count": sum(row["est_buyable_count"] for row in strategies),
        "filled_count": sum(row["filled_count"] for row in strategies),
        "filled_amount": sum(row["filled_amount"] for row in strategies),
        "strategies": strategies,
        "buys": sorted(plans, key=lambda row: (row["submit_time"], row["strategy_id"], row["code"])),
    }


def build_snapshot_from_state(
    state: Dict[str, Any],
    names: Optional[Dict[str, str]] = None,
    candidate_map: Optional[Dict[Tuple[str, str], List[Dict[str, Any]]]] = None,
    estimates: Optional[Dict[str, Any]] = None,
    cash_timeline: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    root = state.get(multi_strategy.STATE_KEY, {}) if isinstance(state, dict) else {}
    root = root if isinstance(root, dict) else {}
    dates = collect_trade_dates(root, estimates, cash_timeline)
    codes = sorted(
        {
            str(plan.get("code") or "")
            for plan in iter_dict_values(root.get("plans", {}))
            if str(plan.get("code") or "")
        }
        | {
            str(item.get("code") or "")
            for items in (candidate_map or {}).values()
            for item in items
            if str(item.get("code") or "")
        }
    )
    stock_names = names if names is not None else resolve_stock_names(codes)
    return {
        "schema_version": 2,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "schedule": "周一至周五 15:30",
        "cash_source": strategy_cash_timeline_source(cash_timeline),
        "latest_date": dates[0],
        "days": [build_day(root, trade_date, stock_names, candidate_map, estimates, cash_timeline) for trade_date in dates],
    }


def recent_trading_days(trade_date: str, count: int = 3) -> List[str]:
    current = dt.datetime.strptime(trade_date, "%Y%m%d").date()
    result = []
    while len(result) < count:
        if util.is_trading_day(current, use_api=False):
            result.append(current.strftime("%Y%m%d"))
        current -= dt.timedelta(days=1)
    return list(reversed(result))


def minute_points(raw: Any, trading_days: Sequence[str]) -> List[Dict[str, Any]]:
    if raw is None or getattr(raw, "empty", True):
        return []
    frame = raw.copy()
    frame["stamp_text"] = frame.index.astype(str).str[:14]
    frame["stamp"] = pd.to_datetime(frame["stamp_text"], format="%Y%m%d%H%M%S", errors="coerce")
    frame = frame.dropna(subset=["stamp"]).sort_values("stamp")
    frame = frame[frame["stamp"].dt.strftime("%Y%m%d").isin(set(trading_days))]
    points = []
    for row in frame.to_dict("records"):
        open_price = safe_float(row.get("open"))
        high_price = safe_float(row.get("high"))
        low_price = safe_float(row.get("low"))
        close_price = safe_float(row.get("close"))
        stamp = row.get("stamp")
        if min(open_price, high_price, low_price, close_price) <= 0 or not isinstance(stamp, pd.Timestamp):
            continue
        points.append(
            {
                "time": int(stamp.to_pydatetime().timestamp()),
                "date": stamp.strftime("%Y%m%d"),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": safe_float(row.get("volume")),
            }
        )
    return points


def build_chart_snapshot_for_periods(
    trade_date: str,
    code_trading_days: Dict[str, Sequence[str]],
    names: Dict[str, str],
) -> Dict[str, Any]:
    charts: Dict[str, Any] = {}
    unique_codes = sorted(code for code in code_trading_days if code)
    all_trading_days = sorted({day for days in code_trading_days.values() for day in days})
    api = _chart_xtdata()
    for index, code in enumerate(unique_codes, start=1):
        trading_days = sorted(set(str(day) for day in code_trading_days.get(code, []) if day))
        if not trading_days:
            trading_days = recent_trading_days(trade_date, 3)
        start_time = f"{trading_days[0]}093000"
        end_time = f"{trading_days[-1]}150000"
        try:
            api.download_history_data(code, "1m", start_time, end_time)
            raw_map = api.get_market_data_ex(
                [],
                [code],
                period="1m",
                start_time=start_time,
                end_time=end_time,
                count=-1,
                dividend_type="none",
                fill_data=True,
            )
            points = minute_points(raw_map.get(code) if isinstance(raw_map, dict) else None, trading_days)
            message = "" if points else "miniQMT未返回该区间分钟行情"
        except Exception as exc:
            points = []
            message = f"分钟行情读取失败：{exc}"
        charts[code] = {
            "code": code,
            "name": names.get(code, ""),
            "trading_days": trading_days,
            "points": points,
            "message": message,
        }
        print(f"minute charts {index}/{len(unique_codes)} {code} points={len(points)}")
    return {
        "schema_version": 2,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": trade_date,
        "trading_days": all_trading_days,
        "charts": charts,
    }


def build_chart_snapshot(trade_date: str, codes: Sequence[str], names: Dict[str, str]) -> Dict[str, Any]:
    trading_days = recent_trading_days(trade_date, 3)
    return build_chart_snapshot_for_periods(
        trade_date,
        {code: trading_days for code in sorted(set(code for code in codes if code))},
        names,
    )


def write_json(data: Dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(str(temp_path), str(output_path))
    return output_path


def write_snapshot(snapshot: Dict[str, Any], output_path: Path = OUTPUT_PATH) -> Path:
    return write_json(snapshot, output_path)


def load_historical_estimates(path: Path = ESTIMATE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "days": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "days": {}}
    return value if isinstance(value, dict) else {"schema_version": 1, "days": {}}


def inject_real_fills(root: Dict[str, Any], cutoff: str = None) -> Dict[str, str]:
    """Fold real QMT-native sim fills (trade_date >= cutoff) into the state root's
    plans + buy_attempts so the main buy pages show today's real virtual-account
    orders instead of the (empty) miniQMT ledger. Also surfaces the brain's
    planned-but-unfilled orders (qmt_orders.json) as "未成交" candidates so a day
    with zero fills still shows what the strategies chose. Mutates root in place;
    returns the {code: name} map. Dates < cutoff stay on estimates."""
    try:
        from visual import build_qmt_fills as qf
    except Exception:
        import build_qmt_fills as qf
    if cutoff is None:
        cutoff = qf.order_cutoff()  # 与估算档 real_order_start_date 同步（默认 0820）
    try:
        qf.accumulate_archive()  # fold the current live fills snapshot into the durable archive
    except Exception as exc:
        print(f"archive accumulate skipped: {exc}")
    try:
        # buy_injection = real fills + the planned/未成交 candidate layer.
        plans, attempts, names = qf.buy_injection(cutoff=cutoff)
    except Exception as exc:
        print(f"real buy injection skipped: {exc}")
        return {}
    if plans:
        rp = root.get("plans")
        if not isinstance(rp, dict):
            rp = {}
            root["plans"] = rp
        # Real fills supersede any state.json plan for the same window.
        for key in list(rp.keys()):
            existing = rp.get(key) or {}
            if str(existing.get("buy_date") or "") >= cutoff:
                rp.pop(key, None)
        rp.update(plans)
    if attempts:
        ra = root.get("buy_attempts")
        if not isinstance(ra, dict):
            ra = {}
            root["buy_attempts"] = ra
        ra.update(attempts)
    return names


def main() -> int:
    state = util.load_state()
    root = state.get(multi_strategy.STATE_KEY, {}) if isinstance(state, dict) else {}
    if not isinstance(root, dict):
        root = {}
    if isinstance(state, dict):
        state[multi_strategy.STATE_KEY] = root
    estimates = load_historical_estimates()
    strategy_cash_ledger.refresh_strategy_cash_ledger(output_path=CASH_LEDGER_PATH)
    cash_timeline = load_strategy_cash_timeline()
    fill_names = inject_real_fills(root)
    dates = collect_trade_dates(root, estimates, cash_timeline)
    util.connect_market_data()
    candidate_map = backfill_candidate_map(state, dates, estimates)
    codes = sorted({str(item.get("code") or "") for items in candidate_map.values() for item in items if item.get("code")})
    names = resolve_stock_names(codes)
    names.update(fill_names)
    snapshot = build_snapshot_from_state(state, names, candidate_map, estimates, cash_timeline)
    target = write_snapshot(snapshot)
    latest_date = snapshot["latest_date"]
    latest_codes = sorted(
        {
            str(item.get("code") or "")
            for strategy_row in snapshot["days"][0]["strategies"]
            for item in strategy_row.get("candidates", [])
            if item.get("code")
        }
    )
    chart_target = write_json(build_chart_snapshot(latest_date, latest_codes, names), CHART_DIR / f"{latest_date}.json")
    print(f"daily buy snapshot updated: {target}")
    print(f"daily buy charts updated: {chart_target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
