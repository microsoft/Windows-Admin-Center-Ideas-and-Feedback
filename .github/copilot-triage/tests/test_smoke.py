"""Smoke tests that do NOT call Azure OpenAI or any external service.

Verify pure-Python pieces: schema, label filter, safety guards, marker parsing,
template rendering.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Allow `python -m pytest tests` from the package root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import _filter_labels, _safety_skip, _render_reply  # noqa: E402
from clients import github_app  # noqa: E402


SAMPLE = json.loads((ROOT / "tests" / "sample_issue.json").read_text(encoding="utf-8"))


def test_filter_labels_keeps_known_prefixes_and_adds_triaged():
    out = _filter_labels(["aMode", "bug", "severity:high", "random-label", ""])
    assert "aMode" in out
    assert "bug" in out
    assert "severity:high" in out
    assert "triaged" in out
    assert "random-label" not in out


def test_safety_skip_for_bot_author():
    issue = dict(SAMPLE)
    issue["user"] = {"login": "dependabot[bot]", "type": "Bot"}
    assert _safety_skip(issue, []) is not None


def test_safety_skip_for_no_triage_label():
    issue = dict(SAMPLE)
    issue["labels"] = [{"name": "no-triage"}]
    assert _safety_skip(issue, []) == "no-triage label present"


def test_safety_does_not_skip_clean_issue():
    assert _safety_skip(SAMPLE, []) is None


def test_extract_ado_id_from_marker():
    comments = [
        {"body": "Hello\n<!-- triaged-by: wac-feedback-bot -->\n<!-- ado-id: 4242 -->"},
    ]
    assert github_app.extract_ado_id(comments) == 4242
    assert github_app.has_been_triaged(comments) is True


def test_render_reply_contains_key_elements():
    body = _render_reply(
        issue=SAMPLE, summary="Installer crashes at HTTPS step.",
        missing_info=["Full installer log location"], ado_id=4242,
        ado_url="https://dev.azure.com/microsoft/OS/_workitems/edit/4242",
    )
    assert "@fictional-customer" in body
    assert "ADO #4242" in body
    assert "Full installer log location" in body
    assert "<!-- triaged-by: wac-feedback-bot -->" in body
    assert "<!-- ado-id: 4242 -->" in body


def test_schema_loads_and_has_expected_fields():
    schema = json.loads((ROOT / "schema" / "triage_output.json").read_text(encoding="utf-8"))
    required = set(schema["required"])
    assert required == {
        "category", "mode", "severity", "labels", "summary",
        "missing_info", "ado_title", "ado_description_html", "ado_tags",
    }


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
