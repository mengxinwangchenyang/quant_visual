# -*- coding: utf-8 -*-
"""Single source of truth for per-strategy cash.

The ledger keeps the historical server replay through the chosen baseline date
and then rolls forward actual QMT deals after that date.  Runtime code should
read this file instead of choosing between multiple cash-state fallbacks.
"""

import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


try:
    import multi_strategy  # type: ignore

    STRATEGY_CONFIGS = multi_strategy.STRATEGY_CONFIGS
except Exception:
    STRATEGY_CONFIGS = {
        "s1": {"initial_cash": 500000.0},
        "s2": {"initial_cash": 500000.0},
        "s3": {"initial_cash": 500000.0},
        "s4": {"initial_cash": 200000.0},
        "s5": {"initial_cash": 500000.0},
        "s6": {"initial_cash": 200000.0},
        "s7": {"initial_cash": 200000.0},
        "s8": {"initial_cash": 500000.0},
        "s9": {"initial_cash": 500000.0},
        "s10": {"initial_cash": 1000000.0},
        "s11": {"initial_cash": 1000000.0},
        "s12": {"initial_cash": 1000000.0},
        "s13": {"initial_cash": 1000000.0},
    }


BASE_DIR = Path(__file__).resolve().parent
QMT_LOCAL = BASE_DIR / "qmt_local"
VIRTUAL_DATA = BASE_DIR / "virtual_qmt_data"

DEFAULT_OUTPUT_PATH = VIRTUAL_DATA / "strategy_cash_ledger.json"
DEFAULT_REPLAY_STATE_PATH = VIRTUAL_DATA / "server_return_strategy_cash_replay.json"
DEFAULT_DEAL_ARCHIVE_PATH = QMT_LOCAL / "qmt_deal_archive.json"
DEFAULT_FILLS_PATH = QMT_LOCAL / "qmt_fills.json"

DEFAULT_BASELINE_DATE = "20260907"


def _today_yyyymmdd() -> str:
    return dt.date.today().strftime("%Y%m%d")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_cash(value: float) -> float:
    return round(float(value), 2)


def _strategy_ids() -> List[str]:
    def _key(strategy_id: str) -> Tuple[int, str]:
        m = re.match(r"^s(\d+)$", str(strategy_id))
        return (int(m.group(1)) if m else 999, str(strategy_id))

    return sorted((str(sid) for sid in STRATEGY_CONFIGS), key=_key)


STRATEGY_IDS = _strategy_ids()


def _initial_cash() -> Dict[str, float]:
    return {
        sid: float((STRATEGY_CONFIGS.get(sid) or {}).get("initial_cash") or 0.0)
        for sid in STRATEGY_IDS
    }


