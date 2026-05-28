#requires -Version 7.0
<#
.SYNOPSIS
  Installs the tools the provisioning scripts depend on: gh and (verifies) az.

.DESCRIPTION
  Uses winget. Idempotent — if a tool is already present, it's left alone.
  Re-launches PATH-refreshing pwsh tip at the end so you don't have to restart
  your shell.

.PARAMETER SkipGh
  Don't try to install gh (e.g., if you'll install it via a corp portal).
#>
param([switch]$SkipGh)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Write-Host "=== Prerequisite check ===" -ForegroundColor Cyan

function Test-Cmd($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

# 1. winget
if (-not (Test-Cmd 'winget')) {
  Write-Warning "winget not available on this box. Install GitHub CLI manually from https://cli.github.com/"
  $useWinget = $false
} else {
  $useWinget = $true
}

# 2. gh
if (Test-Cmd 'gh') {
  Write-Host "  gh        OK ($(Get-Command gh | Select-Object -ExpandProperty Source))" -ForegroundColor Green
} elseif ($SkipGh) {
  Write-Host "  gh        skipped (per -SkipGh)" -ForegroundColor Yellow
} elseif ($useWinget) {
  Write-Host "  gh        installing via winget..."
  winget install --id GitHub.cli --silent --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) { Write-Warning "gh install via winget reported exit code $LASTEXITCODE" }
} else {
  Write-Warning "  gh missing and no winget — install from https://cli.github.com/"
}

# 3. az
if (Test-Cmd 'az') {
  Write-Host "  az        OK ($(Get-Command az | Select-Object -ExpandProperty Source))" -ForegroundColor Green
} elseif ($useWinget) {
  Write-Host "  az        installing via winget..."
  winget install --id Microsoft.AzureCLI --silent --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) { Write-Warning "az install via winget reported exit code $LASTEXITCODE" }
} else {
  Write-Warning "  az missing — install from https://aka.ms/installazurecli"
}

# 4. python
if (Test-Cmd 'python') {
  Write-Host "  python    OK ($(Get-Command python | Select-Object -ExpandProperty Source))" -ForegroundColor Green
} else {
  Write-Warning "  python missing — install from https://python.org or via Microsoft Store"
}

Write-Host ""
Write-Host "Refresh PATH if anything was just installed:" -ForegroundColor Yellow
Write-Host "  Start a new pwsh window OR run:"
Write-Host '    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")'
