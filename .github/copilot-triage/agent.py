"""Entry point for the triage-on-issue workflow.

Runs end-to-end for a single GitHub issue:
  1. Loads the issue payload (from GITHUB_EVENT_PATH or --issue).
  2. Applies safety guards (bot author, no-triage label, idempotency).
  3. Calls Azure OpenAI to produce a TriageResult.
  4. Creates an ADO work item.
  5. Posts the customer reply, adds labels, links the ADO item.
  6. Sends Teams + email notifications.
  7. Writes ./triage-debug.json (always — including dry-run and failure paths).

Exit codes:
  0 — completed (including intentional skip via guards)
  1 — uncaught error (CI step fails; workflow then labels 'triage-failed')

CLI:
  python agent.py                # uses GITHUB_EVENT_PATH
  python agent.py --issue file.json
  python agent.py --dry-run --issue tests/sample_issue.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from clients import aoai, ado as ado_client, github_app, notify
from knowledge.retriever import default_retriever

log = logging.getLogger("wac.triage")

_HERE = Path(__file__).resolve().parent
_REPLY_TPL = _HERE / "prompts" / "reply_template.md"

DEFAULT_REPO = "microsoft/Windows-Admin-Center-Ideas-and-Feedback"
RETRIAGE_THROTTLE_HOURS = 24

# Labels we manage. We never apply or remove labels we don't recognize.
SAFE_LABELS_PREFIXES = (
    "aMode", "vMode", "bug", "feature", "severity:", "area:", "triaged",
    "auth", "gateway", "entra", "installer", "cluster", "perf",
    "ado-linked", "triage-failed", "no-triage",
)


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("WAC_TRIAGE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _load_issue_payload(path: str | None) -> dict:
    """Load the issue payload either from --issue, GITHUB_EVENT_PATH, or stdin."""
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).exists():
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        # `issues` events nest the issue payload under `issue`.
        if "issue" in event:
            return event["issue"]
        return event
    raise SystemExit("No issue payload. Pass --issue or set GITHUB_EVENT_PATH.")


def _safety_skip(issue: dict, comments: list[dict]) -> str | None:
    """Return a reason string if we should skip this issue, else None."""
    user = (issue.get("user") or {}).get("login", "")
    user_type = (issue.get("user") or {}).get("type", "")
    if user_type == "Bot" or user.endswith("[bot]"):
        return f"author is a bot ({user})"

    labels = {l["name"] if isinstance(l, dict) else l for l in issue.get("labels") or []}
    if "no-triage" in labels:
        return "no-triage label present"

    if github_app.has_been_triaged(comments):
        # Re-classification on edits is allowed but throttled.
        last = _last_triage_time(comments)
        if last and (datetime.now(UTC) - last) < timedelta(hours=RETRIAGE_THROTTLE_HOURS):
            return f"already triaged within {RETRIAGE_THROTTLE_HOURS}h"
        # Even past the throttle, never re-create the ADO item.
        return None
    return None


def _last_triage_time(comments: list[dict]) -> datetime | None:
    latest: datetime | None = None
    for c in comments:
        if github_app.TRIAGE_MARKER in (c.get("body") or ""):
            t = c.get("created_at") or c.get("updated_at")
            if t:
                try:
                    dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
                    if latest is None or dt > latest:
                        latest = dt
                except ValueError:
                    continue
    return latest


def _filter_labels(labels: list[str]) -> list[str]:
    safe: list[str] = []
    for raw in labels:
        l = (raw or "").strip()
        if not l:
            continue
        if any(l.startswith(p) for p in SAFE_LABELS_PREFIXES) or l in {
            "aMode", "vMode", "bug", "feature", "triaged",
        }:
            safe.append(l)
    # Always include `triaged` as a marker.
    if "triaged" not in safe:
        safe.append("triaged")
    return sorted(set(safe))


def _render_reply(*, issue: dict, summary: str, missing_info: list[str],
                  ado_id: int, ado_url: str) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_REPLY_TPL.parent)),
        autoescape=select_autoescape(disabled_extensions=("md",), default=False),
        keep_trailing_newline=True,
    )
    tpl = env.get_template(_REPLY_TPL.name)
    return tpl.render(
        author=(issue.get("user") or {}).get("login", "there"),
        summary=summary,
        missing_info=missing_info,
        ado_id=ado_id,
        ado_link=f"[ADO #{ado_id}]({ado_url})",
    )


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", help="Path to an issue JSON payload.")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO))
    ap.add_argument("--dry-run", action="store_true",
                    help="Skip all writes (GitHub, ADO, Teams, email).")
    ap.add_argument(
        "--mode",
        choices=["live", "shadow", "gated", "off"],
        default=os.environ.get("WAC_TRIAGE_MODE", "live"),
        help=(
            "live   = normal operation; "
            "shadow = call LLM + write debug artifact but no comments / ADO / notify; "
            "gated  = only run if the issue has the 'triage-test' label "
            "(safe pre-launch test); "
            "off    = exit immediately, do nothing (kill switch — set the "
            "WAC_TRIAGE_MODE repo variable to 'off' to silence the bot "
            "without disabling the workflow)."
        ),
    )
    ap.add_argument(
        "--use-mock-llm",
        action="store_true",
        default=os.environ.get("WAC_TRIAGE_USE_MOCK", "").lower() in {"1", "true", "yes"},
        help=(
            "Skip Azure OpenAI and use the deterministic heuristic stub in "
            "clients/mock_llm.py. Handy for local demos before AOAI is wired."
        ),
    )
    args = ap.parse_args(argv)

    # Shadow mode is dry-run with one difference: it still needs the AOAI
    # call, but skips all external writes. Internally we treat it like a
    # dry-run for the write paths.
    is_writeable = (args.mode == "live") and not args.dry_run

    debug: dict = {
        "started_at": datetime.now(UTC).isoformat(),
        "repo": args.repo,
        "dry_run": args.dry_run,
        "mode": args.mode,
        "use_mock_llm": args.use_mock_llm,
    }

    try:
        issue = _load_issue_payload(args.issue)
        debug["issue_number"] = issue.get("number")
        debug["issue_url"] = issue.get("html_url")

        # 'off' is the kill switch: skip everything, no AOAI cost.
        if args.mode == "off":
            log.info("Skipping #%s: WAC_TRIAGE_MODE=off (bot is paused)",
                     issue.get("number"))
            debug["skipped"] = "WAC_TRIAGE_MODE=off"
            return 0

        labels_on_issue = {l["name"] if isinstance(l, dict) else l
                           for l in issue.get("labels") or []}
        if args.mode == "gated" and "triage-test" not in labels_on_issue:
            log.info("Skipping #%s: gated mode and no 'triage-test' label",
                     issue.get("number"))
            debug["skipped"] = "gated mode requires 'triage-test' label"
            return 0

        # If we won't write to GitHub, we can't fetch comments either.
        comments: list[dict] = []
        gh: github_app.GitHubAppClient | None = None
        if is_writeable:
            gh = github_app.client_from_env(repo=args.repo)
            comments = gh.list_issue_comments(args.repo, issue["number"])

        skip = _safety_skip(issue, comments)
        if skip:
            log.info("Skipping issue #%s: %s", issue.get("number"), skip)
            debug["skipped"] = skip
            return 0

        # 1. LLM triage
        if args.use_mock_llm:
            from clients.mock_llm import mock_triage
            log.info("Using MOCK LLM (heuristic stub) — no AOAI call")
            result = mock_triage(issue)
            debug["triage"] = result.to_dict()
            debug["model"] = "mock-heuristic-v1"
            debug["prompt"] = {"system": "<mock>", "user": "<mock>"}
            debug["raw_response"] = "<mock>"
            debug["grounding_chunks"] = []
        else:
            result = aoai.triage(issue, default_retriever(), debug_sink=debug)
        labels = _filter_labels(result.labels)

        # 2. ADO work item (only if we don't already have one).
        # ADO failures are non-fatal: customer reply + labels + notify still go out.
        ado_error: str | None = None
        existing_ado_id = github_app.extract_ado_id(comments)
        if existing_ado_id:
            log.info("Issue #%s already linked to ADO #%s; not re-filing",
                     issue["number"], existing_ado_id)
            ado_id = existing_ado_id
            ado_url = (
                f"https://dev.azure.com/{ado_client.DEFAULT_ORG}/"
                f"{ado_client.DEFAULT_PROJECT}/_workitems/edit/{ado_id}"
            )
        elif not is_writeable:
            ado_id = 0
            ado_url = "https://dev.azure.com/microsoft/OS/_workitems/edit/0"
        else:
            try:
                ado = ado_client.AzureDevOpsClient()
                wi = ado.create_work_item(
                    category=result.category,
                    title=result.ado_title,
                    description_html=result.ado_description_html,
                    github_issue_url=issue["html_url"],
                    tags=result.ado_tags,
                )
                ado_id = wi.id
                ado_url = wi.url
                debug["ado"] = {"id": ado_id, "url": ado_url, "type": wi.type}
            except Exception as e:  # noqa: BLE001
                log.warning("ADO work-item creation failed: %s — continuing with reply + labels only", e)
                ado_error = str(e)
                debug["ado_error"] = ado_error
                ado_id = 0
                ado_url = ""

        # 3. Render and post customer reply
        reply = _render_reply(
            issue=issue,
            summary=result.summary,
            missing_info=result.missing_info,
            ado_id=ado_id,
            ado_url=ado_url,
        )
        debug["reply_markdown"] = reply
        debug["labels_to_apply"] = labels

        if not is_writeable:
            mode_label = "DRY RUN" if args.dry_run else f"{args.mode.upper()} MODE"
            print(f"--- {mode_label}: customer reply ---")
            print(reply)
            print(f"--- {mode_label}: labels ---")
            print(labels)
        else:
            assert gh is not None
            gh.post_comment(args.repo, issue["number"], reply)
            label_set = list(labels)
            if ado_id and ado_id > 0:
                label_set.append("ado-linked")
            try:
                gh.add_labels(args.repo, issue["number"], label_set)
            except github_app.GitHubError as e:
                # Missing labels in the repo is non-fatal; log and continue.
                log.warning("add_labels partial failure: %s", e)
                debug["labels_warning"] = str(e)

        # 4. Notifications (fail-soft)
        if is_writeable:
            notify_results = notify.send_per_issue(
                issue=issue, triage=result.to_dict(),
                ado_url=ado_url, ado_id=ado_id,
            )
            debug["notify"] = notify_results
        else:
            debug["notify"] = f"skipped ({args.mode}{'+dry-run' if args.dry_run else ''})"

        debug["status"] = "ok"
        return 0

    except Exception as e:
        log.exception("Triage failed")
        debug["status"] = "error"
        debug["error"] = {"type": type(e).__name__, "message": str(e),
                          "traceback": traceback.format_exc()}
        return 1
    finally:
        finished_at = datetime.now(UTC)
        debug["finished_at"] = finished_at.isoformat()
        try:
            started = datetime.fromisoformat(debug["started_at"])
            debug["elapsed_seconds"] = round((finished_at - started).total_seconds(), 2)
        except Exception:
            debug["elapsed_seconds"] = None
        Path("triage-debug.json").write_text(
            json.dumps(debug, indent=2, default=str), encoding="utf-8"
        )


if __name__ == "__main__":
    sys.exit(main())
