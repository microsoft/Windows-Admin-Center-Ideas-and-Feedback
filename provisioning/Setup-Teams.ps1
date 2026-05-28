#requires -Version 7.0
<#
.SYNOPSIS
  Captures and validates a Teams Incoming Webhook URL for triage notifications.

.DESCRIPTION
  Teams Incoming Webhooks must be added by the user in the Teams client:
    Channel name → ... menu → Manage channel → Connectors → Configure
    "Incoming Webhook" → name = 'WAC Feedback Triage' → Create → copy URL.

  Microsoft has been migrating from O365 Connectors → Workflows. Both are
  supported here as long as the resulting URL accepts a JSON POST that returns
  HTTP 200/202.

  This script:
    1. Opens the MS Learn doc for adding an Incoming Webhook.
    2. Prompts for the URL.
    3. Sends a small Adaptive Card test message to confirm the URL works.
    4. Persists to provisioning\state\teams.json.

.PARAMETER NoBrowser
  Skip auto-opening the Learn doc.

.PARAMETER WebhookUrl
  Optional pre-filled URL (otherwise prompted).

.PARAMETER SkipTest
  Skip sending the test card.
#>
param(
  [switch]$NoBrowser,
  [string]$WebhookUrl = '',
  [switch]$SkipTest
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StateDir  = Join-Path $ScriptDir 'state'
$StatePath = Join-Path $StateDir 'teams.json'
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

Write-Host "=== Teams Incoming Webhook setup ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Set up in your Teams channel (one-time):" -ForegroundColor Yellow
Write-Host "  1. Open the channel where you want triage notifications."
Write-Host "  2. Channel ... menu  ->  Workflows  ->  'Post to a channel when a webhook request is received'"
Write-Host "     (or  Manage channel -> Connectors -> Incoming Webhook  if your tenant still has those)"
Write-Host "  3. Name it 'WAC Feedback Triage' and click Create."
Write-Host "  4. Copy the URL it gives you."
Write-Host ""

$docUrl = 'https://learn.microsoft.com/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook'
if (-not $NoBrowser) {
  try { Start-Process $docUrl } catch { Write-Warning "Couldn't open browser: $_" }
}

if (-not $WebhookUrl) {
  $WebhookUrl = Read-Host "Paste the webhook URL"
}
if (-not $WebhookUrl) { Write-Error "Empty URL."; exit 1 }
if ($WebhookUrl -notmatch '^https://') {
  Write-Error "URL must start with https://"
  exit 1
}

# Test card
if (-not $SkipTest) {
  $card = @{
    type        = 'message'
    attachments = @(
      @{
        contentType = 'application/vnd.microsoft.card.adaptive'
        content     = @{
          '$schema' = 'http://adaptivecards.io/schemas/adaptive-card.json'
          type      = 'AdaptiveCard'
          version   = '1.4'
          body      = @(
            @{ type='TextBlock'; text='WAC Feedback Triage — webhook test'; weight='Bolder'; size='Medium' }
            @{ type='TextBlock'; text='If you can see this card, the webhook URL is working.'; wrap=$true }
            @{ type='TextBlock'; text=("Posted at {0:yyyy-MM-ddTHH:mm:ssZ}" -f (Get-Date).ToUniversalTime()); isSubtle=$true; size='Small' }
          )
        }
      }
    )
  } | ConvertTo-Json -Depth 8 -Compress

  Write-Host "Sending test card..."
  try {
    $r = Invoke-WebRequest -Uri $WebhookUrl -Method Post -Body $card `
           -ContentType 'application/json' -TimeoutSec 30 -SkipHttpErrorCheck
    if ($r.StatusCode -in 200,202) {
      Write-Host ("Test card accepted (HTTP {0})." -f $r.StatusCode) -ForegroundColor Green
    } else {
      Write-Warning ("Webhook returned HTTP {0}: {1}" -f $r.StatusCode, $r.Content)
    }
  } catch {
    Write-Warning "Test post failed: $($_.Exception.Message)"
    Write-Warning "If the URL was copied with extra whitespace or a fragment, please re-check it."
  }
}

# Persist
$cfg = [ordered]@{
  webhook_url = $WebhookUrl
  configured  = (Get-Date).ToUniversalTime().ToString('o')
}
$cfg | ConvertTo-Json | Set-Content -Path $StatePath -Encoding utf8
attrib +H $StatePath 2>$null

Write-Host "Teams webhook saved to: $StatePath" -ForegroundColor Green