def _cash_map(value: Any, base: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    result = dict(base or {})
    if not isinstance(value, dict):
        return result
    for sid in STRATEGY_IDS:
        if sid in value:
            result[sid] = _to_float(value.get(sid), result.get(sid, 0.0))
    return result


def _round_map(value: Dict[str, float]) -> Dict[str, float]:
    return {sid: _round_cash(value.get(sid, 0.0)) for sid in STRATEGY_IDS}


def _zero_map() -> Dict[str, float]:
    return {sid: 0.0 for sid in STRATEGY_IDS}


def _read_json(path: os.PathLike, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_atomic(path: os.PathLike, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(target))


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


def _extract_deal_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("rows"), list):
        return [row for row in payload.get("rows") or [] if isinstance(row, dict)]
    orders_deals = payload.get("orders_deals")
    if isinstance(orders_deals, dict):
        return _extract_deal_rows(orders_deals.get("DEAL"))
    if isinstance(payload.get("DEAL"), dict):
        return _extract_deal_rows(payload.get("DEAL"))
    rows: List[Dict[str, Any]] = []
    for key in ("deals", "trade_deals", "filled_orders"):
        if isinstance(payload.get(key), list):
            rows.extend(row for row in payload.get(key) or [] if isinstance(row, dict))
    return rows


def _deal_key(row: Dict[str, Any]) -> str:
    trade_id = str(row.get("m_strTradeID") or "").strip()
    if trade_id:
        return "tid:%s" % trade_id
    parts = [
        row.get("m_strTradeDate"),
        row.get("m_strTradeTime"),
        row.get("m_strOrderSysID"),
        row.get("m_strRemark"),
        row.get("m_strInstrumentID"),
        row.get("m_strExchangeID"),
        row.get("m_nVolume"),
        row.get("m_dPrice"),
    ]
    return "row:%s" % "|".join(str(x) for x in parts)


def _deal_sort_key(row: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(row.get("m_strTradeDate") or ""),
        str(row.get("m_strTradeTime") or ""),
        str(row.get("m_strTradeID") or row.get("m_strOrderSysID") or ""),
    )


def _load_deal_rows(paths: Sequence[os.PathLike]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    seen = set()
    rows: List[Dict[str, Any]] = []
    raw_count = 0
    for path in paths:
        payload = _read_json(path, {}) or {}
        extracted = _extract_deal_rows(payload)
        raw_count += len(extracted)
        for row in extracted:
            key = _deal_key(row)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return sorted(rows, key=_deal_sort_key), {
        "raw_deal_rows": raw_count,
        "deduped_deal_rows": len(rows),
    }


def _parse_sell_remark(remark: Any) -> str:
    text = str(remark or "").strip()
    if text in STRATEGY_CONFIGS:
        return text
    m = re.match(r"^(s(?:1[0-3]|[1-9]))(?:[tfe])$", text)
    if m:
        return m.group(1)
    m = re.match(r"^(s(?:1[0-3]|[1-9]))-\d{6}-\d{6}(?:[tfe])$", text)
    if m:
        return m.group(1)
    return ""


def _trade_amount(row: Dict[str, Any]) -> float:
    amount = _to_float(row.get("m_dTradeAmount"), 0.0)
    if amount > 0:
        return amount
    return _to_float(row.get("m_dPrice"), 0.0) * _to_float(row.get("m_nVolume"), 0.0)


def _commission(row: Dict[str, Any]) -> float:
    return _to_float(row.get("m_dCommission", row.get("m_dComssion")), 0.0)


def _row_strategy(row: Dict[str, Any]) -> str:
    remark = str(row.get("m_strRemark") or "").strip()
    opt_name = str(row.get("m_strOptName") or "")
    if "买" in opt_name and remark in STRATEGY_CONFIGS:
        return remark
    if "卖" in opt_name:
        return _parse_sell_remark(remark)
    return ""


def _date_plus_one(date_text: str) -> str:
    date_obj = dt.datetime.strptime(date_text, "%Y%m%d").date()
    return (date_obj + dt.timedelta(days=1)).strftime("%Y%m%d")


def _iter_dates(start_date: str, end_date: str, deal_dates: Iterable[str]) -> List[str]:
    dates = set(str(x) for x in deal_dates if str(x).isdigit() and len(str(x)) == 8)
    start = dt.datetime.strptime(start_date, "%Y%m%d").date()
    end = dt.datetime.strptime(end_date, "%Y%m%d").date()
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.add(current.strftime("%Y%m%d"))
        current += dt.timedelta(days=1)
    return sorted(d for d in dates if start_date <= d <= end_date)


def _normalize_replay_accounts(
    replay_state: Dict[str, Any],
    baseline_date: str,
) -> Tuple[Dict[str, Dict[str, Any]], str, Dict[str, float], str]:
    initial = _initial_cash()
    cash = dict(initial)
    daily_accounts: Dict[str, Dict[str, Any]] = {}
    selected_date = ""
    selected_field = ""

    for trade_date, account in _daily_account_items(replay_state.get("daily_accounts")):
        if trade_date > baseline_date:
            break
        before_sell = _cash_map(account.get("cash_before_sell"), cash)
        after_sells = _cash_map(account.get("cash_after_sells"), before_sell)
        before_buy = _cash_map(account.get("cash_before_buy"), after_sells)
        after_buy = _cash_map(account.get("cash_after_buy"), before_buy)
        daily_accounts[trade_date] = {
            "trade_date": trade_date,
            "cash_before_sell": _round_map(before_sell),
            "cash_after_sells": _round_map(after_sells),
            "cash_before_buy": _round_map(before_buy),
            "cash_after_buy": _round_map(after_buy),
            "cash_source": "server_return_full_replay",
        }
        cash = after_buy
        selected_date = trade_date
        if isinstance(account.get("cash_after_buy"), dict):
            selected_field = "cash_after_buy"
        elif isinstance(account.get("cash_before_buy"), dict):
            selected_field = "cash_before_buy"
        elif isinstance(account.get("cash_after_sells"), dict):
            selected_field = "cash_after_sells"
        else:
            selected_field = "cash_before_sell"

    if selected_date:
        return daily_accounts, selected_date, cash, selected_field

    accounts = replay_state.get("accounts")
    if isinstance(accounts, dict):
        for sid in STRATEGY_IDS:
            account = accounts.get(sid)
            if isinstance(account, dict):
                cash[sid] = _to_float(account.get("cash"), cash.get(sid, 0.0))
        return daily_accounts, baseline_date, cash, "accounts"

    raise RuntimeError("server_return_strategy_cash_replay.json has no usable cash baseline")


def refresh_strategy_cash_ledger(
    output_path: os.PathLike = DEFAULT_OUTPUT_PATH,
    replay_state_path: os.PathLike = DEFAULT_REPLAY_STATE_PATH,
    deal_archive_path: os.PathLike = DEFAULT_DEAL_ARCHIVE_PATH,
    fills_path: os.PathLike = DEFAULT_FILLS_PATH,
    baseline_date: str = DEFAULT_BASELINE_DATE,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    write: bool = True,
) -> Dict[str, Any]:
    replay_state = _read_json(replay_state_path, {}) or {}
    if not isinstance(replay_state, dict):
        raise RuntimeError("replay state is not a JSON object: %s" % replay_state_path)
    if replay_state.get("cash_source_mode") != "server_return_full_replay":
        raise RuntimeError("replay state is not server_return_full_replay: %s" % replay_state_path)
    if replay_state.get("market_data_ok") is not True:
        raise RuntimeError("replay state market_data_ok is not true: %s" % replay_state_path)

    replay_accounts, actual_baseline_date, cash, baseline_field = _normalize_replay_accounts(
        replay_state, str(baseline_date or DEFAULT_BASELINE_DATE)
    )
    start_date = str(start_date or _date_plus_one(actual_baseline_date))

    rows, deal_diag = _load_deal_rows([deal_archive_path, fills_path])
    deal_dates = {
        str(row.get("m_strTradeDate") or "")
        for row in rows
        if str(row.get("m_strTradeDate") or "").isdigit()
    }
    end_date = str(end_date or max([_today_yyyymmdd()] + [d for d in deal_dates if d >= start_date]))

    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        trade_date = str(row.get("m_strTradeDate") or "")
        if start_date <= trade_date <= end_date:
            by_date.setdefault(trade_date, []).append(row)

    daily_accounts = dict(replay_accounts)
    applied_buy_rows = 0
    applied_sell_rows = 0
    ignored_rows = 0

    for trade_date in _iter_dates(start_date, end_date, by_date.keys()):
        before_sell = dict(cash)
        sell_amount = _zero_map()
        sell_commission = _zero_map()
        sell_rows = {sid: 0 for sid in STRATEGY_IDS}
        buy_amount = _zero_map()
        buy_commission = _zero_map()
        buy_rows = {sid: 0 for sid in STRATEGY_IDS}

        day_rows = sorted(by_date.get(trade_date, []), key=_deal_sort_key)
        for row in day_rows:
            opt_name = str(row.get("m_strOptName") or "")
            if "卖" not in opt_name:
                continue
            sid = _row_strategy(row)
            amount = _trade_amount(row)
            commission = _commission(row)
            if sid not in cash or amount <= 0:
                ignored_rows += 1
                continue
            cash[sid] += amount - commission
            sell_amount[sid] += amount
            sell_commission[sid] += commission
            sell_rows[sid] += 1
            applied_sell_rows += 1

        after_sells = dict(cash)
        before_buy = dict(cash)

        for row in day_rows:
            opt_name = str(row.get("m_strOptName") or "")
            if "买" not in opt_name:
                continue
            sid = _row_strategy(row)
            amount = _trade_amount(row)
            commission = _commission(row)
            if sid not in cash or amount <= 0:
                ignored_rows += 1
                continue
            cash[sid] -= amount + commission
            buy_amount[sid] += amount
            buy_commission[sid] += commission
            buy_rows[sid] += 1
            applied_buy_rows += 1

        daily_accounts[trade_date] = {
            "trade_date": trade_date,
            "cash_before_sell": _round_map(before_sell),
            "cash_after_sells": _round_map(after_sells),
            "cash_before_buy": _round_map(before_buy),
            "cash_after_buy": _round_map(cash),
            "sell_amount": _round_map(sell_amount),
            "sell_commission": _round_map(sell_commission),
            "sell_rows": sell_rows,
            "buy_amount": _round_map(buy_amount),
            "buy_commission": _round_map(buy_commission),
            "buy_rows": buy_rows,
            "cash_source": "actual_qmt_deals",
        }

    accounts = {
        sid: {
            "initial_cash": _round_cash(_initial_cash().get(sid, 0.0)),
            "cash": _round_cash(cash.get(sid, 0.0)),
        }
        for sid in STRATEGY_IDS
    }
    payload: Dict[str, Any] = {
        "schema_version": 1,
        "cash_source_mode": "strategy_cash_ledger",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "baseline": {
            "source_path": str(replay_state_path),
            "requested_baseline_date": str(baseline_date),
            "actual_baseline_date": actual_baseline_date,
            "baseline_field": baseline_field,
            "estimate_start_date": replay_state.get("estimate_start_date"),
            "estimate_end_date": replay_state.get("estimate_end_date"),
            "sell_eval_end_date": replay_state.get("sell_eval_end_date"),
        },
        "start_date": start_date,
        "end_date": end_date,
        "accounts": accounts,
        "daily_accounts": {date: daily_accounts[date] for date in sorted(daily_accounts)},
        "diagnostics": {
            **deal_diag,
            "applied_buy_rows": applied_buy_rows,
            "applied_sell_rows": applied_sell_rows,
            "ignored_rows": ignored_rows,
            "deal_archive_path": str(deal_archive_path),
            "fills_path": str(fills_path),
        },
    }
    if write:
        _write_json_atomic(output_path, payload)
    return payload


def cash_for_trade_date(payload: Dict[str, Any], trade_date: Optional[str] = None) -> Tuple[Dict[str, float], Dict[str, Any]]:
    trade_date = str(trade_date or _today_yyyymmdd())
    cash = _initial_cash()
    daily_accounts = payload.get("daily_accounts") if isinstance(payload, dict) else {}
    day = daily_accounts.get(trade_date) if isinstance(daily_accounts, dict) else None
    source = "accounts"
    if isinstance(day, dict):
        if isinstance(day.get("cash_before_buy"), dict):
            cash = _cash_map(day.get("cash_before_buy"), cash)
            source = "daily_cash_before_buy:%s" % trade_date
        elif isinstance(day.get("cash_after_sells"), dict):
            cash = _cash_map(day.get("cash_after_sells"), cash)
            source = "daily_cash_after_sells:%s" % trade_date
        elif isinstance(day.get("cash_after_buy"), dict):
            cash = _cash_map(day.get("cash_after_buy"), cash)
            source = "daily_cash_after_buy:%s" % trade_date
    elif isinstance(payload.get("accounts"), dict):
        for sid in STRATEGY_IDS:
            account = payload["accounts"].get(sid)
            if isinstance(account, dict):
                cash[sid] = _to_float(account.get("cash"), cash.get(sid, 0.0))

    return cash, {
        "cash_source_mode": "strategy_cash_ledger",
        "cash_source": source,
        "trade_date": trade_date,
        "generated_at": payload.get("generated_at"),
        "baseline_date": (payload.get("baseline") or {}).get("actual_baseline_date"),
        "ledger_start_date": payload.get("start_date"),
        "ledger_end_date": payload.get("end_date"),
        "applied_real_deals": int((payload.get("diagnostics") or {}).get("applied_buy_rows") or 0)
        + int((payload.get("diagnostics") or {}).get("applied_sell_rows") or 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the canonical QMT strategy cash ledger")
    parser.add_argument("--output-path", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--replay-state-path", default=str(DEFAULT_REPLAY_STATE_PATH))
    parser.add_argument("--deal-archive-path", default=str(DEFAULT_DEAL_ARCHIVE_PATH))
    parser.add_argument("--fills-path", default=str(DEFAULT_FILLS_PATH))
    parser.add_argument("--baseline-date", default=DEFAULT_BASELINE_DATE)
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    args = parser.parse_args()
    payload = refresh_strategy_cash_ledger(
        output_path=args.output_path,
        replay_state_path=args.replay_state_path,
        deal_archive_path=args.deal_archive_path,
        fills_path=args.fills_path,
        baseline_date=args.baseline_date,
        start_date=args.start_date or None,
        end_date=args.end_date or None,
    )
    print(
        "wrote %s dates=%d baseline=%s end=%s"
        % (
            args.output_path,
            len(payload.get("daily_accounts") or {}),
            (payload.get("baseline") or {}).get("actual_baseline_date"),
            payload.get("end_date"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
