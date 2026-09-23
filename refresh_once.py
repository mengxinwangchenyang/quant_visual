# -*- coding: utf-8 -*-
"""单次刷新(供 live_refresh 子进程调用,也可手动跑):折入归档 -> 重建三张快照
-> 定点补空/稀疏个股图。每次都是新进程,分钟/日线数据零缓存残留——14:55 随单
刷新请求触发执行器盘中重导后,下一次调用即可用最新分钟数据愈合当日图表。

只读行情/成交 + 写快照文件,绝不下单。用法: python visual/refresh_once.py
"""

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main():
    from visual import backfill_offline as bo
    from visual import build_qmt_fills as qf
    try:
        qf.accumulate_archive()
    except Exception as exc:  # noqa: BLE001
        print("[refresh_once] accumulate FAILED(non-fatal): %r" % (exc,), flush=True)
    try:
        import strategy_cash_ledger
        ledger = strategy_cash_ledger.refresh_strategy_cash_ledger()
        print("[refresh_once] cash ledger refreshed end=%s dates=%d"
              % (ledger.get("end_date"), len(ledger.get("daily_accounts") or {})),
              flush=True)
    except Exception as exc:  # noqa: BLE001
        print("[refresh_once] cash ledger FAILED: %r" % (exc,), flush=True)
        traceback.print_exc()
        return 1
    bo.install_shim()
    bo.rebuild_pages()
    try:
        from visual import export_minute_request as emr
        emr.main()
    except Exception as exc:  # noqa: BLE001
        print("[refresh_once] export_minute_request FAILED(non-fatal): %r" % (exc,), flush=True)
    try:
        from visual import rebuild_missing_charts as rmc
        from visual import build_daily_buys, build_daily_sells
        rmc.rebuild_buys(build_daily_buys)
        rmc.rebuild_sells(build_daily_buys, build_daily_sells)
    except Exception as exc:  # noqa: BLE001
        print("[refresh_once] chart heal FAILED(non-fatal): %r" % (exc,), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
