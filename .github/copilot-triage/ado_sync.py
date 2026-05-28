"""ADO -> GitHub state sync.

Runs on a schedule. For each open GitHub issue with the `ado-linked` label:
  1. Find the linked ADO work item ID (from the hidden marker in our prior comment).
  2. Query ADO for its current state.
  3. Compare to the last-seen state cached in the Gist.
  4. If changed, render and post a customer-facing comment on the GitHub issue.
  5. Persist the new state in the Gist.

Tolerant of every kind of partial failure: any one issue's failure is logged but
doesn't stop the others.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from clients import ado as ado_client, github_app
from clients.gist_state import GistState

log = logging.getLogger("wac.ado_sync")

_HERE = Path(__file__).resolve().parent
_TPL_DIR = _HERE / "templates"
_DEFAULT_REPO = "microsoft/Windows-Admin-Center-Ideas-and-Feedback"

# Sticky terminal states we report once and then stop polling.
TERMINAL_STATES = {"Closed", "Removed", "Cut"}


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("WAC_TRIAGE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _render_comment(*, author: str, previous_state: str | None, new_state: str) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_TPL_DIR)),
        autoescape=select_autoescape(disabled_extensions=("md", "j2"), default=False),
        keep_trailing_newline=True,
    )
    tpl = env.get_template("ado_state_comment.md.j2")
    return tpl.render(author=author, previous_state=previous_state,
                      new_state=new_state)


def sync_once(repo: str, *, dry_run: bool = False) -> dict:
    debug = {
        "started_at": datetime.now(UTC).isoformat(),
        "repo": repo,
        "dry_run": dry_run,
        "transitions": [],
        "errors": [],
    }

    gh = github_app.client_from_env(repo=repo)
    ado = ado_client.AzureDevOpsClient()
    state_store = GistState()

    state = state_store.load() if not dry_run else {}

    issues = gh.list_open_issues_with_label(repo, "ado-linked")
    log.info("Found %d open ado-linked issues", len(issues))

    for issue in issues:
        number = issue["number"]
        try:
            comments = gh.list_issue_comments(repo, number)
            ado_id = github_app.extract_ado_id(comments)
            if not ado_id:
                log.warning("Issue #%s has ado-linked label but no ado-id marker", number)
                continue

            wi = ado.get_work_item(ado_id)
            current_state = wi.state or "Unknown"
            key = str(number)
            prev = (state.get(key) or {}).get("state")

            if prev == current_state:
                state[key] = {"ado_id": ado_id, "state": current_state,
                              "checked_at": datetime.now(UTC).isoformat()}
                continue

            if prev in TERMINAL_STATES and current_state in TERMINAL_STATES:
                # Don't keep posting once we've already announced a terminal state.
                state[key] = {"ado_id": ado_id, "state": current_state,
                              "checked_at": datetime.now(UTC).isoformat()}
                continue

            author = (issue.get("user") or {}).get("login", "there")
            body = _render_comment(
                author=author, previous_state=prev, new_state=current_state,
            )

            if dry_run:
                log.info("[DRY] Would post on #%s: %s -> %s",
                         number, prev, current_state)
            else:
                gh.post_comment(repo, number, body)

            debug["transitions"].append({
                "issue": number, "ado_id": ado_id,
                "from": prev, "to": current_state,
            })
            state[key] = {"ado_id": ado_id, "state": current_state,
                          "checked_at": datetime.now(UTC).isoformat()}

        except Exception as e:
            log.exception("sync failed for #%s", number)
            debug["errors"].append({
                "issue": number,
                "error": str(e),
                "traceback": traceback.format_exc(),
            })

    if not dry_run:
        try:
            state_store.save(state)
        except Exception as e:
            log.exception("Failed to persist gist state")
            debug["errors"].append({"persistence_error": str(e)})

    debug["finished_at"] = datetime.now(UTC).isoformat()
    return debug


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", _DEFAULT_REPO))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    debug = sync_once(args.repo, dry_run=args.dry_run)
    Path("ado-sync-debug.json").write_text(
        json.dumps(debug, indent=2, default=str), encoding="utf-8"
    )
    return 0 if not debug["errors"] else 0  # never fail the workflow on per-issue errors


if __name__ == "__main__":
    sys.exit(main())
