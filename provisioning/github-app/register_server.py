"""
GitHub App Manifest registration helper.

Implements the documented "Creating a GitHub App from a manifest" flow:
https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest

Flow:
  1. We spin up a localhost HTTP server on port 54017.
  2. Browser opens http://localhost:54017/  -> serves an auto-submitting form
     that POSTs the manifest to https://github.com/settings/apps/new?state=<csrf>.
  3. GitHub shows the user a "Create GitHub App" confirmation page.
  4. After they click Create, GitHub redirects back to
     http://localhost:54017/callback?code=<temp>&state=<csrf>.
  5. We POST that code to https://api.github.com/app-manifests/<code>/conversions
     and receive: id, slug, pem, webhook_secret, client_id, client_secret, html_url.
  6. We persist the credentials to ../state/github-app.json.
  7. We display a final page with the installation URL so the user can install
     the app on their repo with one click.

This script only touches localhost + GitHub's documented public API; no third-party
servers, no credentials leave the box.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import secrets
import socketserver
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

PORT = 54017
STATE_TOKEN = secrets.token_urlsafe(24)

ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "manifest.json"
OUTPUT_DIR = ROOT.parent / "state"
OUTPUT_PATH = OUTPUT_DIR / "github-app.json"


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _render_index(manifest: dict) -> bytes:
    manifest_json = json.dumps(manifest)
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Register wac-feedback-bot</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; max-width: 720px;
          margin: 3em auto; padding: 0 1em; color: #24292f; line-height: 1.5; }}
  h1   {{ font-size: 1.6em; }}
  pre  {{ background: #f6f8fa; padding: 1em; border-radius: 6px;
          overflow-x: auto; font-size: 0.85em; }}
  .big-btn {{ display: block; background: #1f883d; color: white; border: 0;
              padding: 1em 1.5em; border-radius: 8px; font-size: 1.15em;
              cursor: pointer; width: 100%; margin: 1em 0; font-weight: 600; }}
  .big-btn:hover {{ background: #1a7f37; }}
  .steps {{ background: #fff8c5; border: 1px solid #d4a72c; border-radius: 6px;
            padding: 1em 1.5em; margin: 1em 0; }}
  ol li {{ margin: 0.4em 0; }}
</style></head><body>
<h1>Step 1 of 4: Register the <code>wac-feedback-bot</code> GitHub App</h1>

<div class="steps">
  <strong>What's about to happen:</strong>
  <ol>
    <li>You click the green button below.</li>
    <li>GitHub opens a confirmation page showing the app name and permissions.</li>
    <li>You click <strong>Create GitHub App</strong> on that GitHub page.</li>
    <li>GitHub redirects you back here. We write the credentials to a local file
        and you're done with step 1.</li>
  </ol>
</div>

<form action="https://github.com/settings/apps/new?state={STATE_TOKEN}"
      method="post">
  <input type="hidden" name="manifest" value='{manifest_json.replace("'", "&apos;")}'/>
  <button type="submit" class="big-btn">
    Send manifest to GitHub &rarr;
  </button>
</form>

<details><summary>What's in the manifest being sent?</summary>
<pre>{json.dumps(manifest, indent=2)}</pre>
</details>

<p style="color:#656d76;font-size:0.9em;margin-top:2em">
  Server is listening on <code>localhost:{PORT}</code>. Close this tab to cancel.
</p>
</body></html>
"""
    return html.encode("utf-8")


def _render_success(install_url: str, slug: str) -> bytes:
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>App created</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; max-width: 640px;
          margin: 4em auto; padding: 0 1em; color: #24292f; }}
  h1   {{ color: #1f883d; }}
  .card {{ background: #ddf4ff; border: 1px solid #54aeff; padding: 1em;
            border-radius: 6px; margin: 1em 0; }}
  a.btn {{ display: inline-block; background: #1f883d; color: white;
            padding: 0.6em 1.2em; border-radius: 6px; text-decoration: none; }}
</style></head><body>
<h1>App <code>{slug}</code> created!</h1>
<p>Credentials written to <code>provisioning/state/github-app.json</code>.</p>
<div class="card">
  <p><strong>One more click:</strong> install the App on your repository so
     it can read/write issues.</p>
  <p><a class="btn" href="{install_url}" target="_blank">
       Install on Windows-Admin-Center-Ideas-and-Feedback</a></p>
</div>
<p>You can close this tab when done.</p>
</body></html>
"""
    return html.encode("utf-8")


def _render_error(message: str) -> bytes:
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Error</title></head><body>
<h1 style="color:#cf222e">Something went wrong</h1><pre>{message}</pre>
</body></html>
"""
    return html.encode("utf-8")


class Handler(http.server.BaseHTTPRequestHandler):
    manifest: dict = {}
    done: bool = False
    result: dict | None = None

    def log_message(self, fmt, *args):
        # Quiet default logging
        sys.stderr.write("[register_server] " + (fmt % args) + "\n")

    def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (HTTP method name)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index", "/index.html"):
            self._send(200, _render_index(self.manifest))
            return

        if parsed.path == "/callback":
            params = urllib.parse.parse_qs(parsed.query)
            state = (params.get("state") or [""])[0]
            code = (params.get("code") or [""])[0]
            if state != STATE_TOKEN:
                self._send(400, _render_error("state mismatch — possible CSRF; aborting."))
                return
            if not code:
                self._send(400, _render_error("Missing code in callback."))
                return

            try:
                payload = _exchange_code_for_app(code)
            except Exception as exc:  # noqa: BLE001
                self._send(500, _render_error(f"GitHub API call failed: {exc!r}"))
                return

            _persist(payload)
            Handler.result = payload
            install_url = f"{payload['html_url']}/installations/new"
            self._send(200, _render_success(install_url, payload.get("slug", "wac-feedback-bot")))
            Handler.done = True
            return

        self._send(404, b"not found", "text/plain")


def _exchange_code_for_app(code: str) -> dict:
    url = f"https://api.github.com/app-manifests/{code}/conversions"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "wac-feedback-bot-registration/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        body = resp.read().decode("utf-8")
    return json.loads(body)


def _persist(payload: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    slim = {
        "app_id": payload["id"],
        "slug": payload["slug"],
        "name": payload["name"],
        "html_url": payload["html_url"],
        "client_id": payload.get("client_id"),
        "client_secret": payload.get("client_secret"),
        "webhook_secret": payload.get("webhook_secret"),
        "pem": payload["pem"],
    }
    OUTPUT_PATH.write_text(json.dumps(slim, indent=2), encoding="utf-8")
    os.chmod(OUTPUT_PATH, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not auto-open the browser.")
    args = parser.parse_args()

    Handler.manifest = _load_manifest()

    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        url = f"http://localhost:{PORT}/"
        print(f"[register_server] Listening at {url}")
        print(f"[register_server] State token: {STATE_TOKEN[:8]}...")
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001
                print(f"[register_server] Open {url} manually.")
        print("[register_server] Waiting for GitHub callback...")
        while not Handler.done:
            httpd.handle_request()
        print(f"[register_server] Done. Credentials written to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
