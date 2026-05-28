# RUNBOOK — Step 5: Live end-to-end test of the WAC Feedback Triage Agent

This is the *one document* you need when you come back to the keyboard.
Everything in the repo is staged. This walks you through the final 5%.

> **Repo:** `microsoft/Windows-Admin-Center-Ideas-and-Feedback`
> **Goal of step 5:** open a real (test) issue, watch the agent triage it,
> verify every output, then flip from safe-test mode to live.
>
> Time budget: ~20–30 minutes including the one-time provisioning in §1.

---

## TL;DR — Cheat sheet

```powershell
# 0. cd into the agent folder
cd .github\copilot-triage

# 1. One-time provisioning (only if you haven't done it before)
#    Fast path: pwsh ..\..\provisioning\Provision-All.ps1
#    Manual path: see section 1 below.

# 2. Pre-flight (verifies everything is wired up)
pwsh .\scripts\preflight_check.ps1

# 3. Switch the agent into 'gated' mode (only triages issues
#    labeled 'triage-test' — perfect for a first live test)
gh variable set WAC_TRIAGE_MODE -b "gated" `
  --repo microsoft/Windows-Admin-Center-Ideas-and-Feedback
gh label create triage-test -c FBCA04 `
  -d "Bot will triage this in gated mode" `
  --repo microsoft/Windows-Admin-Center-Ideas-and-Feedback

# 4. File the test issue (copy/paste the body from §3)
#    Make sure to add the 'triage-test' label!

# 5. Verify (waits up to 5 min for the workflow)
pwsh .\scripts\verify_triage.ps1 -IssueNumber <N>

# 6. Flip to live
gh variable set WAC_TRIAGE_MODE -b "live" `
  --repo microsoft/Windows-Admin-Center-Ideas-and-Feedback
```

---

## 1. One-time provisioning (skip if already done)

> **The fast path:** the `provisioning/` folder at the repo root contains a
> set of PowerShell scripts that do every step below with a single command.
> Each script is idempotent and writes its results to
> `provisioning/state/*.json`, which `Push-Secrets.ps1` then uploads as repo
> secrets. The manual steps 1a–1e below remain as the fallback path / for
> understanding what's happening under the hood.
>
> ```powershell
> cd ..\..\provisioning
> pwsh .\Provision-All.ps1 -TeamDL wac-feedback@microsoft.com
> # …answer the prompts (browser opens for GitHub App + Teams, az login for AOAI,
> #  ADO PAT prompt). At the end your repo has every secret + variable set.
> ```
>
> See `provisioning/README.md` for individual script usage and the exact
> secret/variable map.

These are the things only a human with admin access to the repo +
Azure + ADO can do.  Each step is independent.

### 1a. Create the GitHub App `wac-feedback-bot`

```powershell
cd .github\copilot-triage
python .\scripts\create_github_app.py
```

This launches a localhost flow that creates the App via the
**manifest** flow. Follow the browser prompts and accept the
defaults. When it finishes, the script writes `.app-creds.json`
to your current directory containing:

- `app_id`
- `pem` (the private key)
- `webhook_secret`

After the App exists:

1. Install it on `microsoft/Windows-Admin-Center-Ideas-and-Feedback`
   (the script prints the install URL).
2. The App needs these permissions (the manifest already requests them,
   but double-check after install): **Issues: read & write**,
   **Metadata: read**, **Contents: read**.

> If your org blocks manifest-flow App creation, fall back to creating
> it manually at `https://github.com/settings/apps/new` with those same
> three permissions, then put the App ID and private key into
> `.app-creds.json` yourself.

### 1b. Provision Azure OpenAI

You need:
- an Azure OpenAI resource,
- a deployment of a structured-output-capable model (e.g. `gpt-4o-2024-08-06`
  or newer; `gpt-4o-mini` works too).

Capture:
- `AZURE_OPENAI_ENDPOINT` (e.g. `https://my-aoai.openai.azure.com/`)
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_DEPLOYMENT` (the deployment name, not the model name)
- optionally `AZURE_OPENAI_API_VERSION` (defaults to a recent stable value)

### 1c. Create the ADO PAT

In the `microsoft` ADO org, create a PAT with **Work Items (Read & write)**
scoped to the `OS` project. Save it; it is `ADO_PAT`.

### 1d. Create the Teams Incoming Webhook + email

- **Teams**: in the channel that should receive triage notifications,
  add the **Incoming Webhook** connector. Save the URL → `TEAMS_WEBHOOK_URL`.
- **Email**: either an Azure Logic App `When a HTTP request is received`
  trigger that sends mail, or your favorite SMTP-to-HTTP shim. Save
  the URL → `EMAIL_WEBHOOK_URL`. The distribution list address goes in
  `TEAM_DL_ADDRESS`.

### 1e. Create the ADO-state gist

This stores the last-known ADO state per linked issue so the sync
workflow knows when to ping the customer.

```powershell
# Create an empty private gist (one file) — you'll see its ID in the URL
gh gist create --desc "WAC triage ADO state" --public=false - <<< "{}"
# Copy the gist ID → ADO_STATE_GIST_ID
# Create a fine-grained PAT with 'gist' write scope → ADO_STATE_GIST_TOKEN
```

### 1f. Push every secret to the repo

The helper script wraps all of this:

```powershell
pwsh .\scripts\setup_secrets.ps1 `
  -Repo microsoft/Windows-Admin-Center-Ideas-and-Feedback
```

