#requires -Version 7.0
<#
.SYNOPSIS
  Captures and validates an Azure DevOps PAT for the wac-feedback-bot agent.

.DESCRIPTION
  ADO PATs cannot be created programmatically — the user has to generate one in
  the ADO web UI. This script:

    1. Opens the ADO PAT creation page (with the exact scopes pre-explained).
    2. Prompts for the PAT as a SecureString.
    3. Validates the PAT against:
         - GET https://dev.azure.com/microsoft/_apis/projects/OS
         - GET https://dev.azure.com/microsoft/OS/_apis/wit/workitemtypes
    4. Tests area-path resolution for the expected path.
    5. Persists to provisioning\state\ado.json (HiddenAttribute set).

.PARAMETER Organization
  ADO org. Default 'microsoft'.

.PARAMETER Project
  ADO project. Default 'OS'.

.PARAMETER AreaPath
  Default area path. Backslash-escaped is fine; double-quote the value.

.PARAMETER NoBrowser
  Skip auto-opening the browser.

.EXAMPLE
  pwsh .\Setup-Ado.ps1
#>
param(
  [string]$Organization = 'microsoft',
  [string]$Project = 'OS',
  [string]$AreaPath = 'OS\Core\SPARC\SIX - Server, Intelligence, and Experiences\Enterprise Windows Admin Center',
  [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StateDir  = Join-Path $ScriptDir 'state'
$StatePath = Join-Path $StateDir 'ado.json'
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

Write-Host "=== Azure DevOps PAT setup ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Please create a PAT with these scopes:" -ForegroundColor Yellow
Write-Host "  - Work Items: Read, write, & manage"
Write-Host "  - Identity:   Read     (needed to look up area-path nodes)"
Write-Host "Recommended lifetime: 90 days. Tag it 'wac-feedback-bot'."
Write-Host ""
$tokenUrl = "https://dev.azure.com/$Organization/_usersSettings/tokens"
Write-Host ("PAT page: {0}" -f $tokenUrl)

if (-not $NoBrowser) {
  try { Start-Process $tokenUrl } catch { Write-Warning "Couldn't open browser: $_" }
}

Write-Host ""
$securePat = Read-Host -AsSecureString "Paste the PAT (input hidden)"
$plainPat = [System.Net.NetworkCredential]::new('', $securePat).Password
if (-not $plainPat) { Write-Error "Empty PAT."; exit 1 }

$basicAuth = [Convert]::ToBase64String([System.Text.Encoding]::ASCII.GetBytes(":${plainPat}"))
$headers = @{ Authorization = "Basic $basicAuth"; Accept = 'application/json' }

# 1. Validate by reading project
$projectUrl = "https://dev.azure.com/$Organization/_apis/projects/$Project`?api-version=7.0"
Write-Host ""
Write-Host "Validating against $projectUrl ..."
try {
  $proj = Invoke-RestMethod -Uri $projectUrl -Headers $headers -Method Get -TimeoutSec 30
  Write-Host ("  Project found: {0} (id={1})" -f $proj.name, $proj.id) -ForegroundColor Green
} catch {
  Write-Error "PAT validation failed at project read: $($_.Exception.Message)"
  exit 2
}

# 2. Validate work-item types Bug + Feature exist
$witUrl = "https://dev.azure.com/$Organization/$Project/_apis/wit/workitemtypes?api-version=7.0"
try {
  $wits = Invoke-RestMethod -Uri $witUrl -Headers $headers -Method Get -TimeoutSec 30
  $types = $wits.value | ForEach-Object { $_.name }
  $missing = @('Bug','Feature') | Where-Object { $types -notcontains $_ }
  if ($missing.Count -gt 0) {
    Write-Warning "Project doesn't expose: $($missing -join ', '). The agent will fall back to whatever Bug/Feature equivalent exists."
  } else {
    Write-Host "  Bug and Feature work item types confirmed." -ForegroundColor Green
  }
} catch {
  Write-Warning "Couldn't read work-item types: $($_.Exception.Message)"
}

# 3. Validate area path resolves
$areaSegments = $AreaPath -split '\\'
$areaTail = $areaSegments[1..($areaSegments.Count - 1)] -join '/'
$areaUrl = "https://dev.azure.com/$Organization/$Project/_apis/wit/classificationnodes/Areas/$areaTail`?api-version=7.0"
try {
  $area = Invoke-RestMethod -Uri $areaUrl -Headers $headers -Method Get -TimeoutSec 30
  Write-Host ("  Area path resolved: id={0}, name={1}" -f $area.id, $area.name) -ForegroundColor Green
} catch {
  Write-Warning "Area path '$AreaPath' did not resolve: $($_.Exception.Message)"
  Write-Warning "The agent will create work items under the project root area until corrected."
}

# 4. Persist (PAT in plain text — that's why we set hidden + restrict file ACL)
$cfg = [ordered]@{
  organization = $Organization
  project      = $Project
  area_path    = $AreaPath
  pat          = $plainPat
}
$cfg | ConvertTo-Json -Depth 3 | Set-Content -Path $StatePath -Encoding utf8

# Tighten ACL: only current user can read.
try {
  $acl = Get-Acl $StatePath
  $acl.SetAccessRuleProtection($true, $false)
  $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
            'Read,Write','Allow')
  $acl.AddAccessRule($rule)
  Set-Acl $StatePath $acl
} catch {
  Write-Warning "Couldn't tighten ACL on $StatePath`: $_"
}
attrib +H $StatePath 2>$null

Write-Host ""
Write-Host "ADO config saved to: $StatePath" -ForegroundColor Green
