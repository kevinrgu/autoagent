# Run this script as Administrator to fix the BIFROST overnight training task.
# Right-click PowerShell → "Run as administrator", then:
#   cd C:\Users\jhpri\projects\autoagent
#   .\fix_overnight_task.ps1

$taskName = "BIFROST-Session-O-AutoAgent"
$python   = "C:\Python313\python.exe"
$workDir  = "C:\Users\jhpri\projects\autoagent"

$action   = New-ScheduledTaskAction -Execute $python -Argument "overnight_run.py" -WorkingDirectory $workDir
$settings = (Get-ScheduledTask -TaskName $taskName).Settings
$settings.ExecutionTimeLimit = "PT4H"   # 3 profiles × 1h + headroom

Set-ScheduledTask -TaskName $taskName -Action $action -Settings $settings

Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan
$t = Get-ScheduledTask -TaskName $taskName
$t.Actions | Select-Object Execute, Arguments, WorkingDirectory | Format-List
$t.Settings | Select-Object ExecutionTimeLimit | Format-List
Write-Host "Done." -ForegroundColor Green
