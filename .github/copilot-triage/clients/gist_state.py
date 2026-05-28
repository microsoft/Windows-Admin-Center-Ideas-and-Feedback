"""Gist-backed key/value store for the ADO state cache.

Uses a single secret gist containing one file `ado-state.json`. The contents are
a JSON object mapping `"<github_issue_number>"` -> `{ "ado_id": ..., "state": ... }`.

Required env:
  ADO_STATE_GIST_ID    — the gist id
  ADO_STATE_GIST_TOKEN — a fine-grained PAT with Gists R/W only
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

FILE_NAME = "ado-state.json"


class GistStateError(RuntimeError):
    pass


class GistState:
    def __init__(self, *, gist_id: str | None = None, token: str | None = None,
                 session: requests.Session | None = None):
        self._gist_id = gist_id or os.environ["ADO_STATE_GIST_ID"]
        self._token = token or os.environ["ADO_STATE_GIST_TOKEN"]
        self._s = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "wac-feedback-bot",
        }

    def load(self) -> dict[str, Any]:
        url = f"https://api.github.com/gists/{self._gist_id}"
        r = self._s.get(url, headers=self._headers(), timeout=15)
        if r.status_code >= 300:
            raise GistStateError(f"GET gist failed {r.status_code}: {r.text[:300]}")
        files = r.json().get("files") or {}
        f = files.get(FILE_NAME)
        if not f:
            return {}
        # If file content is truncated, fetch raw_url
        content = f.get("content")
        if f.get("truncated") and f.get("raw_url"):
            r2 = self._s.get(f["raw_url"], timeout=15)
            if r2.status_code >= 300:
                raise GistStateError(f"GET raw gist failed {r2.status_code}")
            content = r2.text
        try:
            return json.loads(content or "{}")
        except json.JSONDecodeError:
            log.warning("Gist state was not valid JSON; resetting to empty.")
            return {}

    def save(self, state: dict[str, Any]) -> None:
        url = f"https://api.github.com/gists/{self._gist_id}"
        body = {"files": {FILE_NAME: {"content": json.dumps(state, indent=2,
                                                            sort_keys=True)}}}
        r = self._s.patch(url, headers=self._headers(), json=body, timeout=20)
        if r.status_code >= 300:
            raise GistStateError(f"PATCH gist failed {r.status_code}: {r.text[:300]}")
