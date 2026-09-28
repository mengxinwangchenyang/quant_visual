@echo off
REM Post-close visual refresh (read-only on trading data). Run after daily_refresh.bat (15:35).
if not defined PYTHON_EXE set "PYTHON_EXE=C:\Python\Python38\python.exe"
set "LOG=%~dp0refresh.log"
cd /d "%~dp0"
echo [launcher] start %date% %time% >> "%LOG%"
"%PYTHON_EXE%" refresh.py >> "%LOG%" 2>&1
set "RC=%errorlevel%"
echo [launcher] exit code %RC% %date% %time% >> "%LOG%"
exit /b %RC%
