"""ClickUp issue-source adapter.

Self-contained, config-driven adapter that lists tasks from a ClickUp list via
the v2 REST API and normalizes them into the gludd issue dict shape. Supports
status write-back (``PUT /task/{id}``) and comment write-back
(``POST /task/{id}/comment``).

No dependency on sibling adapters, a base class, or package ``__init__`` side
effects. The default transport uses only the stdlib ``urllib``; tests inject a
mock transport.

Transport contract (injectable callable)::

    transport(method: str, url: str, headers: dict[str, str],
              json_body: dict | None, timeout: float) -> tuple[int, dict]

Returns ``(status_code, parsed_json_payload)``. The default transport never
uses ``shell=True`` and is strictly time-bound.
"""

from __future__ import annotations

import ipaddress
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import TypedDict, cast
from urllib.parse import urlsplit


class ClickUpStatus(TypedDict, total=False):
    """Status sub-object on a ClickUp task."""

    status: str


class ClickUpAssignee(TypedDict, total=False):
    """An assignee entry on a ClickUp task."""

    username: str
    email: str
    id: int | str


class ClickUpTag(TypedDict, total=False):
    """A tag entry on a ClickUp task."""

    name: str


class ClickUpPriority(TypedDict, total=False):
    """Priority sub-object on a ClickUp task."""

    priority: str


class ClickUpTask(TypedDict, total=False):
    """A ClickUp task as returned by the v2 ``/list/{id}/task`` endpoint.

    All fields are optional (``total=False``). ``status`` and ``priority`` may
    arrive as either a sub-object or a bare scalar string depending on the
    ClickUp plan / projection, so they are typed as a union with ``str``.
    """

    id: str
    name: str
    description: str
    text_content: str
    url: str
    date_updated: str
    status: ClickUpStatus | str
    assignees: list[ClickUpAssignee]
    tags: list[ClickUpTag]
    priority: ClickUpPriority | str


Transport = Callable[
    [str, str, Mapping[str, str], "dict[str, object] | None", float],
    "tuple[int, dict[str, object]]",
]

_DEFAULT_BASE_URL = "https://api.clickup.com"
_DEFAULT_TIMEOUT = 15.0


def _is_blocked_host(host: str) -> bool:
    """Return True if ``host`` is a literal that must be SSRF-blocked.

    Pure literal inspection; no DNS resolution so a hostile DNS answer cannot
    reclassify a name after the check.
    """
    host = (host or "").strip().lower()
    if not host:
        return True
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
            or ip.is_multicast
        )
    if host in {"localhost", "localhost.localdomain"}:
        return True
    if "." not in host:
        return True
    return host.endswith((".local", ".internal", ".localhost", ".intranet"))


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    json_body: dict[str, object] | None,
    timeout: float,
) -> tuple[int, dict[str, object]]:
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 0) or resp.getcode() or 0)
            body = resp.read()
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        status = int(exc.code)
        body = exc.read() if hasattr(exc, "read") else b""
    payload: dict[str, object] = {}
    if body:
        try:
            parsed: object = json.loads(body.decode("utf-8"))
            payload = parsed if isinstance(parsed, dict) else {"data": parsed}
        except (ValueError, UnicodeDecodeError):
            payload = {}
    return status, payload


