"""Teams + email notification senders.

Both channels are fire-and-forget. Failures are logged and surfaced via
`triage-failed` labels in the calling workflow — they must never raise into the
customer-facing path.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent.parent
_TEMPLATES = _HERE / "templates"
_jinja = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(["html", "xml"]),
    keep_trailing_newline=True,
)


def _render(template_name: str, **ctx: Any) -> str:
    return _jinja.get_template(template_name).render(**ctx)


def send_per_issue(*, issue: dict, triage: dict, ado_url: str, ado_id: int) -> dict:
    """Send the per-issue Teams card + email. Returns a dict of channel results."""
    results: dict[str, Any] = {}

    # ---- Teams ----
    teams_url = os.environ.get("TEAMS_WEBHOOK_URL")
    if teams_url:
        try:
            card_json = _render(
                "teams_card.json.j2",
                issue=issue, triage=triage, ado_url=ado_url, ado_id=ado_id,
            )
            r = requests.post(teams_url, data=card_json,
                              headers={"Content-Type": "application/json"},
                              timeout=15)
            results["teams"] = {"status": r.status_code,
                                "ok": 200 <= r.status_code < 300}
            if not results["teams"]["ok"]:
                log.error("Teams webhook returned %s: %s", r.status_code, r.text[:300])
        except Exception as e:  # pragma: no cover
            log.exception("Teams notification failed")
            results["teams"] = {"status": None, "ok": False, "error": str(e)}
    else:
        log.warning("TEAMS_WEBHOOK_URL not set; skipping Teams notification")
        results["teams"] = {"status": None, "ok": False, "skipped": True}

    # ---- Email ----
    email_url = os.environ.get("EMAIL_WEBHOOK_URL")
    to_addr = os.environ.get("TEAM_DL_ADDRESS")
    if email_url and to_addr:
        try:
            html = _render(
                "email_per_issue.html.j2",
                issue=issue, triage=triage, ado_url=ado_url, ado_id=ado_id,
            )
            subject = (
                f"[WAC triage] #{issue.get('number')} "
                f"({triage.get('category', '?')}/{triage.get('severity', '?')}): "
                f"{issue.get('title')}"
            )
            payload = {
                "to": to_addr,
                "subject": subject[:200],
                "html": html,
                "metadata": {
                    "github_issue_number": issue.get("number"),
                    "github_issue_url": issue.get("html_url"),
                    "ado_id": ado_id,
                    "ado_url": ado_url,
                    "category": triage.get("category"),
                    "severity": triage.get("severity"),
                },
            }
            r = requests.post(email_url, json=payload, timeout=20)
            results["email"] = {"status": r.status_code,
                                "ok": 200 <= r.status_code < 300}
            if not results["email"]["ok"]:
                log.error("Email webhook returned %s: %s", r.status_code, r.text[:300])
        except Exception as e:  # pragma: no cover
            log.exception("Email notification failed")
            results["email"] = {"status": None, "ok": False, "error": str(e)}
    else:
        log.warning("EMAIL_WEBHOOK_URL/TEAM_DL_ADDRESS not set; skipping email")
        results["email"] = {"status": None, "ok": False, "skipped": True}

    return results


def send_digest(*, period_label: str, stats: dict, recent_issues: list,
                ado_items: list) -> dict:
    """Send the weekly digest to Teams + email."""
    results: dict[str, Any] = {}

    teams_url = os.environ.get("TEAMS_WEBHOOK_URL")
    if teams_url:
        try:
            card_json = _render(
                "teams_digest.json.j2",
                period_label=period_label, stats=stats,
                recent_issues=recent_issues, ado_items=ado_items,
            )
            r = requests.post(teams_url, data=card_json,
                              headers={"Content-Type": "application/json"},
                              timeout=20)
            results["teams"] = {"status": r.status_code,
                                "ok": 200 <= r.status_code < 300}
        except Exception as e:  # pragma: no cover
            log.exception("Teams digest failed")
            results["teams"] = {"status": None, "ok": False, "error": str(e)}
    else:
        results["teams"] = {"status": None, "ok": False, "skipped": True}

    email_url = os.environ.get("EMAIL_WEBHOOK_URL")
    to_addr = os.environ.get("TEAM_DL_ADDRESS")
    if email_url and to_addr:
        try:
            html = _render(
                "email_digest.html.j2",
                period_label=period_label, stats=stats,
                recent_issues=recent_issues, ado_items=ado_items,
            )
            r = requests.post(email_url, json={
                "to": to_addr,
                "subject": f"[WAC triage] Weekly digest — {period_label}",
                "html": html,
                "metadata": {"kind": "digest", "period": period_label},
            }, timeout=30)
            results["email"] = {"status": r.status_code,
                                "ok": 200 <= r.status_code < 300}
        except Exception as e:  # pragma: no cover
            log.exception("Email digest failed")
            results["email"] = {"status": None, "ok": False, "error": str(e)}
    else:
        results["email"] = {"status": None, "ok": False, "skipped": True}

    return results
