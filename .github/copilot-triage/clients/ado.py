"""Azure DevOps REST client for work item creation and state polling.

Used by:
  * agent.py             — creates Bug/Feature work items on first triage.
  * ado_sync.py          — polls state for existing linked work items.
  * digest.py            — counts work items filed in the last 7 days.
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from typing import Iterable

import requests

log = logging.getLogger(__name__)

DEFAULT_ORG = "microsoft"
DEFAULT_PROJECT = "OS"
DEFAULT_AREA_PATH = (
    r"OS\Core\SPARC\SIX - Server, Intelligence, and Experiences\Enterprise Windows Admin Center"
)
API_VERSION = "7.1"


class ADOError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"ADO {status} on {url}: {body[:400]}")
        self.status = status
        self.body = body
        self.url = url


@dataclass
class WorkItemRef:
    id: int
    url: str
    state: str | None = None
    title: str | None = None
    type: str | None = None


def _auth_header(pat: str) -> dict[str, str]:
    encoded = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


class AzureDevOpsClient:
    def __init__(
        self,
        *,
        pat: str | None = None,
        organization: str | None = None,
        project: str | None = None,
        area_path: str | None = None,
        session: requests.Session | None = None,
    ):
        # Allow ops to retarget org/project/area-path via repo variables
        # (ADO_ORG, ADO_PROJECT, ADO_AREA_PATH) without code changes.
        self._pat = pat or os.environ["ADO_PAT"]
        self._org = organization or os.environ.get("ADO_ORG") or DEFAULT_ORG
        self._project = project or os.environ.get("ADO_PROJECT") or DEFAULT_PROJECT
        self._area = area_path or os.environ.get("ADO_AREA_PATH") or DEFAULT_AREA_PATH
        self._s = session or requests.Session()

    @property
    def base(self) -> str:
        return f"https://dev.azure.com/{self._org}/{self._project}"

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        return {
            **_auth_header(self._pat),
            "Accept": "application/json",
            "Content-Type": content_type,
            "User-Agent": "wac-feedback-bot",
        }

    # ---- create ----
    def create_work_item(
        self,
        *,
        category: str,            # "bug" or "feature"
        title: str,
        description_html: str,
        github_issue_url: str,
        tags: Iterable[str] = (),
    ) -> WorkItemRef:
        wit = "Bug" if category == "bug" else "Feature"
        url = (
            f"{self.base}/_apis/wit/workitems/${wit}"
            f"?api-version={API_VERSION}"
        )

        ops: list[dict] = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.AreaPath", "value": self._area},
            {"op": "add", "path": "/fields/System.Tags",
             "value": "; ".join({"github-triage", "wac-feedback", *tags})},
            {
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "Hyperlink",
                    "url": github_issue_url,
                    "attributes": {"comment": "Source GitHub issue"},
                },
            },
        ]

        # Bugs use Repro Steps; Features use Description.
        body_with_link = (
            f"{description_html}"
            f"<p><b>GitHub issue:</b> <a href=\"{github_issue_url}\">{github_issue_url}</a></p>"
        )
        if wit == "Bug":
            ops.append({"op": "add",
                        "path": "/fields/Microsoft.VSTS.TCM.ReproSteps",
                        "value": body_with_link})
        else:
            ops.append({"op": "add", "path": "/fields/System.Description",
                        "value": body_with_link})

        r = self._s.post(
            url,
            headers=self._headers("application/json-patch+json"),
            json=ops,
            timeout=30,
        )
        if r.status_code >= 300:
            raise ADOError(r.status_code, r.text, url)
        data = r.json()
        return WorkItemRef(
            id=int(data["id"]),
            url=data.get("_links", {}).get("html", {}).get("href")
                or f"https://dev.azure.com/{self._org}/{self._project}/_workitems/edit/{data['id']}",
            state=(data.get("fields") or {}).get("System.State"),
            title=(data.get("fields") or {}).get("System.Title"),
            type=(data.get("fields") or {}).get("System.WorkItemType"),
        )

    # ---- read ----
    def get_work_item(self, work_item_id: int) -> WorkItemRef:
        url = (
            f"https://dev.azure.com/{self._org}/_apis/wit/workitems/{work_item_id}"
            f"?api-version={API_VERSION}"
        )
        r = self._s.get(url, headers=self._headers(), timeout=20)
        if r.status_code >= 300:
            raise ADOError(r.status_code, r.text, url)
        data = r.json()
        return WorkItemRef(
            id=int(data["id"]),
            url=data.get("_links", {}).get("html", {}).get("href")
                or f"https://dev.azure.com/{self._org}/{self._project}/_workitems/edit/{data['id']}",
            state=(data.get("fields") or {}).get("System.State"),
            title=(data.get("fields") or {}).get("System.Title"),
            type=(data.get("fields") or {}).get("System.WorkItemType"),
        )

    def query_recent_by_tag(self, tag: str, since_iso: str) -> list[WorkItemRef]:
        """Return work items in the configured area path created since `since_iso`.

        Used by the weekly digest. `since_iso` should be `YYYY-MM-DDTHH:MM:SSZ`.
        """
        url = (
            f"{self.base}/_apis/wit/wiql?api-version={API_VERSION}"
        )
        wiql = {
            "query": (
                "SELECT [System.Id], [System.Title], [System.State], "
                "[System.WorkItemType] FROM WorkItems "
                f"WHERE [System.AreaPath] UNDER '{self._area}' "
                f"AND [System.Tags] CONTAINS '{tag}' "
                f"AND [System.CreatedDate] >= '{since_iso}' "
                "ORDER BY [System.CreatedDate] DESC"
            )
        }
        r = self._s.post(url, headers=self._headers(), json=wiql, timeout=30)
        if r.status_code >= 300:
            raise ADOError(r.status_code, r.text, url)
        ids = [int(w["id"]) for w in r.json().get("workItems", [])]
        if not ids:
            return []
        # Batch fetch
        url2 = (
            f"https://dev.azure.com/{self._org}/_apis/wit/workitemsbatch"
            f"?api-version={API_VERSION}"
        )
        out: list[WorkItemRef] = []
        for i in range(0, len(ids), 200):
            chunk = ids[i:i + 200]
            r2 = self._s.post(url2, headers=self._headers(), json={
                "ids": chunk,
                "fields": ["System.Id", "System.Title", "System.State",
                           "System.WorkItemType"],
            }, timeout=30)
            if r2.status_code >= 300:
                raise ADOError(r2.status_code, r2.text, url2)
            for w in r2.json().get("value", []):
                f = w.get("fields") or {}
                out.append(WorkItemRef(
                    id=int(w["id"]),
                    url=f"https://dev.azure.com/{self._org}/{self._project}/_workitems/edit/{w['id']}",
                    state=f.get("System.State"),
                    title=f.get("System.Title"),
                    type=f.get("System.WorkItemType"),
                ))
        return out
