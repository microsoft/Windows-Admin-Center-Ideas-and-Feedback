# Setup automation scripts

These scripts get you from "nothing" to "secrets are configured" in about 5 minutes,
then to a verified live test in another 5.

## The scripts in this folder

| Script | When you run it | What it does |
|---|---|---|
| `create_github_app.py`   | **Once**, first setup     | Creates the `wac-feedback-bot` GitHub App via the manifest flow. |
| `setup_secrets.ps1`      | **Once**, after the App   | Pushes all 11 required secrets to the repo via `gh secret set`. |
| `preflight_check.ps1`    | **Before** the live test  | Verifies auth, secrets, repo variables, workflow file, App installation, and labels. |
| `verify_triage.ps1`      | **After** the live test   | Given an issue #, asserts the workflow ran, the bot commented, ADO was linked, labels were applied, and downloads the debug artifact. |

> **Looking for the step-by-step?** Read [`../RUNBOOK.md`](../RUNBOOK.md). It walks through
> first-time setup, preflight, gated test issue, verification, and the flip to live mode.

## 1. Create the GitHub App

```powershell
cd .github\copilot-triage\scripts
python create_github_app.py --org <your-org>
# Or, for a personal account:
python create_github_app.py
```

What happens:
1. A localhost web server starts on `http://localhost:8765`.
2. Your default browser opens.
3. You're redirected to GitHub. Sign in if needed, then click **Create GitHub App from manifest**.
4. GitHub redirects back to localhost; the script captures your App ID + private key.
5. Files are written to `./out/`:
   - `app-id.txt`
   - `wac-feedback-bot.private-key.pem`
   - `app-info.json` (includes the install URL)
6. The script prints a one-click **install URL**. Open it and install the app on `microsoft/Windows-Admin-Center-Ideas-and-Feedback`.

> **Note on the `microsoft` org:** It has strict GitHub App policies and may require
> a request to the org admins before you can create third-party apps there. If
> creation is blocked, re-run with `--org <your-team-org>` or omit `--org` for your
> personal account. The app can be transferred or re-installed later.

## 2. Push the secrets to your repo

```powershell
pwsh scripts/setup_secrets.ps1 -Repo microsoft/Windows-Admin-Center-Ideas-and-Feedback
```

This uses [`gh` CLI](https://cli.github.com/) (`gh auth login` once if you haven't).
It:
- Reads `WAC_BOT_APP_ID` and `WAC_BOT_APP_PRIVATE_KEY` from the files in `./out/`.
- Prompts you for the remaining secrets (Azure OpenAI endpoint/key/deployment, ADO
  PAT, Teams webhook URL, email webhook URL, team DL, optional gist token).
- Press Enter to skip any field — useful if you want to set them in the GitHub UI.

## 3. Pre-flight check (before going live)

```powershell
pwsh scripts/preflight_check.ps1
# Optional: target a different repo (e.g. a personal fork for testing)
pwsh scripts/preflight_check.ps1 -Repo myorg/myfork
```

Exits 0 if you're ready, non-zero with a list of missing pieces otherwise.

## 4. Verify after the test issue

After you file the test issue (see RUNBOOK.md §3), run:

```powershell
pwsh scripts/verify_triage.ps1 -IssueNumber <N>
```

It waits for the workflow run to complete (up to 5 minutes), then asserts:
- A "Triage on issue" workflow run succeeded for issue #N
- The bot posted a comment containing `<!-- triaged-by: wac-feedback-bot -->`
- The comment contains an `<!-- ado-id: NNN -->` marker
- The issue has the `triaged` and `ado-linked` labels (and not `triage-failed`)
- Downloads `triage-debug-N.json` for inspection

## 5. Clean up

After verifying secrets at `https://github.com/<repo>/settings/secrets/actions`,
delete the local PEM file:

```powershell
Remove-Item .\out\wac-feedback-bot.private-key.pem
```

(GitHub keeps a copy on the App's settings page; you can regenerate any time.)

## Troubleshooting

| Symptom | Fix |
|---|---|
| Browser says "You're not allowed to create GitHub Apps for this organization" | You don't have org-admin on that org. Use a different `--org` or your personal account. |
| Localhost callback never returns | Port 8765 is blocked by something else. Kill that process or edit `PORT` at the top of `create_github_app.py`. |
| `gh secret set` says "Resource not accessible by integration" | `gh auth login` again with `--scopes "repo,read:org"`. |
| You forgot to install the app on the repo | Visit `https://github.com/apps/wac-feedback-bot` and click **Install**. |
| `preflight_check.ps1` says "WAC_TRIAGE_MODE not set" | Recommended for first test: `gh variable set WAC_TRIAGE_MODE -b "gated" --repo <repo>` |
| `verify_triage.ps1` says "No completed run found" | Workflow didn't trigger — usually `no-triage` label or bot author. Check the Actions tab. |
| `verify_triage.ps1` says "missing ado-id marker" | ADO PAT expired or wrong scope. Check the run log for the ADO step. |

