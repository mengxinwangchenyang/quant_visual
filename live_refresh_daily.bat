@echo off
REM Intraday near-real-time viz refresh (Task Scheduler, weekdays 09:25).
REM Watches qmt_fills.json and rebuilds buy/sell snapshots minute-level.
REM Read-only + snapshot writes; never places orders. Exits itself at 15:10.
echo [launcher] start %date% %time% >> C:\Users\ocyy\Desktop\work\quant\ceshi_tick_llm_qmt\qmt_local\live_refresh.log
cd /d C:\Users\ocyy\Desktop\work\quant\ceshi_tick_llm_qmt
C:\Python\Python38\python.exe visual\live_refresh.py >> C:\Users\ocyy\Desktop\work\quant\ceshi_tick_llm_qmt\qmt_local\live_refresh.log 2>&1
echo [launcher] exit code %errorlevel% %date% %time% >> C:\Users\ocyy\Desktop\work\quant\ceshi_tick_llm_qmt\qmt_local\live_refresh.log
