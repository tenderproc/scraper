# Registers a Windows Scheduled Task that re-scrapes all 206 deliberations.be
# communes and re-enriches against BOSA's current data, once a week.
# Council sessions are monthly, so weekly is ample freshness - this is
# intentionally much less frequent than the BOSA scraper's own twice-daily
# task, which covers a nationally-updated feed.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\register_windows_task.ps1

param(
    [string]$TaskName = "TenderProc-Wallonia-Scraper",
    [string]$PythonExe = (Get-Command python).Source,
    [string]$DayOfWeek = "Monday",
    [string]$Time = "06:00"
)

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Action = New-ScheduledTaskAction -Execute $PythonExe `
    -Argument "-m wallonia_scraper.weekly" `
    -WorkingDirectory $ProjectDir

$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $Time

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Weekly full-roster re-scrape and BOSA-dedup re-enrichment for the TenderProc Wallonia deliberations.be prototype." `
    -Force

Write-Host "Registered scheduled task '$TaskName' running weekly: $DayOfWeek at $Time"
Write-Host "Working directory: $ProjectDir"
Write-Host "To test immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To view run history: Get-ScheduledTaskInfo -TaskName '$TaskName'"
