# -*- coding: utf-8 -*-
"""Turn quant_fresh's real QMT fills into the plans/attempts the daily pages render.

The in-client executor writes ORDER / DEAL rows tagged with m_strRemark (the bare
strategy id "s1" for buys, date-scoped "s2-260904-003031e" style tags for sells).
buy_injection / real_sell_injection group those rows by strategy and emit the
shapes build_daily_buys / build_daily_sells render.

STRICTLY READ-ONLY: the deal archive, orders, candidates and buy state all belong
to the trading pipeline (daily_refresh / qmt_auto_buy / exec_logic). This module
never writes any of them.
"""
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

from project_paths import PROJECT_ROOT, VISUAL_ROOT

if str(PROJECT_ROOT / "auto_buy") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "auto_buy"))

import config

FILLS_PATH = PROJECT_ROOT / "qmt_local" / "qmt_fills.json"
ORDERS_PATH = PROJECT_ROOT / "auto_buy" / "qmt_orders.json"
BUY_CANDIDATES_PATH = PROJECT_ROOT / "auto_buy" / "qmt_buy_candidates.json"
BUY_STATE_PATH = PROJECT_ROOT / "qmt_local" / "qmt_buy_state.json"
DATA_ROOT = PROJECT_ROOT / "virtual_qmt_data"
BUY_CHART_ROOT = VISUAL_ROOT / "daily_buy_charts"

ARCHIVE_PATH = PROJECT_ROOT / "daily_refresh" / "qmt_deal_archive.json"


