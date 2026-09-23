# -*- coding: utf-8 -*-
"""每日收盘后自动刷新可视化快照（供 Windows 计划任务 15:30 后调用）。

流程（与手动重建完全一致，绝不单独跑 builder->会触发 supplyHistoryData 200005）：
  1) accumulate_archive(): 把当前易失的 qmt_fills.json 折入持久归档 qmt_deal_archive.json，
     确保当天真实成交（买/卖）落进归档。
  2) install_shim()+rebuild_pages(): 在离线 xtdata shim 下重建 daily_buys / daily_sells /
     weekly 快照，历史 0721-0817 估算 + >=0818 真实成交一并折入，分钟图读本地导出。

serve_daily_buys.py 是 no-store，重建快照后刷新页面即见，无需重启服务。
只读行情/成交 + 写快照文件，绝不下单。

用法: python visual/daily_refresh.py
"""

import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main():
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print("[daily_refresh] start %s" % stamp, flush=True)

    # 1) 折入最新成交到持久归档（若 qmt_fills.json 读空则归档不变，无害）。
    archive_ok = False
    try:
        from visual import build_qmt_fills as qf
        archive = qf.accumulate_archive()
        od = (archive or {}).get("orders_deals") or {}
        n_deal = len((od.get("DEAL") or {}).get("rows") or [])
        archive_ok = True
        print("[daily_refresh] archive DEAL rows=%d" % n_deal, flush=True)
    except Exception as exc:  # noqa: BLE001
        print("[daily_refresh] accumulate_archive FAILED: %r" % (exc,), flush=True)

    # 2) 收盘归档成功后刷新 live 卖出台账。发布前只使用归档成交，且原子
    #    写入 state -> ledger；失败时保留旧台账，绝不影响后续可视化刷新。
    try:
        if not archive_ok:
            raise RuntimeError("archive update failed")
        from qmt_build_sell_ledger import build_ledger, publish, validate_ledger, _deal_rows
        import json
        from qmt_build_sell_ledger import DEFAULT_ARCHIVE_PATH, DEFAULT_FILLS_PATH
        with open(DEFAULT_ARCHIVE_PATH, "r", encoding="utf-8") as handle:
            archive_payload = json.load(handle)
        try:
            with open(DEFAULT_FILLS_PATH, "r", encoding="utf-8") as handle:
                live_payload = json.load(handle)
        except (OSError, ValueError):
            live_payload = {}
        trade_date = max(str(row.get("m_strTradeDate") or "")
                         for row in _deal_rows(archive_payload))
        overrides = {}
        # The 20260824 688127 lots missed their original 20260825 force time;
        # keep the user-approved one-time defer stable across later refreshes
        # until those lots are actually closed and disappear from the ledger.
        for strategy in ("s1", "s2", "s3"):
            overrides["%s:20260824:688127.SH" % strategy] = ("20260826", "10:00:00")
        ledger = build_ledger(archive_payload, trade_date, "90007892", True,
                              force_overrides=overrides)
        validate_ledger(ledger, live_payload)
        publish(ledger, fills=live_payload)
        print("[daily_refresh] sell ledger published batches=%d" % len(ledger["batches"]),
              flush=True)
    except Exception as exc:  # noqa: BLE001
        print("[daily_refresh] sell ledger FAILED (old ledger kept): %r" % (exc,),
              flush=True)

    # 3) 刷新唯一资金账本。它只依赖 9.7 基线 + 真实成交流水，先于页面重建执行；
    #    即使后续图表/页面刷新失败，下一次买单和页面仍能读到最新资金。
    try:
        import strategy_cash_ledger
        cash_ledger = strategy_cash_ledger.refresh_strategy_cash_ledger()
        print("[daily_refresh] cash ledger refreshed end=%s dates=%d"
              % (cash_ledger.get("end_date"), len(cash_ledger.get("daily_accounts") or {})),
              flush=True)
    except Exception as exc:  # noqa: BLE001
        print("[daily_refresh] cash ledger FAILED: %r" % (exc,), flush=True)
        traceback.print_exc()
        return 1

    # 3) 离线 shim 下：先重跑 replay（买入冻结 BUY_FREEZE_DATE，卖出评估推进到最新日线日），
    #    让多日持有、到期日在未来的 tracking 持仓随数据到达逐日结清；再重建全部页面。
    try:
        from visual import backfill_offline as bo
        shim, _bf = bo.install_shim()
        if shim.meta.get("codes_with_bars"):
            sell_end = bo.latest_bar_date(bo.BUY_FREEZE_DATE)
            bo.rebuild_estimates(bo.BUY_FREEZE_DATE, sell_end)
            print("[daily_refresh] rebuild_estimates OK sell_end=%s" % sell_end, flush=True)
        else:
            print("[daily_refresh] no bars -> skip rebuild_estimates (估算档保持上次)", flush=True)
        bo.rebuild_pages()
        print("[daily_refresh] rebuild_pages OK", flush=True)
    except Exception as exc:  # noqa: BLE001
        print("[daily_refresh] rebuild_pages FAILED: %r" % (exc,), flush=True)
        traceback.print_exc()
        return 1

    # 3) 重生成日线导出请求(终点=今天, export_history_request.history_end 动态推进):
    #    次日早晨 QMT 客户端启动 QUANT.py 时执行器按新请求导出含今日的日线,再下一次
    #    refresh 的 replay 即可把到期的 tracking 持仓了结 —— 无需任何手动改日期。
    try:
        from visual import export_history_request as ehr
        ehr.main()
        print("[daily_refresh] history request regenerated end=%s" % ehr.history_end(),
              flush=True)
    except Exception as exc:  # noqa: BLE001
        print("[daily_refresh] export_history_request FAILED(non-fatal): %r" % (exc,),
              flush=True)

    # 4) 重生成分钟导出请求(空/稀疏图代码 + 今日委托代码并入),供执行器下次启动
    #    (或仍在盘中时几秒内)按新签名重导;随后定点补图,愈合一切能愈合的图。
    try:
        from visual import export_minute_request as emr
        emr.main()
        print("[daily_refresh] minute request regenerated", flush=True)
    except Exception as exc:  # noqa: BLE001
        print("[daily_refresh] export_minute_request FAILED(non-fatal): %r" % (exc,),
              flush=True)
    try:
        from visual import rebuild_missing_charts as rmc
        from visual import build_daily_buys, build_daily_sells
        rmc.rebuild_buys(build_daily_buys)
        rmc.rebuild_sells(build_daily_buys, build_daily_sells)
        print("[daily_refresh] chart heal OK", flush=True)
    except Exception as exc:  # noqa: BLE001
        print("[daily_refresh] chart heal FAILED(non-fatal): %r" % (exc,), flush=True)

    print("[daily_refresh] done %s" % time.strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
