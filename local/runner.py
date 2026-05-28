"""Local demo runner for the WAC Feedback Triage Agent.

What this does (no Azure / GitHub App / ADO required):

  1. Pulls one or more recent open issues from the WAC repo via the user's
     existing `gh` CLI authentication.
  2. Runs the agent against each issue with --mode shadow --use-mock-llm,
     so the agent's full code path is exercised but NO writes happen.
  3. Parses the resulting triage-debug.json artifact.
  4. Renders an HTML report and emails it to a configured recipient via
     the user's Outlook (COM automation).
  5. Records the issue number in local state so we don't re-email on
     subsequent runs.

Usage:

  # One-off against a specific issue
  python runner.py once --issue 42

  # One-off against the N most recently updated open issues
  python runner.py once --recent 3

  # Continuous loop (poll every 60s for new updates)
  python runner.py loop --poll-seconds 60

  # Skip email send (just print and write outbox file)
  python runner.py once --recent 1 --no-email

The runner is fully offline-friendly if you point --issue at a local JSON
file with `--from-file`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
AGENT_DIR = ROOT / ".github" / "copilot-triage"
VENV_PY = AGENT_DIR / ".venv" / "Scripts" / "python.exe"
TEMPLATES = HERE / "templates"

sys.path.insert(0, str(HERE))
import email_outlook  # noqa: E402  pylint: disable=wrong-import-position
import state          # noqa: E402  pylint: disable=wrong-import-position

DEFAULT_REPO = "microsoft/Windows-Admin-Center-Ideas-and-Feedback"
DEFAULT_TO   = "trungtran@microsoft.com"

log = logging.getLogger("wac.local")


# ---------------------------------------------------------------------------
# `gh` helpers
# ---------------------------------------------------------------------------

def _gh(args: list[str]) -> str:
    """Run gh, capture stdout, raise on failure."""
    try:
        cp = subprocess.run(
            ["gh", *args],
            check=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "GitHub CLI ('gh') is not installed or not on PATH.\n"
            "Install it from https://cli.github.com/ and run `gh auth login`,\n"
            "or use --from-file to triage a local issue JSON instead."
        ) from exc
    if cp.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} failed (rc={cp.returncode}):\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    return cp.stdout


def fetch_one_issue(repo: str, number: int) -> dict:
    # `gh issue view --json …` gives us a flat object we can pretty-much
    # treat as a GH webhook `issue` payload after a tiny key fixup.
    raw = _gh([
        "issue", "view", str(number), "--repo", repo, "--json",
        "number,title,body,author,labels,url,createdAt,updatedAt,state",
    ])
    data = json.loads(raw)
    return _normalize_issue(data)


def fetch_recent_issues(repo: str, limit: int) -> list[dict]:
    raw = _gh([
        "issue", "list", "--repo", repo, "--limit", str(limit),
        "--state", "open", "--json",
        "number,title,body,author,labels,url,createdAt,updatedAt,state",
    ])
    return [_normalize_issue(d) for d in json.loads(raw)]


def _normalize_issue(d: dict) -> dict:
    """Reshape `gh`'s output to match the webhook `issue` payload shape."""
    author = d.get("author") or {}
    return {
        "number": d.get("number"),
        "title": d.get("title"),
        "body": d.get("body") or "",
        "html_url": d.get("url"),
        "user": {
            "login": author.get("login") or author.get("name") or "unknown",
            # gh issue view doesn't return a type field; assume User unless [bot].
            "type": "Bot" if str(author.get("login", "")).endswith("[bot]") else "User",
        },
        "labels": [{"name": l["name"]} for l in (d.get("labels") or [])],
        "created_at": d.get("createdAt"),
        "updated_at": d.get("updatedAt"),
        "state": d.get("state"),
    }


# ---------------------------------------------------------------------------
# Agent invocation
# ---------------------------------------------------------------------------

def _python_for_agent() -> str:
    """Pick the right Python interpreter for invoking agent.py.

    Prefers the project venv; falls back to the current interpreter.
    """
    if VENV_PY.exists():
        return str(VENV_PY)
    return sys.executable


