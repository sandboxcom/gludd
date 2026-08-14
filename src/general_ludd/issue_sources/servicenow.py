"""ServiceNow issue-source adapter.

Self-contained adapter that talks to a ServiceNow instance's Table API
(``/api/now/table/{table}``) over an injectable HTTP transport. It normalizes
ServiceNow incident records into the project's canonical issue dict shape and
supports updating state / posting work notes.

Design constraints honored here:

* No hardcoded credentials -- auth material is read from environment variables
  named in the adapter config (``user_env`` + ``password_env`` for HTTP Basic,
  or ``token_env`` for Bearer).
* SSRF protection: the ``instance_url`` host is checked against a literal
  block-list of private / loopback / link-local / metadata hosts *without*
  performing DNS resolution. Hostnames that are not literal IPs but resolve to
  obvious internal names (localhost, *.local, *.internal) are also rejected.
* The HTTP transport is injectable so tests can supply a mock. No shell, no
  ``shell=True``, every request is time-bound.
"""

from __future__ import annotations

import base64
import ipaddress
import os
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse


@runtime_checkable
class HTTPTransport(Protocol):
    """Minimal injectable HTTP transport.

    Any object exposing a ``request`` method with this signature is accepted.
    ``httpx.Client`` satisfies this protocol structurally.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        params: dict[str, Any] | None = ...,
        json: dict[str, Any] | None = ...,
        timeout: float | None = ...,
    ) -> Any:
        """Issue an HTTP request and return a response-like object."""
        ...


# Hosts that must never be reached, matched literally (no DNS resolution).
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
    }
)

# Suffixes that strongly indicate an internal target.
_BLOCKED_SUFFIXES = (
    ".local",
    ".internal",
    ".localdomain",
    ".cluster.local",
)


class ServiceNowSSRFError(ValueError):
    """Raised when a ServiceNow instance URL targets a blocked internal host."""


# Compatibility alias retained for existing issue-source integrations.
SSRFError = ServiceNowSSRFError


def _host_is_blocked(host: str) -> bool:
    """Return True if *host* (a literal hostname or IP) must be blocked.

    No DNS resolution is performed -- only literal inspection of the host
    component. IP literals are checked for private / loopback / link-local /
    reserved ranges (both IPv4 and IPv6).
    """
    host = host.strip().lower().rstrip(".")
    if not host:
        return True

    # Strip an IPv6 bracket form e.g. "[::1]".
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]

    if host in _BLOCKED_HOSTNAMES:
        return True
    if any(host.endswith(suffix) for suffix in _BLOCKED_SUFFIXES):
        return True

    # Literal IP address checks (no DNS).
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP literal -- it's a public-looking hostname; allow it.
        return False

    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _require_instance_url(instance_url: str) -> str:
    """Validate and normalize *instance_url*, blocking internal targets."""
    if not instance_url:
        raise ValueError("instance_url is required")
    parsed = urlparse(instance_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"instance_url must be http(s): {instance_url!r}")
    host = parsed.hostname
    if not host:
        raise ValueError(f"instance_url has no host: {instance_url!r}")
    if _host_is_blocked(host):
        raise SSRFError(f"refusing to reach internal/blocked host: {host!r}")
    # Normalize: drop any trailing slash on the base.
    return instance_url.rstrip("/")


def _parse_sn_timestamp(value: str | None) -> float | None:
    """Parse a ServiceNow ``sys_updated_on`` timestamp to a UTC epoch float.

    ServiceNow returns timestamps as ``"YYYY-MM-DD HH:MM:SS"`` in UTC.
    """
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=UTC).timestamp()


class ServiceNowIssueSource:
    """Issue source backed by the ServiceNow Table API."""

    SYSTEM = "servicenow"

    def __init__(
        self,
        config: dict[str, Any],
        transport: HTTPTransport | None = None,
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        """Configure a ServiceNow Table API issue source.

        Args:
            config: Instance URL, table, timeout, state mapping, and names of
                environment variables carrying credentials.
            transport: Optional injected HTTP transport.
            env: Optional environment snapshot used to resolve credentials.

        Raises:
            ValueError: If the instance URL is missing or malformed.
            ServiceNowSSRFError: If the instance points at a blocked host.
        """
        self.name = config.get("name", self.SYSTEM)
        self._env = dict(env) if env is not None else dict(os.environ)

        self.instance_url = _require_instance_url(str(config.get("instance_url", "")))
        self.table = str(config.get("table") or "incident")
        self.timeout = float(config.get("timeout", 30.0))

        # State numeric->name map is optional and config-driven.
        raw_map = config.get("state_map") or {}
        self._state_map: dict[str, str] = {str(k): str(v) for k, v in raw_map.items()}

        # Auth: Bearer (token_env) takes precedence over Basic (user/password).
        self._token_env = config.get("token_env")
        self._user_env = config.get("user_env")
        self._password_env = config.get("password_env")

        self._transport = transport if transport is not None else self._default_transport()

    # -- transport / auth helpers ------------------------------------------

    @staticmethod
    def _default_transport() -> HTTPTransport:
        import httpx

        return httpx.Client()

    def _auth_headers(self) -> dict[str, str]:
        """Build auth headers from environment, never from literal config."""
        if self._token_env:
            token = self._env.get(str(self._token_env))
            if not token:
                raise RuntimeError(f"token env var {self._token_env!r} is not set")
            return {"Authorization": f"Bearer {token}"}

        if self._user_env and self._password_env:
            user = self._env.get(str(self._user_env))
            password = self._env.get(str(self._password_env))
            if not user or not password:
                raise RuntimeError(
                    f"basic-auth env vars {self._user_env!r}/{self._password_env!r} are not set"
                )
            raw = f"{user}:{password}".encode()
            encoded = base64.b64encode(raw).decode("ascii")
            return {"Authorization": f"Basic {encoded}"}

        raise RuntimeError("no auth configured: set token_env or user_env+password_env")

    def _base_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        headers.update(self._auth_headers())
        return headers

    def _table_url(self, sys_id: str | None = None) -> str:
        base = f"{self.instance_url}/api/now/table/{self.table}"
        if sys_id:
            return f"{base}/{sys_id}"
        return base

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        return self._transport.request(
            method,
            url,
            headers=self._base_headers(),
            params=params,
            json=json,
            timeout=self.timeout,
        )

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the instance. Never raises; returns ``{'ok', 'detail'}``."""
        try:
            resp = self._request(
                "GET",
                self._table_url(),
                params={"sysparm_limit": 1},
            )
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

        status = getattr(resp, "status_code", None)
        if status is not None and 200 <= int(status) < 300:
            return {"ok": True, "detail": f"http {status}"}
        return {"ok": False, "detail": f"http {status}"}

    def fetch_issues(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch records from the table and normalize them.

        ``spec`` may carry ``query`` (a ServiceNow encoded query string) and
        ``limit`` (an integer row cap).
        """
        spec = spec or {}
        params: dict[str, Any] = {
            "sysparm_query": str(spec.get("query", "")),
            "sysparm_limit": int(spec.get("limit", 100)),
        }
        resp = self._request("GET", self._table_url(), params=params)
        payload = resp.json()
        records = payload.get("result", []) if isinstance(payload, dict) else []
        return [self._normalize(record) for record in records]

    def update_status(
        self,
        external_id: str,
        status: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """PATCH the record's ``state`` (and optional ``work_notes``)."""
        body: dict[str, Any] = {"state": self._resolve_state(status)}
        if comment:
            body["work_notes"] = comment
        resp = self._request("PATCH", self._table_url(external_id), json=body)
        return self._result_dict(resp)

    def add_comment(self, external_id: str, comment: str) -> dict[str, Any]:
        """PATCH a ``work_notes`` entry onto the record."""
        body = {"work_notes": comment}
        resp = self._request("PATCH", self._table_url(external_id), json=body)
        return self._result_dict(resp)

    # -- normalization helpers ---------------------------------------------

    def _resolve_state(self, status: str) -> str:
        """Map a friendly state name back to its numeric code if configured."""
        # If status already a known numeric code or no reverse map, pass through.
        for code, name in self._state_map.items():
            if name == status:
                return code
        return status

    def _map_state(self, raw_state: str) -> str:
        return self._state_map.get(str(raw_state), str(raw_state))

    @staticmethod
    def _coerce_ref(value: Any) -> str:
        """ServiceNow reference fields may be a dict ``{'value', 'link'}``."""
        if isinstance(value, dict):
            return str(value.get("value", ""))
        return "" if value is None else str(value)

    def _normalize(self, record: dict[str, Any]) -> dict[str, Any]:
        sys_id = self._coerce_ref(record.get("sys_id"))
        number = self._coerce_ref(record.get("number"))
        category = self._coerce_ref(record.get("category"))
        raw_state = self._coerce_ref(record.get("state"))

        labels = [lbl for lbl in (category, number) if lbl]

        return {
            "external_id": sys_id,
            "source": self.SYSTEM,
            "title": self._coerce_ref(record.get("short_description")),
            "description": self._coerce_ref(record.get("description")),
            "status": self._map_state(raw_state) if raw_state else raw_state,
            "assignee": self._coerce_ref(record.get("assigned_to")),
            "labels": labels,
            "priority": self._coerce_ref(record.get("priority")),
            "url": f"{self.instance_url}/nav_to.do?uri={self.table}.do?sys_id={sys_id}",
            "updated_ts": _parse_sn_timestamp(self._coerce_ref(record.get("sys_updated_on")) or None),
            "raw": record,
        }

    @staticmethod
    def _result_dict(resp: Any) -> dict[str, Any]:
        try:
            payload = resp.json()
        except Exception:  # tolerate empty / non-JSON bodies
            return {}
        if isinstance(payload, dict):
            result = payload.get("result", payload)
            return result if isinstance(result, dict) else {"result": result}
        return {"result": payload}
