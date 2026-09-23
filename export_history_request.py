# -*- coding: utf-8 -*-
"""B2：生成 QMT 客户端内日线导出的请求文件 qmt_local/qmt_history_req.json。

离线重算 13 策略预估(B4)需要 0721→0817 各交易日的候选个股日线。候选**选股是纯排序**
(只用预测分数)，不需要行情——唯一的外部依赖是 s1-s3 要与中证1000成分取交集。本机 xtdata
离线，故把 strategy.resolve_target_pools 打桩为读现有 qmt_local/qmt_csi1000.json 的成分，
再原样调用 strategy.select_strategies，取各交易日各策略候选的并集写进请求。

请求里的日线区间从 20260401 起(既覆盖 historical_market 对 0721 的 15 日 previous_close
回看,又让 multi_strategy.listed_under_threshold 能用首根 bar 反推"上市<60天"——最早买入日
0721 往前 60 天到 ~0522,窗口起点须早于此)到 20260819 止。执行器 _export_history 读该请求，
在 QMT 客户端内导出日线到 qmt_history_data.json，供 B4 的离线 shim 回放。

本脚本只读预测归档 + csi1000 文件，不下单、不连行情。
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import strategy
from visual import backfill_historical_strategies as bf

QMT_LOCAL = PROJECT_ROOT / "qmt_local"
VISUAL_ROOT = PROJECT_ROOT / "visual"
CSI1000_FILE = QMT_LOCAL / "qmt_csi1000.json"
HISTORY_REQ = QMT_LOCAL / "qmt_history_req.json"
DAILY_BUYS_SNAPSHOT = VISUAL_ROOT / "daily_buys_snapshot.json"
SELL_LEDGER = QMT_LOCAL / "qmt_sell_ledger.json"
SELL_STATE = QMT_LOCAL / "qmt_sell_state.json"
QMT_ORDERS = QMT_LOCAL / "qmt_orders.json"
BUY_CANDIDATES = QMT_LOCAL / "qmt_buy_candidates.json"
REAL_TRADE_FROM = "20260818"   # 未成交委托层的最早采纳日,与分钟请求一致。

CANDIDATE_START = "20260721"   # 取候选的首个交易日。
CANDIDATE_END = "20260819"     # 取候选的末个交易日(=BUY_FREEZE_DATE:预估窗口延到 0819 后,
                               # 0818/0819 两天新出现的候选也须进日线宇宙,否则无日线/无名字/
                               # 被误标停牌——688392.SH 0819 案例)。
HISTORY_START = "20260401"     # 日线导出起点(含 previous_close 回看 + 反推上市<60天的余量)。


def history_end():
    """日线导出终点 = max(20260819, 今天)。动态推进,使"多日后才卖出"的预估持仓能随
    最新日线到达被 daily_refresh 的 replay 自动了结(如 0819 买入的 46 笔要等 0820 日线)。
    收盘后客户端重跑 QUANT.py 即导出含当日的日线。"""
    import datetime as _dt
    return max("20260819", _dt.date.today().strftime("%Y%m%d"))


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        print("skip %s: %s" % (path.name, exc))
        return None


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
    """收集 qmt_auto_buy 当场落盘的全策略候选，补进日线导出请求。"""
    codes = set()
    doc = _load_json(BUY_CANDIDATES)
    if not isinstance(doc, dict):
        return codes
    td = str(doc.get("trade_date") or "").strip()
    if len(td) != 8 or td < REAL_TRADE_FROM:
        return codes
    strategies = doc.get("strategies")
    if isinstance(strategies, dict):
        for payload in strategies.values():
            codes |= _candidate_codes_from_strategy_payload(payload)
    codes |= _candidate_codes_from_strategy_payload(doc.get("codes"))
    return codes


def collect_real_codes():
    """收集 >=0818 真实成交涉及的股票代码，补进导出请求，避免个股图缺分钟点。

    来源(都在本机、无需行情): daily_buys 快照的真实买入行、卖出台账 batches、
    sell_state 的批次键(格式 s{n}:{buy_date}:{code})。
    """
    codes = set()
    snap = _load_json(DAILY_BUYS_SNAPSHOT)
    if isinstance(snap, dict):
        for day in snap.get("days", []) or []:
            for buy in day.get("buys", []) or []:
                code = str(buy.get("code") or "")
                if code:
                    codes.add(code)
    ledger = _load_json(SELL_LEDGER)
    if isinstance(ledger, dict):
        for batch in ledger.get("batches", []) or []:
            code = str(batch.get("code") or "")
            if code:
                codes.add(code)
    state = _load_json(SELL_STATE)
    if isinstance(state, dict):
        for key in state.keys():
            parts = str(key).split(":")
            if len(parts) == 3 and parts[2]:
                codes.add(parts[2])
    # 未成交委托层:零成交日的 qmt_orders.json 买入意图代码也要有日线,供个股图回退合成。
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
    codes |= collect_auto_buy_candidates()
    return codes


def load_csi1000():
    doc = json.loads(CSI1000_FILE.read_text(encoding="utf-8-sig"))
    codes = set(str(c) for c in (doc.get("codes") or []) if c)
    if not codes:
        raise RuntimeError("qmt_csi1000.json 无成分，无法离线选 s1-s3")
    print("csi1000 pool n=%d date=%s" % (len(codes), doc.get("date")))
    return codes


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成 QMT 日线导出请求")
    parser.add_argument("--candidate-start", default=CANDIDATE_START)
    parser.add_argument("--candidate-end", default=CANDIDATE_END)
    parser.add_argument("--history-start", default=HISTORY_START)
    parser.add_argument("--history-end", default="")
    parser.add_argument("--prediction-source-dir", default="")
    args = parser.parse_args(argv)

    csi1000 = load_csi1000()
    # 打桩：离线用现有 csi1000 成分近似历史成分(与旧回测同口径，已向用户披露)。
    strategy.resolve_target_pools = lambda: {"csi1000": csi1000, "kcb": set()}

    source_dir = Path(args.prediction_source_dir).resolve() if args.prediction_source_dir else None
    dates = bf.trading_dates(args.candidate_start, args.candidate_end)
    union = {}   # 保序去重。
    per_date = []
    for trade_date in dates:
        try:
            if source_dir is None:
                _src, _payload, items, _target = bf.load_prediction(trade_date)
            else:
                _src, _payload, items, _target = bf.load_prediction(trade_date, source_dir=source_dir)
        except Exception as exc:
            print("skip %s: %s" % (trade_date, exc))
            per_date.append((trade_date, 0, 0))
            continue
        selections = strategy.select_strategies(items)
        day_codes = [c for sel in selections.values() for c in sel]
        for c in day_codes:
            union.setdefault(c, True)
        per_date.append((trade_date, len(items), len(set(day_codes))))

    # 并入 >=0818 真实成交涉及的股票代码(候选池外的新票)，保序去重。
    real_codes = collect_real_codes()
    extra = [c for c in sorted(real_codes) if c not in union]
    for c in extra:
        union.setdefault(c, True)
    print("\n真实成交补充代码 n=%d (候选池外 %d): %s"
          % (len(real_codes), len(extra), extra))

    codes = list(union.keys())
    req = {
        "generated_by": "export_history_request",
        "candidate_start": args.candidate_start,
        "candidate_end": args.candidate_end,
        "prediction_source_dir": str(source_dir) if source_dir else "",
        "start": args.history_start,
        "end": args.history_end or history_end(),
        "n_codes": len(codes),
        "codes": codes,
        "real_trade_codes": sorted(real_codes),
    }
    HISTORY_REQ.write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n按日候选:")
    for d, n_items, n_codes in per_date:
        print("  %s scored=%d candidates=%d" % (d, n_items, n_codes))
    print("\n已写 %s codes=%d start=%s end=%s"
          % (HISTORY_REQ, len(codes), args.history_start, args.history_end or history_end()))


if __name__ == "__main__":
    main()
