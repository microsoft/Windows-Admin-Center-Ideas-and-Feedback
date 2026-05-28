<#
.SYNOPSIS
  Pre-flight check for the WAC Feedback Triage Agent.

.DESCRIPTION
  Verifies that everything required for step #5 (live test issue) is in place:
    - You're authenticated to GitHub (`gh auth status`)
    - You can see the target repo
    - All required secrets exist in the repo
    - The WAC_TRIAGE_MODE repository variable is set (and what it is)
    - The triage workflow file has been pushed to the default branch
    - The dedicated GitHub App is installed on the repo (best-effort)
    - The 'triage-test' label exists (needed for gated mode)

  Exits non-zero with a summary of what's missing.

.PARAMETER Repo
  owner/repo. Default: microsoft/Windows-Admin-Center-Ideas-and-Feedback

.PARAMETER AppSlug
  GitHub App slug used to identify the bot's installation. Default: wac-feedback-bot

.EXAMPLE
  pwsh ./preflight_check.ps1
  pwsh ./preflight_check.ps1 -Repo my-org/my-fork
#>

[CmdletBinding()]
param(
    [string]$Repo = "microsoft/Windows-Admin-Center-Ideas-and-Feedback",
    [string]$AppSlug = "wac-feedback-bot"
)

$ErrorActionPreference = 'Stop'
$problems = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]
$ok       = New-Object System.Collections.Generic.List[string]

function Write-Section($name) {
    Write-Host ""
    Write-Host "== $name ==" -ForegroundColor Cyan
}

function Test-GhAvailable {
    $cmd = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $cmd) {
        $problems.Add("GitHub CLI 'gh' is not installed or not on PATH. Install: https://cli.github.com/")
        return $false
    }
    return $true
}

