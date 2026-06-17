"""Azure Boards (Azure DevOps work items) issue-source adapter.

Runs a WIQL query to discover work-item ids, fetches the work items, and
normalizes them into the common issue shape. Supports write-back of state
(``System.State`` via a JSON-Patch) and adding comments.

Self-contained: no shared base class / sibling imports (``issue_sources`` is a
PEP-420 namespace package).

Security / robustness invariants mirror the other adapters: env-only PAT used as
HTTP Basic ``:PAT``, literal-host SSRF block (no DNS) on ``base_url``, injectable
+ time-bound HTTP transport, and a ``health()`` that never raises.
"""

from __future__ import annotations

import base64
import ipaddress
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

SYSTEM = "azure_boards"

_DEFAULT_BASE_URL = "https://dev.azure.com"
_DEFAULT_TIMEOUT = 30.0
_API_VERSION = "7.0"

_BLOCKED_HOST_LITERALS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
    }
)


def _reject_internal_base_url(base_url: str) -> None:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme for base_url: {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise ValueError("base_url has no host")
    if host.lower() in _BLOCKED_HOST_LITERALS:
        raise ValueError(f"internal/blocked host in base_url: {host!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    ):
        raise ValueError(f"internal/blocked IP in base_url: {host!r}")


def _basic_auth(pat: str) -> str:
    raw = f":{pat}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


class AzureBoardsIssueSource:
    """Azure DevOps work items → normalized issues, with state + comment write-back."""

    SYSTEM = SYSTEM

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = str(config.get("name") or SYSTEM)
        self._base_url = str(config.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")
        _reject_internal_base_url(self._base_url)

        self._org = str(config.get("org") or config.get("organization") or "")
        self._project = str(config.get("project") or "")

        pat_env = str(config.get("pat_env") or "AZURE_DEVOPS_PAT")
        self._pat = os.environ.get(pat_env, "")

        self._wiql = str(
            config.get("wiql")
            or "SELECT [System.Id] FROM WorkItems "
            "WHERE [System.State] <> 'Closed' ORDER BY [System.ChangedDate] DESC"
        )

        self._timeout = float(config.get("timeout") or _DEFAULT_TIMEOUT)
        self._transport = transport

    # -- internal helpers -------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
            headers={"Authorization": _basic_auth(self._pat)},
        )

    def _project_path(self) -> str:
        return f"/{self._org}/{self._project}"

    def _fetch_work_items(self, client: httpx.Client, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        id_csv = ",".join(str(i) for i in ids)
        resp = client.get(
            f"/{self._org}/_apis/wit/workitems",
            params={"ids": id_csv, "api-version": _API_VERSION, "$expand": "fields"},
        )
        resp.raise_for_status()
        items: list[dict[str, Any]] = resp.json().get("value", [])
        return items

    @staticmethod
    def _normalize_work_item(item: dict[str, Any]) -> dict[str, Any]:
        fields = item.get("fields", {})
        assigned = fields.get("System.AssignedTo")
        assignee = assigned.get("displayName") if isinstance(assigned, dict) else assigned
        tags_raw = fields.get("System.Tags") or ""
        labels = [t.strip() for t in tags_raw.split(";") if t.strip()]
        return {
            "external_id": str(item.get("id", "")),
            "source": SYSTEM,
            "title": str(fields.get("System.Title", "")),
            "description": str(fields.get("System.Description", "")),
            "status": str(fields.get("System.State", "")),
            "assignee": (str(assignee) if assignee else None),
            "labels": labels,
            "priority": fields.get("Microsoft.VSTS.Common.Priority"),
            "url": str(item.get("url", "")),
            "updated_ts": fields.get("System.ChangedDate"),
            "raw": item,
        }

    # -- public contract --------------------------------------------------

    def health(self) -> dict[str, Any]:
        try:
            with self._client() as client:
                resp = client.get(
                    f"{self._project_path()}/_apis/wit/fields",
                    params={"api-version": _API_VERSION, "$top": "1"},
                )
            if resp.status_code == 200:
                return {"ok": True, "detail": "azure boards reachable"}
            return {"ok": False, "detail": f"http {resp.status_code}"}
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def fetch_issues(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        spec = spec or {}
        wiql = str(spec.get("wiql") or self._wiql)
        with self._client() as client:
            wiql_resp = client.post(
                f"{self._project_path()}/_apis/wit/wiql",
                params={"api-version": _API_VERSION},
                json={"query": wiql},
            )
            wiql_resp.raise_for_status()
            ids = [int(row["id"]) for row in wiql_resp.json().get("workItems", [])]
            items = self._fetch_work_items(client, ids)
        return [self._normalize_work_item(item) for item in items]

    def update_status(
        self,
        external_id: str,
        status: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        with self._client() as client:
            patch = client.patch(
                f"{self._project_path()}/_apis/wit/workitems/{external_id}",
                params={"api-version": _API_VERSION},
                headers={"Content-Type": "application/json-patch+json"},
                json=[{"op": "add", "path": "/fields/System.State", "value": status}],
            )
            patch.raise_for_status()
            result["updated"] = patch.json()
            if comment:
                result["comment"] = self.add_comment(external_id, comment, _client=client)
        return result

    def add_comment(
        self,
        external_id: str,
        comment: str,
        *,
        _client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        def _post(client: httpx.Client) -> dict[str, Any]:
            resp = client.post(
                f"{self._project_path()}/_apis/wit/workItems/{external_id}/comments",
                params={"api-version": f"{_API_VERSION}-preview.3"},
                json={"text": comment},
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

        if _client is not None:
            return _post(_client)
        with self._client() as client:
            return _post(client)
