"""Sentry observability connector (self-contained).

A ``logs``-kind connector that surfaces Sentry *issues* (aggregated error/issue
events) and individual *events* through the Sentry HTTP API, normalizing each
into the project's connector record shape.

Design constraints (see the connector contract):

* No imports of a connector base class, package ``__init__``, or sibling
  modules — this file stands alone and only depends on the stdlib.
* HTTP transport is injectable so tests can supply a mocked transport; the
  default transport uses ``urllib`` from the stdlib.
* SSRF protection: ``base_url`` is rejected at construction time if its host is
  a literal private / loopback / link-local / reserved address. No DNS
  resolution is performed (a literal-host check only — name-based hosts are
  trusted to the operator's egress policy, consistent with "no DNS").
* ``health()`` never raises; it returns ``{"ok": bool, "detail": str}``.
* The Bearer token is read from the environment variable named by
  ``config['token_env']`` and sent as an ``Authorization: Bearer <token>``
  header.
* All network calls are time-bound via an explicit timeout.
* No ``shell=True`` / subprocess usage anywhere.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.security.ssrf import is_url_blocked

__all__ = ["HttpResponse", "SentrySource", "Transport"]

_DEFAULT_BASE_URL = "https://sentry.io"
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_STATS_PERIOD = "24h"
_DEFAULT_LIMIT = 100


class HttpResponse:
    """A minimal, transport-agnostic HTTP response.

    Only the surface the connector needs is modeled: a status code and a raw
    body (``bytes`` or ``str``). ``json()`` decodes the body lazily.
    """

    __slots__ = ("_body", "status")

    def __init__(self, status: int, body: bytes | str) -> None:
        self.status = status
        self._body = body

    @property
    def text(self) -> str:
        if isinstance(self._body, bytes):
            return self._body.decode("utf-8", errors="replace")
        return self._body

    def json(self) -> Any:
        body = self.text.strip()
        if not body:
            return None
        return json.loads(body)


@runtime_checkable
class Transport(Protocol):
    """Injectable HTTP transport.

    Implementations perform a single GET and return an :class:`HttpResponse`.
    A mocked transport in tests can record the requested URL/headers and return
    canned payloads without any network access.
    """

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse: ...


class _UrllibTransport:
    """Default stdlib transport (``urllib``). No third-party dependencies."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = int(getattr(resp, "status", 0) or resp.getcode() or 0)
                raw = resp.read()
            return HttpResponse(status=status, body=raw)
        except urllib.error.HTTPError as exc:  # 4xx/5xx are responses, not failures
            body = exc.read() if hasattr(exc, "read") else b""
            return HttpResponse(status=int(exc.code), body=body)


