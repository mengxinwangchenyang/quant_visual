$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$updateScript = Join-Path $scriptRoot "update_daily_buys_snapshot.ps1"
$taskName = "VirtualQmtDailyBuySnapshot"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$updateScript`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "15:30"
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Registered task: $taskName, weekdays at 15:30"

$webScript = Join-Path $scriptRoot "start_daily_buys_web.ps1"
$webTaskName = "VirtualQmtDailyBuyWeb"
$webAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$webScript`""
$webTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$webSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $webTaskName -Action $webAction -Trigger $webTrigger -Settings $webSettings -Principal $principal -Force | Out-Null
Write-Host "Registered task: $webTaskName, at logon"
