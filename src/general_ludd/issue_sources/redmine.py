"""Redmine issue-source adapter.

Self-contained adapter for reading and updating issues from a Redmine
instance over its REST API. The HTTP transport is injectable so the adapter
can be exercised with a mocked transport in tests and a real client in
production. No base class, package siblings, or shared registry are imported
here on purpose: this module stands alone.

Security posture:
  * The API key is never hardcoded. It is read from the environment variable
    named by ``config['api_key_env']`` and sent as the ``X-Redmine-API-Key``
    header on every request.
  * SSRF protection: the literal host of ``base_url`` is checked against a
    block list of internal/loopback/link-local/metadata ranges. The check is
    done on the literal host string only (no DNS resolution is performed).
  * Every request is time-bound via a configurable timeout.
  * The transport is a plain callable; no shell is ever invoked.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol, TypedDict, cast
from urllib.parse import urlsplit


class RedmineNamedRef(TypedDict, total=False):
    """A Redmine named reference (tracker, status, priority, category, ...).

    Redmine emits these as ``{"id": int, "name": str}`` sub-objects on issues.
    """

    id: int
    name: str


class RedmineIssue(TypedDict, total=False):
    """A Redmine issue as returned by ``GET /issues.json``.

    All fields are optional (``total=False``) because Redmine projections vary
    by endpoint parameters and the sub-objects (tracker, status, ...) may be
    absent on issues that lack the corresponding assignment.
    """

    id: int
    subject: str
    description: str
    updated_on: str
    tracker: RedmineNamedRef
    category: RedmineNamedRef
    status: RedmineNamedRef
    assigned_to: RedmineNamedRef
    priority: RedmineNamedRef


class _Response(Protocol):
    """Minimal response contract the transport must satisfy."""

    status_code: int

    def json(self) -> object:  # pragma: no cover - structural typing only
        ...


# A transport is any callable with the requests-like signature. It must return
# an object exposing ``status_code`` and ``json()``.
Transport = Callable[..., _Response]


class RedmineError(RuntimeError):
    """Raised for non-success HTTP responses on mutating/fetch calls."""


def _default_transport() -> Transport:
    """Return a requests-based transport, imported lazily.

    Importing requests lazily keeps the module importable (and testable with a
    mocked transport) in environments where requests is not installed.
    """

    import requests

    def _call(
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, object] | None = None,
        json: Mapping[str, object] | None = None,
        timeout: float,
    ) -> _Response:
        # Cast narrows our wide config-derived param values to the narrower
        # shape requests' stubs declare (str/int/float values). At runtime
        # requests stringifies whatever it receives, so the cast is sound.
        narrowed_params = cast("dict[str, str]", dict(params)) if params else None
        return requests.request(
            method,
            url,
            headers=dict(headers),
            params=narrowed_params,
            json=dict(json) if json is not None else None,
            timeout=timeout,
        )

    return _call


def _host_is_internal(host: str) -> bool:
    """Return True if ``host`` is a literal internal/loopback/metadata target.

    Only the literal host string is inspected. Hostnames that are not IP
    literals are matched against a small block list of obviously-internal
    names; everything else (including arbitrary DNS names) is allowed, since we
    deliberately do not resolve DNS here.
    """

    if not host:
        return True

    lowered = host.lower().strip("[]")

    blocked_names = {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
    }
    if lowered in blocked_names:
        return True

    # Block the cloud metadata endpoint by literal name fragment too.
    if lowered.endswith(".internal"):
        return True

    try:
        ip = ipaddress.ip_address(lowered)
    except ValueError:
        # Not an IP literal -> treat as an external hostname (no DNS lookup).
        return False

    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _parse_ts(value: object) -> str | None:
    """Parse a Redmine ``updated_on`` timestamp into ISO-8601 UTC text.

    Returns ``None`` when the value is missing or unparseable rather than
    raising, so normalization is total over real-world payloads.
    """

    if not value or not isinstance(value, str):
        return None

    text = value.strip()
    # Redmine commonly emits e.g. "2024-05-01T12:30:00Z".
    candidate = text.replace("Z", "+00:00")
    dt: datetime | None = None
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


class RedmineIssueSource:
    """Issue-source adapter backed by the Redmine REST API."""

    SYSTEM = "redmine"

    def __init__(
        self,
        config: Mapping[str, object],
        transport: Transport | None = None,
    ) -> None:
        base_url = str(config.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("config['base_url'] is required")

        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https"):
            raise ValueError("base_url must be http(s)")
        host = parts.hostname or ""
        if _host_is_internal(host):
            raise ValueError(f"base_url host is internal/blocked: {host!r}")

        self.name = self.SYSTEM
        self.base_url = base_url
        self.project_id = config.get("project_id")

        api_key_env = config.get("api_key_env")
        if not api_key_env:
            raise ValueError("config['api_key_env'] is required")
        self._api_key_env = str(api_key_env)

        raw_timeout = config.get("timeout", 15.0)
        self._timeout = float(raw_timeout) if isinstance(raw_timeout, (int, float, str)) else 15.0
        # Optional explicit status-name -> status-id resolution map.
        self._status_map: dict[str, int] = {}
        raw_status_map = config.get("status_map") or {}
        if isinstance(raw_status_map, Mapping):
            for k, v in raw_status_map.items():
                if isinstance(v, (int, str)) and not isinstance(v, bool):
                    self._status_map[str(k)] = int(v)
        self._transport: Transport = transport or _default_transport()

    # -- internals -----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        api_key = os.environ.get(self._api_key_env, "")
        return {
            "X-Redmine-API-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        json: Mapping[str, object] | None = None,
    ) -> _Response:
        url = f"{self.base_url}{path}"
        return self._transport(
            method,
            url,
            headers=self._headers(),
            params=params,
            json=json,
            timeout=self._timeout,
        )

    def _resolve_status_id(self, status: str) -> int:
        """Map a status name to a Redmine status_id.

        Uses the configured ``status_map`` first, then a sensible default
        mapping that mirrors a stock Redmine install.
        """

        if status in self._status_map:
            return self._status_map[status]

        default_map = {
            "new": 1,
            "in progress": 2,
            "resolved": 3,
            "feedback": 4,
            "closed": 5,
            "rejected": 6,
        }
        key = status.strip().lower()
        if key in default_map:
            return default_map[key]
        raise ValueError(f"cannot resolve status_id for status {status!r}")

    @staticmethod
    def _name_of(node: object) -> str | None:
        if isinstance(node, Mapping):
            value = node.get("name")
            return str(value) if value is not None else None
        return None

    def _normalize(self, issue: RedmineIssue) -> dict[str, object]:
        issue_id = issue.get("id")
        external_id = str(issue_id)

        labels: list[str] = []
        tracker_name = self._name_of(issue.get("tracker"))
        if tracker_name:
            labels.append(tracker_name)
        category_name = self._name_of(issue.get("category"))
        if category_name:
            labels.append(category_name)

        return {
            "external_id": external_id,
            "source": self.SYSTEM,
            "title": issue.get("subject"),
            "description": issue.get("description"),
            "status": self._name_of(issue.get("status")),
            "assignee": self._name_of(issue.get("assigned_to")),
            "labels": labels,
            "priority": self._name_of(issue.get("priority")),
            "url": f"{self.base_url}/issues/{external_id}",
            "updated_ts": _parse_ts(issue.get("updated_on")),
            "raw": dict(issue),
        }

    @staticmethod
    def _extract_issues(payload: object) -> list[RedmineIssue]:
        """Narrow a Redmine ``/issues.json`` response to its issue entries."""
        issues: list[RedmineIssue] = []
        if isinstance(payload, Mapping):
            raw = payload.get("issues", [])
            if isinstance(raw, Sequence):
                for entry in raw:
                    if isinstance(entry, Mapping):
                        issues.append(cast(RedmineIssue, entry))
        return issues

    # -- public API ----------------------------------------------------------

    def health(self) -> dict[str, object]:
        """Probe the Redmine API. Never raises; returns {'ok', 'detail'}."""

        try:
            resp = self._request(
                "GET", "/issues.json", params={"limit": 1}
            )
            ok = 200 <= int(resp.status_code) < 300
            detail = (
                "ok"
                if ok
                else f"unexpected status {resp.status_code}"
            )
            return {"ok": ok, "detail": detail}
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def fetch_issues(
        self, spec: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        """GET /issues.json filtered by spec; return normalized issue dicts."""

        spec_dict: dict[str, object] = dict(spec or {})
        params: dict[str, object] = {}

        project_id = spec_dict.get("project_id", self.project_id)
        if project_id is not None:
            params["project_id"] = project_id

        status_id = spec_dict.get("status_id")
        if status_id is not None:
            params["status_id"] = status_id

        limit = spec_dict.get("limit")
        if limit is not None:
            params["limit"] = limit

        resp = self._request("GET", "/issues.json", params=params)
        if not (200 <= int(resp.status_code) < 300):
            raise RedmineError(f"fetch_issues failed: status {resp.status_code}")

        return [self._normalize(issue) for issue in self._extract_issues(resp.json())]

    def update_status(
        self,
        external_id: str,
        status: str,
        comment: str | None = None,
    ) -> dict[str, object]:
        """PUT /issues/{id}.json setting status_id (and optional notes)."""

        status_id = self._resolve_status_id(status)
        issue_body: dict[str, object] = {"status_id": status_id, "notes": comment}
        body: dict[str, object] = {"issue": issue_body}

        resp = self._request(
            "PUT", f"/issues/{external_id}.json", json=body
        )
        if not (200 <= int(resp.status_code) < 300):
            raise RedmineError(
                f"update_status failed: status {resp.status_code}"
            )
        return {
            "ok": True,
            "external_id": str(external_id),
            "status": status,
            "status_id": status_id,
        }

    def add_comment(self, external_id: str, comment: str) -> dict[str, object]:
        """PUT /issues/{id}.json appending a note."""

        body: dict[str, object] = {"issue": {"notes": comment}}
        resp = self._request(
            "PUT", f"/issues/{external_id}.json", json=body
        )
        if not (200 <= int(resp.status_code) < 300):
            raise RedmineError(
                f"add_comment failed: status {resp.status_code}"
            )
        return {"ok": True, "external_id": str(external_id)}