class SentrySource:
    """Sentry issues/events connector.

    Parameters
    ----------
    config:
        A mapping with keys:

        * ``token_env`` (required) — name of the env var holding the API token.
        * ``org`` (required) — Sentry organization slug.
        * ``project`` (required) — Sentry project slug.
        * ``base_url`` — defaults to ``https://sentry.io``.
        * ``timeout`` — request timeout in seconds (default ``30``).
        * ``name`` — instance name (default ``"sentry"``).
        * ``transport`` — optional injectable :class:`Transport`.
    """

    #: Connector kind — Sentry surfaces error/issue events as logs.
    KIND: str = "logs"

    def __init__(self, config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")

        self.name: str = str(config.get("name") or "sentry")

        token_env = config.get("token_env")
        if not token_env:
            raise ValueError("config['token_env'] is required")
        self._token_env: str = str(token_env)

        org = config.get("org")
        project = config.get("project")
        if not org:
            raise ValueError("config['org'] is required")
        if not project:
            raise ValueError("config['project'] is required")
        self.org: str = str(org)
        self.project: str = str(project)

        base_url = str(config.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")
        self._validate_base_url(base_url)
        self.base_url: str = base_url

        timeout = config.get("timeout", _DEFAULT_TIMEOUT)
        try:
            self.timeout: float = float(timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError("config['timeout'] must be a number") from exc

        transport = config.get("transport")
        if transport is None:
            transport = _UrllibTransport()
        self._transport: Transport = transport

    # ------------------------------------------------------------------ #
    # Construction-time validation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_base_url(base_url: str) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"base_url must be http(s): {base_url!r}")
        host = parts.hostname
        if not host:
            raise ValueError(f"base_url has no host: {base_url!r}")
        if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
            raise ValueError(f"base_url host is blocked (SSRF guard): {host!r}")

    # ------------------------------------------------------------------ #
    # Request plumbing
    # ------------------------------------------------------------------ #
    def _token(self) -> str:
        token = os.environ.get(self._token_env, "")
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/json",
        }

    def _get(self, url: str) -> HttpResponse:
        # Defense in depth: re-validate every URL we are about to fetch.
        self._validate_base_url(url)
        return self._transport.get(url, headers=self._headers(), timeout=self.timeout)

    def _issues_url(self, params: dict[str, str]) -> str:
        path = f"/api/0/projects/{self.org}/{self.project}/issues/"
        query = urllib.parse.urlencode(params, doseq=True)
        return f"{self.base_url}{path}?{query}" if query else f"{self.base_url}{path}"

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #
    def health(self) -> dict[str, Any]:
        """Probe the Sentry API. Never raises.

        Returns ``{"ok": True, "detail": "..."}`` on a 2xx from the API root,
        otherwise ``{"ok": False, "detail": "..."}``. A 401 (bad/missing token)
        therefore reports ``ok=False``.
        """

        url = f"{self.base_url}/api/0/"
        try:
            resp = self._get(url)
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        if 200 <= resp.status < 300:
            return {"ok": True, "detail": f"sentry api {resp.status}"}
        return {"ok": False, "detail": f"sentry api returned {resp.status}"}

    # ------------------------------------------------------------------ #
    # Query
    # ------------------------------------------------------------------ #
    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch issues matching ``spec`` and return normalized records.

        ``spec`` keys (all optional):

        * ``query`` — Sentry issue search query (e.g. ``"is:unresolved"``).
        * ``statsPeriod`` — relative time window (e.g. ``"24h"``, ``"14d"``).
        * ``limit`` — max issues to return.
        """

        spec = spec or {}
        params: dict[str, str] = {}
        query = spec.get("query")
        if query:
            params["query"] = str(query)
        params["statsPeriod"] = str(spec.get("statsPeriod") or _DEFAULT_STATS_PERIOD)
        limit = spec.get("limit", _DEFAULT_LIMIT)
        try:
            limit_int = int(limit)
        except (TypeError, ValueError):
            limit_int = _DEFAULT_LIMIT
        params["limit"] = str(limit_int)

        resp = self._get(self._issues_url(params))
        if not (200 <= resp.status < 300):
            return []
        payload = resp.json()
        if not isinstance(payload, list):
            return []

        records: list[dict[str, Any]] = []
        for issue in payload:
            if isinstance(issue, dict):
                records.append(self._normalize_issue(issue))
            if len(records) >= limit_int:
                break
        return records

    # ------------------------------------------------------------------ #
    # fetch_event
    # ------------------------------------------------------------------ #
    def fetch_event(self, issue_id: str) -> dict[str, Any] | None:
        """Fetch the latest event for ``issue_id`` and normalize it.

        Surfaces ``trace_id`` and selected ``contexts`` keys in ``labels`` so a
        caller can associate the event with traces. Returns ``None`` if the
        event cannot be fetched (non-2xx or empty body).
        """

        safe_id = urllib.parse.quote(str(issue_id), safe="")
        url = f"{self.base_url}/api/0/issues/{safe_id}/events/latest/"
        resp = self._get(url)
        if not (200 <= resp.status < 300):
            return None
        event = resp.json()
        if not isinstance(event, dict):
            return None
        return self._normalize_event(event, issue_id=str(issue_id))

    # ------------------------------------------------------------------ #
    # Normalization
    # ------------------------------------------------------------------ #
    def _normalize_issue(self, issue: dict[str, Any]) -> dict[str, Any]:
        title = issue.get("title") or issue.get("metadata", {}).get("type") or ""
        culprit = issue.get("culprit") or ""
        message = f"{title} — {culprit}".strip(" —") if culprit else str(title)

        count = issue.get("count")
        value: float | None
        try:
            value = float(count) if count is not None else None
        except (TypeError, ValueError):
            value = None

        labels: dict[str, Any] = {}
        if issue.get("shortId"):
            labels["shortId"] = issue["shortId"]
        if issue.get("status"):
            labels["status"] = issue["status"]
        project = issue.get("project")
        if isinstance(project, dict):
            labels["project"] = project.get("slug") or project.get("name")
        elif project:
            labels["project"] = project
        else:
            labels["project"] = self.project
        metadata = issue.get("metadata")
        if isinstance(metadata, dict):
            commit = metadata.get("commit") or metadata.get("commitId")
            if commit:
                labels["commit"] = commit

        return {
            "ts": issue.get("lastSeen"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": issue.get("level"),
            "message": message,
            "value": value,
            "labels": labels,
            "raw": issue,
        }

    def _normalize_event(self, event: dict[str, Any], *, issue_id: str) -> dict[str, Any]:
        title = event.get("title") or event.get("message") or ""
        culprit = event.get("culprit") or ""
        message = f"{title} — {culprit}".strip(" —") if culprit else str(title)

        labels: dict[str, Any] = {"issueId": issue_id}
        event_id = event.get("eventID") or event.get("id")
        if event_id:
            labels["eventId"] = event_id

        contexts = event.get("contexts")
        trace_id = None
        if isinstance(contexts, dict):
            trace = contexts.get("trace")
            if isinstance(trace, dict):
                trace_id = trace.get("trace_id") or trace.get("traceId")
                if trace.get("span_id"):
                    labels["span_id"] = trace["span_id"]
            # Surface a couple of high-signal context keys for association.
            for ctx_key in ("runtime", "os"):
                ctx = contexts.get(ctx_key)
                if isinstance(ctx, dict) and ctx.get("name"):
                    labels[ctx_key] = ctx["name"]
        if trace_id is None:
            # Some payloads carry trace at the top level.
            trace_id = event.get("trace_id") or event.get("traceId")
        if trace_id:
            labels["trace_id"] = trace_id

        return {
            "ts": event.get("dateCreated") or event.get("dateReceived"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": event.get("level"),
            "message": message,
            "value": None,
            "labels": labels,
            "raw": event,
        }
