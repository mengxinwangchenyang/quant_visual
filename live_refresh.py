# -*- coding: utf-8 -*-
"""盘中准实时刷新调度器:交易时段监视成交/分钟导出文件,变了就起子进程重建。

执行器每个实时 bar(约3秒)都在写 qmt_fills.json,分钟导出 qmt_minute_data.json 则在
请求文件变化时盘中重导(如 14:55 大脑随单刷新请求后)。本进程只做轻量调度:
  循环(每 POLL_SECONDS 秒):
    两个数据文件的 mtime 都没变 -> 什么都不做(零成本);
    任一变了 -> 子进程跑 visual/refresh.py(只读重建快照+定点补图)。
子进程每次全新启动,分钟/日线的模块级缓存不会跨周期残留——这是用子进程而非
进程内重建的原因。本脚本到 EXIT_AT 自动退出;收盘后的全量刷新在 daily_refresh.bat
(15:35 归档/卖出账本/资金账本)之后另跑一次 visual/refresh.py。

只读行情/成交 + 写快照文件,绝不下单。用法: python visual/live_refresh.py
"""

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FILLS = PROJECT_ROOT / "qmt_local" / "qmt_fills.json"
MINUTE = PROJECT_ROOT / "qmt_local" / "qmt_minute_data.json"
REFRESH_ONCE = PROJECT_ROOT / "visual" / "refresh.py"
POLL_SECONDS = 60
EXIT_AT = "15:10"   # 收摊后退出;收盘后的全量刷新另行调度。


def log(msg):
    print("%s [live_refresh] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def main():
    log("start (poll=%ds, exit_at=%s)" % (POLL_SECONDS, EXIT_AT))
    last = (0.0, 0.0)
    while True:
        if time.strftime("%H:%M") >= EXIT_AT:
            log("past %s -> exit" % EXIT_AT)
            return 0
        cur = (_mtime(FILLS), _mtime(MINUTE))
        if cur != last:
            t0 = time.time()
            proc = subprocess.run(
                [sys.executable, str(REFRESH_ONCE)],
                cwd=str(PROJECT_ROOT), stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT, timeout=600)
            if proc.returncode == 0:
                last = cur
                log("refreshed in %.1fs (fills @ %s, minute @ %s)"
                    % (time.time() - t0,
                       time.strftime("%H:%M:%S", time.localtime(cur[0])),
                       time.strftime("%H:%M:%S", time.localtime(cur[1]))))
            else:
                log("refresh exit=%s (will retry next poll)" % proc.returncode)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
