"""Weekly digest generator.

Aggregates GitHub issues triaged in the last 7 days and ADO work items filed
under the WAC area path with the `github-triage` tag in the same window.
Renders Teams + email digest via clients.notify.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from clients import ado as ado_client, github_app, notify

log = logging.getLogger("wac.digest")

_DEFAULT_REPO = "microsoft/Windows-Admin-Center-Ideas-and-Feedback"

# Heuristics to recover the triage classification from the hidden HTML
# we ship in our reply markdown. Falls back to label inspection.
_LABEL_TO_CATEGORY = {"bug": "bug", "feature": "feature"}
_SEVERITY_LABELS = ("critical", "high", "medium", "low")


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("WAC_TRIAGE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _classify_from_labels(labels: list[dict]) -> tuple[str | None, str | None]:
    names = [(l["name"] if isinstance(l, dict) else l).lower() for l in labels]
    category = next((c for c in ("bug", "feature") if c in names), None)
    severity = next((s for s in _SEVERITY_LABELS
                     if f"severity:{s}" in names or s in names), None)
    return category, severity


def _was_triaged_in_window(comments: list[dict], since: datetime) -> bool:
    for c in comments:
        if github_app.TRIAGE_MARKER in (c.get("body") or ""):
            t = c.get("created_at")
            if not t:
                continue
            try:
                dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
                if dt >= since:
                    return True
            except ValueError:
                continue
    return False


def build_digest(repo: str, *, days: int = 7) -> dict:
    gh = github_app.client_from_env(repo=repo)
    ado = ado_client.AzureDevOpsClient()
    since = datetime.now(UTC) - timedelta(days=days)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    issues_recent = gh.list_recent_issues(repo, since_iso)

    triaged: list[dict] = []
    for issue in issues_recent:
        comments = gh.list_issue_comments(repo, issue["number"])
        if not _was_triaged_in_window(comments, since):
            continue
        category, severity = _classify_from_labels(issue.get("labels") or [])
        triaged.append({
            "number": issue["number"],
            "title": issue["title"],
            "url": issue["html_url"],
            "category": category or "unknown",
            "severity": severity or "unknown",
        })

    cats = Counter(t["category"] for t in triaged)
    sevs = Counter(t["severity"] for t in triaged)

    ado_items_raw = ado.query_recent_by_tag("github-triage", since_iso)
    ado_items = [
        {"id": w.id, "title": w.title or "", "state": w.state or "?",
         "type": w.type or "?", "url": w.url}
        for w in ado_items_raw
    ]
    ado_resolved = sum(1 for w in ado_items_raw
                       if (w.state or "") in {"Resolved", "Closed"})

    stats = {
        "total": len(triaged),
        "bugs": cats.get("bug", 0),
        "features": cats.get("feature", 0),
        "high_or_critical": sevs.get("high", 0) + sevs.get("critical", 0),
        "ado_filed": len(ado_items),
        "ado_resolved": ado_resolved,
    }

    period_label = f"{since.date().isoformat()} → {datetime.now(UTC).date().isoformat()}"

    return {
        "period_label": period_label,
        "stats": stats,
        "recent_issues": triaged[:25],
        "ado_items": ado_items[:25],
    }


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", _DEFAULT_REPO))
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    digest = build_digest(args.repo, days=args.days)
    Path("digest-debug.json").write_text(
        json.dumps(digest, indent=2, default=str), encoding="utf-8"
    )

    if args.dry_run:
        print(json.dumps(digest, indent=2))
        return 0

    notify.send_digest(**digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
