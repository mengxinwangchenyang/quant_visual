@echo off
REM Intraday visual refresh: rebuild on fills/minute changes, exits at 15:10.
if not defined PYTHON_EXE set "PYTHON_EXE=C:\Python\Python38\python.exe"
set "LOG=%~dp0live_refresh.log"
cd /d "%~dp0"
"%PYTHON_EXE%" live_refresh.py >> "%LOG%" 2>&1
exit /b %errorlevel%