It will prompt for each value, then `gh secret set` it for you.
Alternatively, set them by hand:

```powershell
gh secret set WAC_BOT_APP_ID         --repo $repo
gh secret set WAC_BOT_APP_PRIVATE_KEY --repo $repo < path\to\private-key.pem
gh secret set AZURE_OPENAI_ENDPOINT  --repo $repo
gh secret set AZURE_OPENAI_API_KEY   --repo $repo
gh secret set AZURE_OPENAI_DEPLOYMENT --repo $repo
gh secret set ADO_PAT                --repo $repo
gh secret set TEAMS_WEBHOOK_URL      --repo $repo
gh secret set EMAIL_WEBHOOK_URL      --repo $repo
gh secret set TEAM_DL_ADDRESS        --repo $repo
gh secret set ADO_STATE_GIST_ID      --repo $repo
gh secret set ADO_STATE_GIST_TOKEN   --repo $repo
```

### 1g. Push the agent code to the repo

From the project root (`C:\Users\trungtran\wac-feedback-triage`):

```powershell
git init -b main
git remote add origin https://github.com/microsoft/Windows-Admin-Center-Ideas-and-Feedback.git
git fetch origin
git checkout -b feature/wac-feedback-triage origin/main
git add .
git commit -m "WAC feedback triage agent: scaffolding, agent, workflows, runbook"
git push -u origin feature/wac-feedback-triage
gh pr create --repo microsoft/Windows-Admin-Center-Ideas-and-Feedback `
  --title "WAC feedback triage agent" `
  --body "See .github/copilot-triage/README.md and RUNBOOK.md"
# Merge the PR, then move on to step 2.
```

> Tip: while you're staging this for the *first* time, consider pushing
> the workflows with `WAC_TRIAGE_MODE` already set to `shadow` on the
> repo (see §3). That way even if the workflow fires unintentionally,
> it won't post comments or file ADO items — it just writes the debug
> artifact for you to inspect.

---

## 2. Pre-flight check

Once everything in §1 is done:

```powershell
cd .github\copilot-triage
pwsh .\scripts\preflight_check.ps1
```

This verifies, in order:

1. `gh` is installed and authenticated.
2. You can read the repo.
3. All 11 required secrets are present.
4. The `WAC_TRIAGE_MODE` repo variable (if you set it).
5. `.github/workflows/triage-on-issue.yml` is on the default branch.
6. The `wac-feedback-bot` App is installed on the repo (best-effort).
7. The `triage-test` label exists (needed for gated mode).
8. The other labels the agent applies (`triaged`, `ado-linked`, etc.)
   — non-fatal warnings if missing.

Exit code 0 means you're good. **Do not skip this step.**

---

## 3. The actual live test issue

### 3a. Switch to safe-test mode

```powershell
$repo = "microsoft/Windows-Admin-Center-Ideas-and-Feedback"

# Only triage issues labeled 'triage-test'. This is the safest
# possible posture for a first live run.
gh variable set WAC_TRIAGE_MODE -b "gated" --repo $repo

# Create the gate label if preflight said it was missing
gh label create triage-test -c "FBCA04" `
  -d "Bot will triage this in gated mode" `
  --repo $repo

# Helpful labels the agent applies; create any missing ones
$labels = @{
  'triaged'        = '0E8A16'
  'ado-linked'     = 'C5DEF5'
  'triage-failed'  = 'D93F0B'
  'no-triage'      = '6F6F6F'
  'bug'            = 'D73A4A'
  'feature'        = 'A2EEEF'
  'aMode'          = 'BFD4F2'
  'vMode'          = 'D4C5F9'
}
foreach ($name in $labels.Keys) {
  gh label create $name -c $labels[$name] --repo $repo 2>$null
}
```

### 3b. File the test issue

Open https://github.com/microsoft/Windows-Admin-Center-Ideas-and-Feedback/issues/new

- **Title:** `[TEST — please ignore] WAC gateway installer crashes at Configure HTTPS step on Server 2022 Core`
- **Labels:** add `triage-test`
- **Body:** (copy from `tests/sample_issue.json` -> `body`, or use the
  block below):

```text
**Describe the bug**
The Windows Admin Center gateway installer (Setup MSI 2.0.x) crashes during the
"Configure HTTPS" step on a fresh Windows Server 2022 Server Core (no GUI) box.
The installer rolls back. After the rollback I have no WAC service registered
and port 6516 is not listening.

**Repro**
1. Fresh Server 2022 Core, fully patched.
2. Download the WAC 2.0.x MSI.
3. `msiexec /i WindowsAdminCenter.msi /qb` (also tried `/qn`).
4. Watch it advance through Files → Services → Configure HTTPS → ROLLBACK.

**Expected**
Install completes, gateway running on 443.

**Actual**
Installer rolls back. Event log has a "Configure HTTPS endpoint" failure but
the install log under %TEMP% doesn't surface a clear inner exception.

**Environment**
- WAC: 2.0.x (latest)
- Host: Server 2022 Core, fully patched, joined to AD
- Account: domain admin
- Browser used to reach this site: Edge 122
```

