<#
.SYNOPSIS
  Shows whether the background triage runner is alive, what it's done, and
  tails the recent log.

.PARAMETER Tail
  Number of log lines to show. Default: 20.
#>

[CmdletBinding()]
param([int]$Tail = 20)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $PSCommandPath
$stateDir = Join-Path $here "state"
$pidFile  = Join-Path $stateDir "loop.pid"
$logFile  = Join-Path $stateDir "loop.log"
$projectRoot = Split-Path -Parent $here
$agentVenvPy = Join-Path $projectRoot ".github\copilot-triage\.venv\Scripts\python.exe"

Write-Host "== Background runner =="
if (Test-Path $pidFile) {
    $loopPid = (Get-Content $pidFile -Raw).Trim()
    $proc = Get-Process -Id $loopPid -ErrorAction SilentlyContinue
    if ($proc) {
        $uptime = (Get-Date) - $proc.StartTime
        Write-Host "  Status   : RUNNING" -ForegroundColor Green
        Write-Host "  PID      : $loopPid"
        Write-Host "  Started  : $($proc.StartTime)"
        Write-Host ("  Uptime   : {0:hh\:mm\:ss}" -f $uptime)
        Write-Host "  RAM (MB) : $([Math]::Round($proc.WorkingSet64 / 1MB,1))"
    } else {
        Write-Host "  Status   : DEAD (stale PID file)" -ForegroundColor Yellow
        Write-Host "  PID file : $pidFile (PID $loopPid not found)"
    }
} else {
    Write-Host "  Status   : NOT RUNNING" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "== Local state ==" -ForegroundColor Cyan
& $agentVenvPy (Join-Path $here 'runner.py') status

Write-Host ""
if (Test-Path $logFile) {
    Write-Host "== Last $Tail log lines ($logFile) ==" -ForegroundColor Cyan
    $content = Get-Content -Tail $Tail $logFile
    if ($content) { $content } else { Write-Host "(log is empty — runner has had nothing to say)" -ForegroundColor DarkGray }
} else {
    Write-Host "(no log file yet at $logFile)" -ForegroundColor DarkGray
}

$errLog = "$logFile.err"
if (Test-Path $errLog) {
    Write-Host ""
    Write-Host "== Last $Tail err lines ($errLog) ==" -ForegroundColor Cyan
    $errContent = Get-Content -Tail $Tail $errLog
    if ($errContent) { $errContent } else { Write-Host "(no errors logged)" -ForegroundColor DarkGray }
}