def _parse_archive_rows(text: str, section_name: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    in_section = False
    in_rows = False
    current: Dict[str, Any] = {}
    header = f'"{section_name}": {{'
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not in_section:
            if line == header:
                in_section = True
            continue
        if not in_rows:
            if line.startswith('"rows"') and line.endswith('['):
                in_rows = True
            continue
        if line.startswith(']'):
            if current:
                rows.append(current)
            current = {}
            in_section = False
            in_rows = False
            continue
        if line.startswith('{'):
            current = {}
            continue
        if line.startswith('}'):
            if current:
                rows.append(current)
            current = {}
            continue
        colon = raw_line.find(':')
        if colon < 0:
            continue
        key_text = raw_line[:colon].strip()
        if len(key_text) < 2 or not key_text.startswith('"') or not key_text.endswith('"'):
            continue
        value_text = raw_line[colon + 1:].strip()
        if value_text.endswith(','):
            value_text = value_text[:-1].rstrip()
        if not value_text:
            continue
        try:
            current[key_text[1:-1]] = json.loads(value_text)
        except Exception:
            continue
    if current:
        rows.append(current)
    return rows


def _load_archive_fallback(path: Path) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    deal_rows = _parse_archive_rows(text, "DEAL")
    order_rows = _parse_archive_rows(text, "ORDER")
    if not deal_rows and not order_rows:
        return {}
    return {
        "orders_deals": {
            "DEAL": {"rows": deal_rows, "n": len(deal_rows)},
            "ORDER": {"rows": order_rows, "n": len(order_rows)},
        }
    }


def _read_json_file(path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (ValueError, OSError, UnicodeDecodeError):
        try:
            with path.open("r", encoding="gbk") as handle:
                return json.load(handle)
        except (ValueError, OSError, UnicodeDecodeError):
            if path == ARCHIVE_PATH:
                return _load_archive_fallback(path)
            return {}
def _deal_row_key(row: Dict[str, Any]) -> tuple:
    trade_id = str(row.get("m_strTradeID") or "").strip()
    if trade_id:
        return ("trade", trade_id, _code(row),
                str(row.get("m_strOptName") or "").strip())
    return (
        "legacy",
        str(row.get("m_strOrderSysID") or "").strip(),
        _code(row),
        str(row.get("m_strTradeDate") or "").strip(),
        str(row.get("m_strTradeTime") or "").strip(),
        int(row.get("m_nVolume") or 0),
        round(float(row.get("m_dPrice") or 0.0), 4),
        str(row.get("m_strOptName") or "").strip(),
    )


def _merge_sections(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """Union orders_deals DEAL/ORDER rows from `extra` into `base` (dedup)."""
    base_od = base.setdefault("orders_deals", {}) if isinstance(base.get("orders_deals"), dict) or "orders_deals" not in base else base["orders_deals"]
    if not isinstance(base_od, dict):
        base_od = {}
        base["orders_deals"] = base_od
    extra_od = extra.get("orders_deals") if isinstance(extra.get("orders_deals"), dict) else {}
    for section in ("DEAL", "ORDER"):
        extra_rows = ((extra_od.get(section) or {}).get("rows")
                      if isinstance(extra_od.get(section), dict) else None) or []
        if not extra_rows:
            continue
        sect = base_od.get(section)
        if not isinstance(sect, dict):
            sect = {"rows": []}
            base_od[section] = sect
        rows = sect.setdefault("rows", [])
        seen = {_deal_row_key(r) for r in rows if isinstance(r, dict)}
        for r in extra_rows:
            if not isinstance(r, dict):
                continue
            k = _deal_row_key(r)
            if k not in seen:
                rows.append(r)
                seen.add(k)
        sect["n"] = len(rows)
    return base


def _load_fills() -> Dict[str, Any]:
    """Return the union of the durable archive and the live snapshot so a rebuild
    never loses real fills just because the current executor bar read empty.

    The archive is only folded forward by daily_refresh at 15:35, so intraday the
    live qmt_fills.json is what carries today's rows. The union is in-memory only."""
    live = _read_json_file(FILLS_PATH)
    archive = _read_json_file(ARCHIVE_PATH)
    if not archive:
        return live
    merged = json.loads(json.dumps(archive))  # deep copy, don't mutate archive on disk
    _merge_sections(merged, live if isinstance(live, dict) else {})
    return merged


def _fmt_time(raw: Any) -> str:
    text = str(raw or "").strip()
    if len(text) == 6 and text.isdigit():
        return "{}:{}:{}".format(text[0:2], text[2:4], text[4:6])
    return text


def _fmt_date(raw: Any) -> str:
    text = str(raw or "").strip()
    if len(text) == 8 and text.isdigit():
        return "{}-{}-{}".format(text[0:4], text[4:6], text[6:8])
    return text


def _code(row: Dict[str, Any]) -> str:
    inst = str(row.get("m_strInstrumentID") or "").strip()
    exch = str(row.get("m_strExchangeID") or "").strip().upper()
    if inst and exch:
        return "{}.{}".format(inst, exch)
    return inst


# ---------------------------------------------------------------------------
# Fold real QMT-native fills into the daily buys/sells visualization. Every
# trade_date >= config.START_DATE is rendered from these real sim fills; the
# helpers read local exports only (no xtdata) and emit the shapes the daily
# builders inject directly.
# ---------------------------------------------------------------------------
def order_cutoff() -> str:
    """真实成交注入的起始日 = config.START_DATE（虚拟盘重置日）。"""
    return str(config.START_DATE)


def _code6(code) -> str:
    """'600000.SH' -> '600000'."""
    return str(code or "").split(".", 1)[0].strip()


def _known_strategy_ids() -> set:
    return set(config.STRATEGY_CONFIGS.keys())


def _strategy_name(strategy_id: str) -> str:
    cfg = config.STRATEGY_CONFIGS.get(strategy_id) or {}
    return str(cfg.get("name") or strategy_id)


def _initial_cash(strategy_id: str) -> float:
    cfg = config.STRATEGY_CONFIGS.get(strategy_id) or {}
    return float(cfg.get("initial_cash") or 0.0)

# --- 涨停(limit-up) exclusion for real buy fills ---------------------------
# The live buy bridge had no near-limit guard before 2026-08-19, so on 0818 it
# filled 65/66 buys at 涨停. Per user decision those real sim positions are left
# as-is (executor now skips 涨停 via NEAR_LIMIT_RATIO=0.995), but the daily-buys
# VIEW must not count 涨停 fills as valid buys — they render as excluded near_limit
# candidates instead. Detection mirrors the brain's 涨停 filter:
# up_limit = round(prev_close * (1 + band)); near_limit if price >= up_limit*0.995.
# Self-contained (prev_close from qmt_history_data.json day bars) so the rebuild
# needs no live xtdata. Sells (_buy_price_lookup) are deliberately NOT filtered so
# a later sell of a held 涨停 position keeps its real cost basis.
HISTORY_PATH = PROJECT_ROOT / "qmt_local" / "qmt_history_data.json"
CSI1000_PATH = PROJECT_ROOT / "qmt_local" / "qmt_csi1000.json"
ORDER_NAMES_PATH = PROJECT_ROOT / "qmt_local" / "qmt_order_names.json"
MINUTE_PATH = PROJECT_ROOT / "qmt_local" / "qmt_minute_data.json"
NEAR_LIMIT_RATIO = 0.995
# The executor consumes a batch on the realtime bar right after the brain writes
# it (~14:53), so real buys fill ~14:56 (0818: 65/66 fills stamped 14:56), NOT at
# the open. A planned buy's reference price is therefore the ~14:56 price, not the
# open — a stock that opens low but SEALS 涨停 by 14:56 cannot be bought.
_EXEC_MINUTES = ("145600", "145500", "145400", "145300", "145700", "145800", "150000")
_HISTORY_CACHE: Dict[str, Any] = {}
_ORDER_NAME_CACHE: Dict[str, Any] = {}
_MINUTE_CACHE: Dict[str, Any] = {}
_CHART_CACHE: Dict[str, Any] = {}


def _order_name(code: str) -> str:
    """Name for a planned buy code from the executor's qmt_order_names.json export
    (get_instrumentdetail of the current batch). Empty until the executor runs."""
    if "loaded" not in _ORDER_NAME_CACHE:
        data = _read_json_file(ORDER_NAMES_PATH)
        nm = data.get("names") if isinstance(data, dict) else {}
        _ORDER_NAME_CACHE["names"] = nm if isinstance(nm, dict) else {}
        _ORDER_NAME_CACHE["loaded"] = True
    return str(_ORDER_NAME_CACHE["names"].get(code) or "")


def _history() -> Dict[str, Any]:
    if "loaded" not in _HISTORY_CACHE:
        data = _read_json_file(HISTORY_PATH)
        _HISTORY_CACHE["bars"] = data.get("bars") if isinstance(data, dict) else {}
        _HISTORY_CACHE["details"] = data.get("details") if isinstance(data, dict) else {}
        _HISTORY_CACHE["loaded"] = True
    return _HISTORY_CACHE


def _history_name(code: str) -> str:
    det = _history().get("details") or {}
    row = det.get(code) or det.get(code.split(".")[0]) or {}
    return str(row.get("InstrumentName") or "") if isinstance(row, dict) else ""


def _qmt_csi1000_pool() -> set:
    try:
        doc = json.loads(CSI1000_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return set()
    return set(str(code).strip() for code in (doc.get("codes") or []) if str(code).strip())


def _select_strategies_with_qmt_csi(strategy_mod, items):
    pool = _qmt_csi1000_pool()
    if not pool:
        return strategy_mod.select_strategies(items)
    original = strategy_mod.resolve_target_pools
    try:
        strategy_mod.resolve_target_pools = lambda: {"csi1000": pool, "kcb": set()}
        return strategy_mod.select_strategies(items)
    finally:
        strategy_mod.resolve_target_pools = original


def _chart_doc(trade_date: str) -> Dict[str, Any]:
    key = str(trade_date or "")
    if key not in _CHART_CACHE:
        path = BUY_CHART_ROOT / f"{key}.json"
        data = _read_json_file(path)
        _CHART_CACHE[key] = data if isinstance(data, dict) else {}
    return _CHART_CACHE[key]


def _chart_points(code: str, trade_date: str) -> List[Dict[str, Any]]:
    charts = (_chart_doc(trade_date).get("charts") or {})
    row = charts.get(code) if isinstance(charts, dict) else None
    points = (row or {}).get("points") if isinstance(row, dict) else []
    return [p for p in (points or []) if isinstance(p, dict)]


def _point_hhmmss(point: Dict[str, Any]) -> str:
    try:
        return dt.datetime.fromtimestamp(int(point.get("time") or 0)).strftime("%H%M%S")
    except Exception:
        return ""


def _point_date(point: Dict[str, Any]) -> str:
    date_text = str(point.get("date") or "")
    if len(date_text) == 8:
        return date_text
    try:
        return dt.datetime.fromtimestamp(int(point.get("time") or 0)).strftime("%Y%m%d")
    except Exception:
        return ""


def _chart_prev_close(code: str, trade_date: str) -> float:
    points = _chart_points(code, trade_date)
    prior = [p for p in points if _point_date(p) < trade_date and float(p.get("close") or 0.0) > 0.0]
    if not prior:
        return 0.0
    prior.sort(key=lambda p: (_point_date(p), int(p.get("time") or 0)))
    return float(prior[-1].get("close") or 0.0)


def _chart_exec_ref_price(code: str, trade_date: str) -> float:
    points = [p for p in _chart_points(code, trade_date) if _point_date(p) == trade_date]
    if not points:
        return 0.0
    by_time = {_point_hhmmss(p): p for p in points}
    for hhmmss in _EXEC_MINUTES:
        point = by_time.get(hhmmss)
        if isinstance(point, dict):
            px = float(point.get("close") or point.get("open") or 0.0)
            if px > 0.0:
                return px
    points.sort(key=lambda p: int(p.get("time") or 0))
    return float(points[-1].get("close") or points[-1].get("open") or 0.0)


def _local_prev_close(code: str, trade_date: str) -> float:
    bars = (_history().get("bars") or {}).get(code) or {}
    prior = [d for d in bars if d < trade_date and float((bars[d] or {}).get("close") or 0.0) > 0.0]
    if prior:
        return float(bars[max(prior)].get("close") or 0.0)
    series = (_minute().get("series") or {}).get(code) or {}
    if isinstance(series, dict):
        prev_keys = [k for k in series if str(k)[:8] < trade_date]
        if prev_keys:
            bar = series.get(max(prev_keys)) or {}
            px = float(bar.get("close") or 0.0)
            if px > 0.0:
                return px
    return 0.0


def _local_exec_ref_price(code: str, trade_date: str) -> float:
    series = _minute().get("series") or {}
    bars_1m = series.get(code) or {}
    if isinstance(bars_1m, dict):
        for hhmmss in _EXEC_MINUTES:
            bar = bars_1m.get(trade_date + hhmmss)
            if isinstance(bar, dict):
                px = float(bar.get("close") or bar.get("open") or 0.0)
                if px > 0.0:
                    return px
    day = ((_history().get("bars") or {}).get(code) or {}).get(trade_date) or {}
    return float(day.get("close") or day.get("open") or 0.0)


def _planned_market_source(code: str, trade_date: str) -> str:
    if _local_prev_close(code, trade_date) > 0.0 and _local_exec_ref_price(code, trade_date) > 0.0:
        return "qmt_local_export"
    if _chart_prev_close(code, trade_date) > 0.0 and _chart_exec_ref_price(code, trade_date) > 0.0:
        return "daily_buy_charts"
    return "missing"


def _prev_close(code: str, trade_date: str) -> float:
    px = _local_prev_close(code, trade_date)
    if px > 0.0:
        return px
    chart_px = _chart_prev_close(code, trade_date)
    if chart_px > 0.0:
        return chart_px
    return 0.0


def _limit_band(code: str, name: str) -> float:
    if "ST" in str(name).upper() or "退" in str(name):
        return 0.05
    head = code.split(".")[0]
    if code.endswith(".BJ") or head.startswith("4") or head.startswith("8"):
        return 0.30
    if head.startswith("300") or head.startswith("301") or head.startswith("688") or head.startswith("689"):
        return 0.20
    return 0.10


def _round_price(value: float) -> float:
    from decimal import Decimal, ROUND_HALF_UP
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _same_day_first_price(code: str, trade_date: str) -> float:
    series = (_minute().get("series") or {}).get(code) or {}
    if isinstance(series, dict):
        for key in sorted(k for k in series if str(k)[:8] == trade_date):
            bar = series.get(key) or {}
            px = float(bar.get("open") or bar.get("close") or 0.0)
            if px > 0.0:
                return px
    points = [p for p in _chart_points(code, trade_date) if _point_date(p) == trade_date]
    points.sort(key=lambda p: int(p.get("time") or 0))
    for point in points:
        px = float(point.get("open") or point.get("close") or 0.0)
        if px > 0.0:
            return px
    return 0.0


def _same_day_implied_up_price(code: str, trade_date: str, name: str) -> float:
    first = _same_day_first_price(code, trade_date)
    if first <= 0.0:
        return 0.0
    return _round_price(first * (1.0 + _limit_band(code, name or _history_name(code))))


def _near_limit_status(code: str, trade_date: str, price: float, name: str) -> str:
    """Classify a buy vs that day's 涨停: 'near' / 'clear' / 'unknown'.

    'unknown' means the prior close (hence the limit-up) is not in the exported
    history, so the buy CANNOT be checked. Mirrors the executor guard (enforce
    ONLY when limit-up is knowable) but surfaces the un-checkable case explicitly
    so it is counted rather than silently kept (#8)."""
    if price <= 0.0:
        return "unknown"
    name = name or _history_name(code)
    pc = _prev_close(code, trade_date)
    if pc > 0.0:
        up = _round_price(pc * (1.0 + _limit_band(code, name)))
        if up > 0.0 and price >= up * NEAR_LIMIT_RATIO:
            return "near"
        # Guard against stale/mismatched previous close when the daily bar was not
        # exported for a candidate. 001316.SZ on 2026-09-04 had minute data but no
        # daily bar in qmt_history_data, so using the prior minute close missed a
        # limit-up at 30.18.
        implied = _same_day_implied_up_price(code, trade_date, name)
        if implied > 0.0 and price >= implied * NEAR_LIMIT_RATIO:
            return "near"
        return "clear"
    implied = _same_day_implied_up_price(code, trade_date, name)
    if implied > 0.0 and price >= implied * NEAR_LIMIT_RATIO:
        return "near"
    return "unknown"


def _minute() -> Dict[str, Any]:
    if "loaded" not in _MINUTE_CACHE:
        data = _read_json_file(MINUTE_PATH)
        series = data.get("minute") if isinstance(data, dict) else {}
        _MINUTE_CACHE["series"] = series if isinstance(series, dict) else {}
        _MINUTE_CACHE["loaded"] = True
    return _MINUTE_CACHE


def _exec_ref_price(code: str, trade_date: str) -> float:
    """Reference price a planned buy would actually transact at. The executor fills
    ~14:56 (near the close, NOT the open), so use the 14:56/14:53 minute-bar close
    when available, else the daily close, else the daily open, else 0. This is what
    decides buyability: a stock that SEALS 涨停 by 14:56 is un-buyable even if it
    opened low."""
    px = _local_exec_ref_price(code, trade_date)
    if px > 0.0:
        return px
    chart_px = _chart_exec_ref_price(code, trade_date)
    if chart_px > 0.0:
        return chart_px
    return 0.0


# Back-compat alias: the buyability reference is the execution-time (~14:56) price.
_planned_open_price = _exec_ref_price


def _planned_up_price(code: str, trade_date: str, name: str) -> float:
    """涨停价 = round(prev_close * (1 + band)); 0 when the prior close is unknown."""
    pc = _prev_close(code, trade_date)
    if pc <= 0.0:
        return 0.0
    up = _round_price(pc * (1.0 + _limit_band(code, name or _history_name(code))))
    return up if up > 0.0 else 0.0


def _planned_up_price_for_price(code: str, trade_date: str, name: str, price: float) -> float:
    up = _planned_up_price(code, trade_date, name)
    if up > 0.0 and price > 0.0 and price >= up * NEAR_LIMIT_RATIO:
        return up
    implied = _same_day_implied_up_price(code, trade_date, name)
    if implied > 0.0 and price > 0.0 and price >= implied * NEAR_LIMIT_RATIO:
        return implied
    return up


def _planned_market_source_for_price(code: str, trade_date: str, name: str, price: float) -> str:
    up = _planned_up_price(code, trade_date, name)
    implied = _same_day_implied_up_price(code, trade_date, name)
    if (implied > 0.0 and price > 0.0 and price >= implied * NEAR_LIMIT_RATIO
            and not (up > 0.0 and price >= up * NEAR_LIMIT_RATIO)):
        return "qmt_minute_open_implied"
    return _planned_market_source(code, trade_date)


def _chart_paused(code: str, trade_date: str) -> bool:
    points = [p for p in _chart_points(code, trade_date) if _point_date(p) == trade_date]
    if not points:
        return False
    has_price = any(float((p or {}).get("close") or (p or {}).get("open") or 0.0) > 0.0 for p in points)
    if not has_price:
        return False
    return all(float((p or {}).get("volume") or 0.0) <= 0.0 for p in points)


def _planned_paused_source(code: str, trade_date: str) -> str:
    series = (_minute().get("series") or {}).get(code) or {}
    if isinstance(series, dict):
        day = [v for k, v in series.items() if str(k)[:8] == trade_date]
        if day and all(float((v or {}).get("volume") or 0.0) <= 0.0 for v in day):
            return "qmt_local_export"
        if day:
            return ""
    bar = ((_history().get("bars") or {}).get(code) or {}).get(trade_date) or {}
    if isinstance(bar, dict) and bar:
        try:
            if float(bar.get("volume") or 0.0) <= 0.0 and float(bar.get("close") or 0.0) > 0.0:
                return "qmt_local_export"
        except (TypeError, ValueError):
            pass
    if _chart_paused(code, trade_date):
        return "daily_buy_charts"
    return ""


def _planned_paused(code: str, trade_date: str) -> bool:
    """停牌判定(未成交层):该日分钟 bar 存在但全天 volume 全为 0(miniQMT 对停牌股
    返回横线合成 bar,如 002155 0820),或日线 bar 存在且 volume==0。qmt_local 缺
    行情时用 daily_buy_charts 兜底,避免停牌票误落入通道断线。"""
    return bool(_planned_paused_source(code, trade_date))


def _is_near_limit_buy(code: str, trade_date: str, price: float, name: str) -> bool:
    """True if a buy at `price` on `trade_date` landed at/near that day's 涨停.

    Unknown (un-checkable) -> False, i.e. keep the buy. Retained for callers/tests;
    real_buy_injection uses _near_limit_status directly to also count 'unknown'."""
    return _near_limit_status(code, trade_date, price, name) == "near"


def _epoch(date_text: str, time_text: str) -> float:
    try:
        stamp = "{}{}".format(str(date_text or "").strip(), str(time_text or "").strip())
        return dt.datetime.strptime(stamp[:14], "%Y%m%d%H%M%S").timestamp()
    except Exception:
        return 0.0


def _iter_deal_rows(fills: Dict[str, Any]) -> List[Dict[str, Any]]:
    od = fills.get("orders_deals") or {}
    deal = od.get("DEAL") if isinstance(od, dict) else None
    rows = (deal or {}).get("rows") if isinstance(deal, dict) else deal
    return [r for r in (rows or []) if isinstance(r, dict)]


def _group_buy_deals(fills: Dict[str, Any], cutoff: str) -> Dict[tuple, Dict[str, Any]]:
    """Group real BUY deals by (trade_date, strategy, code) for dates >= cutoff.

    A buy deal has m_strRemark == a bare strategy id (sells carry s{n}t/s{n}e) and
    m_strOptName contains the buy character. Returns aggregates: shares, notional,
    commission, earliest submit epoch, name, order id."""
    known = _known_strategy_ids()
    groups: Dict[tuple, Dict[str, Any]] = {}
    for row in _iter_deal_rows(fills):
        remark = str(row.get("m_strRemark") or "").strip()
        if remark not in known:
            continue
        optname = str(row.get("m_strOptName") or "")
        if optname and "买" not in optname:  # require the buy char, skip sells
            continue
        trade_date = str(row.get("m_strTradeDate") or "").strip()
        if not trade_date or trade_date < cutoff:
            continue
        code = _code(row)
        if not code:
            continue
        vol = int(row.get("m_nVolume") or 0)
        price = float(row.get("m_dPrice") or 0.0)
        if vol <= 0 or price <= 0:
            continue
        key = (trade_date, remark, code)
        grp = groups.get(key)
        if grp is None:
            grp = {
                "trade_date": trade_date, "strategy_id": remark, "code": code,
                "name": str(row.get("m_strInstrumentName") or "").strip(),
                "shares": 0, "notional": 0.0, "commission": 0.0,
                "submit_epoch": 0.0, "order_id": 0,
            }
            groups[key] = grp
        grp["shares"] += vol
        grp["notional"] += price * vol
        grp["commission"] += float(row.get("m_dCommission") or row.get("m_dComssion") or 0.0)
        epoch = _epoch(trade_date, row.get("m_strTradeTime"))
        if epoch > 0 and (grp["submit_epoch"] == 0.0 or epoch < grp["submit_epoch"]):
            grp["submit_epoch"] = epoch
        try:
            oid = int(str(row.get("m_strOrderSysID") or "0").strip() or 0)
        except Exception:
            oid = 0
        if oid and not grp["order_id"]:
            grp["order_id"] = oid
        if not grp["name"]:
            grp["name"] = str(row.get("m_strInstrumentName") or "").strip()
    return groups


def real_buy_injection(fills: Dict[str, Any] = None, cutoff: str = None):
    """Build (plans, attempts, names) from real QMT buy fills for dates >= cutoff.

    - plans: {plan_key: plan_dict} shaped like a state.json buy plan (filled).
    - attempts: {"date:sid": attempt_dict} with an embedded candidate list so the
      daily builder marks the strategy attempted WITHOUT hitting live market data.
    - names: {code: name} sourced from the fill rows."""
    if cutoff is None:
        cutoff = order_cutoff()
    if fills is None:
        fills = _load_fills()
    groups = _group_buy_deals(fills, cutoff)
    plans: Dict[str, Dict[str, Any]] = {}
    attempts: Dict[str, Dict[str, Any]] = {}
    names: Dict[str, str] = {}
    by_day_strat: Dict[tuple, List[Dict[str, Any]]] = {}
    unchecked_by_day: Dict[tuple, int] = {}
    for (trade_date, sid, code), grp in groups.items():
        shares = int(grp["shares"])
        if shares <= 0:
            continue
        avg_price = round(grp["notional"] / shares, 4)
        buy_amount = round(grp["notional"], 2)
        if grp["name"]:
            names[code] = grp["name"]
        # 涨停封板本不该有对手成交——QMT 模拟盘的 bug 让这些"涨停价成交"落地了。它们不是
        # 真实可得的持仓，故从 plans/成交统计中 **剔除**，只作为"涨停·模拟误成交·已忽略"候选
        # 展示（可见但不计入 filled_count/持仓）。unknown（缺历史无法判定）计数上报，避免静默。
        status = _near_limit_status(code, trade_date, avg_price, grp["name"])
        near_limit = (status == "near")
        if status == "unknown":
            unchecked_by_day[(trade_date, sid)] = unchecked_by_day.get((trade_date, sid), 0) + 1
        by_day_strat.setdefault((trade_date, sid), []).append({
            "code": code, "name": grp["name"], "price": avg_price,
            "shares": shares, "filled_shares": shares,
            "order_id": int(grp["order_id"] or 0),
            "near_limit": near_limit,
        })
        if near_limit:
            continue  # 不计入持仓/成交
        plan_key = "%s:%s:%s" % (sid, trade_date, code)
        plans[plan_key] = {
            "position_key": plan_key,
            "strategy_id": sid,
            "strategy_name": _strategy_name(sid),
            "code": code,
            "buy_date": trade_date,
            "shares": shares,
            "filled_shares": shares,
            "buy_price": avg_price,
            "buy_amount": buy_amount,
            "buy_limit_price": avg_price,
            "buy_fee": round(grp["commission"], 2),
            "status": "filled",
            "near_limit_fill": False,
            "buy_submit_time": grp["submit_epoch"],
            "buy_order_id": int(grp["order_id"] or 0),
            "buy_remark": sid,
            "source": "qmt_real_fill",
        }
    for (trade_date, sid) in set(by_day_strat):
        rows = by_day_strat.get((trade_date, sid), [])
        rows.sort(key=lambda r: (r["code"]))
        candidates = []
        rank = 0
        for r in rows:
            rank += 1
            near = bool(r.get("near_limit"))
            candidates.append({
                "rank": rank, "code": r["code"], "name": r["name"],
                "price": r["price"], "up_price": 0.0,
                "eligible": not near,
                "reason": "near_limit" if near else "submitted",
                "near_limit": near,
                # 涨停封板的"成交"是模拟盘 bug，剔除持仓，仅标注忽略。
                "reason_label": "涨停封板 · 模拟误成交，已忽略" if near else "已成交",
                "reason_source": "qmt_real_fill",
                "order_status": "filled", "order_id": r["order_id"],
                "shares": r["shares"], "filled_shares": r["filled_shares"],
                "chart_key": "%s:%s" % (trade_date, r["code"]),
            })
        near_n = sum(1 for r in rows if r.get("near_limit"))
        submitted_n = len(rows) - near_n  # 计入成交的仅非涨停单
        filters = {}
        if near_n:
            # 涨停封板被剔除：作为过滤项计数上报。
            filters["near_limit"] = near_n
        unchecked_n = unchecked_by_day.get((trade_date, sid), 0)
        if unchecked_n:
            filters["near_limit_unchecked"] = unchecked_n
        attempts["%s:%s" % (trade_date, sid)] = {
            "time": "%s-%s-%sT14:53:00" % (trade_date[:4], trade_date[4:6], trade_date[6:8]),
            "candidate_count": len(candidates),
            "accepted_count": submitted_n,
            "estimated_buy_count": submitted_n,
            "submitted_count": submitted_n,
            "cash_before": _initial_cash(sid),
            "filters": filters,
            "candidates": candidates,
            "source": "qmt_real_fill",
        }
    return plans, attempts, names


# ---------------------------------------------------------------------------
# Planned-but-unfilled buy intents (qmt_orders.json).
# The brain writes its intended 13-strategy buy batch to qmt_orders.json; the
# in-client executor consumes it on a live handlebar bar. When the batch is
# written after the 15:00 model teardown (e.g. 0819 @ 15:14), NOTHING fills and
# real_buy_injection emits nothing -> the page shows an empty day. This layer
# surfaces those intents as "未成交" candidates so the viz reflects what the
# strategies chose even with zero fills. These are INTENTS, never positions:
# they only enrich the candidate list (attempts), never the plans/cost basis.
# ---------------------------------------------------------------------------
def _score_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    result = payload.get("result") if isinstance(payload, dict) else None
    return result if isinstance(result, dict) and isinstance(result.get("scores"), list) else payload


def _score_two_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    scores_payload = _score_payload(payload)
    rows = scores_payload.get("scores") if isinstance(scores_payload, dict) else []
    items: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stock_code") or row.get("code") or "")
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
            # predict_client.numeric_score_field reads the nested scores/score_stds shape.
            items.append({"stock_code": code, "mean": mean_value, "std": std_value,
                          "scores": {"2": mean_value}, "score_stds": {"2": std_value}})
    return items


def _prediction_file_trade_date(path: Path, payload: Dict[str, Any]) -> str:
    name = path.name
    if name.startswith("predict_sync_") and name.endswith("_latest.json"):
        text = name[len("predict_sync_"):-len("_latest.json")]
        if len(text) == 8 and text.isdigit():
            return text
    created = str((payload or {}).get("created_at") or "")
    digits = "".join(ch for ch in created[:10] if ch.isdigit())
    return digits if len(digits) == 8 else ""


def _processed_buy_dates() -> set:
    state = _read_json_file(BUY_STATE_PATH)
    if not isinstance(state, dict):
        return set()
    dates = set()
    placed = state.get("placed_orders")
    if isinstance(placed, dict):
        for key in placed:
            date_text = str(key).split("-", 1)[0]
            if len(date_text) == 8 and date_text.isdigit():
                dates.add(date_text)
    batches = state.get("processed_batches")
    if isinstance(batches, list):
        for key in batches:
            date_text = str(key).split("-", 1)[0]
            if len(date_text) == 8 and date_text.isdigit():
                dates.add(date_text)
    return dates




def _candidate_codes_from_payload(payload) -> List[str]:
    if isinstance(payload, dict):
        raw = payload.get("codes")
        if raw is None:
            raw = payload.get("candidates")
    else:
        raw = payload
    out = []
    seen = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                code = str(item.get("code") or "").strip()
            else:
                code = str(item or "").strip()
            if code and code not in seen:
                out.append(code)
                seen.add(code)
    return out


def _candidate_rows_from_payload(payload) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        raw = payload.get("candidates")
        if raw is None:
            raw = payload.get("codes")
    else:
        raw = payload
    out = []
    seen = set()
    if isinstance(raw, list):
        for idx, item in enumerate(raw, start=1):
            if isinstance(item, dict):
                row = dict(item)
                code = str(row.get("code") or "").strip()
            else:
                row = {}
                code = str(item or "").strip()
            if not code or code in seen:
                continue
            row["code"] = code
            row.setdefault("rank", idx)
            out.append(row)
            seen.add(code)
    return out


def stored_buy_candidate_groups(cutoff: str = None, orders: Dict[str, Any] = None) -> Dict[tuple, List[Dict[str, Any]]]:
    """Use qmt_auto_buy's persisted full candidate pool for the matching batch.

    This is the earliest single source for the candidates that the buy page should
    display. qmt_orders.json only contains executable order slots; the prediction
    archive fallback may need to recompute index membership.
    """
    if cutoff is None:
        cutoff = order_cutoff()
    doc = _read_json_file(BUY_CANDIDATES_PATH)
    if not isinstance(doc, dict):
        return {}
    trade_date = str(doc.get("trade_date") or "").strip()
    if len(trade_date) != 8 or trade_date < cutoff:
        return {}
    orders_doc = orders if isinstance(orders, dict) else _read_json_file(ORDERS_PATH)
    order_date = str((orders_doc or {}).get("trade_date") or "").strip()
    if trade_date != order_date:
        return {}
    order_batch = str((orders_doc or {}).get("batch_id") or "").strip()
    candidate_batch = str(doc.get("batch_id") or "").strip()
    if order_batch and candidate_batch and order_batch != candidate_batch:
        return {}

    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    strategies = doc.get("strategies")
    if isinstance(strategies, dict):
        for sid, payload in strategies.items():
            sid = str(sid or "").strip()
            if sid not in _known_strategy_ids():
                continue
            rows = []
            try:
                order_budget = int(payload.get("order_count") or 0)
            except (TypeError, ValueError):
                order_budget = 0
            for rank, item in enumerate(_candidate_rows_from_payload(payload), start=1):
                code = str(item.get("code") or "").strip()
                if not code:
                    continue
                row = {
                    "code": code,
                    "amount": float(item.get("amount") or 0.0),
                    "source": "qmt_buy_candidates",
                    "candidate_rank": int(item.get("rank") or rank),
                    "order_budget": order_budget,
                    "planned_by_auto_buy": bool(item.get("planned_order")),
                    "recorded_reason": str(item.get("reason") or ""),
                    "recorded_reason_label": str(item.get("reason_label") or ""),
                    "recorded_eligible": bool(item.get("eligible")),
                    "recorded_price": float(item.get("price") or 0.0),
                    "recorded_previous_close": float(item.get("previous_close") or 0.0),
                    "recorded_up_price": float(item.get("up_price") or 0.0),
                    "cash_before": item.get("cash_before"),
                }
                rows.append(row)
            if rows:
                groups[(trade_date, sid)] = rows
    return groups


def archived_prediction_buy_groups(cutoff: str = None) -> Dict[tuple, List[Dict[str, Any]]]:
    """Recover historical planned buy candidates from predict_sync_YYYYMMDD files.

    qmt_orders.json only contains the latest batch, so dates with no durable QMT
    ORDER/DEAL rows disappear from the buy visualization. The prediction sync
    archive plus qmt_buy_state's processed batch list is the durable source for
    those planned-but-unfilled historical candidates.
    """
    if cutoff is None:
        cutoff = order_cutoff()
    try:
        import predict_client as strategy
    except Exception:
        return {}
    known = _known_strategy_ids()
    processed_dates = _processed_buy_dates()
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for source in sorted(DATA_ROOT.glob("predict_sync_*_latest.json")):
        try:
            payload = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        trade_date = _prediction_file_trade_date(source, payload)
        if len(trade_date) != 8 or trade_date < cutoff:
            continue
        if processed_dates and trade_date not in processed_dates:
            continue
        scores_payload = _score_payload(payload)
        context_date = str(((scores_payload or {}).get("live") or {}).get("live_context_date") or "")
        if context_date and context_date != trade_date:
            continue
        items = _score_two_items(payload)
        if not items:
            continue
        try:
            selections = _select_strategies_with_qmt_csi(strategy, items)
        except Exception:
            continue
        for sid, codes in selections.items():
            sid = str(sid or "")
            if sid not in known or not isinstance(codes, list):
                continue
            seen = set()
            rows = groups.setdefault((trade_date, sid), [])
            for code in codes:
                code = str(code or "").strip()
                if not code or code in seen:
                    continue
                seen.add(code)
                rows.append({"code": code, "amount": 0.0, "source": "predict_sync_archive"})
    return groups


def planned_buy_groups(orders: Dict[str, Any] = None, cutoff: str = None) -> Dict[tuple, List[Dict[str, Any]]]:
    """Group the brain's intended BUY orders by (trade_date, strategy) for the
    batch's trade_date >= cutoff. Reads qmt_orders.json when `orders` is None."""
    if cutoff is None:
        cutoff = order_cutoff()
    if orders is None:
        orders = _read_json_file(ORDERS_PATH)
    if not isinstance(orders, dict):
        return {}
    trade_date = str(orders.get("trade_date") or "").strip()
    if not trade_date or trade_date < cutoff:
        return {}
    known = _known_strategy_ids()
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for od in orders.get("orders") or []:
        if not isinstance(od, dict):
            continue
        if str(od.get("side") or "buy").strip() != "buy":
            continue
        sid = str(od.get("strategy") or "").strip()
        code = str(od.get("code") or "").strip()
        if sid not in known or not code:
            continue
        groups.setdefault((trade_date, sid), []).append({
            "code": code,
            "amount": float(od.get("amount") or 0.0),
            "source": "qmt_orders",
        })
    return groups


def _engine_buy_order_keys(fills: Dict[str, Any]) -> set:
    """(date, strategy, code6) 集合:柜台 ORDER 表里确实存在的买入委托。

    passorder 在交易通道断线时被客户端静默拒绝(0821 14:55 "存在未登录的账号,
    不能下单"),计划单根本没到柜台——viz 据此把"已提交,未成交"与"委托未到
    柜台"区分开,不再误导为已提交。"""
    keys = set()
    od = (fills or {}).get("orders_deals") or {}
    for row in ((od.get("ORDER") or {}).get("rows") or []):
        if not isinstance(row, dict):
            continue
        if "买" not in str(row.get("m_strOptName") or ""):
            continue
        date = str(row.get("m_strInsertDate") or "").strip()
        sid = str(row.get("m_strRemark") or "").strip()
        inst = str(row.get("m_strInstrumentID") or "").strip()
        if date and sid and inst:
            keys.add((date, sid, inst))
    return keys


def buy_injection(fills: Dict[str, Any] = None, orders: Dict[str, Any] = None, cutoff: str = None):
    """real_buy_injection + a "未成交" candidate layer from planned orders.

    Real fills win: a (date, strategy, code) that filled shows as 已成交 (or the
    涨停 exclusion); every OTHER planned code for that (date, strategy) shows as a
    pending 未成交 candidate. plans/names carry only real fills + planned names.
    Returns (plans, attempts, names), the same shape real_buy_injection does, so
    build_daily_buys can inject it unchanged."""
    if cutoff is None:
        cutoff = order_cutoff()
    if fills is None:
        fills = _load_fills()
    plans, attempts, names = real_buy_injection(fills, cutoff)
    explicit_orders = isinstance(orders, dict)
    orders_doc = orders if explicit_orders else _read_json_file(ORDERS_PATH)
    planned = planned_buy_groups(orders_doc, cutoff)
    candidate_sources = [stored_buy_candidate_groups(cutoff, orders_doc)]
    if not explicit_orders:
        candidate_sources.append(archived_prediction_buy_groups(cutoff))
    for source_groups in candidate_sources:
        for hist_key, hist_items in source_groups.items():
            rows = planned.setdefault(hist_key, [])
            seen = {str(row.get("code") or "") for row in rows if isinstance(row, dict)}
            for hist_item in hist_items:
                code = str(hist_item.get("code") or "") if isinstance(hist_item, dict) else ""
                if code and code not in seen:
                    rows.append(hist_item)
                    seen.add(code)
    # 未买入原因(批次级):委托生成于收盘(15:00)后 -> 执行器已停止,委托未提交;否则仅未成交。
    after_hours = False
    if isinstance(orders_doc, dict):
        gen = str(orders_doc.get("generated_at") or "")
        after_hours = len(gen) >= 16 and gen[11:16] >= "15:00"
    unfilled_label = ("预估可买入 · 委托于收盘后生成，未提交"
                      if after_hours else "预估可买入 · 已提交，未成交")
    engine_orders = _engine_buy_order_keys(fills)
    for (trade_date, sid), items in planned.items():
        key = "%s:%s" % (trade_date, sid)
        existing = attempts.get(key)
        existing_by_code = {}
        if existing:
            for cand in existing.get("candidates", []) or []:
                code = str(cand.get("code") or "")
                if code:
                    existing_by_code[code] = cand
        try:
            budget = max(int(it.get("order_budget") or 0) for it in items if isinstance(it, dict))
        except ValueError:
            budget = 0
        except (TypeError, ValueError):
            budget = 0
        if budget <= 0:
            budget = sum(1 for it in items if isinstance(it, dict) and str(it.get("source") or "") == "qmt_orders")
        cash_before = _initial_cash(sid)
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                val = float(it.get("cash_before"))
            except (TypeError, ValueError):
                continue
            if val > 0.0:
                cash_before = val
                break
        candidates: List[Dict[str, Any]] = []
        seen = set()
        rank = 0
        buyable = 0  # 预估可买入(未近涨停)的未成交候选数。
        submitted = 0
        for it in items:
            code = it["code"]
            if code in seen:
                continue
            seen.add(code)
            rank += 1
            nm = names.get(code) or _order_name(code) or _history_name(code)
            if nm:
                names[code] = nm
            price = _exec_ref_price(code, trade_date)
            prev_close = _prev_close(code, trade_date)
            up = _planned_up_price_for_price(code, trade_date, nm, price)
            limit_source = _planned_market_source_for_price(code, trade_date, nm, price)
            paused_source = _planned_paused_source(code, trade_date)
            if not paused_source and _planned_paused(code, trade_date):
                paused_source = "qmt_local_export"
            near = up > 0.0 and price > 0.0 and price >= up * NEAR_LIMIT_RATIO

            existing_cand = existing_by_code.get(code)
            if existing_cand:
                cand = dict(existing_cand)
                cand["rank"] = rank
                if nm and not cand.get("name"):
                    cand["name"] = nm
                if price > 0.0:
                    cand["price"] = price if cand.get("reason") != "submitted" else cand.get("price", price)
                cand["previous_close"] = prev_close
                if up > 0.0 and not float(cand.get("up_price") or 0.0):
                    cand["up_price"] = up
                cand["limit_check_source"] = limit_source
                cand["limit_check_ratio"] = NEAR_LIMIT_RATIO
                cand["paused_check_source"] = paused_source
                cand.setdefault("chart_key", "%s:%s" % (trade_date, code))
                cand["planned_order"] = bool(
                    str(it.get("source") or "") == "qmt_orders"
                    or bool(it.get("planned_by_auto_buy"))
                )
                candidates.append(cand)
                if cand.get("reason") == "submitted" and not cand.get("near_limit"):
                    submitted += 1
                continue

            source = str(it.get("source") or "qmt_orders")
            should_attempt = source == "qmt_orders" or bool(it.get("planned_by_auto_buy"))
            recorded_reason = str(it.get("recorded_reason") or "")
            recorded_label = str(it.get("recorded_reason_label") or "")
            if float(it.get("recorded_price") or 0.0) > 0.0:
                price = float(it.get("recorded_price") or 0.0)
            if float(it.get("recorded_previous_close") or 0.0) > 0.0:
                prev_close = float(it.get("recorded_previous_close") or 0.0)
            if float(it.get("recorded_up_price") or 0.0) > 0.0:
                up = float(it.get("recorded_up_price") or 0.0)
                limit_source = "qmt_plan_market"
            near = up > 0.0 and price > 0.0 and price >= up * NEAR_LIMIT_RATIO
            if recorded_reason and recorded_reason not in ("eligible", "planned", "raw_candidate"):
                reason = recorded_reason
                reason_label = recorded_label or recorded_reason
                eligible = False
            elif paused_source:
                reason, reason_label, eligible = (
                    "paused", "停牌 · 预估无法买入", False)
            elif price <= 0.0:
                reason, reason_label, eligible = (
                    "no_data", "行情未导出，无法评估", False)
            elif near:
                reason, reason_label, eligible = (
                    "near_limit", "涨停/近涨停 · 预估无法买入", False)
            elif should_attempt:
                if after_hours or (trade_date, sid, code.split(".")[0]) in engine_orders:
                    reason, reason_label, eligible = "pending", unfilled_label, True
                    buyable += 1
                else:
                    label = "预估可买入 · 通道断线，委托未到柜台"
                    reason, reason_label, eligible = (
                        "not_submitted",
                        label, True)
                    buyable += 1
            else:
                reason, reason_label, eligible = (
                    "not_planned", "候选展示 · 预算已满未进入买单", False)
            reason_source = str(it.get("source") or "qmt_orders")
            candidates.append({
                "rank": rank, "code": code, "name": nm,
                "price": price, "previous_close": prev_close, "up_price": up,
                "limit_check_source": limit_source,
                "limit_check_ratio": NEAR_LIMIT_RATIO,
                "paused_check_source": paused_source,
                "eligible": eligible, "reason": reason,
                "reason_label": reason_label,
                "reason_source": reason_source,
                "reason_source_label": (
                    "大脑委托·未成交" if reason_source == "qmt_orders"
                    else ("买单计划" if should_attempt else "策略候选")
                ),
                "planned_order": should_attempt,
                "order_status": "unfilled", "order_id": 0,
                "shares": 0, "filled_shares": 0,
                "chart_key": "%s:%s" % (trade_date, code),
            })
        for code, existing_cand in existing_by_code.items():
            if code in seen:
                continue
            rank += 1
            cand = dict(existing_cand)
            cand["rank"] = rank
            candidates.append(cand)
            if cand.get("reason") == "submitted" and not cand.get("near_limit"):
                submitted += 1
        if not candidates:
            continue
        pending_count = sum(1 for cand in candidates if cand.get("order_status") != "filled")
        if existing:
            existing["candidates"] = candidates
            existing["candidate_count"] = len(candidates)
            existing["pending_count"] = pending_count
            existing["est_buyable_count"] = buyable
            existing["accepted_count"] = submitted
            existing["estimated_buy_count"] = submitted
            existing["submitted_count"] = submitted
            existing["cash_before"] = cash_before
        else:
            attempts[key] = {
                "time": "%s-%s-%sT14:53:00" % (trade_date[:4], trade_date[4:6], trade_date[6:8]),
                "candidate_count": len(candidates),
                "accepted_count": 0,
                "estimated_buy_count": 0,
                "submitted_count": 0,
                "pending_count": pending_count,
                "est_buyable_count": buyable,
                "cash_before": cash_before,
                "filters": {},
                "candidates": candidates,
                "source": "qmt_planned_order",
            }
    return plans, attempts, names


def _buy_price_lookup(fills: Dict[str, Any], cutoff: str) -> Dict[tuple, Dict[str, Any]]:
    """Aggregate real BUY deals by (strategy, buy_date, code) for sell cost basis.

    Deliberately does NOT apply the `cutoff` date filter (kept in the signature
    only for caller compatibility): a position sold on/after the cutoff may have
    been ESTABLISHED before it, and pricing that sell needs the ACTUAL buy fill
    whenever it happened. Filtering buys by cutoff was the pnl bug -- a pre-cutoff
    buy fell through, buy_price became 0.0, and the sell showed a fake ~100%%
    gain. Date-scoped sell tags let us keep repeated strategy+code buys separate.
    Returns per-(sid,buy_date,code): shares, notional, buy_commission, name."""
    known = _known_strategy_ids()
    out: Dict[tuple, Dict[str, Any]] = {}
    for row in _iter_deal_rows(fills):
        remark = str(row.get("m_strRemark") or "").strip()
        if remark not in known:
            continue
        optname = str(row.get("m_strOptName") or "")
        if optname and "买" not in optname:
            continue
        trade_date = str(row.get("m_strTradeDate") or "").strip()
        if not trade_date:
            continue
        code = _code(row)
        vol = int(row.get("m_nVolume") or 0)
        price = float(row.get("m_dPrice") or 0.0)
        if not code or vol <= 0 or price <= 0:
            continue
        key = (remark, trade_date, code)
        grp = out.get(key)
        if grp is None:
            grp = {"shares": 0, "notional": 0.0, "commission": 0.0,
                   "buy_date": trade_date, "name": str(row.get("m_strInstrumentName") or "").strip()}
            out[key] = grp
        grp["shares"] += vol
        grp["notional"] += price * vol
        grp["commission"] += float(row.get("m_dCommission") or row.get("m_dComssion") or 0.0)
        if trade_date < grp["buy_date"]:
            grp["buy_date"] = trade_date
        if not grp["name"]:
            grp["name"] = str(row.get("m_strInstrumentName") or "").strip()
    return out


def _parse_sell_remark(remark: str, known: set) -> tuple:
    """Return (strategy_id, buy_date_hint, code6_hint, action) for a sell remark."""
    text = str(remark or "").strip()
    for suffix in ("t", "e"):
        if not text.endswith(suffix):
            continue
        prefix = text[:-1]
        if prefix in known:
            return prefix, "", "", suffix
        parts = prefix.split("-")
        strategy = parts[0] if parts else ""
        if strategy in known and len(parts) == 3 and len(parts[1]) == 6 and len(parts[2]) == 6:
            return strategy, "20" + parts[1], parts[2], suffix
    return None, "", "", None


def _split_sell_remark(remark: str, known: set) -> tuple:
    """Split a sell remark like 's8t'/'s8e' into (base_strategy_id, action).

    action is 't' (take profit) or 'e' (forced/expiry). Returns (None, None) if the
    remark is not a recognized sell tag."""
    strategy_id, _buy_date, _code6, action = _parse_sell_remark(remark, known)
    return strategy_id, action


def _fallback_buy_match(buys: Dict[tuple, Dict[str, Any]], sid: str, code: str) -> Dict[str, Any]:
    matches = [
        item for (buy_sid, _buy_date, buy_code), item in buys.items()
        if buy_sid == sid and buy_code == code
    ]
    if not matches:
        return {}
    if len(matches) == 1:
        return matches[0]
    return {
        "shares": sum(int(item.get("shares") or 0) for item in matches),
        "notional": sum(float(item.get("notional") or 0.0) for item in matches),
        "commission": sum(float(item.get("commission") or 0.0) for item in matches),
        "buy_date": min(str(item.get("buy_date") or "") for item in matches if str(item.get("buy_date") or "")),
        "name": next((str(item.get("name") or "") for item in matches if item.get("name")), ""),
    }


def _target_price_for_sell(strategy_id: str, buy_price: float) -> float:
    cfg = config.STRATEGY_CONFIGS.get(strategy_id) or {}
    try:
        rate = float(cfg.get("take_profit_rate") or 0.0)
    except (TypeError, ValueError):
        rate = 0.0
    return round(float(buy_price or 0.0) * (1.0 + rate), 4) if buy_price > 0 and rate > 0 else 0.0


def _proportional_commission(total_commission: float, sold_shares: int, buy_shares: int) -> float:
    if total_commission <= 0 or sold_shares <= 0 or buy_shares <= 0:
        return 0.0
    ratio = min(float(sold_shares), float(buy_shares)) / float(buy_shares)
    return float(total_commission) * ratio




def real_trade_dates(fills: Dict[str, Any] = None, cutoff: str = None) -> List[str]:
    """Every date >= cutoff that has ANY real DEAL row (buy or sell). Lets the daily-sell page still render a (zero-sell) day for a date
    the system actually traded — e.g. 0819 where the only sells were 涨停 positions
    that got ignored, so the day would otherwise vanish. Sorted desc."""
    if cutoff is None:
        cutoff = order_cutoff()
    if fills is None:
        fills = _load_fills()
    dates = set()
    for row in _iter_deal_rows(fills):
        d = str(row.get("m_strTradeDate") or "").strip()
        if len(d) == 8 and d >= cutoff:
            dates.add(d)
    return sorted(dates, reverse=True)


def _limit_up_positions(fills: Dict[str, Any], cutoff: str) -> set:
    """(strategy, code6) whose real BUY fill was at 涨停封板 -> a QMT sim-bug
    position (sealed limit-up has no counterparty; the sim filled it anyway). Its
    buy is already dropped from the viz; its later SELL must be dropped too so the
    two sides stay consistent (user: '涨停买入是假的，卖出也一并忽略')."""
    out = set()
    for (trade_date, sid, code), grp in _group_buy_deals(fills, cutoff).items():
        shares = int(grp["shares"])
        if shares <= 0:
            continue
        avg = grp["notional"] / shares
        if _near_limit_status(code, trade_date, avg, grp["name"]) == "near":
            out.add((sid, _code6(code)))
    return out


def real_sell_injection(fills: Dict[str, Any] = None, cutoff: str = None) -> Dict[str, Dict[str, Any]]:
    """Build synthetic state.json-style plans from real QMT SELL fills (>= cutoff).

    The executor tags sells as date-scoped batches such as s2-260904-003031e.
    Each sell is matched back to the exact buy date when that tag is present, then
    shaped with the sell fields build_daily_sells.actual_rows consumes.
    Returns {} when no sells exist yet (forward-looking; must not crash empty)."""
    if cutoff is None:
        cutoff = order_cutoff()
    if fills is None:
        fills = _load_fills()
    known = _known_strategy_ids()
    buys = _buy_price_lookup(fills, cutoff)
    limit_up = _limit_up_positions(fills, cutoff)  # 涨停 bug 持仓 (sid, code6)：卖出一并忽略
    groups: Dict[tuple, Dict[str, Any]] = {}
    for row in _iter_deal_rows(fills):
        base_sid, buy_date_hint, code6_hint, action = _parse_sell_remark(row.get("m_strRemark"), known)
        if not base_sid:
            continue
        optname = str(row.get("m_strOptName") or "")
        if optname and "卖" not in optname:
            continue
        trade_date = str(row.get("m_strTradeDate") or "").strip()
        if not trade_date or trade_date < cutoff:
            continue
        code = _code(row)
        vol = int(row.get("m_nVolume") or 0)
        price = float(row.get("m_dPrice") or 0.0)
        if not code or vol <= 0 or price <= 0:
            continue
        if code6_hint and _code6(code) != code6_hint:
            continue
        if (base_sid, _code6(code)) in limit_up:  # 涨停 bug 持仓的卖出：忽略
            continue
        key = (trade_date, base_sid, buy_date_hint, code, action)
        grp = groups.get(key)
        if grp is None:
            grp = {
                "trade_date": trade_date, "strategy_id": base_sid, "code": code,
                "buy_date_hint": buy_date_hint,
                "action": action, "name": str(row.get("m_strInstrumentName") or "").strip(),
                "shares": 0, "notional": 0.0, "commission": 0.0,
                "sell_epoch": 0.0, "order_id": 0,
            }
            groups[key] = grp
        grp["shares"] += vol
        grp["notional"] += price * vol
        grp["commission"] += float(row.get("m_dCommission") or row.get("m_dComssion") or 0.0)
        epoch = _epoch(trade_date, row.get("m_strTradeTime"))
        if epoch > 0 and (grp["sell_epoch"] == 0.0 or epoch < grp["sell_epoch"]):
            grp["sell_epoch"] = epoch
        try:
            oid = int(str(row.get("m_strOrderSysID") or "0").strip() or 0)
        except Exception:
            oid = 0
        if oid and not grp["order_id"]:
            grp["order_id"] = oid
        if not grp["name"]:
            grp["name"] = str(row.get("m_strInstrumentName") or "").strip()

    plans: Dict[str, Dict[str, Any]] = {}
    for (trade_date, sid, buy_date_hint, code, _action), grp in groups.items():
        shares = int(grp["shares"])
        if shares <= 0:
            continue
        sell_price = round(grp["notional"] / shares, 4)
        sell_amount = round(grp["notional"], 2)
        buy = buys.get((sid, buy_date_hint, code)) if buy_date_hint else {}
        if not buy:
            buy = _fallback_buy_match(buys, sid, code)
        buy_shares = int(buy.get("shares") or 0)
        buy_price = round(buy["notional"] / buy_shares, 4) if buy_shares > 0 else 0.0
        buy_date = str(buy.get("buy_date") or "")
        buy_commission = _proportional_commission(float(buy.get("commission") or 0.0), shares, buy_shares)
        target_price = _target_price_for_sell(sid, buy_price)
        if buy_date and buy_date < cutoff:
            # 持仓建立于虚拟盘重置日(cutoff)之前 -> 旧账户时代的持仓,其清理卖出不入 viz;
            # 只展示 cutoff 起新建仓位的买卖。
            continue
        name = grp["name"] or str(buy.get("name") or "")
        plan_key = "%s:%s:%s" % (sid, buy_date or trade_date, code)
        plans[plan_key] = {
            "strategy_id": sid,
            "strategy_name": _strategy_name(sid),
            "code": code,
            "name": name,
            "buy_date": buy_date,
            "buy_price": buy_price,
            "buy_commission": round(buy_commission, 4),
            "target_price": target_price,
            "shares": shares,
            "filled_shares": shares,
            "sold_shares": shares,
            "sell_volume": shares,
            "sell_price": sell_price,
            "sell_amount": sell_amount,
            "sell_order_id": int(grp["order_id"] or 0),
            "sell_order_date": trade_date,
            "sell_trade_time": grp["sell_epoch"],
            "sell_submit_time": grp["sell_epoch"],
            "sell_commission": round(grp["commission"], 4),
            "sell_action": grp["action"],
            "sell_remark": "%s%s" % (sid, grp["action"]),
            "status": "filled",
            "source": "qmt_real_fill",
        }
    return plans
