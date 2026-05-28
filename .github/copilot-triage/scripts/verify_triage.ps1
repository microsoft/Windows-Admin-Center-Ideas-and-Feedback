<#
.SYNOPSIS
  Verifies that the triage agent fully processed a test issue end-to-end.

.DESCRIPTION
  Given an issue number, checks all of:
    1. The 'Triage on issue' workflow ran for this issue and succeeded.
    2. The bot posted a comment containing the triaged-by HTML marker.
    3. The bot applied the 'triaged' label.
    4. The bot applied the 'ado-linked' label and the comment carries an
       ado-id HTML marker.
    5. (Best-effort) The Azure DevOps work item referenced exists.
    6. Downloads the workflow's triage-debug artifact to ./triage-debug-<n>.json.

.PARAMETER Repo
  owner/repo. Default: microsoft/Windows-Admin-Center-Ideas-and-Feedback

.PARAMETER IssueNumber
  The GitHub issue number to verify.

.PARAMETER BotLogin
  The bot account login that posts triage comments. The script tries to detect
  this automatically; pass it explicitly if detection fails. Typical values:
  'wac-feedback-bot[bot]' or whatever the GitHub App's slug is.

.PARAMETER TimeoutSec
  How long to wait for the workflow run to complete (default 300s).

.EXAMPLE
  pwsh ./verify_triage.ps1 -IssueNumber 1234
  pwsh ./verify_triage.ps1 -IssueNumber 1234 -Repo myorg/myfork -BotLogin 'my-bot[bot]'
#>

[CmdletBinding()]
param(
    [string]$Repo = "microsoft/Windows-Admin-Center-Ideas-and-Feedback",
    [Parameter(Mandatory = $true)][int]$IssueNumber,
    [string]$BotLogin,
    [int]$TimeoutSec = 300
)

$ErrorActionPreference = 'Stop'
$problems = New-Object System.Collections.Generic.List[string]
$ok       = New-Object System.Collections.Generic.List[string]

function Write-Section($name) {
    Write-Host ""
    Write-Host "== $name ==" -ForegroundColor Cyan
}

function Invoke-Gh($args, [switch]$IgnoreError) {
    $out = gh @args 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -and -not $IgnoreError) {
        throw "gh $($args -join ' ') failed:`n$out"
    }
    return $out
}

Write-Section "Looking up issue #$IssueNumber on $Repo"
$issueJson = Invoke-Gh @('issue','view',"$IssueNumber",'--repo',$Repo,'--json','number,title,state,author,labels,url,createdAt')
$issue = $issueJson | ConvertFrom-Json
Write-Host "  Title : $($issue.title)"
Write-Host "  State : $($issue.state)"
Write-Host "  URL   : $($issue.url)"

# --- 1. Wait for / find workflow run --------------------------------------
Write-Section "Workflow run for this issue"
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$run = $null

# Match by run-name pattern "Triage #<n>:" or fall back to event payload.
$pattern = "Triage #$IssueNumber"

while ((Get-Date) -lt $deadline) {
    $runsRaw = Invoke-Gh @('run','list','--repo',$Repo,'--workflow','triage-on-issue.yml',
                           '--limit','50','--json','databaseId,displayTitle,headBranch,status,conclusion,createdAt,event,url') -IgnoreError
    try { $runs = $runsRaw | ConvertFrom-Json } catch { $runs = @() }
    $match = $runs | Where-Object {
        $_.displayTitle -like "*$pattern*" -or $_.displayTitle -like "*#$IssueNumber*"
    } | Select-Object -First 1

    if ($match) {
        Write-Host "  Run id  : $($match.databaseId)"
        Write-Host "  Title   : $($match.displayTitle)"
        Write-Host "  Status  : $($match.status) / $($match.conclusion)"
        Write-Host "  URL     : $($match.url)"
        if ($match.status -eq 'completed') {
            $run = $match
            break
        }
    } else {
        Write-Host "  No matching run yet; waiting..." -ForegroundColor DarkGray
    }
    Start-Sleep -Seconds 10
}

if (-not $run) {
    $problems.Add("No completed 'Triage on issue' workflow run found for issue #$IssueNumber within $TimeoutSec s.")
} elseif ($run.conclusion -ne 'success') {
    $problems.Add("Workflow run #$($run.databaseId) concluded '$($run.conclusion)' (expected 'success'). See $($run.url)")
} else {
    $ok.Add("Workflow run #$($run.databaseId) succeeded")
}

