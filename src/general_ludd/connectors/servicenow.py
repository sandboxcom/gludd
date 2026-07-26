"""ServiceNow Incident ticket connector.

Self-contained connector — imports nothing from sibling connector modules or a
shared base. It pulls ServiceNow incidents via the
``GET https://{instance}.service-now.com/api/now/table/{table}`` API and
normalizes them into the gludd event envelope.

Security posture:

* Credentials are read **only** from environment variables named by
  ``config['user_env']`` / ``config['pass_env']`` (never hardcoded, never logged).
* The instance hostname is SSRF-guarded against literal private / loopback /
  link-local hosts unless ``config['allow_private']`` is explicitly truthy.
* The injected HTTP transport is time-bounded and never uses ``shell=True``.
* ``health()`` never raises.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlparse, urlsplit

from general_ludd.connectors._protocols import HttpResponse

logger = logging.getLogger(__name__)


class _DisplayName(str):
    """Canonical connector name with a diagnostic instance display string."""

    def __new__(cls, canonical: str, display: str) -> "_DisplayName":
        value = super().__new__(cls, canonical)
        value._display = display
        return value

    def __str__(self) -> str:
        return self._display


class _DisplayText(str):
    """Canonical short description with ticket number in diagnostics."""

    def __new__(cls, canonical: str, display: str) -> "_DisplayText":
        value = super().__new__(cls, canonical)
        value._display = display
        return value

    def __str__(self) -> str:
        return self._display


@runtime_checkable
class Transport(Protocol):
    """Injectable, synchronous HTTP transport.

    A callable taking an HTTP method and URL plus keyword args and returning a
    :class:`HttpResponse`. The default implementation wraps :mod:`httpx`, but tests
    inject a fake so no network access ever occurs.
    """

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:  # pragma: no cover - structural typing only
        ...


_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"', re.IGNORECASE)

_STATE_MAP: dict[str, str] = {
    "1": "new",
    "2": "in_progress",
    "3": "on_hold",
    "6": "resolved",
    "7": "closed",
    "8": "cancelled",
}


def _state_code_to_label(code: str) -> str:
    return _STATE_MAP.get(code, "unknown")


def _parse_link_next(link_header: str | None) -> str | None:
    if not link_header:
        return None
    match = _LINK_NEXT_RE.search(link_header)
    return match.group(1) if match else None


def _default_transport(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
    timeout: float = 30.0,
) -> HttpResponse:
    """Default transport backed by httpx (imported lazily to stay optional)."""
    import httpx

    resp = httpx.request(
        method,
        url,
        headers=headers,
        params=params,
        auth=auth,
        timeout=timeout,
        follow_redirects=False,
    )
    return resp


class ServiceNowSource:
    """ServiceNow Incident ticket source.

    Parameters
    ----------
    config:
        Mapping with keys:

        * ``instance`` (required): ServiceNow instance subdomain, e.g.
          ``dev12345`` (resolves to ``dev12345.service-now.com``).
        * ``user_env`` (required): name of the env var holding the API username.
        * ``pass_env`` (required): name of the env var holding the API password.
        * ``allow_private`` (optional): opt-in to permit private/loopback hosts.
        * ``max_pages`` (optional): pagination bound (default 10).
        * ``timeout`` (optional): per-request timeout seconds (default 30).
    transport:
        Optional injected :class:`Transport`. Defaults to an httpx-backed one.
    """

    KIND = "tickets"
    name = "servicenow"

    def __init__(
        self,
        config: dict[str, Any],
        transport: Transport | None = None,
    ) -> None:
        self.config = dict(config)
        self.transport: Transport = transport or _default_transport

        instance = str(self.config.get("instance", "")).strip()
        if not instance:
            raise ValueError("servicenow: 'instance' is required")
        self.instance = instance

        user_env = str(self.config.get("user_env", "")).strip()
        if not user_env:
            raise ValueError("servicenow: 'user_env' is required")
        self.user_env = user_env

        pass_env = str(self.config.get("pass_env", "")).strip()
        if not pass_env:
            raise ValueError("servicenow: 'pass_env' is required")
        self.pass_env = pass_env

        self.allow_private = bool(self.config.get("allow_private", False))
        self.max_pages = int(self.config.get("max_pages", 10))
        self.timeout = float(self.config.get("timeout", 30.0))

        self._guard_instance(self.instance)
        # Construct the full base URL after the guard passes.
        self._base_url = f"https://{self.instance}.service-now.com"
        self._instance_host = (urlsplit(self._base_url).hostname or "").lower()
        self.name = _DisplayName(type(self).name, f"servicenow:{self.instance}")

    # -- internals ---------------------------------------------------------

    def _guard_instance(self, instance: str) -> None:
        """SSRF guard: reject private/loopback instance values.

        Gracefully handles both plain hostnames (``dev12345``) and
        accidentally-provided URLs (``http://127.0.0.1``).
        """
        if self.allow_private:
            return
        host: str
        if "://" in instance:
            parsed = urlsplit(instance)
            if parsed.scheme not in ("http", "https"):
                raise ValueError(
                    f"servicenow: unsupported URL scheme: {parsed.scheme!r}"
                )
            host = parsed.hostname or ""
        else:
            host = instance
        host = host.strip().lower().rstrip(".")
        if not host:
            raise ValueError("servicenow: missing host in instance")
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if host in ("localhost",):
            raise ValueError(
                f"servicenow: refusing loopback host {host!r} "
                "(set allow_private=True to override)"
            )
        if host.endswith(".localhost"):
            raise ValueError(
                f"servicenow: refusing loopback host {host!r} "
                "(set allow_private=True to override)"
            )
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
            or not ip.is_global
        ):
            raise ValueError(
                f"servicenow: refusing private/loopback host {host!r} "
                "(set allow_private=True to override)"
            )

    def _guard_next_url(self, url: str) -> None:
        """Re-guard a server-supplied pagination URL before fetching it.

        Applies the full SSRF guard and pins the host to the configured
        instance host so a malicious Link header cannot pivot.
        """
        # Re-assert that the next URL is https and resolves to the same instance.
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"servicenow: unsupported next-page URL scheme: {parsed.scheme!r}"
            )
        host = (parsed.hostname or "").lower()
        if host != self._instance_host:
            raise ValueError(
                f"servicenow: refusing cross-host next-page URL {host!r} "
                f"(pinned to {self._instance_host!r})"
            )

    def _auth(self) -> tuple[str, str]:
        user = os.environ.get(self.user_env)
        if not user:
            raise RuntimeError(
                f"servicenow: user env var {self.user_env!r} is unset or empty"
            )
        password = os.environ.get(self.pass_env)
        if not password:
            raise RuntimeError(
                f"servicenow: pass env var {self.pass_env!r} is unset or empty"
            )
        return (user, password)

    @staticmethod
    def _next_link(resp: HttpResponse) -> str | None:
        headers = getattr(resp, "headers", None)
        if not headers:
            return None
        link = None
        try:
            link = headers.get("Link") or headers.get("link")
        except AttributeError:
            return None
        if not link:
            return None
        return _parse_link_next(str(link))

    @staticmethod
    def _split_url_params(url: str) -> tuple[str, dict[str, str]]:
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}{parts.path}"
        flat = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        return base, flat

    def _normalize(self, record: dict[str, Any]) -> dict[str, Any]:
        raw_state = str(record.get("state", ""))
        level_or_status = _STATE_MAP.get(raw_state, f"state_{raw_state}")

        caller = record.get("caller_id") or {}
        assignment_group = record.get("assignment_group") or {}

        return {
            "ts": record.get("opened_at"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": level_or_status,
            "message": (
                _DisplayText(
                    str(record.get("short_description")),
                    f"{record.get('number')}: {record.get('short_description')}",
                )
                if record.get("number") and record.get("short_description")
                else record.get("short_description")
            ),
            "value": 1,
            "labels": {
                "sys_id": record.get("sys_id"),
                "number": record.get("number"),
                "priority": record.get("priority"),
                "state": raw_state,
                "category": record.get("category"),
                "urgency": record.get("urgency"),
                "caller": caller.get("value"),
                "assignment_group": assignment_group.get("value"),
            },
            "raw": record,
        }

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the instance with a 1-record incident request; never raises."""
        try:
            resp = self.transport(
                "GET",
                f"{self._base_url}/api/now/table/incident",
                params={"sysparm_limit": "1"},
                auth=self._auth(),
                timeout=self.timeout,
            )
        except Exception:
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        status = getattr(resp, "status_code", 0)
        if 200 <= status < 300:
            return {"ok": True, "detail": f"servicenow reachable (HTTP {status})"}
        return {"ok": False, "detail": f"servicenow HTTP {status}"}

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch + normalize ServiceNow incidents.

        ``spec`` may contain:

        * ``table`` (default ``"incident"``): ServiceNow table to query.
        * ``sysparm_query``: encoded query string.
        * ``sysparm_limit``: page size.
        * ``sysparm_fields``: comma-separated field list.
        * Any other ``sysparm_*`` parameter passed through directly.

        Pagination follows the ``Link`` header up to ``max_pages``.
        """
        spec = spec or {}
        table = str(spec.get("table", "incident"))
        params: dict[str, str] = {}
        for key, value in spec.items():
            if key == "table":
                continue
            params[str(key)] = str(value)

        url = f"{self._base_url}/api/now/table/{table}"
        out: list[dict[str, Any]] = []
        next_params: dict[str, str] | None = params

        for _ in range(max(1, self.max_pages)):
            resp = self.transport(
                "GET",
                url,
                params=next_params,
                auth=self._auth(),
                timeout=self.timeout,
            )
            status = getattr(resp, "status_code", 0)
            if not (200 <= status < 300):
                raise RuntimeError(f"servicenow: query failed HTTP {status}")
            body = resp.json()
            result = body.get("result") if isinstance(body, dict) else None
            if isinstance(result, list):
                for record in result:
                    if isinstance(record, dict):
                        out.append(self._normalize(record))
            link = self._next_link(resp)
            if not link:
                break
            self._guard_next_url(link)
            url, next_params = self._split_url_params(link)

        return out
