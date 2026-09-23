$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$pythonExe = if ($env:PYTHON_EXE) { $env:PYTHON_EXE } else { "C:\Python\Python38\python.exe" }
& $pythonExe ".\visual\daily_refresh.py"
exit $LASTEXITCODE
