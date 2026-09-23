# -*- coding: utf-8 -*-
"""B4：用离线 xtdata 替身重跑 backfill_historical_strategies.replay(0721->0817)。

把 visual/xtdata_offline_shim.py 注入到所有在 import 期绑定了 xtdata 的模块，再把
util.connect_market_data 打成 no-op，然后**复用现有全部预估逻辑**跑 13 策略回放，产出：
  - visual/historical_buy_estimates.json  (13 策略，real_order_start_date=next(BUY_FREEZE_DATE))
  - visual/estimated_sell_snapshot.json   (公开卖出快照)
  - virtual_qmt_data/estimated_sell_state.json (卖出跟踪状态)

前置：QMT 客户端已重跑一次 QUANT.py，生成 qmt_local/qmt_history_data.json。
默认跳过逐股图表(--charts 可开)。粒度为日级近似(见 shim 顶部说明)。
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from visual import xtdata_offline_shim

# 买入冻结点：预估买入只到此日,0820+ 交给真实模拟盘(90007892)。真实层 cutoff 由本日的
# next_trading_day 派生(见 build_qmt_fills.order_cutoff),改预估窗只需动这一处。
BUY_FREEZE_DATE = "20260819"
ESTIMATE_START_DATE = "20260721"
HISTORY_DATA = PROJECT_ROOT / "qmt_local" / "qmt_history_data.json"


def latest_bar_date(fallback: str = BUY_FREEZE_DATE) -> str:
    """qmt_history_data.json 里所有日线中的最大交易日(卖出评估可推进到此日)。

    随执行器每天导出新日线,该值前移,daily_refresh 重跑 replay 时到期的 tracking 持仓
    即被结清。取不到时回退 fallback(买入冻结点)。"""
    try:
        doc = json.loads(HISTORY_DATA.read_text(encoding="utf-8-sig"))
    except Exception:
        return fallback
    latest = ""
    for days in (doc.get("bars", {}) or {}).values():
        if not isinstance(days, dict):
            continue
        for day_str in days.keys():
            d = str(day_str or "").strip()
            if len(d) == 8 and d > latest:
                latest = d
    return latest or fallback


def install_shim():
    shim = xtdata_offline_shim.OfflineXtData()
    # 覆盖 sys.modules，兜底任何后续 import。
    sys.modules["xtquant.xtdata"] = shim
    # 逐个覆盖已在 import 期绑定了真实 xtdata 的模块属性。
    import util
    import multi_strategy
    import qmt_broker
    from visual import import_missed_prediction
    from visual import backfill_historical_strategies as bf
    for mod in (util, multi_strategy, qmt_broker, import_missed_prediction, bf):
        setattr(mod, "xtdata", shim)
    # 行情“连接”变 no-op（离线无端口可连）。
    util.connect_market_data = lambda *a, **k: None
    return shim, bf


def rebuild_pages():
    """在同一 shim 下重建三张页面快照：日买入(折入 >=0818 真实成交) -> 日卖出
    (折入 >=0818 真实卖出) -> 周快照(纯读前两张)。全部离线，无需连行情。"""
    import sys as _sys
    from visual import build_daily_buys, build_daily_sells, build_weekly_snapshot
    print("rebuild daily_buys ...")
    build_daily_buys.main()
    print("rebuild daily_sells ...")
    build_daily_sells.main()
    print("rebuild weekly ...")
    saved = _sys.argv
    _sys.argv = ["build_weekly_snapshot"]  # 它用 argparse，隔离本脚本的 argv
    try:
        build_weekly_snapshot.main()
    finally:
        _sys.argv = saved


def rebuild_estimates(buy_end=BUY_FREEZE_DATE, sell_end=None,
                      start_date=ESTIMATE_START_DATE, charts=False,
                      prediction_source_dir=None, estimate_output=None,
                      sell_state_output=None, sell_public_output=None):
    """离线 replay(买入止于 buy_end,卖出评估到 sell_end)并写 estimate/sell_state 三档。

    须先 install_shim()。sell_end 缺省=latest_bar_date()——随日线数据到达前移,到期的
    tracking 持仓被逐日结清。返回 (archive, sell_state)。"""
    from visual import backfill_historical_strategies as bf
    from visual import build_daily_buys
    if sell_end is None:
        sell_end = latest_bar_date(buy_end)
    archive, sell_state = bf.replay(
        start_date, buy_end, sell_end,
        prediction_source_dir=prediction_source_dir,
    )
    estimate_path = Path(estimate_output).resolve() if estimate_output else bf.ESTIMATE_PATH
    sell_state_path = Path(sell_state_output).resolve() if sell_state_output else bf.SELL_STATE_PATH
    sell_public_path = Path(sell_public_output).resolve() if sell_public_output else bf.SELL_PUBLIC_PATH
    build_daily_buys.write_json(archive, estimate_path)
    build_daily_buys.write_json(sell_state, sell_state_path)
    build_daily_buys.write_json(sell_state, sell_public_path)
    print("wrote estimate=%s sell_state=%s public=%s (buy_end=%s sell_end=%s)"
          % (estimate_path, sell_state_path, sell_public_path, buy_end, sell_end))
    if charts:
        bf.build_historical_charts(archive, sell_state)
    positions = sell_state.get("positions", {})
    sold = sum(1 for r in positions.values() if r.get("status") == "estimated_sold")
    tracking = sum(1 for r in positions.values() if r.get("status") == "tracking")
    print("days=%d positions=%d estimated_sells=%d tracking=%d"
          % (len(archive.get("days", {})), len(positions), sold, tracking))
    return archive, sell_state


def main():
    parser = argparse.ArgumentParser(description="离线重跑 13 策略历史预估(日线近似)")
    parser.add_argument("--start-date", default=ESTIMATE_START_DATE)
    parser.add_argument("--end-date", default=BUY_FREEZE_DATE,
                        help="买入冻结点(预估买入只到此日,含)")
    parser.add_argument("--sell-end-date", default=None,
                        help="卖出评估末日(默认=最新日线日,可越买入冻结点)")
    parser.add_argument("--prediction-source-dir", default=None,
                        help="外部分数目录，支持 predict_sync_YYYYMMDD_latest.json")
    parser.add_argument("--estimate-output", default=None,
                        help="历史买入估算输出；默认覆盖 visual/historical_buy_estimates.json")
    parser.add_argument("--sell-state-output", default=None,
                        help="卖出/现金状态输出；默认覆盖 virtual_qmt_data/estimated_sell_state.json")
    parser.add_argument("--sell-public-output", default=None,
                        help="公开卖出快照输出；默认覆盖 visual/estimated_sell_snapshot.json")
    parser.add_argument("--charts", action="store_true", help="额外重建逐股图表(较慢)")
    parser.add_argument("--no-pages", action="store_true", help="只重算预估，不重建页面快照")
    args = parser.parse_args()

    shim, _bf = install_shim()
    print("shim loaded meta=%s" % (shim.meta,))
    if not shim.meta.get("codes_with_bars"):
        raise SystemExit("qmt_history_data.json 无日线数据：请先在 QMT 客户端重跑一次 QUANT.py")

    rebuild_estimates(
        buy_end=args.end_date, sell_end=args.sell_end_date,
        start_date=args.start_date, charts=args.charts,
        prediction_source_dir=args.prediction_source_dir,
        estimate_output=args.estimate_output,
        sell_state_output=args.sell_state_output,
        sell_public_output=args.sell_public_output,
    )

    if not args.no_pages:
        rebuild_pages()


if __name__ == "__main__":
    main()
