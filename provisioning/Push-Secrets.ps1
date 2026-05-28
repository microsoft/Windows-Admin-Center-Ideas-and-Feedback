#requires -Version 7.0
<#
.SYNOPSIS
  Pushes all collected credentials to the GitHub repo as Actions secrets + variables.

.DESCRIPTION
  Consumes the JSON files produced by the other provisioning scripts:
    state\github-app.json   (from Register-GitHubApp.ps1)
    state\aoai.json         (from Provision-AzureOpenAI.ps1)
    state\ado.json          (from Setup-Ado.ps1)
    state\teams.json        (from Setup-Teams.ps1)

  ...and sets the corresponding repository secrets/variables via `gh secret set`
  and `gh variable set`. Requires `gh` CLI installed and `gh auth login`-ed with
  admin:repo on the target repo.

  Idempotent: re-running overwrites existing values.

.PARAMETER Repo
  Target repo (owner/name). Default microsoft/Windows-Admin-Center-Ideas-and-Feedback.

.PARAMETER TeamDL
  Optional team email distribution list to set as TEAM_DL_ADDRESS secret.

.PARAMETER Mode
  Initial WAC_TRIAGE_MODE variable. Default 'gated' for safety on first push.

.PARAMETER DryRun
  Only print what would be set; don't actually call `gh`.

.EXAMPLE
  pwsh .\Push-Secrets.ps1 -TeamDL wac-feedback@microsoft.com -Mode gated
#>
param(
  [string]$Repo = 'microsoft/Windows-Admin-Center-Ideas-and-Feedback',
  [string]$TeamDL = '',
  [string]$EmailWebhookUrl = '',
  [ValidateSet('live','shadow','gated')]
  [string]$Mode = 'gated',
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StateDir  = Join-Path $ScriptDir 'state'

function Read-State($name) {
  $p = Join-Path $StateDir $name
  if (-not (Test-Path $p)) { return $null }
  return Get-Content $p -Raw | ConvertFrom-Json
}

# Verify gh (skip when only previewing)
if (-not $DryRun) {
  $gh = Get-Command gh -ErrorAction SilentlyContinue
  if (-not $gh) {
    Write-Error "gh CLI not found. Install: winget install --id GitHub.cli ; then 'gh auth login'."
    exit 1
  }
  & gh auth status --hostname github.com 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Error "gh not authenticated. Run: gh auth login"
    exit 1
  }
}

# Gather sources
$ghApp = Read-State 'github-app.json'
$aoai  = Read-State 'aoai.json'
$ado   = Read-State 'ado.json'
$adoEntra = Read-State 'ado-entra.json'
$teams = Read-State 'teams.json'

# Build the secret map. Each entry: (name, value, source-or-$null).
$secrets = @()

if ($ghApp) {
  $secrets += [pscustomobject]@{ Name='WAC_BOT_APP_ID';          Value=$ghApp.app_id.ToString() }
  $secrets += [pscustomobject]@{ Name='WAC_BOT_APP_PRIVATE_KEY'; Value=$ghApp.pem }
} else {
  Write-Warning "github-app.json missing — skipping WAC_BOT_APP_ID / PRIVATE_KEY."
}

if ($aoai) {
  $secrets += [pscustomobject]@{ Name='AZURE_OPENAI_ENDPOINT';   Value=$aoai.endpoint }
  $secrets += [pscustomobject]@{ Name='AZURE_OPENAI_API_KEY';    Value=$aoai.api_key }
  $secrets += [pscustomobject]@{ Name='AZURE_OPENAI_DEPLOYMENT'; Value=$aoai.deployment }
  $secrets += [pscustomobject]@{ Name='AZURE_OPENAI_API_VERSION';Value=$aoai.api_version }
} else {
  Write-Warning "aoai.json missing — skipping AZURE_OPENAI_* secrets."
}

if ($ado) {
  $secrets += [pscustomobject]@{ Name='ADO_PAT';                 Value=$ado.pat }
} elseif (-not $adoEntra) {
  Write-Warning "Neither ado.json (PAT) nor ado-entra.json (OIDC) present — ADO calls will be disabled."
}