Click **Submit**. Make sure the `triage-test` label is set before you
hit submit (or add it within the first few seconds — the workflow has
a small grace period via the `edited` trigger).

### 3c. Watch the workflow

```powershell
gh run watch --repo $repo
# Or: open https://github.com/<repo>/actions/workflows/triage-on-issue.yml
```

You should see a run named **"Triage #<N>: [TEST — please ignore] …"**.
It should finish in ~30–90s.

---

## 4. Verify the agent did everything right

```powershell
pwsh .\scripts\verify_triage.ps1 -IssueNumber <N>
```

The script will:

1. Wait for the latest "Triage #N" workflow run to complete and check
   it succeeded.
2. Look for a comment on the issue containing
   `<!-- triaged-by: wac-feedback-bot -->`.
3. Pull the `<!-- ado-id: NNN -->` value out of that comment.
4. Confirm the labels `triaged` and `ado-linked` are applied (and that
   `triage-failed` is **not**).
5. Download `triage-debug-<N>.json` to `./artifact-<N>/` and print the
   classification.

If anything is wrong it exits non-zero with a precise diagnosis.

### Manual things to also eyeball

- The bot's comment reads naturally and is grounded in the body
  (it should ask for the installer log under `%TEMP%`).
- The Teams channel got a card (or, in gated mode, you may not have
  enabled this yet — that's fine).
- The ADO work item exists under the configured Area Path and links
  back to the GitHub issue.

If you want to clean up the test:
```powershell
gh issue close <N> --repo $repo --reason "not planned" --comment "Test issue — closing."
# Optionally also delete the ADO work item from the ADO UI.
```

---

## 5. Flip from gated -> live

Only do this once §3 + §4 have passed end-to-end:

```powershell
gh variable set WAC_TRIAGE_MODE -b "live" --repo $repo
```

From here on every `issues.opened` and `issues.edited` event will trigger
the full agent. The 24h re-triage throttle on edits prevents reply storms.

---

## Rollback / kill switch

If something goes wrong after going live, any of the following stops it:

```powershell
# Hardest stop: disable the workflow
gh workflow disable triage-on-issue.yml --repo $repo

# Softer stop: switch to shadow mode (still calls LLM, no writes)
gh variable set WAC_TRIAGE_MODE -b "shadow" --repo $repo

# Softest stop: switch back to gated mode (only opt-in issues)
gh variable set WAC_TRIAGE_MODE -b "gated"  --repo $repo

# Stop the agent on a single issue:
gh issue edit <N> --repo $repo --add-label "no-triage"
```

---

## What's in this repo

Quick map for future maintainers:

```
.github/
├── copilot-triage/
│   ├── agent.py              # entry point for triage-on-issue workflow
│   ├── ado_sync.py           # entry point for ado-sync workflow
│   ├── digest.py             # entry point for weekly-digest workflow
│   ├── clients/              # AOAI, GitHub App, ADO, notifier, gist
│   ├── knowledge/            # pluggable retriever (no-op default)
│   ├── prompts/              # system prompt + reply skeleton
│   ├── schema/triage_output.json
│   ├── templates/            # Jinja templates: Teams card, email, etc.
│   ├── scripts/
│   │   ├── create_github_app.py   # one-time App provisioning
│   │   ├── setup_secrets.ps1      # one-time secret push
│   │   ├── preflight_check.ps1    # use before step 5
│   │   └── verify_triage.ps1      # use after step 5
│   ├── tests/                # pytest (smoke + mocked e2e)
│   ├── README.md
│   ├── SETUP.md              # detailed first-time setup
│   └── RUNBOOK.md            # <-- this file
└── workflows/
    ├── triage-on-issue.yml   # main triage workflow
    ├── ado-sync.yml          # every 30 min, ADO -> GitHub
    └── weekly-digest.yml     # Monday 09:00 UTC
```

---

## FAQ

**Q. The workflow says "skipped" — what does that mean?**
Look at the `triage-debug-<N>.json` artifact. It will tell you exactly
which guard fired: `author is a bot`, `no-triage label present`,
`already triaged within 24h`, or `gated mode requires 'triage-test' label`.

**Q. The LLM picked the wrong category.**
The prompt is at `.github/copilot-triage/prompts/system.md`. Tweak,
push to a branch, run the agent in `shadow` mode against a few real
issues, then re-merge.

**Q. The bot can't comment ("Resource not accessible by integration").**
The GitHub App is installed but probably without **Issues: write**.
Re-check at `https://github.com/<repo>/settings/installations`.

**Q. ADO filing fails with 401.**
PAT expired or missing **Work Items (R&W)** scope on the OS project.

**Q. The customer reply contains placeholder text like "ADO #0".**
The agent ran in `shadow` mode (no ADO item was created so the ID is 0).
That `#0` reply is **rendered in the artifact only**, never posted.
