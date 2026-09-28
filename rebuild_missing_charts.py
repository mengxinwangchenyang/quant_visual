# -*- coding: utf-8 -*-
"""定点重建"个股分钟图为空或稀疏"的图表文件。

daily_buy_charts/<date>.json / daily_sell_charts/<date>.json 里每个 code 有一段分钟K线
(charts[code].points)。build_daily_* 只为最新日建图;历史日若当时分钟导出不全,points 为空
或只有日线合成的稀疏点。本脚本经离线 shim(优先读 qmt_local/qmt_minute_data.json 的真实分钟)
**只重建当前含空点/稀疏的图**,代码/窗口取自最新快照,绝不覆盖已正常的图。
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

VISUAL_ROOT = PROJECT_ROOT / "visual"
BUY_SNAP = VISUAL_ROOT / "daily_buys_snapshot.json"
SELL_SNAP = VISUAL_ROOT / "daily_sells_snapshot.json"
BUY_CHART_DIR = VISUAL_ROOT / "daily_buy_charts"
SELL_CHART_DIR = VISUAL_ROOT / "daily_sell_charts"
# 与 export_minute_request.MIN_REAL_POINTS 一致:空或点数 < 此值(日线合成稀疏图)才重建。
MIN_REAL_POINTS = 60


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _days_with_empty_charts(chart_dir):
    """返回 {date: set(codes)} —— 当前分钟图**空或稀疏**(点数 < MIN_REAL_POINTS)的日期。

    稀疏 = 日线合成的 4 点/日近似;~720 点的真实图远超阈值,不会被选中(故绝不覆盖已正常图)。"""
    out = {}
    for f in sorted(Path(chart_dir).glob("*.json")):
        dt = f.name[:8]
        try:
            charts = _load(f).get("charts") or {}
        except Exception as exc:
            print("skip %s: %s" % (f.name, exc))
            continue
        incomplete = {c for c, v in charts.items()
                      if isinstance(v, dict) and len(v.get("points") or []) < MIN_REAL_POINTS}
        if incomplete:
            out[dt] = incomplete
    return out


def _report(kind, dt, snapshot):
    charts = snapshot.get("charts") or {}
    ok = sum(1 for v in charts.values() if v.get("points"))
    still_empty = [c for c, v in charts.items() if not v.get("points")]
    print("%s %s: 重建 %d 只, 有分钟 %d, 仍空 %d %s"
          % (kind, dt, len(charts), ok, len(still_empty),
             still_empty if still_empty else ""))


def _candidates_for_day(day):
    """取该日全部策略候选 -> (codes:set, names:dict)。"""
    codes, names = set(), {}
    for strat in day.get("strategies", []) or []:
        for cand in strat.get("candidates", []) or []:
            code = str(cand.get("code") or "").strip()
            if not code:
                continue
            codes.add(code)
            if cand.get("name"):
                names[code] = cand["name"]
    return codes, names


def rebuild_buys(build_daily_buys):
    """保证每个买入候选都有真实分钟图。**按代码合并**:对每个交易日,只对图中
    缺失或稀疏(<MIN_REAL_POINTS)的候选重建,已达标(~720 点)的真实图原样保留,
    绝不整日覆盖 —— 避免把 0721-0814 的真实图退化成离线合成稀疏图。"""
    snap = _load(BUY_SNAP)
    day_by_date = {d.get("date"): d for d in snap.get("days", [])}
    changed = 0
    for dt, day in sorted(day_by_date.items()):
        codes, names = _candidates_for_day(day)
        if not codes:
            continue
        chart_path = build_daily_buys.CHART_DIR / ("%s.json" % dt)
        doc = _load(chart_path) if chart_path.exists() else {}
        existing = doc.get("charts") if isinstance(doc, dict) else None
        existing = existing if isinstance(existing, dict) else {}
        good = {c for c, v in existing.items()
                if isinstance(v, dict) and len(v.get("points") or []) >= MIN_REAL_POINTS}
        need = sorted(c for c in codes if c not in good)
        if not need:
            continue  # 该日所有候选均已有达标真实图
        fresh = build_daily_buys.build_chart_snapshot(dt, need, names)
        fresh_charts = fresh.get("charts") or {}
        if existing:
            merged = dict(existing)
            merged.update(fresh_charts)   # 只覆盖 need 的条目,保留其余达标图
            doc["charts"] = merged
            doc.setdefault("schema_version", 2)
            doc["trade_date"] = dt
            doc["generated_at"] = fresh.get("generated_at", doc.get("generated_at"))
            out_doc = doc
        else:
            out_doc = fresh              # 该日本无图文件,直接用新建的
        build_daily_buys.write_json(out_doc, chart_path)
        still = [c for c in need
                 if len((out_doc["charts"].get(c) or {}).get("points") or []) < MIN_REAL_POINTS]
        print("buy %s: 候选 %d, 补建 %d, 仍空/稀疏 %d %s"
              % (dt, len(codes), len(need), len(still), still if still else ""))
        changed += 1
    if not changed:
        print("buy: 所有候选均已有达标分钟图,跳过")


def rebuild_sells(build_daily_buys, build_daily_sells):
    snap = _load(SELL_SNAP)
    day_by_date = {d.get("date"): d for d in snap.get("days", [])}
    targets = _days_with_empty_charts(SELL_CHART_DIR)
    if not targets:
        print("sell: 无空图,跳过")
        return
    for dt in sorted(targets):
        day = day_by_date.get(dt)
        if not day:
            print("sell %s: 快照无此日,跳过" % dt)
            continue
        rows = day.get("records") or []
        names = {str(r.get("code")): r.get("name")
                 for r in rows if r.get("code") and r.get("name")}
        snapshot = build_daily_sells.build_sell_chart_snapshot(dt, rows, names)
        build_daily_buys.write_json(snapshot, build_daily_sells.CHART_DIR / ("%s.json" % dt))
        _report("sell", dt, snapshot)


def main():
    from visual import build_daily_buys, build_daily_sells
    rebuild_buys(build_daily_buys)
    rebuild_sells(build_daily_buys, build_daily_sells)


if __name__ == "__main__":
    main()
