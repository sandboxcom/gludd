"""Standalone GitLab issue-source adapter.

Self-contained: imports no sibling modules and defines no shared base. The
adapter is config-driven, reads its token from the environment, blocks SSRF to
internal hosts on the configured ``base_url`` (literal-host only, no DNS), and
talks to the GitLab REST API (v4) through an injectable HTTP transport so tests
can run fully mocked.

Normalized issue dict shape (``fetch_issues``)::

    {
        "external_id": str,   # issue iid as string
        "source": "gitlab",
        "title": str,
        "description": str,
        "status": "open" | "closed",
        "assignee": str | None,
        "labels": list[str],
        "priority": str | None,
        "url": str,
        "updated_ts": str | None,
        "raw": dict,          # original API payload
    }
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from general_ludd.security.ssrf import host_is_blocked

SYSTEM = "gitlab"

_DEFAULT_BASE_URL = "https://gitlab.com"
_DEFAULT_TIMEOUT = 30.0

# Label names that map to a coarse priority signal.
_PRIORITY_LABELS = {
    "p0": "critical",
    "p1": "high",
    "p2": "medium",
    "p3": "low",
    "critical": "critical",
    "urgent": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}


@runtime_checkable
class HTTPResponse(Protocol):
    """Minimal response surface the adapter needs from a transport."""

    status_code: int

    def json(self) -> Any: ...


@runtime_checkable
class HTTPTransport(Protocol):
    """Injectable HTTP transport.

    A callable that performs a single time-bound request and returns an
    :class:`HTTPResponse`. The default production transport wraps ``httpx``.
    """

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> HTTPResponse: ...


def _is_internal_host(host: str) -> bool:
    """Return ``True`` if ``host`` is a literal internal/loopback target.

    Literal-host only — this never performs DNS resolution. A hostname that is
    not an IP literal is treated as external (resolution-time SSRF is out of
    scope for a config-time guard and would require a DNS lookup). Delegates
    entirely to the canonical
    :func:`general_ludd.security.ssrf.host_is_blocked` so this adapter's
    classification (previously a local reimplementation missing the
    ``not is_global`` catch for CGNAT/TEST-NET ranges and the cloud-metadata
    NAME/IP blocklist) can never drift from the single source of truth.
    """
    return host_is_blocked(host)


def _default_transport() -> HTTPTransport:
    import httpx

    def _transport(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> HTTPResponse:
        resp = httpx.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json,
            timeout=timeout,
        )
        return resp  # httpx.Response satisfies HTTPResponse

    return _transport


class GitLabIssueSource:
    """Config-driven GitLab issue adapter with an injectable transport."""

    name = SYSTEM

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: HTTPTransport | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._config = dict(config)
        self._env = dict(env) if env is not None else dict(os.environ)

        base_url = str(config.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")
        self._base_url = base_url
        self._project_id = str(config.get("project_id") or "")
        self._token_env = str(config.get("token_env") or "GITLAB_TOKEN")
        self._timeout = float(config.get("timeout") or _DEFAULT_TIMEOUT)

        parsed = urlparse(base_url)
        self._host = parsed.hostname or ""
        self._scheme = parsed.scheme or "https"
        self._base_internal = _is_internal_host(self._host)

        self._transport = transport

    # -- internal helpers ------------------------------------------------

    def _token(self) -> str:
        return self._env.get(self._token_env, "")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "general-ludd-agent",
        }
        token = self._token()
        if token:
            headers["PRIVATE-TOKEN"] = token
        return headers

    def _get_transport(self) -> HTTPTransport:
        if self._transport is None:
            self._transport = _default_transport()
        return self._transport

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        if self._base_internal:
            raise PermissionError(f"refusing request to internal host: {self._host!r}")
        url = f"{self._base_url}/api/v4{path}"
        return self._get_transport()(
            method,
            url,
            headers=self._headers(),
            params=params,
            json=json,
            timeout=self._timeout,
        )

    @staticmethod
    def _normalize(issue: dict[str, Any]) -> dict[str, Any]:
        labels_raw = issue.get("labels") or []
        labels = [
            lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
            for lbl in labels_raw
        ]
        priority: str | None = None
        for ln in labels:
            mapped = _PRIORITY_LABELS.get(ln.lower())
            if mapped is not None:
                priority = mapped
                break

        assignee = None
        assignee_obj = issue.get("assignee")
        if isinstance(assignee_obj, dict):
            assignee = assignee_obj.get("username")
        else:
            assignees = issue.get("assignees")
            if isinstance(assignees, list) and assignees:
                first = assignees[0]
                if isinstance(first, dict):
                    assignee = first.get("username")

        # GitLab issue state is "opened" | "closed".
        state = str(issue.get("state") or "opened").lower()
        status = "closed" if state == "closed" else "open"

        iid = issue.get("iid")
        external_id = str(iid) if iid is not None else str(issue.get("id", ""))

        return {
            "external_id": external_id,
            "source": SYSTEM,
            "title": issue.get("title") or "",
            "description": issue.get("description") or "",
            "status": status,
            "assignee": assignee,
            "labels": labels,
            "priority": priority,
            "url": issue.get("web_url") or "",
            "updated_ts": issue.get("updated_at"),
            "raw": issue,
        }

    # -- public contract -------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return ``{'ok': bool, 'detail': str}``; never raises."""

        if self._base_internal:
            return {"ok": False, "detail": f"internal base_url blocked: {self._host}"}
        if not self._project_id:
            return {"ok": False, "detail": "missing 'project_id' in config"}
        if not self._token():
            return {"ok": False, "detail": f"missing token in env {self._token_env}"}
        try:
            resp = self._request("GET", f"/projects/{self._project_id}")
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"request failed: {exc}"}
        ok = 200 <= resp.status_code < 300
        return {
            "ok": ok,
            "detail": f"GET /projects/{self._project_id} -> {resp.status_code}",
        }

    def fetch_issues(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch and normalize project issues."""

        spec = spec or {}
        params: dict[str, str] = {}
        state = spec.get("state")
        if state:
            # Map a generic "open" to GitLab's "opened" vocabulary.
            params["state"] = "opened" if str(state).lower() == "open" else str(state)
        labels = spec.get("labels")
        if labels:
            params["labels"] = (
                ",".join(str(label) for label in labels)
                if isinstance(labels, (list, tuple))
                else str(labels)
            )

        resp = self._request(
            "GET",
            f"/projects/{self._project_id}/issues",
            params=params,
        )
        data = resp.json()
        if not isinstance(data, list):
            return []
        return [self._normalize(item) for item in data if isinstance(item, dict)]

    def update_status(
        self,
        external_id: str,
        status: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Transition an issue to open/closed, with an optional comment.

        GitLab uses ``state_event`` ('close' | 'reopen') on a PUT to the issue.
        """

        norm = str(status).lower()
        if norm not in {"open", "closed"}:
            raise ValueError(f"status must be 'open' or 'closed', got {status!r}")
        state_event = "close" if norm == "closed" else "reopen"
        resp = self._request(
            "PUT",
            f"/projects/{self._project_id}/issues/{external_id}",
            json={"state_event": state_event},
        )
        result: dict[str, Any] = {
            "external_id": str(external_id),
            "status": norm,
            "state_event": state_event,
            "status_code": resp.status_code,
            "ok": 200 <= resp.status_code < 300,
        }
        if comment:
            comment_resp = self.add_comment(external_id, comment)
            result["comment"] = comment_resp
        return result

    def add_comment(self, external_id: str, comment: str) -> dict[str, Any]:
        """Post a note on an issue."""

        resp = self._request(
            "POST",
            f"/projects/{self._project_id}/issues/{external_id}/notes",
            json={"body": comment},
        )
        return {
            "external_id": str(external_id),
            "status_code": resp.status_code,
            "ok": 200 <= resp.status_code < 300,
            "raw": resp.json() if 200 <= resp.status_code < 300 else None,
        }
