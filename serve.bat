@echo off
REM Serve the visual pages at http://127.0.0.1:5510/daily_buys.html
if not defined PYTHON_EXE set "PYTHON_EXE=C:\Python\Python38\python.exe"
cd /d "%~dp0"
"%PYTHON_EXE%" serve_daily_buys.py
