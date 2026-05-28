# Local demo runner — start/stop the WAC triage bot on your laptop

This is the **demo-only** path that needs none of the cloud provisioning
(no Azure OpenAI, no GitHub App, no ADO PAT, no Teams webhook). It runs
the same `agent.py` you'd run in CI, but:

- the LLM call is replaced by a **deterministic heuristic mock**
  (`clients/mock_llm.py`),
- nothing is posted to GitHub / ADO / Teams (always `--mode shadow`),
- the triage result is rendered as an HTML email and **sent to your inbox
  via your installed Outlook** (no creds needed — uses your existing
  session).

You get to see exactly what the bot would do, then start/stop it at will.

---

## Prerequisites

- **Python venv** (one-time setup below) with `pywin32` + `jinja2`.
- **GitHub CLI** (`gh`) installed and authenticated **if** you want
  `-IssueNumber` or `-RecentCount` to pull from the live repo. Install
  from <https://cli.github.com/> and run `gh auth login`. (The offline
  `-FromFile` path doesn't need `gh`.)
- **Outlook** installed and signed in to send email. If it isn't, the
  runner falls back to writing HTML to `local/outbox/` automatically.

## One-time setup (≈2 minutes)

```powershell
cd C:\Users\trungtran\wac-feedback-triage

# Create the agent's venv if you haven't
cd .github\copilot-triage
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r ..\..\local\requirements.txt
```

`local/requirements.txt` adds `pywin32` for the Outlook COM bridge.

> If `pywin32` isn't installed, the runner silently falls back to writing
> the email HTML into `local/outbox/` so the demo still works.

---

## Try it (manual tests)

### Test 1 — Use the bundled sample (offline, no GitHub at all)

```powershell
cd C:\Users\trungtran\wac-feedback-triage\local
pwsh .\Invoke-WacTriageOnce.ps1 `
  -FromFile ..\.github\copilot-triage\tests\sample_issue.json
```

You'll receive an email titled
`[WAC triage demo] BUG sev=high — #999001 …` showing the bot's classification,
the drafted customer reply, and the would-be ADO title.

### Test 2 — Triage the 3 most recent open issues from the live repo

```powershell
pwsh .\Invoke-WacTriageOnce.ps1 -RecentCount 3
```

This uses your `gh` CLI auth to pull from
`microsoft/Windows-Admin-Center-Ideas-and-Feedback`. You'll get one email per
issue.

### Test 3 — Triage a specific issue

```powershell
pwsh .\Invoke-WacTriageOnce.ps1 -IssueNumber 42
```

### Test 4 — Don't send email; just write the HTML to disk

```powershell
pwsh .\Invoke-WacTriageOnce.ps1 -RecentCount 1 -NoEmail
# Look in: local\outbox\*.html
```

---

## Start the bot / Stop the bot

The "bot" is a background Python process that polls the repo and emails
you a report for each *new* issue it sees.

```powershell
cd C:\Users\trungtran\wac-feedback-triage\local

# Start — polls every 60s, looks at 10 most-recent open issues
pwsh .\Start-WacTriageBot.ps1

# Check on it (status + uptime + tail of log + which issues it has seen)
pwsh .\Get-WacTriageBotStatus.ps1

# Stop
pwsh .\Stop-WacTriageBot.ps1
```

`Start-WacTriageBot.ps1` accepts `-PollSeconds`, `-RecentCount`, `-To`, and
`-UseRealLlm`. The PID is recorded at `local\state\loop.pid` so
`Stop-WacTriageBot.ps1` can find it. Logs are at `local\state\loop.log`.

To forget which issues have been processed (so the next loop re-emails
everything):

```powershell
& "..\.github\copilot-triage\.venv\Scripts\python.exe" .\runner.py reset
```

---

## Switching from mock LLM to real Azure OpenAI

When you eventually provision Azure OpenAI, set these env vars in the
PowerShell session before invoking the script, and pass `-UseRealLlm`:

```powershell
$env:AZURE_OPENAI_ENDPOINT   = "https://my-aoai.openai.azure.com/"
$env:AZURE_OPENAI_API_KEY    = "<key>"
$env:AZURE_OPENAI_DEPLOYMENT = "wac-triage-gpt4o"

pwsh .\Invoke-WacTriageOnce.ps1 -RecentCount 1 -UseRealLlm
```

The mock LLM produces reasonable triage from simple keyword heuristics
(bug/feature, severity, area), so it's good enough to demo the *flow*. The
actual classification quality matches what the real model would do only
on the obvious cases.

---

## What's in this folder

```
local/
├── runner.py                       # Python orchestrator (subcommands: once, loop, status, reset)
├── email_outlook.py                # Outlook COM email sender (with file fallback)
├── state.py                        # JSON-on-disk store for "seen" issues
├── templates/per_issue_email.html.j2  # HTML email template
├── requirements.txt                # pywin32
├── Invoke-WacTriageOnce.ps1        # Demo wrapper: triage one or N issues
├── Start-WacTriageBot.ps1          # Start the background poll loop
├── Stop-WacTriageBot.ps1           # Stop it
├── Get-WacTriageBotStatus.ps1      # Status + tail of log
├── state/                          # seen.json, loop.pid, loop.log, raw debug JSON
└── outbox/                         # HTML files when -NoEmail or Outlook unavailable
```

---

## How it relates to the production agent

| Local demo runner            | Production GitHub Actions agent       |
|------------------------------|---------------------------------------|
| `python local/runner.py`     | `.github/workflows/triage-on-issue.yml` invokes `agent.py` |
| Fetch via `gh issue list`    | Workflow receives the webhook payload |
| `--mode shadow --use-mock-llm` | `--mode live` with Azure OpenAI      |
| Outlook COM → your inbox     | Teams card + email webhook + ADO item |
| Local `state/seen.json`      | HTML markers in the issue's first bot comment |

The agent code is identical — the local runner just constrains the
flags and replaces the LLM and the notifier.

When you're ready to run the production pipeline against a real test
issue, see [`.github/copilot-triage/RUNBOOK.md`](../.github/copilot-triage/RUNBOOK.md).
