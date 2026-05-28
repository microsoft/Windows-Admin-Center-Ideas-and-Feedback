#requires -Version 7.0
<#
.SYNOPSIS
  Creates (or reuses) an Azure OpenAI resource and a gpt-4o deployment for the
  wac-feedback-bot agent, then writes endpoint+key to state\aoai.json.

.DESCRIPTION
  Idempotent. Re-running with the same names is safe — existing resources are
  detected and reused.

  Requires you to be `az login`-ed and to have a subscription with AOAI access
  (most Microsoft FTE tenants do). If you don't have AOAI quota in your selected
  subscription, the script will fail clearly on the cognitive-services step.

.PARAMETER Subscription
  Azure subscription ID or name. If omitted, uses the currently-selected one.

.PARAMETER Location
  Azure region. Default 'eastus2' (broad gpt-4o availability).

.PARAMETER ResourceGroup
  Resource group name. Default 'wac-feedback-bot-rg'.

.PARAMETER AccountName
  Cognitive Services / AOAI account name (globally unique).
  Default '<RG>-<random>' to avoid collisions.

.PARAMETER DeploymentName
  Model deployment name. Default 'wac-triage-gpt'.

.PARAMETER Model
  Model id. Default 'gpt-5.1' (Azure OpenAI's current flagship with structured outputs).

.PARAMETER ModelVersion
  Model version. Default '2025-11-13'.

.PARAMETER Capacity
  Deployment capacity (TPM ÷ 1000). Default 50.

.PARAMETER WhatIf
  Print what would be done; create no resources.

.EXAMPLE
  pwsh .\Provision-AzureOpenAI.ps1 -Location eastus2
#>
param(
  [string]$Subscription = '',
  [string]$Location = 'eastus2',
  [string]$ResourceGroup = 'wac-feedback-bot-rg',
  [string]$AccountName = '',
  [string]$DeploymentName = 'wac-triage-gpt',
  [string]$Model = 'gpt-5.1',
  [string]$ModelVersion = '2025-11-13',
  [int]$Capacity = 50,
  [switch]$WhatIfMode
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StateDir  = Join-Path $ScriptDir 'state'
$StatePath = Join-Path $StateDir 'aoai.json'
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

function Invoke-Az {
  param([string[]]$AzArgs)
  Write-Host "  az $($AzArgs -join ' ')" -ForegroundColor DarkGray
  if ($WhatIfMode) { return $null }
  $out = az @AzArgs 2>&1
  if ($LASTEXITCODE -ne 0) { throw "az failed: $out" }
  return $out
}

Write-Host "=== Azure OpenAI provisioning ===" -ForegroundColor Cyan

# 1. Verify az login
$showJson = az account show 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Error "Not logged in. Run: az login"
  exit 1
}
$acct = $showJson | ConvertFrom-Json
Write-Host ("Active subscription: {0} ({1})" -f $acct.name, $acct.id)

# 2. Switch subscription if requested
if ($Subscription) {
  Write-Host "Switching to subscription: $Subscription"
  Invoke-Az -AzArgs @('account','set','--subscription',$Subscription) | Out-Null
  $acct = az account show | ConvertFrom-Json
}

# 3. Derive account name if not provided
if (-not $AccountName) {
  $suffix = -join ((48..57) + (97..122) | Get-Random -Count 5 | ForEach-Object {[char]$_})
  $AccountName = "wac-fb-aoai-$suffix"
}
Write-Host ("Resource group: {0}" -f $ResourceGroup)
Write-Host ("Location:       {0}" -f $Location)
Write-Host ("AOAI account:   {0}" -f $AccountName)
Write-Host ("Deployment:     {0} ({1} v{2}, capacity={3})" -f $DeploymentName, $Model, $ModelVersion, $Capacity)
Write-Host ""

# 4. Resource group
$rgExists = az group show -n $ResourceGroup 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host "Creating resource group..." -ForegroundColor Yellow
  Invoke-Az -AzArgs @('group','create','-n',$ResourceGroup,'-l',$Location) | Out-Null
} else {
  Write-Host "Resource group exists." -ForegroundColor Green
}

# 5. Cognitive Services account (kind=OpenAI)
$acctExists = az cognitiveservices account show -n $AccountName -g $ResourceGroup 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host "Creating Azure OpenAI account..." -ForegroundColor Yellow
  Invoke-Az -AzArgs @(
    'cognitiveservices','account','create',
    '-n',$AccountName,'-g',$ResourceGroup,'-l',$Location,
    '--kind','OpenAI','--sku','S0',
    '--custom-domain', $AccountName,
    '--yes'
  ) | Out-Null
} else {
  Write-Host "AOAI account exists." -ForegroundColor Green
}

# 6. Model deployment
$depExists = az cognitiveservices account deployment show `
  -n $AccountName -g $ResourceGroup --deployment-name $DeploymentName 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host "Creating model deployment..." -ForegroundColor Yellow
  Invoke-Az -AzArgs @(
    'cognitiveservices','account','deployment','create',
    '-n',$AccountName,'-g',$ResourceGroup,
    '--deployment-name',$DeploymentName,
    '--model-name',$Model,
    '--model-version',$ModelVersion,
    '--model-format','OpenAI',
    '--sku-capacity',$Capacity.ToString(),
    '--sku-name','Standard'
  ) | Out-Null
} else {
  Write-Host "Model deployment exists." -ForegroundColor Green
}

if ($WhatIfMode) {
  Write-Host "WhatIf mode — skipping endpoint/key capture." -ForegroundColor Yellow
  exit 0
}

# 7. Capture endpoint + key
$endpoint = (az cognitiveservices account show -n $AccountName -g $ResourceGroup --query properties.endpoint -o tsv).Trim()
$key = (az cognitiveservices account keys list -n $AccountName -g $ResourceGroup --query key1 -o tsv).Trim()

if (-not $endpoint -or -not $key) {
  throw "Failed to read endpoint or key for $AccountName"
}

# 8. Quick liveness probe — list deployments via the REST API
$probeUrl = "$endpoint" + "openai/deployments?api-version=2024-08-01-preview"
try {
  $resp = Invoke-RestMethod -Uri $probeUrl -Headers @{ 'api-key' = $key } -Method Get -TimeoutSec 30
  $models = ($resp.data | ForEach-Object { $_.id }) -join ', '
  Write-Host ("AOAI live probe OK. Visible deployments: {0}" -f $models) -ForegroundColor Green
} catch {
  Write-Warning "Live probe failed: $($_.Exception.Message). Endpoint and key still saved."
}

# 9. Persist
$cfg = [ordered]@{
  subscription   = $acct.id
  resource_group = $ResourceGroup
  account_name   = $AccountName
  endpoint       = $endpoint
  api_key        = $key
  deployment     = $DeploymentName
  model          = $Model
  model_version  = $ModelVersion
  api_version    = '2024-08-01-preview'
}
$cfg | ConvertTo-Json -Depth 4 | Set-Content -Path $StatePath -Encoding utf8
attrib +H $StatePath 2>$null

Write-Host ""
Write-Host "AOAI provisioning complete." -ForegroundColor Green
Write-Host ("Endpoint:   {0}" -f $endpoint)
Write-Host ("Deployment: {0}" -f $DeploymentName)
Write-Host ("Saved to:   {0}" -f $StatePath)
