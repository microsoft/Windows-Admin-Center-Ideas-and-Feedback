#requires -Version 7.0
<#
.SYNOPSIS
  Orchestrates the full provisioning sequence for wac-feedback-bot.

.DESCRIPTION
  Runs each step in order, with confirmation prompts between them. You can
  skip any step that's already done by passing the corresponding -Skip switch.

  Order:
    0. Install-Prereqs.ps1       (gh, az)
    1. Register-GitHubApp.ps1    (browser-driven manifest flow)
    2. Provision-AzureOpenAI.ps1 (az CLI, requires `az login`)
    3. Setup-Ado.ps1             (PAT prompt + REST validation)
    4. Setup-Teams.ps1           (URL prompt + test card)
    5. Push-Secrets.ps1          (gh secret set + gh variable set)

  Each step writes its output to provisioning\state\*.json and is independently
  re-runnable.

.PARAMETER Repo
  Target GitHub repo. Default microsoft/Windows-Admin-Center-Ideas-and-Feedback.

.PARAMETER TeamDL
  Optional team distribution list (email).

.PARAMETER InitialMode
  Mode to set on first push. Default 'gated'. After verifying a gated run,
  flip with: gh variable set WAC_TRIAGE_MODE --body live --repo <repo>.

.PARAMETER SkipPrereqs / -SkipApp / -SkipAoai / -SkipAdo / -SkipTeams / -SkipPush
  Skip individual steps.

.PARAMETER Yes
  Don't pause between steps.

.EXAMPLE
  pwsh .\Provision-All.ps1 -TeamDL wac-feedback@microsoft.com
#>
param(
  [string]$Repo = 'microsoft/Windows-Admin-Center-Ideas-and-Feedback',
  [string]$TeamDL = '',
  [ValidateSet('live','shadow','gated')]
  [string]$InitialMode = 'gated',
  [switch]$SkipPrereqs,
  [switch]$SkipApp,
  [switch]$SkipAoai,
  [switch]$SkipAdo,
  [switch]$SkipTeams,
  [switch]$SkipPush,
  [switch]$Yes
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Step([string]$Name, [scriptblock]$Action) {
  Write-Host ""
  Write-Host ("=" * 70) -ForegroundColor Cyan
  Write-Host ("STEP: $Name") -ForegroundColor Cyan
  Write-Host ("=" * 70) -ForegroundColor Cyan
  if (-not $Yes) {
    $ans = Read-Host "Run this step? [Y/n/s(kip)]"
    if ($ans -match '^[sn]') { Write-Host "Skipped."; return }
  }
  & $Action
}

if (-not $SkipPrereqs) {
  Step '0/5  Install prerequisites' {
    & (Join-Path $ScriptDir 'Install-Prereqs.ps1')
  }
}

if (-not $SkipApp) {
  Step '1/5  Register GitHub App (browser)' {
    & (Join-Path $ScriptDir 'Register-GitHubApp.ps1')
  }
}

if (-not $SkipAoai) {
  Step '2/5  Provision Azure OpenAI (az CLI)' {
    & (Join-Path $ScriptDir 'Provision-AzureOpenAI.ps1')
  }
}

if (-not $SkipAdo) {
  Step '3/5  Set up Azure DevOps PAT' {
    & (Join-Path $ScriptDir 'Setup-Ado.ps1')
  }
}

if (-not $SkipTeams) {
  Step '4/5  Set up Teams Incoming Webhook' {
    & (Join-Path $ScriptDir 'Setup-Teams.ps1')
  }
}

if (-not $SkipPush) {
  Step '5/5  Push secrets + variables to repo' {
    $args = @('-Repo', $Repo, '-Mode', $InitialMode)
    if ($TeamDL) { $args += @('-TeamDL', $TeamDL) }
    & (Join-Path $ScriptDir 'Push-Secrets.ps1') @args
  }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Push the .github/ tree to $Repo  (the workflow will then be active)."
Write-Host "  2. Follow .github\copilot-triage\RUNBOOK.md to file your first gated test issue."
