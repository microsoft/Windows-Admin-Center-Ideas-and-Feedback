"""GitHub App authentication and minimal REST helpers.

The triage workflows authenticate as the `wac-feedback-bot` GitHub App. This module
mints a short-lived (10 min) JWT, swaps it for an installation token, and exposes
small wrappers around the Issues / Reactions / Search endpoints we use.

Why not use `actions/create-github-app-token` only?
  The workflow does call that action to mint the installation token, but we also
  use these helpers in `digest.py` and `ado_sync.py` which need to enumerate
  issues, parse comments, and post updates. Centralizing the HTTP plumbing keeps
  the call sites tidy.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable

import jwt
import requests

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
ACCEPT = "application/vnd.github+json"
API_VERSION = "2022-11-28"

# Marker we embed in every triage comment so we can detect prior triage runs.
TRIAGE_MARKER = "<!-- triaged-by: wac-feedback-bot -->"


class GitHubError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"GitHub {status} on {url}: {body[:300]}")
        self.status = status
        self.body = body
        self.url = url


@dataclass
class InstallationToken:
    token: str
    expires_at: str  # ISO timestamp from GitHub


def _make_app_jwt(app_id: str, private_key: str) -> str:
    now = int(time.time())
    payload = {"iat": now - 30, "exp": now + 9 * 60, "iss": str(app_id)}
    return jwt.encode(payload, private_key, algorithm="RS256")


class GitHubAppClient:
    """Authenticates as the App and exposes installation-scoped REST calls.

    Two modes:
      1. **Pre-minted token** (preferred in workflows): pass `installation_token`
         directly. This is what `actions/create-github-app-token` produces.
      2. **Mint from App credentials**: pass `app_id` + `private_key`; the client
         will discover the installation on the repo and mint a token itself.
    """

    def __init__(
        self,
        *,
        installation_token: str | None = None,
        app_id: str | None = None,
        private_key: str | None = None,
        repo: str | None = None,
        session: requests.Session | None = None,
    ):
        self._session = session or requests.Session()
        self._repo = repo
        self._token = installation_token
        self._app_id = app_id
        self._private_key = private_key
        if not self._token and not (self._app_id and self._private_key and self._repo):
            raise ValueError(
                "Provide installation_token OR (app_id + private_key + repo)."
            )

    # ---- auth ----
    def _ensure_token(self) -> str:
        if self._token:
            return self._token
        assert self._app_id and self._private_key and self._repo
        app_jwt = _make_app_jwt(self._app_id, self._private_key)
        owner, name = self._repo.split("/", 1)
        url = f"{GITHUB_API}/repos/{owner}/{name}/installation"
        r = self._session.get(url, headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": ACCEPT,
            "X-GitHub-Api-Version": API_VERSION,
        }, timeout=15)
        if r.status_code >= 300:
            raise GitHubError(r.status_code, r.text, url)
        inst_id = r.json()["id"]

        url2 = f"{GITHUB_API}/app/installations/{inst_id}/access_tokens"
        r2 = self._session.post(url2, headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": ACCEPT,
            "X-GitHub-Api-Version": API_VERSION,
        }, timeout=15)
        if r2.status_code >= 300:
            raise GitHubError(r2.status_code, r2.text, url2)
        self._token = r2.json()["token"]
        return self._token

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = path if path.startswith("http") else f"{GITHUB_API}{path}"
        headers = kwargs.pop("headers", {}) or {}
        headers.setdefault("Authorization", f"Bearer {self._ensure_token()}")
        headers.setdefault("Accept", ACCEPT)
        headers.setdefault("X-GitHub-Api-Version", API_VERSION)
        headers.setdefault("User-Agent", "wac-feedback-bot")
        r = self._session.request(method, url, headers=headers, timeout=30, **kwargs)
        if r.status_code >= 300:
            raise GitHubError(r.status_code, r.text, url)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # ---- issue operations ----
    def get_issue(self, repo: str, number: int) -> dict:
        return self._request("GET", f"/repos/{repo}/issues/{number}")

    def list_issue_comments(self, repo: str, number: int) -> list[dict]:
        items: list[dict] = []
        page = 1
        while True:
            chunk = self._request(
                "GET", f"/repos/{repo}/issues/{number}/comments",
                params={"per_page": 100, "page": page},
            ) or []
            items.extend(chunk)
            if len(chunk) < 100:
                return items
            page += 1

    def post_comment(self, repo: str, number: int, body: str) -> dict:
        return self._request(
            "POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body}
        )

    def add_labels(self, repo: str, number: int, labels: Iterable[str]) -> list[dict]:
        payload = {"labels": list(labels)}
        return self._request("POST", f"/repos/{repo}/issues/{number}/labels", json=payload)

    def list_open_issues_with_label(self, repo: str, label: str) -> list[dict]:
        items: list[dict] = []
        page = 1
        while True:
            chunk = self._request(
                "GET", f"/repos/{repo}/issues",
                params={
                    "state": "open", "labels": label,
                    "per_page": 100, "page": page,
                },
            ) or []
            # /issues returns PRs too; filter them out.
            chunk = [i for i in chunk if "pull_request" not in i]
            items.extend(chunk)
            if len(chunk) < 100:
                return items
            page += 1

    def list_recent_issues(self, repo: str, since_iso: str) -> list[dict]:
        items: list[dict] = []
        page = 1
        while True:
            chunk = self._request(
                "GET", f"/repos/{repo}/issues",
                params={
                    "state": "all", "since": since_iso,
                    "per_page": 100, "page": page,
                    "sort": "created", "direction": "desc",
                },
            ) or []
            chunk = [i for i in chunk if "pull_request" not in i]
            items.extend(chunk)
            if len(chunk) < 100:
                return items
            page += 1


def client_from_env(repo: str | None = None) -> GitHubAppClient:
    """Build a client from standard env vars.

    Preference order:
      1. `GITHUB_TOKEN` (injected by `actions/create-github-app-token`)
      2. `WAC_BOT_APP_ID` + `WAC_BOT_APP_PRIVATE_KEY` + repo
    """
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return GitHubAppClient(installation_token=token, repo=repo)
    app_id = os.environ.get("WAC_BOT_APP_ID")
    pem = os.environ.get("WAC_BOT_APP_PRIVATE_KEY")
    if app_id and pem and repo:
        return GitHubAppClient(app_id=app_id, private_key=pem, repo=repo)
    raise RuntimeError(
        "Cannot build GitHubAppClient: set GITHUB_TOKEN or "
        "WAC_BOT_APP_ID+WAC_BOT_APP_PRIVATE_KEY (+repo)."
    )


def has_been_triaged(comments: list[dict]) -> bool:
    return any(TRIAGE_MARKER in (c.get("body") or "") for c in comments)


def extract_ado_id(comments: list[dict]) -> int | None:
    """Extract the ADO work item ID from the hidden marker in any prior comment."""
    import re
    for c in comments:
        body = c.get("body") or ""
        m = re.search(r"<!--\s*ado-id:\s*(\d+)\s*-->", body)
        if m:
            return int(m.group(1))
    return None
