"""Asana issue-source adapter.

Reads tasks from an Asana project and normalizes them into the common issue
shape. Supports write-back of status (completing a task, or moving it to a
mapped section) and adding comments (stories).

Self-contained: no shared base class / sibling imports (``issue_sources`` is a
PEP-420 namespace package).

Security / robustness invariants mirror the other adapters: env-only Bearer
token, literal-host SSRF block (no DNS) on ``base_url``, injectable + time-bound
HTTP transport, and a ``health()`` that never raises.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

SYSTEM = "asana"

_DEFAULT_BASE_URL = "https://app.asana.com"
_DEFAULT_TIMEOUT = 30.0

_BLOCKED_HOST_LITERALS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
    }
)

# Fields requested so normalization has everything it needs in one round-trip.
_TASK_FIELDS = (
    "name,notes,completed,permalink_url,modified_at,"
    "assignee.name,memberships.section.name,tags.name"
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


class AsanaIssueSource:
    """Asana project tasks → normalized issues, with status + comment write-back."""

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

        self._project = str(config.get("project") or config.get("project_gid") or "")

        token_env = str(config.get("token_env") or "ASANA_TOKEN")
        self._token = os.environ.get(token_env, "")

        # status -> {"completed": bool} and/or {"section": gid} for write-back.
        raw_map = config.get("status_map") or {}
        self._status_map: dict[str, dict[str, Any]] = {
            str(k): dict(v) for k, v in raw_map.items()
        }

        self._timeout = float(config.get("timeout") or _DEFAULT_TIMEOUT)
        self._transport = transport

    # -- internal helpers -------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
            headers={"Authorization": f"Bearer {self._token}"},
        )

    @staticmethod
    def _normalize_task(task: dict[str, Any]) -> dict[str, Any]:
        assignee = task.get("assignee") or {}
        memberships = task.get("memberships") or []
        section_name = ""
        if memberships:
            section = memberships[0].get("section") or {}
            section_name = str(section.get("name", ""))
        status = section_name or ("completed" if task.get("completed") else "open")
        labels = [str(tag.get("name", "")) for tag in task.get("tags", []) if tag.get("name")]
        return {
            "external_id": str(task.get("gid", "")),
            "source": SYSTEM,
            "title": str(task.get("name", "")),
            "description": str(task.get("notes", "")),
            "status": status,
            "assignee": (str(assignee.get("name")) if assignee.get("name") else None),
            "labels": labels,
            "priority": None,
            "url": str(task.get("permalink_url", "")),
            "updated_ts": task.get("modified_at"),
            "raw": task,
        }

    # -- public contract --------------------------------------------------

    def health(self) -> dict[str, Any]:
        try:
            with self._client() as client:
                resp = client.get("/api/1.0/users/me", params={"opt_fields": "gid"})
            if resp.status_code == 200:
                return {"ok": True, "detail": "asana reachable"}
            return {"ok": False, "detail": f"http {resp.status_code}"}
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def fetch_issues(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        spec = spec or {}
        project = str(spec.get("project") or self._project)
        with self._client() as client:
            resp = client.get(
                f"/api/1.0/projects/{project}/tasks",
                params={"opt_fields": _TASK_FIELDS},
            )
            resp.raise_for_status()
            tasks = resp.json().get("data", [])
        return [self._normalize_task(task) for task in tasks]

    def update_status(
        self,
        external_id: str,
        status: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        mapping = self._status_map.get(status)
        if mapping is None:
            raise ValueError(f"no Asana mapping for status {status!r}")
        result: dict[str, Any] = {}
        with self._client() as client:
            if "completed" in mapping:
                resp = client.put(
                    f"/api/1.0/tasks/{external_id}",
                    json={"data": {"completed": bool(mapping["completed"])}},
                )
                resp.raise_for_status()
                result["updated"] = resp.json().get("data", {})
            if mapping.get("section"):
                section_resp = client.post(
                    f"/api/1.0/sections/{mapping['section']}/addTask",
                    json={"data": {"task": external_id}},
                )
                section_resp.raise_for_status()
                result["section_move"] = section_resp.json().get("data", {})
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
                f"/api/1.0/tasks/{external_id}/stories",
                json={"data": {"text": comment}},
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json().get("data", {})
            return data

        if _client is not None:
            return _post(_client)
        with self._client() as client:
            return _post(client)