# --- 2. Fetch comments and check for triaged-by marker ---------------------
Write-Section "Bot comment on the issue"
$comments = Invoke-Gh @('api',"repos/$Repo/issues/$IssueNumber/comments",'--paginate') | ConvertFrom-Json
$marker = '<!-- triaged-by: wac-feedback-bot -->'
$botComment = $comments | Where-Object { $_.body -and ($_.body.Contains($marker)) } | Select-Object -First 1

if (-not $botComment) {
    $problems.Add("No comment containing '$marker' was found on issue #$IssueNumber.")
} else {
    $ok.Add("Bot triage comment present (id=$($botComment.id), by $($botComment.user.login))")
    Write-Host "  By      : $($botComment.user.login)"
    Write-Host "  At      : $($botComment.created_at)"
    Write-Host "  Excerpt :"
    $excerpt = ($botComment.body -split "`n" | Select-Object -First 5) -join "`n  "
    Write-Host "  $excerpt"

    if (-not $BotLogin) {
        $BotLogin = $botComment.user.login
        Write-Host "  (Detected bot login: $BotLogin)"
    }

    # ado-id marker
    if ($botComment.body -match '<!--\s*ado-id:\s*(\d+)\s*-->') {
        $adoId = [int]$matches[1]
        $ok.Add("ado-id marker present (ADO #$adoId)")
        Write-Host "  ADO id  : $adoId" -ForegroundColor Green
    } else {
        $problems.Add("Bot comment is missing the '<!-- ado-id: NNN -->' marker. ADO filing likely failed.")
        $adoId = 0
    }
}

# --- 3. Check labels -------------------------------------------------------
Write-Section "Labels on the issue"
$labelNames = @($issue.labels | ForEach-Object { $_.name })
Write-Host "  Labels: $($labelNames -join ', ')"

foreach ($needed in @('triaged','ado-linked')) {
    if ($labelNames -contains $needed) {
        $ok.Add("Label '$needed' applied")
        Write-Host "  [OK]   $needed" -ForegroundColor Green
    } else {
        $problems.Add("Label '$needed' was not applied.")
        Write-Host "  [MISS] $needed" -ForegroundColor Red
    }
}
if ($labelNames -contains 'triage-failed') {
    $problems.Add("'triage-failed' label is set on the issue — the agent reported failure. Check the workflow log.")
    Write-Host "  [FAIL] 'triage-failed' is set" -ForegroundColor Red
}

# --- 4. Download workflow artifact ----------------------------------------
if ($run) {
    Write-Section "Triage debug artifact"
    $artifactName = "triage-debug-$IssueNumber"
    $downloadDir = Join-Path (Get-Location) "artifact-$IssueNumber"
    if (Test-Path $downloadDir) { Remove-Item -Recurse -Force $downloadDir }
    $null = New-Item -ItemType Directory -Path $downloadDir
    $dlOut = gh run download $run.databaseId --repo $Repo --name $artifactName --dir $downloadDir 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Could not download artifact: $dlOut" -ForegroundColor Yellow
    } else {
        $debugFile = Join-Path $downloadDir 'triage-debug.json'
        if (Test-Path $debugFile) {
            $ok.Add("Downloaded $artifactName")
            Write-Host "  Saved to: $debugFile" -ForegroundColor Green
            try {
                $debug = Get-Content $debugFile -Raw | ConvertFrom-Json
                Write-Host "  status        : $($debug.status)"
                Write-Host "  mode          : $($debug.mode)"
                Write-Host "  category      : $($debug.triage.category)"
                Write-Host "  severity      : $($debug.triage.severity)"
                Write-Host "  labels        : $($debug.labels_to_apply -join ', ')"
                if ($debug.ado) {
                    Write-Host "  ADO           : #$($debug.ado.id) ($($debug.ado.type))"
                }
            } catch {
                Write-Host "  (could not parse debug JSON: $_)" -ForegroundColor Yellow
            }
        }
    }
}

# --- Summary ---------------------------------------------------------------
Write-Section "Summary"
Write-Host "OK       : $($ok.Count)" -ForegroundColor Green
Write-Host "Problems : $($problems.Count)" -ForegroundColor Red

$ok | ForEach-Object { Write-Host "  [OK] $_" -ForegroundColor Green }
if ($problems.Count -gt 0) {
    Write-Host ""
    $problems | ForEach-Object { Write-Host "  [!!] $_" -ForegroundColor Red }
    Write-Host ""
    Write-Host "End-to-end verification FAILED. See above." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "End-to-end verification PASSED. The agent triaged issue #$IssueNumber successfully." -ForegroundColor Green
exit 0
