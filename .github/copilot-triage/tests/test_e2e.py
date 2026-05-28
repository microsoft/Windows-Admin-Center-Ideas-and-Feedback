"""End-to-end test of agent.main with all external services mocked.

This proves the full code path works:
  - load issue payload from file
  - safety guards
  - aoai.triage (mocked to return a deterministic TriageResult)
  - dry-run path skipping ADO/GitHub/Teams/email
  - reply rendering with the real Jinja template
  - triage-debug.json artifact written

No network. No credentials required.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent  # noqa: E402
from clients.aoai import TriageResult  # noqa: E402


FAKE_TRIAGE = TriageResult(
    category="bug",
    mode="aMode",
    severity="high",
    labels=["aMode", "bug", "severity:high", "auth"],
    summary="WAC gateway installer crashes at the Configure HTTPS step on Server Core.",
    missing_info=[
        "Full installer log (typically %TEMP%\\WindowsAdminCenter-Install.log)",
        "Whether SCONFIG was customized before the install attempt",
    ],
    ado_title="[GH #999001] WAC gateway installer crashes at HTTPS config on Server 2022 Core",
    ado_description_html="<p>Installer crashes during the Configure HTTPS step.</p>",
    ado_tags=["installer", "gateway"],
)


@pytest.fixture
def isolated_workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def test_e2e_dry_run_with_mocked_llm(isolated_workdir, monkeypatch):
    sample = ROOT / "tests" / "sample_issue.json"

    # Make sure no real-world env leaks in
    for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY",
              "AZURE_OPENAI_DEPLOYMENT", "GITHUB_TOKEN", "GITHUB_EVENT_PATH",
              "ADO_PAT", "TEAMS_WEBHOOK_URL", "EMAIL_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)

    def fake_triage(issue, retriever, *, debug_sink=None):
        if debug_sink is not None:
            debug_sink["prompt"] = {"system": "<mocked>", "user": "<mocked>"}
            debug_sink["raw_response"] = "<mocked>"
            debug_sink["triage"] = FAKE_TRIAGE.to_dict()
            debug_sink["model"] = "mock-gpt"
            debug_sink["grounding_chunks"] = []
        return FAKE_TRIAGE

    with patch.object(agent.aoai, "triage", side_effect=fake_triage):
        rc = agent.main(["--dry-run", "--issue", str(sample),
                         "--repo", "microsoft/Windows-Admin-Center-Ideas-and-Feedback"])

    assert rc == 0

    debug = json.loads((isolated_workdir / "triage-debug.json").read_text(encoding="utf-8"))
    assert debug["status"] == "ok"
    assert debug["dry_run"] is True
    assert debug["issue_number"] == 999001
    assert debug["triage"]["category"] == "bug"
    assert debug["triage"]["severity"] == "high"

    # Reply markdown must contain the customer @-mention, the ADO link
    # placeholder values, and the idempotency markers.
    reply = debug["reply_markdown"]
    assert "@fictional-customer" in reply
    assert "ADO #0" in reply  # dry-run uses ado_id=0
    assert "<!-- triaged-by: wac-feedback-bot -->" in reply
    assert "<!-- ado-id: 0 -->" in reply
    assert "Full installer log" in reply


def test_e2e_skips_bot_authored_issue(isolated_workdir, monkeypatch):
    bot_issue = isolated_workdir / "bot.json"
    bot_issue.write_text(json.dumps({
        "number": 42,
        "title": "Automated",
        "html_url": "https://example.invalid/42",
        "user": {"login": "dependabot[bot]", "type": "Bot"},
        "labels": [],
        "body": "hi",
    }), encoding="utf-8")

    # If the agent didn't skip, this would explode (no AOAI creds).
    rc = agent.main(["--dry-run", "--issue", str(bot_issue),
                     "--repo", "microsoft/x"])
    assert rc == 0

    debug = json.loads((isolated_workdir / "triage-debug.json").read_text(encoding="utf-8"))
    assert debug.get("skipped", "").startswith("author is a bot")


def test_e2e_skips_no_triage_label(isolated_workdir):
    issue = isolated_workdir / "opt-out.json"
    issue.write_text(json.dumps({
        "number": 43,
        "title": "Please ignore",
        "html_url": "https://example.invalid/43",
        "user": {"login": "someone", "type": "User"},
        "labels": [{"name": "no-triage"}],
        "body": "we'll handle this manually",
    }), encoding="utf-8")

    rc = agent.main(["--dry-run", "--issue", str(issue), "--repo", "microsoft/x"])
    assert rc == 0

    debug = json.loads((isolated_workdir / "triage-debug.json").read_text(encoding="utf-8"))
    assert debug.get("skipped") == "no-triage label present"


def test_e2e_gated_mode_skips_without_triage_test_label(isolated_workdir, monkeypatch):
    """In `gated` mode, issues without the 'triage-test' label are skipped before LLM."""
    issue = isolated_workdir / "ungated.json"
    issue.write_text(json.dumps({
        "number": 51,
        "title": "Random customer report",
        "html_url": "https://example.invalid/51",
        "user": {"login": "external-user", "type": "User"},
        "labels": [],  # no triage-test label
        "body": "It crashes.",
    }), encoding="utf-8")

    # If we accidentally call the LLM here, it would fail (no creds + no patch).
    rc = agent.main(["--mode", "gated", "--issue", str(issue), "--repo", "microsoft/x"])
    assert rc == 0

    debug = json.loads((isolated_workdir / "triage-debug.json").read_text(encoding="utf-8"))
    assert debug["mode"] == "gated"
    assert "gated mode" in debug.get("skipped", "")


def test_e2e_gated_mode_runs_with_triage_test_label(isolated_workdir, monkeypatch):
    """In `gated` mode, an issue WITH 'triage-test' label DOES get triaged
    (writes are still skipped because is_writeable = mode==live)."""
    issue = isolated_workdir / "gated.json"
    issue.write_text(json.dumps({
        "number": 52,
        "title": "Test issue please ignore",
        "html_url": "https://example.invalid/52",
        "user": {"login": "test-user", "type": "User"},
        "labels": [{"name": "triage-test"}],
        "body": "Synthetic body for end-to-end test.",
    }), encoding="utf-8")

    def fake_triage(issue, retriever, *, debug_sink=None):
        if debug_sink is not None:
            debug_sink["triage"] = FAKE_TRIAGE.to_dict()
        return FAKE_TRIAGE

    with patch.object(agent.aoai, "triage", side_effect=fake_triage):
        rc = agent.main(["--mode", "gated", "--issue", str(issue), "--repo", "microsoft/x"])

    assert rc == 0
    debug = json.loads((isolated_workdir / "triage-debug.json").read_text(encoding="utf-8"))
    assert debug["mode"] == "gated"
    assert debug["status"] == "ok"
    # Gated mode with the label present DOES run the triage but skips writes.
    # The reply is rendered (visible in the artifact for inspection) but not posted.
    assert "skipped" not in debug
    assert debug["reply_markdown"]
    assert "<!-- triaged-by: wac-feedback-bot -->" in debug["reply_markdown"]
    assert isinstance(debug["notify"], str) and "skipped" in debug["notify"]


def test_e2e_shadow_mode_calls_llm_but_no_writes(isolated_workdir, monkeypatch):
    """In `shadow` mode, the LLM is called but no comments, ADO, or notify writes happen."""
    sample = ROOT / "tests" / "sample_issue.json"

    called = {"triage": 0}

    def fake_triage(issue, retriever, *, debug_sink=None):
        called["triage"] += 1
        if debug_sink is not None:
            debug_sink["triage"] = FAKE_TRIAGE.to_dict()
            debug_sink["model"] = "mock-gpt"
        return FAKE_TRIAGE

    with patch.object(agent.aoai, "triage", side_effect=fake_triage):
        rc = agent.main(["--mode", "shadow", "--issue", str(sample), "--repo", "microsoft/x"])

    assert rc == 0
    assert called["triage"] == 1  # LLM was invoked
    debug = json.loads((isolated_workdir / "triage-debug.json").read_text(encoding="utf-8"))
    assert debug["mode"] == "shadow"
    assert debug["status"] == "ok"
    # Reply text was rendered (visible in artifact for inspection) but not posted
    assert "@fictional-customer" in debug["reply_markdown"]
    assert isinstance(debug["notify"], str) and "skipped" in debug["notify"]
    # ADO id is the stand-in 0 because no work item was created
    assert "ADO #0" in debug["reply_markdown"]


def test_e2e_writes_debug_artifact_even_on_failure(isolated_workdir, monkeypatch):
    sample = ROOT / "tests" / "sample_issue.json"

    def boom(issue, retriever, *, debug_sink=None):
        raise RuntimeError("simulated AOAI outage")

    with patch.object(agent.aoai, "triage", side_effect=boom):
        rc = agent.main(["--dry-run", "--issue", str(sample), "--repo", "microsoft/x"])

    assert rc == 1
    debug = json.loads((isolated_workdir / "triage-debug.json").read_text(encoding="utf-8"))
    assert debug["status"] == "error"
    assert "simulated AOAI outage" in debug["error"]["message"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
