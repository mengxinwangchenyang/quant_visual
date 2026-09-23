@echo off
REM QMT post-close visualization refresh.
set "ROOT=%~dp0.."
if not defined PYTHON_EXE set "PYTHON_EXE=C:\Python\Python38\python.exe"
set "LOG=%ROOT%\visual\daily_refresh.log"
if not exist "%PYTHON_EXE%" (
  echo [launcher] ERROR: Python interpreter not found: %PYTHON_EXE% >> "%LOG%"
  exit /b 2
)
echo [launcher] start %date% %time% >> "%LOG%"
cd /d "%ROOT%"
"%PYTHON_EXE%" visual\daily_refresh.py >> "%LOG%" 2>&1
set "RC=%errorlevel%"
echo [launcher] exit code %RC% %date% %time% >> "%LOG%"
exit /b %RC%
