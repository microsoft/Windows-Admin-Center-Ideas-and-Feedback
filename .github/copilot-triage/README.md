# WAC Feedback Triage Agent

AI-powered triage for issues filed on `microsoft/Windows-Admin-Center-Ideas-and-Feedback`.

## What it does

On every new or edited issue:

1. Reads the issue body + labels + author.
2. Calls Azure OpenAI (structured output) to classify, summarize, and draft a reply.
3. Posts a customer-facing acknowledgement comment.
4. Applies labels (`aMode`/`vMode`, `bug`/`feature`, severity).
5. Creates a `Bug` or `Feature` work item in `microsoft/OS` under area path
   `OS\Core\SPARC\SIX - Server, Intelligence, and Experiences\Enterprise Windows Admin Center`.
6. Notifies the team via Microsoft Teams + email.

On a recurring schedule:

- Every 30 min — checks linked ADO items and posts customer-facing updates on GitHub
  whenever a state transition happens (Active → Resolved → Closed).
- Weekly Mondays 09:00 UTC — sends a digest of all triaged issues + ADO items filed.

## Project layout

```
.github/
  workflows/
    triage-on-issue.yml     # issues.opened, issues.edited
    ado-sync.yml            # cron */30 * * * *
    weekly-digest.yml       # cron Mondays 09:00 UTC
  copilot-triage/
    agent.py                # entry point: triages one issue end-to-end
    digest.py               # weekly digest generator
    ado_sync.py             # ADO state poller + GH comment poster
    clients/
      aoai.py               # Azure OpenAI structured output client
      github_app.py         # GitHub App auth + REST helpers
      ado.py                # Azure DevOps REST client
      notify.py             # Teams + email senders
      gist_state.py         # Gist-backed key/value store for ADO state
    prompts/
      system.md             # system prompt (persona + output contract)
      reply_template.md     # customer reply skeleton (Jinja2)
    schema/
      triage_output.json    # JSON schema enforced on the LLM
    knowledge/
      sources.yml           # configured knowledge sources (RAG)
      retriever.py          # pluggable retriever interface
    templates/
      ado_state_comment.md.j2
      teams_card.json.j2
      email_per_issue.html.j2
      teams_digest.json.j2
      email_digest.html.j2
    tests/
      sample_issue.json
      test_smoke.py
    SETUP.md                # one-time setup walkthrough
    README.md               # this file
    requirements.txt
```

## Local testing

```powershell
cd .github\copilot-triage
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:AZURE_OPENAI_ENDPOINT = "https://..."
$env:AZURE_OPENAI_API_KEY  = "..."
$env:AZURE_OPENAI_DEPLOYMENT = "wac-triage-gpt4o"

python -m pytest tests
python agent.py --dry-run --issue tests/sample_issue.json
```

`--dry-run` prints what would be posted/created without calling GitHub, ADO, Teams, or email.

## Operating principles

- **Idempotent.** A `<!-- triaged-by: wac-feedback-bot -->` HTML marker in the first
  comment prevents duplicate triage on `issues.edited`. Labels and ADO links are still
  updated if the classification materially changes.
- **Opt-out.** Issues with the `no-triage` label are skipped entirely.
- **Bot-author skip.** Issues authored by `*[bot]` accounts are ignored.
- **Fail-soft.** If ADO, Teams, or email fail, the customer reply still goes through and
  a `triage-failed` label + Teams alert flag the issue for manual follow-up.
- **Observability.** Every run uploads a `triage-debug.json` artifact with the LLM
  prompt, output, and decisions.
- **Modes.** The repo variable `WAC_TRIAGE_MODE` controls the agent posture:
  - `live`   — default, full operation.
  - `shadow` — calls the LLM and writes the debug artifact but skips all writes
    (no comments, no ADO, no notifications). Useful for prompt tuning.
  - `gated`  — only triages issues that carry the `triage-test` label. Useful for
    the first live test (see `RUNBOOK.md`).
- **Kill switch.** `gh workflow disable triage-on-issue.yml --repo <repo>` stops
  the agent immediately. Alternatively flip `WAC_TRIAGE_MODE` to `shadow` or
  `gated`, or add `no-triage` to any individual issue.

## First time? Read these in order

1. [`../../provisioning/README.md`](../../provisioning/README.md) — **one-command provisioning** of
   the GitHub App, Azure OpenAI, ADO PAT, Teams webhook, and repo secrets. Replaces
   most of the manual steps below.
2. [`SETUP.md`](SETUP.md) — what gets created, the secret reference, and the manual
   fallback if a `provisioning/` script can't run in your environment.
3. [`scripts/README.md`](scripts/README.md) — what each helper script does.
4. [`RUNBOOK.md`](RUNBOOK.md) — the step-by-step for the first live test issue and the flip to live mode.
