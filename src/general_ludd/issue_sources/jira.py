"""Jira issue-source adapter.

A self-contained, config-driven adapter that maps Jira Cloud issues onto the
gludd normalized-issue shape. It speaks the Jira Cloud REST v3 API over an
**injectable** HTTP transport (so tests can mock it) using HTTP Basic auth with
an ``email:api_token`` pair sourced exclusively from environment variables —
credentials are NEVER read from the config dict or hardcoded.

Security posture:

* SSRF defense — the ``base_url`` host is checked against a literal block list
  (loopback, link-local, private RFC-1918 ranges, and the cloud metadata
  endpoint) at construction time. The check is purely literal: it never
  performs DNS resolution, so it cannot be tricked into a resolver-side SSRF
  and never makes a network call just to validate the URL.
* Time-bound — every request carries an explicit timeout.
* No ``shell=True`` / no subprocess — pure HTTP.

The adapter intentionally does not import any sibling adapter, base class, or
package ``__init__``; it stands alone.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.security.ssrf import host_is_blocked

__all__ = ["JiraIssueSource"]


@runtime_checkable
class HttpResponse(Protocol):
    """Structural type for the response objects the transport returns."""

    status_code: int

    def json(self) -> Any: ...


@runtime_checkable
class HttpTransport(Protocol):
    """Injectable HTTP transport.

    Any object exposing this ``request`` signature works — including a mock in
    tests or a thin :mod:`httpx` wrapper in production (see
    :func:`_default_transport`).
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        json: Any = ...,
        timeout: float | None = ...,
    ) -> HttpResponse: ...

    def close(self) -> None: ...


_DEFAULT_TIMEOUT = 30.0


def _default_transport() -> HttpTransport:
    """Build the production transport lazily.

    Importing this module never requires :mod:`httpx`, and tests need not touch
    the network.
    """
    import httpx

    class _HttpxTransport:
        def __init__(self) -> None:
            self._client = httpx.Client()

        def request(
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            json: Any = None,
            timeout: float | None = None,
        ) -> HttpResponse:
            return self._client.request(
                method,
                url,
                headers=headers,
                json=json,
                timeout=timeout,
            )

        def close(self) -> None:
            self._client.close()

    return _HttpxTransport()


def _is_blocked_host(host: str) -> bool:
    """Return True if *host* is a literal address/name we refuse to contact.

    Purely literal — no DNS. A bare hostname that is not blocked by the
    canonical guard is allowed (it will be resolved by the transport at
    request time, outside our control); the point of this gate is to stop the
    obvious internal targets (loopback, RFC-1918, link-local, cloud metadata)
    that an attacker would put directly into ``base_url``. Delegates entirely
    to :func:`general_ludd.security.ssrf.host_is_blocked` so this adapter's
    classification (previously a local 4-name blocklist missing
    ``metadata.goog``/``instance-data``/the ``ip6-*`` aliases and the
    ``not is_global`` catch for CGNAT/TEST-NET ranges) can never drift from
    the single source of truth.
    """
    return host_is_blocked(host)


