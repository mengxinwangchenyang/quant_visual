# -*- coding: utf-8 -*-
"""一次性重建全部可视化快照(只读 quant_fresh 交易数据,只写本仓库下的文件)。

  1) 日买入快照 + 最新日买入分钟图
  2) 日卖出快照 + 最新日卖出分钟图
  3) 周/累计快照(纯读前两张)
  4) 定点补建历史日中缺失/稀疏的分钟图

数据来源:auto_buy/qmt_orders.json、qmt_buy_candidates.json、daily_refresh/qmt_deal_archive.json、
cash_ledger/strategy_cash_ledger.json、qmt_local/*.json、virtual_qmt_data/predict_sync_*。
绝不写这些文件,也不下单。用法: python refresh.py(在 quant_visual 目录下)
"""

import sys
import traceback

import project_paths  # noqa: F401  挂载 quant_fresh 到 sys.path


def rebuild_pages():
    import build_daily_buys, build_daily_sells, build_weekly_snapshot
    print("rebuild daily_buys ...", flush=True)
    build_daily_buys.main()
    print("rebuild daily_sells ...", flush=True)
    build_daily_sells.main()
    print("rebuild weekly ...", flush=True)
    saved = sys.argv
    sys.argv = ["build_weekly_snapshot"]  # 它用 argparse,隔离本脚本的 argv
    try:
        build_weekly_snapshot.main()
    finally:
        sys.argv = saved


def main():
    try:
        rebuild_pages()
        import build_daily_buys, build_daily_sells, rebuild_missing_charts
        rebuild_missing_charts.rebuild_buys(build_daily_buys)
        rebuild_missing_charts.rebuild_sells(build_daily_buys, build_daily_sells)
    except Exception:
        traceback.print_exc()
        return 1
    print("visual refresh done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
