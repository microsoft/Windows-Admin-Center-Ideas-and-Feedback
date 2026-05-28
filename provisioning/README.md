# `provisioning/` — one-time setup for the production bot

This folder contains the scripts that stand up the **real** dependencies the
GitHub Actions workflow needs to reach the live bot's promised behaviour
(`agent.py` in `.github/copilot-triage/`). Run it once per environment.

> The **local demo** under `..\local\` does not need any of this. It uses a
> mock LLM and your Outlook session. Use the local demo to validate end-to-end
> message generation; use this folder when you're ready to push the code to the
> repo and have the workflow run on real issues.

## What gets provisioned

| Step | Resource                    | Where it lives                                    | Who creates it                      |
|------|-----------------------------|---------------------------------------------------|-------------------------------------|
| 0    | `gh` + `az` CLIs            | this machine                                      | `winget` (this script)              |
| 1    | GitHub App `wac-feedback-bot` | `github.com/settings/apps/wac-feedback-bot`     | manifest flow (browser, you click Create) |
| 2    | Azure OpenAI account + gpt-4o deployment | Azure subscription you select         | `az` CLI (this script)              |
| 3    | Azure DevOps PAT            | your ADO user profile                             | ADO web UI (you click Create), validated by this script |
| 4    | Teams Incoming Webhook      | your Teams channel                                | Teams client (you click Create), validated by this script |
| 5    | Repo secrets + variables    | `microsoft/Windows-Admin-Center-Ideas-and-Feedback` | `gh secret set` (this script)     |

## TL;DR — one command

After running once, all real resources exist and the workflow can run:

```powershell
cd C:\Users\trungtran\wac-feedback-triage\provisioning
pwsh .\Provision-All.ps1 -TeamDL wac-feedback@microsoft.com
```

That orchestrator runs steps 0 through 5 in order with a confirmation prompt
between each. Use `-Yes` to skip the prompts; use `-Skip*` switches to skip
steps already done.

## Individual scripts

You can run any step independently — each writes its output to
`state\<name>.json` and is safe to re-run.

```powershell
pwsh .\Install-Prereqs.ps1               # 0. winget install gh; verify az
pwsh .\Register-GitHubApp.ps1            # 1. browser-driven manifest flow
pwsh .\Provision-AzureOpenAI.ps1 -Location eastus2   # 2. az CLI
pwsh .\Setup-Ado.ps1                     # 3. PAT prompt + validation
pwsh .\Setup-Teams.ps1                   # 4. webhook URL + test card
pwsh .\Push-Secrets.ps1 -TeamDL wac@example.com -Mode gated   # 5. gh secret set
```

## What's in `state/`

After each step, a JSON file lands in `provisioning\state\`. **These files
contain real secrets** — don't commit them. The `.gitignore` already excludes
the folder.

| File                  | Created by                | Contains                                                  |
|-----------------------|---------------------------|-----------------------------------------------------------|
| `github-app.json`     | `Register-GitHubApp.ps1`  | `app_id`, `slug`, `client_id/secret`, `webhook_secret`, **`pem`** |
| `aoai.json`           | `Provision-AzureOpenAI.ps1` | `endpoint`, **`api_key`**, deployment name, api_version    |
| `ado.json`            | `Setup-Ado.ps1`           | org, project, area path, **`pat`**                         |
| `teams.json`          | `Setup-Teams.ps1`         | webhook URL                                               |

`Push-Secrets.ps1` reads from these and writes the matching repo secrets:

| Secret                    | Source                                |
|---------------------------|---------------------------------------|
| `WAC_BOT_APP_ID`          | `github-app.json` → `app_id`          |
| `WAC_BOT_APP_PRIVATE_KEY` | `github-app.json` → `pem`             |
| `AZURE_OPENAI_ENDPOINT`   | `aoai.json` → `endpoint`              |
| `AZURE_OPENAI_API_KEY`    | `aoai.json` → `api_key`               |
| `AZURE_OPENAI_DEPLOYMENT` | `aoai.json` → `deployment`            |
| `AZURE_OPENAI_API_VERSION`| `aoai.json` → `api_version`           |
| `ADO_PAT`                 | `ado.json` → `pat`                    |
| `TEAMS_WEBHOOK_URL`       | `teams.json` → `webhook_url`          |
| `TEAM_DL_ADDRESS`         | `-TeamDL` flag                        |

Plus variables (`WAC_TRIAGE_MODE`, `ADO_SYNC_ENABLED`, `ADO_ORG`, `ADO_PROJECT`,
`ADO_AREA_PATH`).

## What I **cannot** do for you (and why)

| Step                                          | Why not                                  |
|-----------------------------------------------|------------------------------------------|
| Sign in to Azure                              | Requires your interactive identity       |
| Click "Create GitHub App" / "Install"         | GitHub's manifest flow is browser-confirm only |
| Generate the ADO PAT                          | Microsoft doesn't expose a PAT-creation API |
| Click "Create webhook" in Teams               | Teams UI only                            |
| Approve Azure OpenAI quota in your subscription | If your subscription lacks AOAI quota, you'll need to request it via the AOAI request form |

Everything else (manifest content, App permissions, model deployment, area-path
validation, webhook test, secret upload) is automated.

## After provisioning

1. Push `.github/` to your repo:
   ```powershell
   cd C:\Users\trungtran\wac-feedback-triage
   git init
   git remote add origin https://github.com/microsoft/Windows-Admin-Center-Ideas-and-Feedback.git
   git checkout -b chore/feedback-triage-bot
   git add .github
   git commit -m "Add wac-feedback-bot triage workflow"
   git push -u origin chore/feedback-triage-bot
   ```
   …and open a PR. Once merged, the workflow is active.

2. Follow `..\\.github\\copilot-triage\\RUNBOOK.md` for the first gated test.

## Reset

To start over, delete `state\` and re-run. Existing Azure / ADO / GitHub
resources will be detected and reused; nothing is destroyed.

```powershell
Remove-Item -Recurse -Force .\state
```