class ClickUpIssueSource:
    """Issue source backed by the ClickUp v2 REST API."""

    SYSTEM = "clickup"

    def __init__(
        self,
        config: Mapping[str, object],
        *,
        transport: Transport | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._config: dict[str, object] = dict(config or {})
        self.name = str(self._config.get("name") or self.SYSTEM)
        self._env: Mapping[str, str] = env if env is not None else os.environ
        self._transport: Transport = transport or _default_transport

        base_url = str(self._config.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")
        parts = urlsplit(base_url)
        if parts.scheme not in {"https", "http"} or not parts.hostname:
            raise ValueError(f"invalid base_url: {base_url!r}")
        if _is_blocked_host(parts.hostname):
            raise ValueError(f"base_url host is internal/blocked: {parts.hostname!r}")
        self._base_url = base_url

        raw_timeout = self._config.get("timeout", _DEFAULT_TIMEOUT)
        self._timeout = float(raw_timeout) if isinstance(raw_timeout, (int, float, str)) else _DEFAULT_TIMEOUT
        self._list_id = self._config.get("list_id")

    # -- secrets ---------------------------------------------------------
    def _token(self) -> str:
        env_key = str(self._config.get("token_env", "CLICKUP_API_TOKEN"))
        return str(self._env.get(env_key, ""))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._token(),
            "Content-Type": "application/json",
        }

    def _request(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> tuple[int, dict[str, object]]:
        url = f"{self._base_url}/api/v2{path}"
        return self._transport(method, url, self._headers(), body, self._timeout)

    # -- health ----------------------------------------------------------
    def health(self) -> dict[str, object]:
        """Probe the authenticated-user endpoint. Never raises."""
        try:
            status, payload = self._request("GET", "/user")
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"transport error: {exc}"}
        if status != 200:
            return {"ok": False, "detail": f"http {status}"}
        if payload.get("err") or payload.get("ECODE"):
            return {"ok": False, "detail": str(payload.get("err") or payload.get("ECODE"))}
        user = payload.get("user") or {}
        if not (isinstance(user, Mapping) and user.get("id")):
            return {"ok": False, "detail": "no authenticated user"}
        return {"ok": True, "detail": "ok"}

    # -- normalization ---------------------------------------------------
    def _normalize(self, task: ClickUpTask) -> dict[str, object]:
        task_id = str(task.get("id") or "")
        status_obj = task.get("status") or {}
        status = (
            str(status_obj.get("status") or "")
            if isinstance(status_obj, Mapping)
            else str(status_obj or "")
        )
        assignees = task.get("assignees") or []
        assignee = ""
        if assignees:
            first = assignees[0] or {}
            assignee = str(first.get("username") or first.get("email") or first.get("id") or "")
        labels = [str((t or {}).get("name") or "") for t in (task.get("tags") or [])]
        labels = [label for label in labels if label]
        prio_obj = task.get("priority") or {}
        priority = (
            str(prio_obj.get("priority") or "")
            if isinstance(prio_obj, Mapping)
            else str(prio_obj or "")
        )
        return {
            "external_id": task_id,
            "source": self.SYSTEM,
            "title": str(task.get("name") or ""),
            "description": str(task.get("description") or task.get("text_content") or ""),
            "status": status,
            "assignee": assignee,
            "labels": labels,
            "priority": priority,
            "url": str(task.get("url") or (f"https://app.clickup.com/t/{task_id}" if task_id else "")),
            "updated_ts": str(task.get("date_updated") or ""),
            "raw": dict(task),
        }

    @staticmethod
    def _extract_tasks(payload: object) -> list[ClickUpTask]:
        """Narrow a ClickUp list response to its task entries."""
        tasks: list[ClickUpTask] = []
        raw = payload.get("tasks") if isinstance(payload, Mapping) else None
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict):
                    tasks.append(cast(ClickUpTask, entry))
        return tasks

    def fetch_issues(
        self, spec: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        spec_dict: dict[str, object] = dict(spec or {})
        list_id = spec_dict.get("list_id", self._list_id)
        if list_id is None:
            raise ValueError("list_id required (spec or config)")
        path = f"/list/{list_id}/task"
        params: list[str] = []
        if spec_dict.get("include_closed"):
            params.append("include_closed=true")
        if spec_dict.get("page") is not None:
            raw_page = spec_dict["page"]
            if isinstance(raw_page, (int, float, str)):
                params.append(f"page={int(raw_page)}")
        if params:
            path = path + "?" + "&".join(params)
        status, payload = self._request("GET", path)
        if status != 200:
            raise RuntimeError(f"clickup fetch failed: http {status}")
        return [self._normalize(t) for t in self._extract_tasks(payload)]

    # -- write-back ------------------------------------------------------
    def update_status(
        self, external_id: str, status: str, comment: str | None = None
    ) -> dict[str, object]:
        code, payload = self._request(
            "PUT", f"/task/{external_id}", {"status": status}
        )
        result: dict[str, object] = {
            "ok": code == 200 and not payload.get("err"),
            "external_id": str(external_id),
            "status": status,
            "http": code,
            "raw": payload,
        }
        if comment:
            result["comment"] = self.add_comment(external_id, comment)
        return result

    def add_comment(self, external_id: str, comment: str) -> dict[str, object]:
        code, payload = self._request(
            "POST",
            f"/task/{external_id}/comment",
            {"comment_text": comment, "notify_all": False},
        )
        return {
            "ok": code in (200, 201) and not payload.get("err"),
            "external_id": str(external_id),
            "http": code,
            "raw": payload,
        }
