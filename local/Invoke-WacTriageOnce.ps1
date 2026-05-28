<#
.SYNOPSIS
  One-off triage of a specific issue (or N most-recent issues) with email report.

.DESCRIPTION
  Wraps `python local/runner.py once …`. Sends an HTML report to your email
  (default: trungtran@microsoft.com) via Outlook COM. Does NOT post anything
  to GitHub or ADO — this is the safe local demo.

.PARAMETER IssueNumber
  A specific issue number on the WAC feedback repo.

.PARAMETER RecentCount
  Triage the N most-recently-updated open issues. Mutually exclusive with -IssueNumber.

.PARAMETER FromFile
  Use an issue payload from a local JSON file (for offline demos).

.PARAMETER To
  Email recipient. Default: trungtran@microsoft.com

.PARAMETER NoEmail
  Skip sending email; write the report to local/outbox/ instead.

.PARAMETER UseRealLlm
  Disable the mock LLM and use real Azure OpenAI. Requires AZURE_OPENAI_*
  env vars to be set in this PowerShell session.

.PARAMETER Repo
  Override the target repo. Default: microsoft/Windows-Admin-Center-Ideas-and-Feedback

.EXAMPLE
  pwsh .\Invoke-WacTriageOnce.ps1 -IssueNumber 42

.EXAMPLE
  pwsh .\Invoke-WacTriageOnce.ps1 -RecentCount 3

.EXAMPLE
  pwsh .\Invoke-WacTriageOnce.ps1 -FromFile ..\.github\copilot-triage\tests\sample_issue.json -NoEmail
#>

[CmdletBinding(DefaultParameterSetName = 'Recent')]
param(
    [Parameter(ParameterSetName = 'One', Mandatory = $true)]
    [int]$IssueNumber,

    [Parameter(ParameterSetName = 'Recent')]
    [int]$RecentCount = 1,

    [Parameter(ParameterSetName = 'File', Mandatory = $true)]
    [string]$FromFile,

    [string]$To = "trungtran@microsoft.com",
    [switch]$NoEmail,
    [switch]$UseRealLlm,
    [string]$Repo = "microsoft/Windows-Admin-Center-Ideas-and-Feedback",
    [switch]$Verbose_
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $PSCommandPath
$projectRoot = Split-Path -Parent $here
$agentVenvPy = Join-Path $projectRoot ".github\copilot-triage\.venv\Scripts\python.exe"

if (-not (Test-Path $agentVenvPy)) {
    Write-Host "Python venv not found at $agentVenvPy" -ForegroundColor Yellow
    Write-Host "Run setup first:" -ForegroundColor Yellow
    Write-Host "  cd $projectRoot\.github\copilot-triage" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\Activate.ps1" -ForegroundColor Yellow
    Write-Host "  pip install -r requirements.txt" -ForegroundColor Yellow
    Write-Host "  pip install -r ..\..\local\requirements.txt" -ForegroundColor Yellow
    exit 1
}

$cmd = @('--repo', $Repo, '--to', $To, '--agent-mode', 'shadow')
if ($NoEmail)    { $cmd += '--no-email' }
if ($UseRealLlm) { $cmd += '--no-mock' }
if ($Verbose_)   { $cmd += '--verbose' }
$cmd += 'once'

switch ($PSCmdlet.ParameterSetName) {
    'One'    { $cmd += @('--issue',     "$IssueNumber") }
    'Recent' { $cmd += @('--recent',    "$RecentCount") }
    'File'   { $cmd += @('--from-file', $FromFile) }
}

Write-Host "Running: python local/runner.py $($cmd -join ' ')" -ForegroundColor Cyan
& $agentVenvPy (Join-Path $here 'runner.py') @cmd
exit $LASTEXITCODE