if ($teams) {
  $secrets += [pscustomobject]@{ Name='TEAMS_WEBHOOK_URL';       Value=$teams.webhook_url }
} else {
  Write-Warning "teams.json missing — skipping TEAMS_WEBHOOK_URL."
}

if ($TeamDL) {
  $secrets += [pscustomobject]@{ Name='TEAM_DL_ADDRESS';         Value=$TeamDL }
}

if ($EmailWebhookUrl) {
  $secrets += [pscustomobject]@{ Name='EMAIL_WEBHOOK_URL';       Value=$EmailWebhookUrl }
}

if ($secrets.Count -eq 0) {
  Write-Error "Nothing to push. Run the provisioning scripts first."
  exit 1
}

Write-Host "=== Pushing secrets to $Repo ===" -ForegroundColor Cyan
foreach ($s in $secrets) {
  $preview = if ($s.Value.Length -le 12) { $s.Value } else { $s.Value.Substring(0,8) + '...' }
  Write-Host ("  {0,-32} = {1}" -f $s.Name, $preview)
  if (-not $DryRun) {
    $s.Value | & gh secret set $s.Name --repo $Repo 2>&1 | Out-String | Write-Verbose
    if ($LASTEXITCODE -ne 0) { throw "gh secret set $($s.Name) failed" }
  }
}

# Variables (non-secret)
$vars = @(
  [pscustomobject]@{ Name='WAC_TRIAGE_MODE';   Value=$Mode },
  [pscustomobject]@{ Name='ADO_SYNC_ENABLED'; Value='false' }
)
if ($ado) {
  $vars += [pscustomobject]@{ Name='ADO_ORG';     Value=$ado.organization }
  $vars += [pscustomobject]@{ Name='ADO_PROJECT'; Value=$ado.project }
  $vars += [pscustomobject]@{ Name='ADO_AREA_PATH'; Value=$ado.area_path }
}
if ($adoEntra) {
  # OIDC variables consumed by .github/workflows/triage-on-issue.yml. With
  # AZURE_CLIENT_ID + AZURE_TENANT_ID set, the workflow does azure/login@v2
  # via OIDC federation and mints an ADO bearer token at run time. No
  # long-lived secret is stored.
  $vars += [pscustomobject]@{ Name='AZURE_CLIENT_ID'; Value=$adoEntra.app_id }
  $vars += [pscustomobject]@{ Name='AZURE_TENANT_ID'; Value=$adoEntra.tenant_id }
  $vars += [pscustomobject]@{ Name='ADO_AUTH_MODE';   Value='entra' }
  # If org/project/area-path didn't come from ado.json, default them here.
  if (-not $ado) {
    $vars += [pscustomobject]@{ Name='ADO_ORG';       Value='microsoft' }
    $vars += [pscustomobject]@{ Name='ADO_PROJECT';   Value='OS' }
    $vars += [pscustomobject]@{ Name='ADO_AREA_PATH'; Value='OS\Core\SPARC\SIX - Server, Intelligence, and Experiences\Enterprise Windows Admin Center' }
  }
}

Write-Host ""
Write-Host "=== Pushing variables to $Repo ===" -ForegroundColor Cyan
foreach ($v in $vars) {
  Write-Host ("  {0,-32} = {1}" -f $v.Name, $v.Value)
  if (-not $DryRun) {
    & gh variable set $v.Name --repo $Repo --body $v.Value 2>&1 | Out-String | Write-Verbose
    if ($LASTEXITCODE -ne 0) { throw "gh variable set $($v.Name) failed" }
  }
}

Write-Host ""
if ($DryRun) {
  Write-Host "DRY RUN — no changes applied." -ForegroundColor Yellow
} else {
  Write-Host "All secrets and variables pushed." -ForegroundColor Green
  Write-Host ("Mode is initially '{0}' — flip to 'live' once you've smoke-tested." -f $Mode) -ForegroundColor Yellow
  Write-Host ("  gh variable set WAC_TRIAGE_MODE --repo $Repo --body live")
}
