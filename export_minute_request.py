# -*- coding: utf-8 -*-
"""生成 QMT 客户端内**分钟线**导出的请求文件 qmt_local/qmt_minute_req.json。

个股图(daily_buy_charts / daily_sell_charts)要显示买入/卖出日各自前后三个交易日的
真实分钟 K 线。本机 xtdata 离线,故让执行器 _export_minute 在 QMT 客户端内按本请求导出
真实 1 分钟 OHLCV 到 qmt_minute_data.json,再由离线 shim(fill_data=True 时)喂给图表
构建器,替代日线合成的 4 点近似。

请求 = 真实成交涉及的股票代码(>=0818 的买入/卖出) + 一个覆盖所有票前后三交易日的
全局时间区间。shim 会按各票各自的窗口再切片,故全局多导一点无妨。

本脚本只读快照/台账/状态文件,不下单、不连行情。执行器读该请求需**重跑一次 QUANT.py**。
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from visual import backfill_historical_strategies as bf

QMT_LOCAL = PROJECT_ROOT / "qmt_local"
VISUAL_ROOT = PROJECT_ROOT / "visual"
MINUTE_REQ = QMT_LOCAL / "qmt_minute_req.json"
MINUTE_DATA = QMT_LOCAL / "qmt_minute_data.json"
SELL_LEDGER = QMT_LOCAL / "qmt_sell_ledger.json"
SELL_STATE = QMT_LOCAL / "qmt_sell_state.json"
QMT_ORDERS = QMT_LOCAL / "qmt_orders.json"
BUY_CANDIDATES = QMT_LOCAL / "qmt_buy_candidates.json"
DAILY_BUYS_SNAPSHOT = VISUAL_ROOT / "daily_buys_snapshot.json"
BUY_CHART_DIR = VISUAL_ROOT / "daily_buy_charts"
SELL_CHART_DIR = VISUAL_ROOT / "daily_sell_charts"

# 真实成交只从 0818 起(见计划红线:下单只在模拟盘 90007892)。全局历法足够宽,覆盖买/卖
# 日各自前后三个交易日的余量。
REAL_TRADE_FROM = "20260818"
CALENDAR_START = "20260701"
CALENDAR_END = "20260930"
PAD_TRADING_DAYS = 3
# 图完整度阈值:真实分钟约 240 点/日、每票窗约 3 日(~720 点);日线合成退化到约 4 点/日
# (~12 点)。低于此值视为"空或稀疏",需补真实分钟。取 60 稳妥区分二者。
MIN_REAL_POINTS = 60
# 改成纯预估天后需按新预估候选补图的交易日(其候选多为合成稀疏图)。
ESTIMATE_CHART_DAYS = ("20260818", "20260819")


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        print("skip %s: %s" % (path.name, exc))
        return None


def collect_existing_minute_codes():
    """保留已导出的分钟代码，避免新请求因快照时序变小而覆盖旧数据。"""
    doc = _load_json(MINUTE_DATA)
    minute = doc.get("minute") if isinstance(doc, dict) else {}
    if not isinstance(minute, dict):
        return set()
    return {str(code).strip() for code in minute.keys() if str(code).strip()}


def collect_real():
    """返回 (codes:set, dates:set) —— >=0818 真实成交涉及的代码与相关交易日。

    代码来源: daily_buys 快照真实买入行 + 卖出台账 batches + sell_state 键。
    日期来源: 买入日(trade_date) + 台账 buy_date/force_sell_date。用于定全局区间。
    """
    codes, dates = set(), set()
    snap = _load_json(DAILY_BUYS_SNAPSHOT)
    if isinstance(snap, dict):
        for day in snap.get("days", []) or []:
            td = str(day.get("trade_date") or day.get("date") or "")
            for buy in day.get("buys", []) or []:
                code = str(buy.get("code") or "")
                if code:
                    codes.add(code)
                    if td >= REAL_TRADE_FROM:
                        dates.add(td)
    ledger = _load_json(SELL_LEDGER)
    if isinstance(ledger, dict):
        for batch in ledger.get("batches", []) or []:
            code = str(batch.get("code") or "")
            if code:
                codes.add(code)
            for key in ("buy_date", "force_sell_date"):
                d = str(batch.get(key) or "")
                if len(d) == 8 and d >= REAL_TRADE_FROM:
                    dates.add(d)
    state = _load_json(SELL_STATE)
    if isinstance(state, dict):
        for key in state.keys():
            parts = str(key).split(":")
            if len(parts) == 3 and parts[2]:
                codes.add(parts[2])
            if len(parts) == 3 and len(parts[1]) == 8 and parts[1] >= REAL_TRADE_FROM:
                dates.add(parts[1])
    # 未成交委托层:零成交日(如 0819)没有 buys 行,但 qmt_orders.json 记录了大脑的买入
    # 意图。这些 pending 候选也要能显示分钟图,故把其代码并入导出请求。
    orders = _load_json(QMT_ORDERS)
    if isinstance(orders, dict):
        td = str(orders.get("trade_date") or "")
        if len(td) == 8 and td >= REAL_TRADE_FROM:
            for od in orders.get("orders") or []:
                if not isinstance(od, dict):
                    continue
                if str(od.get("side") or "buy") != "buy":
                    continue
                code = str(od.get("code") or "").strip()
                if code:
                    codes.add(code)
                    dates.add(td)
    return codes, dates




def _candidate_codes_from_strategy_payload(payload):
    if isinstance(payload, dict):
        raw = payload.get("codes")
        if raw is None:
            raw = payload.get("candidates")
    else:
        raw = payload
    codes = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                code = str(item.get("code") or "").strip()
            else:
                code = str(item or "").strip()
            if code:
                codes.add(code)
    return codes


def collect_auto_buy_candidates():
    """读取 qmt_auto_buy 当场落盘的全量策略候选。

    qmt_orders.json 只有真正下单槽位；daily_buys_snapshot 要等刷新后才会包含
    页面展示的完整候选池。这个文件在自动下单同一进程里写出,是生成分钟
    请求时覆盖"页面将展示候选"的最早可靠来源。
    """
    codes, dates = set(), set()
    doc = _load_json(BUY_CANDIDATES)
    if not isinstance(doc, dict):
        return codes, dates
    td = str(doc.get("trade_date") or "").strip()
    if len(td) != 8 or td < REAL_TRADE_FROM:
        return codes, dates
    strategies = doc.get("strategies")
    if isinstance(strategies, dict):
        for payload in strategies.values():
            codes |= _candidate_codes_from_strategy_payload(payload)
    # 顶层 codes 是冗余汇总,用于兼容人工修复/旧格式。
    codes |= _candidate_codes_from_strategy_payload(doc.get("codes"))
    if codes:
        dates.add(td)
    return codes, dates


def collect_incomplete_chart_codes():
    """扫描买入/卖出个股图,返回 (codes:set, days:set) —— 当前分钟图**空或稀疏**的代码及窗口。

    个股图文件形如 daily_buy_charts/<date>.json / daily_sell_charts/<date>.json,结构
    {charts: {code: {points: [...], trading_days: [...]}}}。points 为空、或点数
    < MIN_REAL_POINTS(说明是日线合成的稀疏图,如 0817 预估候选、0818/0819 改预估天后的
    新候选、跨越 0813 起点前的卖出图),都需并入本次请求让执行器补真实分钟。trading_days
    并入日期集,使 padded_window 前扩到覆盖这些窗口。~720 点的真实图远超阈值,不会被选中。
    """
    codes, days = set(), set()
    for chart_dir in (BUY_CHART_DIR, SELL_CHART_DIR):
        if not chart_dir.exists():
            continue
        for path in sorted(chart_dir.glob("*.json")):
            doc = _load_json(path)
            if not isinstance(doc, dict):
                continue
            for code, entry in (doc.get("charts") or {}).items():
                if not isinstance(entry, dict):
                    continue
                if len(entry.get("points") or []) >= MIN_REAL_POINTS:
                    continue  # 已有足量真实分钟,跳过
                c = str(code or "").strip()
                if c:
                    codes.add(c)
                for d in entry.get("trading_days") or []:
                    d = str(d or "").strip()
                    if len(d) == 8:
                        days.add(d)
    return codes, days


def collect_uncharted_candidates():
    """遍历 daily_buys 快照**每个交易日**的全部策略候选(strategies[].candidates[].code),
    凡该日买入图里**缺失(整只没建)或稀疏(<MIN_REAL_POINTS)**的候选,并入请求 —— 保证
    "只要是候选就有真实分钟图"(用户要求:落选候选也要建图)。返回 (codes:set, days:set)。

    这泛化了原先仅覆盖 ESTIMATE_CHART_DAYS(0818/0819)的做法:历史日(如 0721)在 13 策略
    扩容前建的旧图只含入选票,漏掉 rank 靠后/未过风控的候选(如 0721 的 603123),此处一并补齐。
    ~720 点的真实图点数远超阈值,不会被选中,故不会重复导出、也不影响既有图。"""
    codes, days = set(), set()
    snap = _load_json(DAILY_BUYS_SNAPSHOT)
    if not isinstance(snap, dict):
        return codes, days
    for day in snap.get("days", []) or []:
        td = str(day.get("date") or day.get("trade_date") or "").strip()
        if len(td) != 8:
            continue
        cands = set()
        for strat in day.get("strategies", []) or []:
            for cand in strat.get("candidates", []) or []:
                code = str(cand.get("code") or "").strip()
                if code:
                    cands.add(code)
        if not cands:
            continue
        chart_path = BUY_CHART_DIR / ("%s.json" % td)
        chart_doc = _load_json(chart_path) if chart_path.exists() else None
        charts = (chart_doc or {}).get("charts") or {}
        for code in cands:
            entry = charts.get(code)
            pts = entry.get("points") if isinstance(entry, dict) else None
            if entry is None or len(pts or []) < MIN_REAL_POINTS:
                codes.add(code)
                days.add(td)
    return codes, days


def padded_window(dates):
    """把 [min(dates), max(dates)] 各向外扩 PAD_TRADING_DAYS 个交易日,返回 14 位起止。"""
    cal = bf.trading_dates(CALENDAR_START, CALENDAR_END)
    if not dates:
        raise RuntimeError("无真实成交日期,无法定分钟导出区间")
    lo, hi = min(dates), max(dates)
    try:
        i_lo = cal.index(lo)
    except ValueError:
        i_lo = min(range(len(cal)), key=lambda i: abs((cal[i] > lo) - 0.5))
    try:
        i_hi = cal.index(hi)
    except ValueError:
        i_hi = i_lo
    i_lo = max(0, i_lo - PAD_TRADING_DAYS)
    i_hi = min(len(cal) - 1, i_hi + PAD_TRADING_DAYS)
    return cal[i_lo] + "093000", cal[i_hi] + "150000"


def main(argv=None):
    # argv is accepted for programmatic callers; this script has no CLI flags.
    _ = argv
    codes, dates = collect_real()
    auto_codes, auto_days = collect_auto_buy_candidates()
    codes |= auto_codes
    dates |= auto_days
    existing_codes = collect_existing_minute_codes()
    codes |= existing_codes
    # 并入当前分钟图空或稀疏的代码(如 0817 预估候选、跨窗卖出票、0818/0819 合成图),补真实分钟。
    incomplete_codes, incomplete_days = collect_incomplete_chart_codes()
    codes |= incomplete_codes
    dates |= incomplete_days  # 稀疏图窗口纳入 padded_window,前扩起点覆盖 0810/0813
    # 并入每日缺图/稀疏图的候选(泛化:任何日、任何策略的候选都要有真实分钟图)。
    est_codes, est_days = collect_uncharted_candidates()
    codes |= est_codes
    dates |= est_days
    if not codes:
        raise SystemExit("无真实成交代码:请先跑 auto-buy / 建 qmt_sell_ledger.json")
    start, end = padded_window(dates)
    codes = sorted(codes)
    req = {
        "generated_by": "export_minute_request",
        "real_trade_from": REAL_TRADE_FROM,
        "trade_dates": sorted(dates),
        "pad_trading_days": PAD_TRADING_DAYS,
        "start": start,
        "end": end,
        "n_codes": len(codes),
        "codes": codes,
    }
    MINUTE_REQ.write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写 %s codes=%d start=%s end=%s" % (MINUTE_REQ, len(codes), start, end))
    print("自动下单候选代码: %d 只; 空/稀疏图待补代码: %d 只; 缺图候选(全日全策略): %d 只; 已有分钟数据保留代码: %d 只"
          % (len(auto_codes), len(incomplete_codes), len(est_codes), len(existing_codes)))
    print("交易日: %s" % (sorted(dates),))
    print("代码: %s" % (codes,))


if __name__ == "__main__":
    main()