class JiraIssueSource:
    """Config-driven Jira Cloud issue source.

    Expected config keys:

    * ``base_url`` — Jira site root, e.g. ``https://acme.atlassian.net``.
    * ``project`` — project key used to build the default JQL (optional if
      ``jql`` is supplied either in config or per-fetch spec).
    * ``jql`` — explicit JQL (optional; a per-fetch ``spec['jql']`` overrides
      it).
    * ``email_env`` — name of the env var holding the account email.
    * ``token_env`` — name of the env var holding the API token.
    * ``max_results`` — optional search page size (default 50).
    * ``timeout`` — optional per-request timeout in seconds (default 30).
    """

    SYSTEM = "jira"

    def __init__(self, config: dict[str, Any], *, transport: HttpTransport | None = None) -> None:
        """Create a Jira source with an owned or caller-supplied transport."""
        self.config: dict[str, Any] = dict(config)
        self.name: str = self.SYSTEM

        base_url = str(self.config.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("Jira config requires 'base_url'")

        parsed = urlsplit(base_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Jira base_url must be http(s): {base_url!r}")
        host = parsed.hostname or ""
        if _is_blocked_host(host):
            raise ValueError(f"Jira base_url host is blocked (SSRF guard): {host!r}")

        self.base_url = base_url
        self._email_env = str(self.config.get("email_env", "JIRA_EMAIL"))
        self._token_env = str(self.config.get("token_env", "JIRA_API_TOKEN"))
        self._project = self.config.get("project")
        self._jql = self.config.get("jql")
        self._max_results = int(self.config.get("max_results", 50))
        self._timeout = float(self.config.get("timeout", _DEFAULT_TIMEOUT))
        self._owns_transport = transport is None
        self._transport: HttpTransport = transport if transport is not None else _default_transport()

    def close(self) -> None:
        """Release the default transport while preserving injected ownership."""
        if not self._owns_transport:
            return
        self._transport.close()
        self._owns_transport = False

    # ----------------------------------------------------------------- auth #
    def _auth_header(self) -> str:
        """Build the HTTP Basic ``email:token`` header from env vars.

        Raises if either secret is absent — credentials are required and are
        only ever read from the environment.
        """
        email = os.environ.get(self._email_env)
        token = os.environ.get(self._token_env)
        if not email or not token:
            raise ValueError(
                f"Missing Jira credentials in env: {self._email_env} / {self._token_env}"
            )
        raw = f"{email}:{token}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._auth_header(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, *, json_body: Any = None) -> HttpResponse:
        url = f"{self.base_url}{path}"
        return self._transport.request(
            method,
            url,
            headers=self._headers(),
            json=json_body,
            timeout=self._timeout,
        )

    # --------------------------------------------------------------- health #
    def health(self) -> dict[str, Any]:
        """Probe ``/rest/api/3/myself`` without raising.

        Failures become ``{'ok': False, 'detail': ...}``.
        """
        try:
            resp = self._request("GET", "/rest/api/3/myself")
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"transport error: {exc}"}

        status = getattr(resp, "status_code", 0)
        if status == 200:
            return {"ok": True, "detail": "200 OK"}
        return {"ok": False, "detail": f"unhealthy: HTTP {status}"}

    # --------------------------------------------------------------- fetch #
    def _resolve_jql(self, spec: dict[str, Any]) -> str:
        jql = spec.get("jql") or self._jql
        if jql:
            return str(jql)
        if self._project:
            return f"project = {self._project} ORDER BY updated DESC"
        return "ORDER BY updated DESC"

    def fetch_issues(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """POST the Jira search endpoint and normalize each issue."""
        body = {
            "jql": self._resolve_jql(spec),
            "maxResults": int(spec.get("max_results", self._max_results)),
            "fields": [
                "summary",
                "description",
                "status",
                "assignee",
                "labels",
                "priority",
                "updated",
            ],
        }
        resp = self._request("POST", "/rest/api/3/search", json_body=body)
        payload = resp.json() or {}
        issues = payload.get("issues", []) if isinstance(payload, dict) else []
        return [self._normalize(issue) for issue in issues]

    def _normalize(self, issue: dict[str, Any]) -> dict[str, Any]:
        key = issue.get("key", "")
        fields = issue.get("fields") or {}

        status = fields.get("status") or {}
        assignee = fields.get("assignee") or None
        priority = fields.get("priority") or None

        return {
            "external_id": key,
            "source": self.SYSTEM,
            "title": fields.get("summary"),
            "description": fields.get("description"),
            "status": status.get("name") if isinstance(status, dict) else None,
            "assignee": assignee.get("displayName") if isinstance(assignee, dict) else None,
            "labels": list(fields.get("labels") or []),
            "priority": priority.get("name") if isinstance(priority, dict) else None,
            "url": f"{self.base_url}/browse/{key}",
            "updated_ts": _parse_updated(fields.get("updated")),
            "raw": issue,
        }

    # -------------------------------------------------------- update_status #
    def update_status(
        self, external_id: str, status: str, comment: str | None = None
    ) -> dict[str, Any]:
        """Transition an issue to the workflow state matching *status*.

        Looks up the available transitions, finds the one whose ``to.name``
        (falling back to the transition's own ``name``) equals *status*, then
        POSTs it. An optional *comment* is attached to the same transition.
        """
        transitions_path = f"/rest/api/3/issue/{external_id}/transitions"
        resp = self._request("GET", transitions_path)
        payload = resp.json() or {}
        transitions = payload.get("transitions", []) if isinstance(payload, dict) else []

        transition_id: str | None = None
        target = status.strip().lower()
        for tr in transitions:
            to_name = (tr.get("to") or {}).get("name", "")
            if to_name.strip().lower() == target or tr.get("name", "").strip().lower() == target:
                transition_id = str(tr.get("id"))
                break

        if transition_id is None:
            available = [
                (tr.get("to") or {}).get("name") or tr.get("name") for tr in transitions
            ]
            raise ValueError(
                f"No Jira transition to status {status!r} on {external_id}; available: {available}"
            )

        body: dict[str, Any] = {"transition": {"id": transition_id}}
        if comment:
            body["update"] = {"comment": [{"add": {"body": _adf(comment)}}]}

        self._request("POST", transitions_path, json_body=body)
        return {
            "external_id": external_id,
            "status": status,
            "transition_id": transition_id,
            "ok": True,
        }

    # ---------------------------------------------------------- add_comment #
    def add_comment(self, external_id: str, comment: str) -> dict[str, Any]:
        """POST a comment to the issue."""
        path = f"/rest/api/3/issue/{external_id}/comment"
        resp = self._request("POST", path, json_body={"body": _adf(comment)})
        status = getattr(resp, "status_code", 0)
        return {
            "external_id": external_id,
            "ok": status in (200, 201),
            "detail": f"HTTP {status}",
        }


def _parse_updated(value: Any) -> float:
    """Parse a Jira ``updated`` timestamp into an epoch float; 0.0 on failure."""
    if not value or not isinstance(value, str):
        return 0.0
    text = value.strip()
    # Jira emits e.g. "2026-06-12T10:30:00.000+0000"; normalize the offset so
    # datetime.fromisoformat accepts it on all supported versions.
    if len(text) >= 5 and (text[-5] in "+-") and text[-3] != ":":
        text = text[:-2] + ":" + text[-2:]
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _adf(text: str) -> dict[str, Any]:
    """Wrap plain text in a minimal Atlassian Document Format body."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
        ],
    }
