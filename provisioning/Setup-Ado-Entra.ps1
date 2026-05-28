#requires -Version 7.0
<#
.SYNOPSIS
  Provisions Microsoft Entra OIDC federation so GitHub Actions can mint
  short-lived Azure DevOps access tokens — replaces the PAT approach.

.DESCRIPTION
  Required because Microsoft tenant policy restricts ADO PATs to packaging
  scope only (mid-2026 enforcement), and Global PATs are decommissioned on
  Dec 1, 2026 per
  https://devblogs.microsoft.com/devops/retirement-of-global-personal-access-tokens-in-azure-dev-ops/

  This script does the automatable half:
    1. Creates an Entra app registration  'wac-feedback-bot'  in your tenant.
    2. Creates a service principal for it.
    3. Adds federated credentials trusting GitHub Actions on:
       - microsoft/Windows-Admin-Center-Ideas-and-Feedback (any branch)
       - <Production-Repo> (optional override)
       - <Test-Repo>       (defaults to trungtran-msft/wac-feedback-test)
    4. Persists app_id / tenant_id / sp_object_id to
       provisioning\state\ado-entra.json

  After this completes you must do TWO manual UI steps in ADO (no API exists
  to automate them):
    A. Organization Settings -> Users -> Add user
       Paste the service principal object id, give it access to the project.
    B. Project Settings -> Permissions -> Contributors -> Members -> Add
       Add the service principal. Grant the area path 'Create Child Items'.
  The script prints the exact object id you need and opens both pages.

.PARAMETER AppDisplayName
  Display name for the Entra app registration. Default: wac-feedback-bot.

.PARAMETER ProductionRepo
  Production GitHub repo in 'owner/repo' form. Default:
  microsoft/Windows-Admin-Center-Ideas-and-Feedback.

.PARAMETER TestRepo
  Test GitHub repo for staging. Default: trungtran-msft/wac-feedback-test.

.PARAMETER SkipFederatedCredentials
  Skip creating federated credentials (use only if you've already added them
  via the portal).

.PARAMETER WhatIfMode
  Print the commands that would run without invoking az.

.EXAMPLE
  pwsh provisioning\Setup-Ado-Entra.ps1
