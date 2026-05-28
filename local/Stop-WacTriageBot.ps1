<#
.SYNOPSIS
  Stops the local triage runner started by Start-WacTriageBot.ps1.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $PSCommandPath
$pidFile = Join-Path $here "state\loop.pid"

if (-not (Test-Path $pidFile)) {
    Write-Host "No PID file at $pidFile. The runner does not appear to be running." -ForegroundColor Yellow
    exit 0
}

$loopPid = (Get-Content $pidFile -Raw).Trim()
$proc = Get-Process -Id $loopPid -ErrorAction SilentlyContinue
if (-not $proc) {
    Write-Host "PID $loopPid is not running. Cleaning up stale PID file." -ForegroundColor Yellow
    Remove-Item $pidFile -Force
    exit 0
}

Write-Host "Stopping PID=$loopPid ($($proc.ProcessName))..." -ForegroundColor Cyan
Stop-Process -Id $loopPid -Force
Start-Sleep -Seconds 1
if (Get-Process -Id $loopPid -ErrorAction SilentlyContinue) {
    Write-Host "Process did not exit. Try again or kill manually." -ForegroundColor Red
    exit 1
}
Remove-Item $pidFile -Force
Write-Host "Stopped." -ForegroundColor Green
exit 0
