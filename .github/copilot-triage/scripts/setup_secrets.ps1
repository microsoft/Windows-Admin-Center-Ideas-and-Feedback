<#
.SYNOPSIS
  Pushes all wac-feedback-bot secrets to a GitHub repo using the gh CLI.

.DESCRIPTION
  Reads the App ID + private key from files created by create_github_app.py and
  prompts you for the remaining values (Azure OpenAI, ADO PAT, Teams webhook,
  email webhook, team DL, optional gist token).

  You can re-run this safely — gh secret set overwrites existing secrets.

.PARAMETER Repo
  The owner/repo to set secrets on (e.g. microsoft/Windows-Admin-Center-Ideas-and-Feedback).

.PARAMETER AppIdPath
  Path to app-id.txt produced by create_github_app.py. Default: ./out/app-id.txt

.PARAMETER PemPath
  Path to the .pem private key. Default: ./out/wac-feedback-bot.private-key.pem

.PARAMETER SkipPrompts
  If set, only the App ID + PEM are pushed; you set the other secrets yourself.

.EXAMPLE
  pwsh setup_secrets.ps1 -Repo microsoft/Windows-Admin-Center-Ideas-and-Feedback
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Repo,
    [string] $AppIdPath = "./out/app-id.txt",
    [string] $PemPath   = "./out/wac-feedback-bot.private-key.pem",
    [switch] $SkipPrompts
)

$ErrorActionPreference = 'Stop'

function Assert-Gh {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        Write-Error "gh CLI not found. Install from https://cli.github.com/ and run 'gh auth login'."
    }
    # Verify auth
    & gh auth status 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "gh is not authenticated. Run 'gh auth login' first."
    }
}

function Set-SecretFromValue {
    param([string] $Name, [string] $Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        Write-Host "  - $Name : (empty, skipped)" -ForegroundColor Yellow
        return
    }
    $Value | & gh secret set $Name --repo $Repo --body -
    if ($LASTEXITCODE -ne 0) { Write-Error "Failed to set secret $Name" }
    Write-Host "  - $Name : set" -ForegroundColor Green
}

function Set-SecretFromFile {
    param([string] $Name, [string] $Path)
    if (-not (Test-Path $Path)) {
        Write-Warning "Expected file $Path not found; skipping $Name."
        return
    }
    $content = Get-Content $Path -Raw
    Set-SecretFromValue -Name $Name -Value $content.TrimEnd()
}

function Read-Plain {
    param([string] $Prompt, [string] $Default = "")
    if ($Default) {
        $v = Read-Host "$Prompt [$Default]"
        if (-not $v) { return $Default }
        return $v
    }
    return (Read-Host $Prompt)
}

function Read-Secure {
    param([string] $Prompt)
    $secure = Read-Host -AsSecureString $Prompt
    $ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

Assert-Gh

Write-Host "Pushing secrets to $Repo ..." -ForegroundColor Cyan
Write-Host ""

# 1. From files
Set-SecretFromFile -Name "WAC_BOT_APP_ID"          -Path $AppIdPath
Set-SecretFromFile -Name "WAC_BOT_APP_PRIVATE_KEY" -Path $PemPath

if ($SkipPrompts) {
    Write-Host "`nSkipPrompts set — finished after file-based secrets." -ForegroundColor Cyan
    return
}

Write-Host "`nEnter remaining secrets (press Enter to skip any one):" -ForegroundColor Cyan

$aoaiEndpoint = Read-Plain  "Azure OpenAI endpoint (https://<resource>.openai.azure.com)"
Set-SecretFromValue -Name "AZURE_OPENAI_ENDPOINT" -Value $aoaiEndpoint

$aoaiKey = Read-Secure "Azure OpenAI API key"
Set-SecretFromValue -Name "AZURE_OPENAI_API_KEY" -Value $aoaiKey

$aoaiDeployment = Read-Plain "Azure OpenAI deployment name" "wac-triage-gpt4o"
Set-SecretFromValue -Name "AZURE_OPENAI_DEPLOYMENT" -Value $aoaiDeployment

$adoPat = Read-Secure "ADO PAT (Work Items R/W on microsoft/OS)"
Set-SecretFromValue -Name "ADO_PAT" -Value $adoPat

$teamsUrl = Read-Plain "Microsoft Teams incoming webhook URL"
Set-SecretFromValue -Name "TEAMS_WEBHOOK_URL" -Value $teamsUrl

$emailUrl = Read-Plain "Email webhook URL (Logic App / Power Automate HTTP trigger)"
Set-SecretFromValue -Name "EMAIL_WEBHOOK_URL" -Value $emailUrl

$teamDl = Read-Plain "Team distribution-list email address"
Set-SecretFromValue -Name "TEAM_DL_ADDRESS" -Value $teamDl

$gistId = Read-Plain "Secret gist ID for ADO state cache (Enter to skip — sync workflow will be disabled)"
Set-SecretFromValue -Name "ADO_STATE_GIST_ID" -Value $gistId

if ($gistId) {
    $gistToken = Read-Secure "Fine-grained PAT with Gists R/W (for ADO state cache)"
    Set-SecretFromValue -Name "ADO_STATE_GIST_TOKEN" -Value $gistToken
}

Write-Host ""
Write-Host "All done. Review at https://github.com/$Repo/settings/secrets/actions" -ForegroundColor Cyan
Write-Host "Reminder: delete the local .pem file once you have verified the secrets work." -ForegroundColor Yellow
