# WAC Feedback Triage Agent — Setup

This document walks the owners of `microsoft/Windows-Admin-Center-Ideas-and-Feedback`
through the one-time setup required for the AI triage agent.

> **Already done one-time setup and just want to run the first live test?**
> Skip ahead to [`RUNBOOK.md`](RUNBOOK.md).

## 1. Register the GitHub App (`wac-feedback-bot`)

1. Go to https://github.com/organizations/microsoft/settings/apps and click **New GitHub App**.
2. Fill in:
   - **Name:** `wac-feedback-bot`
   - **Homepage URL:** the repo URL
   - **Webhook:** uncheck "Active" (we don't need a webhook; workflows trigger the agent).
3. **Repository permissions:**
   - Issues — **Read & write**
   - Metadata — **Read-only**
   - Contents — **Read-only** (raise to **Read & write** only if you want the bot
     to commit state files; this project uses a gist instead).
4. **Subscribe to events:** none required.
5. **Where can this GitHub App be installed?** Only on this account.
6. Click **Create GitHub App**, then:
   - Note the **App ID** (top of the page).
   - Click **Generate a private key** → downloads a `.pem` file.
7. **Install the app** on `microsoft/Windows-Admin-Center-Ideas-and-Feedback` only.

## 2. Create the Azure OpenAI resource

- Provision (or reuse) an Azure OpenAI resource your team owns.
- Deploy a model — recommended: `gpt-4o` (deployment name e.g. `wac-triage-gpt4o`).
- Note the endpoint, API key, and deployment name.

## 3. Create the ADO PAT

- In Azure DevOps `microsoft/OS`, create a PAT with these scopes:
  - **Work Items:** Read, write, & manage
- Set expiration to the org maximum and put a calendar reminder to rotate it.

## 4. Provision team notification channels

- **Teams channel:** add an *Incoming Webhook* connector to the team channel and copy the URL.
- **Email:** either
  - Set up a Logic App / Power Automate flow with an HTTP trigger that sends mail
    on behalf of the team mailbox, and use its URL, **or**
  - Use a simple SMTP relay (you'll need to extend `clients/notify.py` accordingly).

## 5. Create a gist for ADO state cache

The ADO → GitHub sync workflow caches the last-seen state of each linked ADO item
in a secret gist owned by the bot.

1. Sign in as the GitHub App (or a service account that the App can read on behalf of).
2. Create a **secret** gist with a single file `ado-state.json` containing `{}`.
3. Note the gist ID.

> Alternative: commit state into `.github/copilot-triage/state/ado-state.json` from
> the workflow. Simpler, but creates noisy commit history. Off by default.

## 6. Configure repository secrets

In **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|---|---|
| `WAC_BOT_APP_ID` | App ID from step 1 |
| `WAC_BOT_APP_PRIVATE_KEY` | Entire contents of the `.pem` file from step 1 |
| `AZURE_OPENAI_ENDPOINT` | e.g. `https://wac-aoai.openai.azure.com` |
| `AZURE_OPENAI_API_KEY` | from step 2 |
| `AZURE_OPENAI_DEPLOYMENT` | deployment name from step 2 |
| `AZURE_OPENAI_API_VERSION` | e.g. `2024-10-21` (optional; defaults in code) |
| `ADO_PAT` | from step 3 |
| `TEAMS_WEBHOOK_URL` | from step 4 |
| `EMAIL_WEBHOOK_URL` | from step 4 |
| `TEAM_DL_ADDRESS` | e.g. `wac-triage@microsoft.com` |
| `ADO_STATE_GIST_ID` | from step 5 |
| `ADO_STATE_GIST_TOKEN` | a fine-grained PAT with **Gists: read/write** scope only |

## 7. Optional knowledge sources

Edit `.github/copilot-triage/knowledge/sources.yml` to add any combination of:

- SharePoint / OneDrive folders (Graph API)
- Private GitHub repos of markdown documentation
- An ADO Wiki
- An Azure AI Search index

Each entry will be picked up by the retriever. See `knowledge/sources.yml` for the schema.

## 8. Enable the workflows

Once secrets are set, the three workflows under `.github/workflows/` will run automatically:

- `triage-on-issue.yml` — every new/edited issue
- `ado-sync.yml` — every 30 minutes
- `weekly-digest.yml` — Mondays at 09:00 UTC

To test safely first, you can run them on a private fork or restrict the issue triage
to issues with a `triage-test` label by editing the `if:` condition in
`triage-on-issue.yml`.
