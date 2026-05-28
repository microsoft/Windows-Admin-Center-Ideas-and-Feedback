#requires -Version 7.0
<#
.SYNOPSIS
  Registers the wac-feedback-bot GitHub App via the documented manifest flow.

.DESCRIPTION
  Launches a localhost HTTP server (port 54017), opens the browser to a one-click
  "Create GitHub App" page, and on callback exchanges the temporary code for the
  real App credentials. Result is written to
  provisioning\state\github-app.json (file mode 0600).

  The browser still has to be signed in to your GitHub account, and you still have
  to click "Create GitHub App" then "Install" — those steps are required by GitHub.

.PARAMETER NoBrowser
  Skip auto-opening the browser; you open http://localhost:54017/ manually.

.EXAMPLE
  pwsh .\Register-GitHubApp.ps1
#>
param(
  [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $ScriptDir 'github-app\register_server.py'
$Output = Join-Path $ScriptDir 'state\github-app.json'

if (-not (Test-Path $Py)) { throw "Missing $Py" }

# Use the project venv if present, else fall back to system python.
$VenvPython = Join-Path (Split-Path $ScriptDir -Parent) '.github\copilot-triage\.venv\Scripts\python.exe'
$Python = if (Test-Path $VenvPython) { $VenvPython } else { 'python' }

Write-Host "=== GitHub App registration ==="
Write-Host "Using Python: $Python"
Write-Host "Listening on http://localhost:54017/"
Write-Host ""
Write-Host "Steps that happen next:" -ForegroundColor Cyan
Write-Host "  1. Browser opens to localhost (auto-submits manifest to GitHub)"
Write-Host "  2. GitHub shows 'Create GitHub App' page — you click Create"
Write-Host "  3. GitHub redirects back to localhost with a one-time code"
Write-Host "  4. This script trades the code for App credentials + private key"
Write-Host "  5. Credentials saved to: $Output"
Write-Host "  6. Browser shows an 'Install on your repo' button — you click it"
Write-Host ""

$args_ = @($Py)
if ($NoBrowser) { $args_ += '--no-browser' }
& $Python @args_
$exit = $LASTEXITCODE

if ($exit -ne 0) {
  Write-Error "Registration helper exited with code $exit"
  exit $exit
}

if (-not (Test-Path $Output)) {
  Write-Error "Expected credentials file not found: $Output"
  exit 2
}

Write-Host ""
Write-Host "Credentials saved." -ForegroundColor Green
$cfg = Get-Content $Output -Raw | ConvertFrom-Json
Write-Host ("  app_id:       {0}" -f $cfg.app_id)
Write-Host ("  slug:         {0}" -f $cfg.slug)
Write-Host ("  html_url:     {0}" -f $cfg.html_url)
Write-Host ("  pem length:   {0} chars" -f $cfg.pem.Length)
Write-Host ""
Write-Host "Don't forget to click 'Install' in the browser tab if you haven't already." -ForegroundColor Yellow
