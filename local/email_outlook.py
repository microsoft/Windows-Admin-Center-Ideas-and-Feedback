"""Send mail via the user's installed Outlook (COM automation).

This is the simplest way to send notifications when the user is a Microsoft
employee on a managed Windows box: no SMTP creds, no service principal,
no Graph token — just open Outlook and ask it to send.

If Outlook isn't available (no pywin32, no Outlook installed) we fall back
to writing the email to disk under ./outbox/ so the runner can still be
demoed.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class EmailResult:
    sent: bool
    transport: str   # "outlook" or "outbox-file"
    detail: str      # identifier or file path


_OUTBOX = Path(__file__).resolve().parent / "outbox"


def _via_outlook(to: str, subject: str, html_body: str) -> EmailResult:
    import win32com.client  # type: ignore[import-not-found]

    outlook = win32com.client.Dispatch("Outlook.Application")
    mail = outlook.CreateItem(0)  # 0 = olMailItem
    mail.To = to
    mail.Subject = subject
    mail.HTMLBody = html_body
    mail.Send()
    log.info("Sent email to %s via Outlook: %r", to, subject)
    return EmailResult(sent=True, transport="outlook", detail=f"Outlook:{datetime.now(UTC).isoformat()}")


def _via_outbox(to: str, subject: str, html_body: str) -> EmailResult:
    _OUTBOX.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_subj = "".join(c if c.isalnum() or c in "-_." else "_" for c in subject)[:80]
    path = _OUTBOX / f"{stamp}__{safe_subj}.html"
    path.write_text(
        f"<!-- To: {to} -->\n<!-- Subject: {subject} -->\n{html_body}",
        encoding="utf-8",
    )
    log.warning("Outlook unavailable — wrote email to %s", path)
    return EmailResult(sent=False, transport="outbox-file", detail=str(path))


def send_html(to: str, subject: str, html_body: str, *, force_outbox: bool = False) -> EmailResult:
    if force_outbox or sys.platform != "win32":
        return _via_outbox(to, subject, html_body)
    try:
        return _via_outlook(to, subject, html_body)
    except Exception as exc:  # pragma: no cover - depends on local Outlook
        log.warning("Outlook send failed (%s) — falling back to outbox file", exc)
        return _via_outbox(to, subject, html_body)