def run_agent(issue: dict, *, mode: str = "shadow", use_mock: bool = True) -> dict:
    """Invoke `agent.py` against an issue payload and return the debug dict."""
    tmp = HERE / "state" / f"_issue-{issue['number']}.json"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_text(json.dumps(issue, indent=2), encoding="utf-8")

    cmd = [
        _python_for_agent(), "agent.py",
        "--mode", mode,
        "--issue", str(tmp),
        "--repo", DEFAULT_REPO,
    ]
    if use_mock:
        cmd.append("--use-mock-llm")

    env = os.environ.copy()
    # Make sure the runner doesn't inherit fake GH env from a CI shell:
    env.pop("GITHUB_EVENT_PATH", None)
    # Force the child to emit UTF-8 on stdout/stderr (agent.py prints em-dashes
    # in dry-run mode and Windows defaults to cp1252).
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    log.info("Running agent for issue #%s (mode=%s, mock=%s)",
             issue["number"], mode, use_mock)
    cp = subprocess.run(
        cmd, cwd=AGENT_DIR, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    log.debug("agent stdout:\n%s", cp.stdout)
    if cp.stderr and cp.stderr.strip():
        log.debug("agent stderr:\n%s", cp.stderr)

    debug_path = AGENT_DIR / "triage-debug.json"
    if not debug_path.exists():
        raise RuntimeError(
            f"agent.py did not produce triage-debug.json (rc={cp.returncode})\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    debug = json.loads(debug_path.read_text(encoding="utf-8"))

    # Move the artifact out so back-to-back runs don't smear results.
    rotated = HERE / "state" / f"triage-debug-{issue['number']}.json"
    rotated.write_text(json.dumps(debug, indent=2), encoding="utf-8")

    if cp.returncode != 0 and debug.get("status") != "ok":
        log.warning("agent rc=%s status=%s", cp.returncode, debug.get("status"))

    return debug


# ---------------------------------------------------------------------------
# Email rendering & sending
# ---------------------------------------------------------------------------

def _render_email(issue: dict, debug: dict) -> tuple[str, str]:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    tpl = env.get_template("per_issue_email.html.j2")
    excerpt = (issue.get("body") or "").strip()
    if len(excerpt) > 2000:
        excerpt = excerpt[:2000] + "\n… (truncated)"
    html = tpl.render(
        issue=issue,
        triage=debug.get("triage") or {},
        reply_markdown=debug.get("reply_markdown") or "(no reply rendered)",
        issue_body_excerpt=excerpt,
        run={
            "mode": debug.get("mode"),
            "use_mock_llm": debug.get("use_mock_llm"),
            "dry_run": debug.get("dry_run"),
            "runner_host": socket.gethostname(),
            "started_at": debug.get("started_at"),
            "elapsed_seconds": debug.get("elapsed_seconds"),
            "ado": debug.get("ado"),
        },
    )
    triage = debug.get("triage") or {}
    cat = (triage.get("category") or "issue").upper()
    sev = triage.get("severity", "?")
    subject = (
        f"[WAC triage demo] {cat} sev={sev} — #{issue['number']} "
        f"{(issue.get('title') or '')[:80]}"
    )
    return subject, html


def email_report(issue: dict, debug: dict, *, to: str, no_email: bool = False) -> dict:
    subject, html = _render_email(issue, debug)
    if no_email:
        out = HERE / "outbox"
        out.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = out / f"{stamp}__issue-{issue['number']}.html"
        path.write_text(html, encoding="utf-8")
        return {"sent": False, "transport": "no-email", "detail": str(path)}
    result = email_outlook.send_html(to, subject, html)
    return {"sent": result.sent, "transport": result.transport, "detail": result.detail}


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------

def cmd_once(args) -> int:
    issues = _resolve_issues(args)
    if not issues:
        print("No issues to process.", file=sys.stderr)
        return 1
    rc_overall = 0
    for issue in issues:
        rc_overall |= _process_one(issue, args)
    return rc_overall


def cmd_loop(args) -> int:
    log.info("Starting poll loop (every %ss) — Ctrl+C to stop", args.poll_seconds)
    args._loop_started_at = datetime.now(UTC).isoformat()
    try:
        while True:
            try:
                issues = fetch_recent_issues(args.repo, args.recent)
            except Exception as exc:
                log.warning("Issue fetch failed: %s", exc)
                time.sleep(args.poll_seconds)
                continue

            new = [i for i in issues if not state.is_seen(i["number"])]
            log.info("Poll: %s open, %s unseen", len(issues), len(new))
            for issue in new:
                _process_one(issue, args)
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        log.info("Loop interrupted by user.")
        return 0


def cmd_status(args) -> int:
    print("Local triage runner — state:")
    seen = list(state.list_seen())
    if not seen:
        print("  (no issues processed yet)")
    else:
        for n, when in seen:
            print(f"  #{n}  last_processed={when}")
    pid_file = HERE / "state" / "loop.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            print(f"\nBackground loop PID file present: {pid_file} (pid={pid})")
        except Exception:
            print(f"\nBackground loop PID file present but unreadable: {pid_file}")
    else:
        print("\nNo background loop PID file. (Loop is not running, or was started elsewhere.)")
    return 0


def cmd_reset(args) -> int:
    state.reset()
    print("Reset 'seen' state.")
    return 0


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _resolve_issues(args) -> list[dict]:
    if args.from_file:
        return [json.loads(Path(args.from_file).read_text(encoding="utf-8"))]
    if args.issue:
        return [fetch_one_issue(args.repo, args.issue)]
    if args.recent:
        return fetch_recent_issues(args.repo, args.recent)
    return []


def _process_one(issue: dict, args) -> int:
    n = issue["number"]
    log.info("Processing issue #%s: %s", n, (issue.get("title") or "")[:80])
    try:
        debug = run_agent(issue, mode=args.agent_mode, use_mock=args.use_mock)
    except Exception as exc:
        log.exception("Agent invocation failed for #%s", n)
        return 1
    if debug.get("status") != "ok":
        log.warning("Agent reported status=%s for #%s; emailing anyway",
                    debug.get("status"), n)
    if args.print_only:
        print(json.dumps(debug.get("triage", {}), indent=2))
    res = email_report(issue, debug, to=args.to, no_email=args.no_email)
    log.info("Email: sent=%s via=%s detail=%s",
             res["sent"], res["transport"], res["detail"])
    state.mark_seen(n, datetime.now(UTC).isoformat())
    return 0


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="WAC Feedback Triage local demo runner.")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--to", default=DEFAULT_TO,
                   help="Email recipient (default: trungtran@microsoft.com)")
    p.add_argument("--no-email", action="store_true",
                   help="Don't send email; just write the report to local/outbox/")
    p.add_argument("--print-only", action="store_true",
                   help="Also print the triage JSON to stdout.")
    p.add_argument("--use-mock", action="store_true", default=True,
                   help="Use the heuristic mock LLM (default; faster, no creds).")
    p.add_argument("--no-mock", dest="use_mock", action="store_false",
                   help="Disable the mock and use real Azure OpenAI "
                        "(requires AZURE_OPENAI_* env vars).")
    p.add_argument("--agent-mode", default="shadow", choices=["live", "shadow", "gated"],
                   help="Mode flag passed to agent.py (default: shadow — no writes).")
    p.add_argument("-v", "--verbose", action="store_true")

    sub = p.add_subparsers(dest="cmd")

    s_once = sub.add_parser("once", help="Triage one or a small set of issues, then exit.")
    s_once.add_argument("--issue", type=int, help="Issue number to triage.")
    s_once.add_argument("--recent", type=int,
                        help="Triage the N most recently updated open issues.")
    s_once.add_argument("--from-file", help="Load issue JSON from a local file.")
    s_once.set_defaults(func=cmd_once)

    s_loop = sub.add_parser("loop", help="Continuously poll the repo for new issues.")
    s_loop.add_argument("--poll-seconds", type=int, default=60)
    s_loop.add_argument("--recent", type=int, default=10,
                        help="How many recent open issues to inspect per poll.")
    s_loop.set_defaults(func=cmd_loop)

    s_status = sub.add_parser("status", help="Show local runner state.")
    s_status.set_defaults(func=cmd_status)

    s_reset = sub.add_parser("reset", help="Forget all previously-seen issues.")
    s_reset.set_defaults(func=cmd_reset)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
