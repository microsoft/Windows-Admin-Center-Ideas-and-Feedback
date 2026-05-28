"""Deterministic, heuristic-based mock triager.

Produces a `TriageResult`-shaped dict from an issue payload without calling
any LLM. Used by the local demo runner so you can see the full end-to-end
flow before provisioning Azure OpenAI.

The output is intentionally conservative: it picks a category and severity
from keyword presence, builds a short summary by taking the first 1-3
sentences of the body, and asks 2-3 generic missing-info questions.

When the real Azure OpenAI client is wired up, this module is unused.
"""

from __future__ import annotations

import re
from typing import Any

from clients.aoai import TriageResult


_BUG_WORDS = (
    "bug", "crash", "crashes", "error", "exception", "fail", "fails", "failed",
    "broken", "doesn't work", "does not work", "not working", "stuck",
    "freeze", "hang", "regression", "rollback", "stack trace", "0x", "denied",
)
_FEATURE_WORDS = (
    "feature", "request", "wish", "would be nice", "please add", "support for",
    "ability to", "add support", "enhancement", "improve", "suggestion",
    "would love", "consider", "could you", "we need",
)

_HIGH_SEV_WORDS = (
    "production", "blocker", "blocking", "all users", "everyone", "data loss",
    "security", "vulnerability", "cve", "crashes", "down",
)
_LOW_SEV_WORDS = (
    "typo", "cosmetic", "minor", "nit", "wording", "label", "spelling",
)

_VMODE_WORDS = (
    "hyper-v", "hyperv", "virtual machine", " vm ", "vms", "vhd", "vhdx",
    "checkpoint", "live migration", "cluster shared volume", "csv",
)
_AMODE_WORDS = (
    "azure", "arc", "entra", "aad", "intune", "azure ad", "azure stack",
    "azure portal",
)

_AREA_WORDS = {
    "auth":      ("auth", "sign-in", "sign in", "login", "credential", "kerberos",
                  "ntlm", "oauth", "sso", "single sign", "entra", "aad", "azure ad"),
    "gateway":   ("gateway", "port 443", "port 6516", "https", "tls", "certificate",
                  "wac.exe", "service stopped"),
    "installer": ("install", "installer", "msi", "setup.exe", "uninstall", "upgrade"),
    "cluster":   ("cluster", "failover", "csv", "shared volume", "quorum"),
    "perf":      ("slow", "performance", "lag", "latency", "timeout", "hang"),
}


def _norm(s: str | None) -> str:
    return (s or "").lower()


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _first_sentences(body: str, max_chars: int = 300) -> str:
    body = (body or "").strip()
    if not body:
        return "(no body provided)"
    body = re.sub(r"\s+", " ", body)
    if len(body) <= max_chars:
        return body
    cut = body[:max_chars]
    last_dot = cut.rfind(".")
    if last_dot > max_chars * 0.5:
        return cut[: last_dot + 1]
    return cut.rstrip() + "…"


def mock_triage(issue: dict[str, Any]) -> TriageResult:
    title = _norm(issue.get("title"))
    body = _norm(issue.get("body"))
    blob = f"{title}\n{body}"

    # Category
    has_bug = _has_any(blob, _BUG_WORDS)
    has_feat = _has_any(blob, _FEATURE_WORDS)
    if has_feat and not has_bug:
        category = "feature"
    elif has_bug:
        category = "bug"
    else:
        category = "feature"  # benign default for ambiguous reports

    # Severity (only meaningful for bugs; features get "medium" as a placeholder)
    if category == "bug":
        if _has_any(blob, _HIGH_SEV_WORDS):
            severity = "high"
        elif _has_any(blob, _LOW_SEV_WORDS):
            severity = "low"
        else:
            severity = "medium"
    else:
        severity = "medium"

    # Deployment mode
    if _has_any(blob, _VMODE_WORDS) and not _has_any(blob, _AMODE_WORDS):
        mode = "vMode"
    elif _has_any(blob, _AMODE_WORDS):
        mode = "aMode"
    else:
        mode = "aMode"  # default

    # Area labels
    area_labels = []
    for area, words in _AREA_WORDS.items():
        if _has_any(blob, words):
            area_labels.append(area)

    # Summary
    summary_lead = _first_sentences(issue.get("body") or "", 280)
    summary = (
        f"{category.capitalize()} report — {summary_lead}"
        if category == "bug"
        else f"Feature request — {summary_lead}"
    )

    # Missing info questions
    missing_info: list[str] = []
    if "version" not in blob and "wac " not in blob:
        missing_info.append("Which version of Windows Admin Center are you running (Help → About)?")
    if "windows server" not in blob and "windows 10" not in blob and "windows 11" not in blob:
        missing_info.append("What is the OS version of the gateway host (winver output)?")
    if category == "bug" and "log" not in blob and "event" not in blob:
        missing_info.append("Could you attach the gateway log (%ProgramData%\\Server Management Experience\\Logs)?")
    if not missing_info:
        missing_info.append("Could you share any additional repro steps or screenshots?")

    # Labels
    labels = ["triaged", category, mode, f"severity:{severity}", *area_labels]

    # ADO description (HTML)
    ado_description_html = (
        f"<p><strong>Source:</strong> "
        f"<a href=\"{issue.get('html_url','')}\">GitHub #{issue.get('number','?')}</a></p>"
        f"<p><strong>Reported by:</strong> @{(issue.get('user') or {}).get('login','unknown')}</p>"
        f"<p><strong>Summary:</strong> {summary}</p>"
        f"<hr><pre>{(issue.get('body') or '').strip()[:4000]}</pre>"
    )

    title_short = (issue.get("title") or "(no title)").strip()[:160]
    ado_title = f"[GH #{issue.get('number','?')}] {title_short}"

    return TriageResult(
        category=category,
        mode=mode,
        severity=severity,
        labels=labels,
        summary=summary,
        missing_info=missing_info,
        ado_title=ado_title,
        ado_description_html=ado_description_html,
        ado_tags=area_labels or ["unclassified"],
    )
