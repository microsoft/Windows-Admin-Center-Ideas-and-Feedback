<#
.SYNOPSIS
  Starts the local triage runner in the background. It polls the repo for new
  open issues and emails you a triage report for each one.

.DESCRIPTION
  Spawns the Python runner as a *detached* Windows process so it survives this
  PowerShell session ending. Records the PID at local\state\loop.pid for
  Stop-WacTriageBot.ps1 to find.

  Logs are written to local\state\loop.log.

.PARAMETER PollSeconds
  How often to poll. Default: 60.

.PARAMETER RecentCount
  How many recent open issues to inspect per poll (helps catch fast bursts).
  Default: 10.

.PARAMETER To
  Email recipient. Default: trungtran@microsoft.com

.PARAMETER UseRealLlm
  Disable the mock LLM and use real Azure OpenAI (needs AZURE_OPENAI_* in env).

.PARAMETER Repo
  Override the target repo. Default: microsoft/Windows-Admin-Center-Ideas-and-Feedback

.EXAMPLE
  pwsh .\Start-WacTriageBot.ps1 -PollSeconds 30
#>

[CmdletBinding()]
param(
    [int]$PollSeconds = 60,
    [int]$RecentCount = 10,
    [string]$To = "trungtran@microsoft.com",
    [switch]$UseRealLlm,
    [string]$Repo = "microsoft/Windows-Admin-Center-Ideas-and-Feedback"
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $PSCommandPath
$projectRoot = Split-Path -Parent $here
$agentVenvPy = Join-Path $projectRoot ".github\copilot-triage\.venv\Scripts\python.exe"
$stateDir = Join-Path $here "state"
$pidFile  = Join-Path $stateDir "loop.pid"
$logFile  = Join-Path $stateDir "loop.log"

New-Item -ItemType Directory -Path $stateDir -Force | Out-Null

if (Test-Path $pidFile) {
    $existingPid = (Get-Content $pidFile -Raw).Trim()
    $proc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "A loop is already running (PID=$existingPid)." -ForegroundColor Yellow
        Write-Host "Stop it first:  pwsh .\Stop-WacTriageBot.ps1" -ForegroundColor Yellow
        exit 1
    } else {
        Write-Host "Stale PID file found; removing." -ForegroundColor DarkGray
        Remove-Item $pidFile -Force
    }
}

if (-not (Test-Path $agentVenvPy)) {
    Write-Host "Python venv not found at $agentVenvPy" -ForegroundColor Red
    exit 1
}

$args = @(
    (Join-Path $here 'runner.py'),
    '--repo', $Repo,
    '--to',   $To,
    '--agent-mode', 'shadow'
)
if ($UseRealLlm) { $args += '--no-mock' }
$args += 'loop'
$args += @('--poll-seconds', "$PollSeconds", '--recent', "$RecentCount")

Write-Host "Starting background runner..." -ForegroundColor Cyan
Write-Host "  log: $logFile"
Write-Host "  pid: $pidFile"

# Force UTF-8 for the child python so we can decode em-dashes safely.
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# Empty the log so a previous run's tail doesn't confuse status output.
if (Test-Path $logFile) { Clear-Content $logFile -ErrorAction SilentlyContinue }

$errLog = "$logFile.err"
$proc = Start-Process -FilePath $agentVenvPy `
    -ArgumentList $args `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError  $errLog `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 2

# Verify it's still alive (it should be, since 'loop' runs forever).
if (-not $proc -or $proc.HasExited) {
    Write-Host "Runner exited immediately. Diagnostics:" -ForegroundColor Red
    if (Test-Path $logFile) { Get-Content $logFile -Tail 30 }
    if (Test-Path $errLog)  { Get-Content $errLog  -Tail 30 }
    exit 1
}

Set-Content -Path $pidFile -Value $proc.Id
Write-Host "Started runner (PID=$($proc.Id))." -ForegroundColor Green
Write-Host "Use:" -ForegroundColor Green
Write-Host "  pwsh .\Get-WacTriageBotStatus.ps1   # check status / tail log"
Write-Host "  pwsh .\Stop-WacTriageBot.ps1        # stop"
exit 0