#>
param(
  [string]$AppDisplayName = 'wac-feedback-bot',
  [string]$ProductionRepo = 'microsoft/Windows-Admin-Center-Ideas-and-Feedback',
  [string]$TestRepo       = 'trungtran-msft/wac-feedback-test',
  [switch]$SkipFederatedCredentials,
  [switch]$WhatIfMode
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StateDir  = Join-Path $ScriptDir 'state'
$StatePath = Join-Path $StateDir 'ado-entra.json'
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

Write-Host "=== Microsoft Entra OIDC federation for ADO ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "This replaces the legacy PAT approach. After this completes," -ForegroundColor Yellow
Write-Host "GitHub Actions will mint short-lived ADO bearer tokens at run time," -ForegroundColor Yellow
Write-Host "no long-lived secret will be stored, and you'll be ready for the" -ForegroundColor Yellow
Write-Host "Dec 1 2026 Global PAT decommissioning." -ForegroundColor Yellow
Write-Host ""

# -------- 1. Verify az login --------
$azCmd = Get-Command az -ErrorAction SilentlyContinue
if (-not $azCmd) {
  Write-Error "Azure CLI not found. Install: winget install Microsoft.AzureCLI"
  exit 1
}

try {
  $accountJson = az account show --output json 2>$null
  if (-not $accountJson) { throw "not logged in" }
  $account = $accountJson | ConvertFrom-Json
} catch {
  Write-Error "Run 'az login' first (sign in with your microsoft.com identity)."
  exit 1
}
$tenantId = $account.tenantId
$userName = $account.user.name
Write-Host ("Signed in as : {0}" -f $userName) -ForegroundColor Green
Write-Host ("Tenant id    : {0}" -f $tenantId) -ForegroundColor Green
if ($userName -notlike '*@microsoft.com') {
  Write-Warning "Your az session is signed in as '$userName' which doesn't look like a microsoft.com identity. The Entra app must be in the microsoft tenant to grant it ADO access. Run 'az login --tenant microsoft.onmicrosoft.com' if needed."
}
Write-Host ""

if ($WhatIfMode) {
  Write-Host "WhatIf mode — would do:" -ForegroundColor Cyan
  Write-Host "  az ad app create --display-name $AppDisplayName"
  Write-Host "  az ad sp create --id <appId>"
  Write-Host "  az ad app federated-credential create  (x3: prod main, test main, production branch add/wac-feedback-bot)"
  exit 0
}

# -------- 2. App registration --------
Write-Host "Looking up or creating Entra app '$AppDisplayName'..." -ForegroundColor Cyan
$existing = az ad app list --display-name $AppDisplayName --output json 2>$null | ConvertFrom-Json
if ($existing -and $existing.Count -gt 0) {
  $app = $existing[0]
  Write-Host ("  Reusing existing app : appId={0}" -f $app.appId) -ForegroundColor Green
} else {
  $appJson = az ad app create --display-name $AppDisplayName --sign-in-audience AzureADMyOrg --output json
  $app = $appJson | ConvertFrom-Json
  Write-Host ("  Created new app      : appId={0}" -f $app.appId) -ForegroundColor Green
}
$appId  = $app.appId
$objectId = $app.id

# -------- 3. Service principal --------
Write-Host "Looking up or creating service principal..." -ForegroundColor Cyan
$spExisting = az ad sp list --filter "appId eq '$appId'" --output json 2>$null | ConvertFrom-Json
if ($spExisting -and $spExisting.Count -gt 0) {
  $sp = $spExisting[0]
  Write-Host ("  Reusing existing SP  : objectId={0}" -f $sp.id) -ForegroundColor Green
} else {
  $spJson = az ad sp create --id $appId --output json
  $sp = $spJson | ConvertFrom-Json
  Write-Host ("  Created new SP       : objectId={0}" -f $sp.id) -ForegroundColor Green
}
$spObjectId = $sp.id

# -------- 4. Federated credentials --------
if (-not $SkipFederatedCredentials) {
  $repos = @($ProductionRepo, $TestRepo) | Where-Object { $_ -and $_.Trim() } | Select-Object -Unique
  $credConfigs = @()
  foreach ($r in $repos) {
    $owner, $name = $r.Split('/', 2)
    if (-not $name) {
      Write-Warning "Skipping malformed repo '$r' (expected owner/repo)."
      continue
    }
    # Cover the default branch for both repos. The actions/checkout step
    # in the workflow already pins to refs/heads/<branch> at run time.
    $credConfigs += @(
      [pscustomobject]@{ Name = "gh-$owner-$name-main"; Subject = "repo:$($r):ref:refs/heads/main";                  Desc = "GitHub Actions on $r (main branch)" }
    )
  }
  # Add the in-development feature branch on the production repo so PR #367
  # workflow runs work before merge.
  $credConfigs += [pscustomobject]@{ Name = "gh-prod-feature-branch"; Subject = "repo:$($ProductionRepo):ref:refs/heads/add/wac-feedback-bot"; Desc = "GitHub Actions on $ProductionRepo (feature branch)" }

  Write-Host "Configuring federated credentials..." -ForegroundColor Cyan
  $existingFC = az ad app federated-credential list --id $appId --output json 2>$null | ConvertFrom-Json
  $existingSubjects = @{}
  if ($existingFC) {
    foreach ($f in $existingFC) { $existingSubjects[$f.subject] = $f.name }
  }
  foreach ($c in $credConfigs) {
    if ($existingSubjects.ContainsKey($c.Subject)) {
      Write-Host ("  Skip (exists)        : {0}" -f $c.Subject) -ForegroundColor Yellow
      continue
    }
    $params = @{
      name        = $c.Name
      issuer      = 'https://token.actions.githubusercontent.com'
      subject     = $c.Subject
      description = $c.Desc
      audiences   = @('api://AzureADTokenExchange')
    } | ConvertTo-Json -Compress
    # az ad app federated-credential create expects the parameter set as a json string.
    $params | Out-File -Encoding utf8 (Join-Path $env:TEMP "fc.json")
    az ad app federated-credential create --id $appId --parameters "@$(Join-Path $env:TEMP 'fc.json')" --only-show-errors | Out-Null
    Write-Host ("  Created              : {0}" -f $c.Subject) -ForegroundColor Green
  }
}

# -------- 5. Persist state --------
$cfg = [ordered]@{
  app_display_name = $AppDisplayName
  app_id           = $appId
  app_object_id    = $objectId
  sp_object_id     = $spObjectId
  tenant_id        = $tenantId
  production_repo  = $ProductionRepo
  test_repo        = $TestRepo
  configured       = (Get-Date).ToUniversalTime().ToString('o')
}
$cfg | ConvertTo-Json -Depth 6 | Set-Content -Path $StatePath -Encoding utf8
attrib +H $StatePath 2>$null
Write-Host ""
Write-Host ("State saved to: {0}" -f $StatePath) -ForegroundColor Green

# -------- 6. Tell the user the UI-only steps --------
Write-Host ""
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " TWO MANUAL UI STEPS REMAIN (no API exists to automate these)" -ForegroundColor Yellow
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Service principal object id (copy this — you'll paste it below):" -ForegroundColor Yellow
Write-Host ""
Write-Host ("    {0}" -f $spObjectId) -ForegroundColor White -BackgroundColor DarkBlue
Write-Host ""
Write-Host "STEP A. Grant the SP access to the microsoft ADO organization" -ForegroundColor Cyan
Write-Host "  1. Open https://dev.azure.com/microsoft/_settings/users"
Write-Host "  2. Click '+ Add users'."
Write-Host "  3. In 'Users or service principals' paste:  $AppDisplayName"
Write-Host "     (or the object id above if name search doesn't match)."
Write-Host "  4. Access level     : Basic"
Write-Host "  5. Add to projects  : OS"
Write-Host "  6. Click 'Add'."
Write-Host ""
Write-Host "STEP B. Grant 'Create Work Items' on the area path" -ForegroundColor Cyan
Write-Host "  1. Open https://dev.azure.com/microsoft/OS/_settings/permissions"
Write-Host "  2. Find/create a group that can create work items in:"
Write-Host "     '\\OS\\Core\\SPARC\\SIX - Server, Intelligence, and Experiences\\Enterprise Windows Admin Center'"
Write-Host "  3. Add '$AppDisplayName' (the SP) to that group."
Write-Host "     Easiest: add to 'Contributors' if SIX project allows it."
Write-Host "  4. If you don't have permission to grant project access," 
Write-Host "     contact the OS / SIX project admin and forward them this"
Write-Host "     service principal name + object id."
Write-Host ""
$open = Read-Host "Open both pages in your browser now? [Y/n]"
if ($open -ne 'n' -and $open -ne 'N') {
  Start-Process 'https://dev.azure.com/microsoft/_settings/users'
  Start-Sleep -Seconds 1
  Start-Process 'https://dev.azure.com/microsoft/OS/_settings/permissions'
}

Write-Host ""
Write-Host "Once both steps are done, push the new secrets/vars to the repos:" -ForegroundColor Cyan
Write-Host "  pwsh provisioning\Push-Secrets.ps1 -Repo $TestRepo -TeamDL trungtran@microsoft.com -Mode live"
Write-Host "  pwsh provisioning\Push-Secrets.ps1 -Repo $ProductionRepo -TeamDL trungtran@microsoft.com -Mode gated"
Write-Host ""
Write-Host "Then edit the test issue title to re-trigger and watch the run." -ForegroundColor Cyan