# --- 1. gh CLI auth ----------------------------------------------------------
Write-Section "GitHub CLI auth"
if (-not (Test-GhAvailable)) {
    Write-Host "  Skipping further checks because 'gh' is missing." -ForegroundColor Yellow
} else {
    $authJson = gh auth status 2>&1
    if ($LASTEXITCODE -ne 0) {
        $problems.Add("gh auth status failed. Run: gh auth login")
        Write-Host "  $authJson" -ForegroundColor Yellow
    } else {
        $ok.Add("gh CLI is authenticated")
        Write-Host "  $authJson"
    }

    # --- 2. Repo visibility --------------------------------------------------
    Write-Section "Repo visibility"
    $repoInfo = gh repo view $Repo --json name,defaultBranchRef,visibility 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        $problems.Add("Cannot access repo $Repo. Confirm gh auth has access.")
        Write-Host "  $repoInfo" -ForegroundColor Yellow
    } else {
        $ok.Add("Repo $Repo is accessible")
        Write-Host "  $repoInfo"
    }

    # --- 3. Required secrets -------------------------------------------------
    Write-Section "Required secrets"
    $required = @(
        'WAC_BOT_APP_ID',
        'WAC_BOT_APP_PRIVATE_KEY',
        'AZURE_OPENAI_ENDPOINT',
        'AZURE_OPENAI_API_KEY',
        'AZURE_OPENAI_DEPLOYMENT',
        'ADO_PAT',
        'TEAMS_WEBHOOK_URL',
        'EMAIL_WEBHOOK_URL',
        'TEAM_DL_ADDRESS',
        'ADO_STATE_GIST_ID',
        'ADO_STATE_GIST_TOKEN'
    )
    $optional = @('AZURE_OPENAI_API_VERSION')

    $secretsRaw = gh secret list --repo $Repo --json name 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        $problems.Add("gh secret list failed for $Repo. You may lack admin permission on the repo.")
        Write-Host "  $secretsRaw" -ForegroundColor Yellow
    } else {
        try {
            $present = ($secretsRaw | ConvertFrom-Json).name
        } catch {
            $present = @()
        }
        foreach ($s in $required) {
            if ($present -contains $s) {
                $ok.Add("Secret present: $s")
                Write-Host "  [OK]   $s" -ForegroundColor Green
            } else {
                $problems.Add("Missing required secret: $s")
                Write-Host "  [MISS] $s" -ForegroundColor Red
            }
        }
        foreach ($s in $optional) {
            if ($present -contains $s) {
                Write-Host "  [OK]   $s (optional)" -ForegroundColor Green
            } else {
                Write-Host "  [--]   $s (optional, will use default)" -ForegroundColor DarkGray
            }
        }
    }

    # --- 4. Repository variables ---------------------------------------------
    Write-Section "Repository variables"
    $varsRaw = gh variable list --repo $Repo --json name,value 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        $warnings.Add("Cannot list repo variables (need admin). WAC_TRIAGE_MODE will default to 'live'.")
        Write-Host "  $varsRaw" -ForegroundColor Yellow
    } else {
        try {
            $vars = $varsRaw | ConvertFrom-Json
        } catch {
            $vars = @()
        }
        $modeVar = $vars | Where-Object { $_.name -eq 'WAC_TRIAGE_MODE' }
        if ($modeVar) {
            $ok.Add("WAC_TRIAGE_MODE = $($modeVar.value)")
            Write-Host "  [OK] WAC_TRIAGE_MODE = $($modeVar.value)" -ForegroundColor Green
            if ($modeVar.value -eq 'live') {
                $warnings.Add("WAC_TRIAGE_MODE is 'live'. For a first test consider setting it to 'gated' first: gh variable set WAC_TRIAGE_MODE -b 'gated' --repo $Repo")
            }
        } else {
            $warnings.Add("WAC_TRIAGE_MODE not set; will default to 'live'. Recommended for first test: gh variable set WAC_TRIAGE_MODE -b 'gated' --repo $Repo")
            Write-Host "  [--] WAC_TRIAGE_MODE not set (will default to 'live')" -ForegroundColor Yellow
        }

        $syncVar = $vars | Where-Object { $_.name -eq 'ADO_SYNC_ENABLED' }
        if ($syncVar) {
            Write-Host "  [OK] ADO_SYNC_ENABLED = $($syncVar.value)" -ForegroundColor Green
        } else {
            Write-Host "  [--] ADO_SYNC_ENABLED not set (defaults to true)" -ForegroundColor DarkGray
        }
    }

    # --- 5. Workflow file on default branch ----------------------------------
    Write-Section "Workflow file on default branch"
    $wf = gh api "repos/$Repo/contents/.github/workflows/triage-on-issue.yml" 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        $problems.Add(".github/workflows/triage-on-issue.yml not found on the default branch. Push the agent code first.")
        Write-Host "  [MISS] triage-on-issue.yml" -ForegroundColor Red
    } else {
        $ok.Add("triage-on-issue.yml exists on default branch")
        Write-Host "  [OK] triage-on-issue.yml" -ForegroundColor Green
    }

    # --- 6. GitHub App installation (best-effort) ----------------------------
    Write-Section "GitHub App installation"
    $installs = gh api "repos/$Repo/installation" 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        # The /installation endpoint requires the App's JWT — gh won't be able
        # to call it. Fallback: check that the app slug exists.
        $appInfo = gh api "apps/$AppSlug" 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            $warnings.Add("Could not verify GitHub App '$AppSlug'. Make sure the App exists and is installed on $Repo.")
            Write-Host "  [??] Could not verify '$AppSlug'. Verify manually:" -ForegroundColor Yellow
            Write-Host "       https://github.com/settings/apps  (or your org's app settings)" -ForegroundColor Yellow
            Write-Host "       https://github.com/$Repo/settings/installations" -ForegroundColor Yellow
        } else {
            Write-Host "  [OK] App '$AppSlug' exists on GitHub." -ForegroundColor Green
            Write-Host "       Confirm it's installed on $Repo at:" -ForegroundColor DarkGray
            Write-Host "       https://github.com/$Repo/settings/installations" -ForegroundColor DarkGray
        }
    } else {
        $ok.Add("A GitHub App is installed on $Repo")
        Write-Host "  [OK] Installation found." -ForegroundColor Green
    }

    # --- 7. triage-test label (required for gated mode) ----------------------
    Write-Section "'triage-test' label"
    $labelRaw = gh label list --repo $Repo --search "triage-test" --json name 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        $warnings.Add("Could not list labels on $Repo.")
        Write-Host "  $labelRaw" -ForegroundColor Yellow
    } else {
        try {
            $labels = $labelRaw | ConvertFrom-Json
        } catch {
            $labels = @()
        }
        $hasTriageTest = $labels | Where-Object { $_.name -eq 'triage-test' }
        if ($hasTriageTest) {
            $ok.Add("'triage-test' label exists")
            Write-Host "  [OK] 'triage-test' label exists" -ForegroundColor Green
        } else {
            $warnings.Add("'triage-test' label missing. Create with: gh label create triage-test -c '#FBCA04' -d 'Bot will triage in gated mode' --repo $Repo")
            Write-Host "  [--] 'triage-test' label missing (only needed for gated mode)" -ForegroundColor Yellow
        }

        # Also check the labels the agent applies
        $expected = @('triaged','ado-linked','triage-failed','no-triage','bug','feature','aMode','vMode')
        $missingLabels = @()
        foreach ($name in $expected) {
            $hit = gh label list --repo $Repo --search $name --json name 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue
            if (-not ($hit | Where-Object { $_.name -eq $name })) {
                $missingLabels += $name
            }
        }
        if ($missingLabels.Count -gt 0) {
            $warnings.Add("Missing labels (agent will warn but continue): $($missingLabels -join ', ')")
            Write-Host "  [--] Missing managed labels (non-fatal): $($missingLabels -join ', ')" -ForegroundColor Yellow
        } else {
            Write-Host "  [OK] All managed labels exist" -ForegroundColor Green
        }
    }
}

# --- Summary ----------------------------------------------------------------
Write-Section "Summary"
Write-Host "OK       : $($ok.Count)"      -ForegroundColor Green
Write-Host "Warnings : $($warnings.Count)" -ForegroundColor Yellow
Write-Host "Problems : $($problems.Count)" -ForegroundColor Red

if ($warnings.Count -gt 0) {
    Write-Host ""
    Write-Host "Warnings (you can usually still proceed):" -ForegroundColor Yellow
    $warnings | ForEach-Object { Write-Host "  * $_" -ForegroundColor Yellow }
}

if ($problems.Count -gt 0) {
    Write-Host ""
    Write-Host "Problems (must fix before running step #5):" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  * $_" -ForegroundColor Red }
    exit 1
}

Write-Host ""
Write-Host "Pre-flight OK. You're ready for the live test issue." -ForegroundColor Green
Write-Host "Next: open RUNBOOK.md and follow Step 5." -ForegroundColor Green
exit 0
