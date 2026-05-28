"""One-shot GitHub App creator for the WAC feedback triage bot.

Uses GitHub's App Manifest flow:
  https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest

What this does for you:
  1. Spins up a tiny localhost web server to act as the OAuth-style callback.
  2. Opens your browser to a page that auto-POSTs a pre-filled manifest to GitHub.
  3. After you click "Create GitHub App from manifest", GitHub redirects back here
     with a short-lived code.
  4. Exchanges that code for the app credentials (App ID, private key PEM,
     webhook secret, client ID/secret).
  5. Writes them to ./out/:
       - app-id.txt
       - wac-feedback-bot.private-key.pem
       - app-info.json
  6. Prints a one-click URL to install the app on your repo.

What you still have to do (GitHub does not let scripts do these):
  * Be signed into GitHub in the browser as someone with permission to create
    GitHub Apps on the target account/org.
  * Click "Create GitHub App from manifest" once.
  * Click "Install" on the install page once.

Usage:
  # On the microsoft org (you must be an org owner):
  python create_github_app.py --org microsoft

  # On a personal account (default):
  python create_github_app.py

  # On a different org:
  python create_github_app.py --org my-team-org

  # Custom output directory:
  python create_github_app.py --org microsoft --out C:\\secrets\\wac-bot

Requires Python 3.10+. Uses only the standard library.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 8765
CALLBACK_PATH = "/callback"
STATE_TOKEN = secrets.token_urlsafe(24)


def build_manifest(redirect_url: str, repo_url: str) -> dict:
    """Manifest reflecting the permissions documented in SETUP.md."""
    return {
        "name": "wac-feedback-bot",
        "url": repo_url,
        "description": (
            "AI triage agent for the Windows Admin Center Ideas & Feedback repo. "
            "Reads issues, posts acknowledgements, applies labels, and links Azure "
            "DevOps work items."
        ),
        "public": False,
        "redirect_url": redirect_url,
        "callback_urls": [redirect_url],
        "request_oauth_on_install": False,
        "setup_on_update": False,
        "hook_attributes": {
            "url": "https://example.invalid/no-webhook",
            "active": False,
        },
        "default_permissions": {
            "issues": "write",
            "metadata": "read",
            "contents": "read",
        },
        "default_events": [],
    }


class _State:
    code: str | None = None
    org: str | None = None
    manifest: dict | None = None
    error: str | None = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):  # silence default logging
        pass

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._serve_start_page()
        elif parsed.path == CALLBACK_PATH:
            self._handle_callback(parsed.query)
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_start_page(self) -> None:
        org = _State.org
        manifest = _State.manifest
        assert manifest is not None
        endpoint = (
            f"https://github.com/organizations/{org}/settings/apps/new"
            if org
            else "https://github.com/settings/apps/new"
        )
        # JSON inside an HTML attribute: escape single quotes.
        manifest_json = json.dumps(manifest).replace("'", "&#39;")
        target_label = f"organization <code>{org}</code>" if org else "your personal account"
        html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Create wac-feedback-bot</title>
  <style>
    body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 720px;
            margin: 40px auto; padding: 0 20px; color: #1f2328; }}
    .card {{ border: 1px solid #d0d7de; border-radius: 6px; padding: 24px; }}
    code {{ background: #f6f8fa; padding: 2px 6px; border-radius: 3px; }}
    button {{ font-size: 16px; padding: 10px 18px; cursor: pointer; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>Create the GitHub App</h2>
    <p>Submitting this form will send a pre-filled GitHub App manifest to
       GitHub for {target_label}. You'll see a confirmation page on
       github.com — click the green <b>Create GitHub App from manifest</b>
       button.</p>
    <p>If you are not signed in as someone with permission to create apps
       on this account, GitHub will show an error. In that case, re-run
       the script with <code>--org &lt;a-different-org&gt;</code> or omit
       <code>--org</code> to create on your personal account.</p>
    <form action="{endpoint}?state={STATE_TOKEN}" method="post" id="f">
      <input type="hidden" name="manifest" value='{manifest_json}'>
      <button type="submit">Continue to GitHub &rarr;</button>
    </form>
  </div>
  <script>
    // Auto-submit after a brief pause so the user sees what's happening.
    setTimeout(() => document.getElementById('f').submit(), 800);
  </script>
</body>
</html>"""
        self._send_html(200, html)

    def _handle_callback(self, query: str) -> None:
        params = urllib.parse.parse_qs(query)
        state = params.get("state", [""])[0]
        if state != STATE_TOKEN:
            _State.error = "state token mismatch"
            self._send_html(400, "<h2>State mismatch</h2><p>Close this tab and retry.</p>")
            return
        code = params.get("code", [""])[0]
        if not code:
            _State.error = "missing code"
            self._send_html(400, "<h2>Missing code</h2><p>Close this tab and retry.</p>")
            return
        _State.code = code
        self._send_html(
            200,
            "<h2>Got it!</h2><p>You can close this tab and return to the terminal.</p>"
            "<script>setTimeout(()=>window.close(),500)</script>",
        )

    def _send_html(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def exchange_code(code: str) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com/app-manifests/{code}/conversions",
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "wac-feedback-bot-installer",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create the wac-feedback-bot GitHub App.")
    ap.add_argument("--org", help="GitHub org to create the app under. Omit for personal account.")
    ap.add_argument(
        "--repo",
        default="microsoft/Windows-Admin-Center-Ideas-and-Feedback",
        help="The repo the app will eventually be installed on (used for the App URL).",
    )
    ap.add_argument("--out", default="./out", help="Where to write the App ID + private key.")
    ap.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Seconds to wait for the GitHub redirect before giving up (default: 600).",
    )
    args = ap.parse_args(argv)

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    redirect_url = f"http://localhost:{PORT}{CALLBACK_PATH}"
    _State.manifest = build_manifest(redirect_url, f"https://github.com/{args.repo}")
    _State.org = args.org

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://localhost:{PORT}/"
        target = f"org '{args.org}'" if args.org else "your personal account"
        print(f"Opening {url} in your browser...")
        print(f"Target: {target}")
        print("In the GitHub UI that opens, click 'Create GitHub App from manifest'.")
        print(f"Waiting up to {args.timeout}s for the redirect back to localhost...")
        webbrowser.open(url)

        deadline = time.monotonic() + args.timeout
        while _State.code is None and _State.error is None:
            if time.monotonic() > deadline:
                print("ERROR: timed out waiting for GitHub redirect.", file=sys.stderr)
                return 2
            time.sleep(0.5)

        if _State.error:
            print(f"ERROR: {_State.error}", file=sys.stderr)
            return 3

        print("Exchanging code for app credentials...")
        result = exchange_code(_State.code)  # type: ignore[arg-type]
    finally:
        server.shutdown()

    app_id = result["id"]
    pem = result["pem"]
    html_url = result["html_url"]

    pem_path = out / "wac-feedback-bot.private-key.pem"
    pem_path.write_text(pem, encoding="utf-8")
    (out / "app-id.txt").write_text(str(app_id), encoding="utf-8")
    info = {
        "app_id": app_id,
        "name": result.get("name"),
        "slug": result.get("slug"),
        "html_url": html_url,
        "owner": (result.get("owner") or {}).get("login"),
        "install_url": f"{html_url}/installations/new",
        "client_id": result.get("client_id"),
        "webhook_secret": result.get("webhook_secret"),
    }
    (out / "app-info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")

    print()
    print("==== SUCCESS ====")
    print(f"  App ID:        {app_id}")
    print(f"  App page:      {html_url}")
    print(f"  Install URL:   {info['install_url']}")
    print(f"  Files written: {out}")
    print()
    print("Next steps:")
    print(f"  1. Open the install URL above and install on '{args.repo}'.")
    print(f"  2. Push secrets to the repo:")
    print(f"     pwsh scripts/setup_secrets.ps1 -Repo {args.repo} \\")
    print(f"        -AppIdPath \"{(out / 'app-id.txt').as_posix()}\" \\")
    print(f"        -PemPath \"{pem_path.as_posix()}\"")
    print()
    print("Keep the .pem file safe — it is the only copy. Delete it after pushing the secret.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
