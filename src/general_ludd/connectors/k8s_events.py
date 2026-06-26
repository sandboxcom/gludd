"""Kubernetes Events connector — `K8sEventsSource`.

Self-contained connector that reads the core/v1 Events API of a Kubernetes
cluster via an INJECTED HTTP transport:

    GET {api}/api/v1/events?...        (Bearer token from *_env, optional CA)

and normalizes each event into the gludd connector record shape:

    {ts, source, kind, level_or_status, message, value, labels, raw}

Security / contract notes:
  * KIND class attribute = 'events'.
  * The Bearer token is read from the environment variable NAMED by
    config['token_env'] — the token value itself is never stored in config.
  * SSRF guard: the API base_url host is checked against a LITERAL
    private/loopback/link-local/metadata block list (no DNS resolution). Since
    Kubernetes API servers are internal, this is opt-in via
    config['allow_private'] = True; otherwise a private host is REJECTED.
  * Watch-bounded: this connector performs a single bounded GET (optionally
    ?limit=N&timeoutSeconds=T) — it NEVER opens an infinite ?watch=1 stream.
  * `health()` NEVER raises — returns {'ok': bool, 'detail': str}.

No imports from sibling connectors or any gludd base module.
"""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Any, Protocol
from urllib.parse import urlsplit


class _Response(Protocol):
    status_code: int

    def json(self) -> Any: ...


class _Transport(Protocol):
    """Injected HTTP transport.

    A callable that performs a GET and returns an object exposing
    ``status_code`` and ``json()``. Tests inject a canned transport; production
    injects an httpx-backed one. The connector never imports a network library.
    """

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        verify: str | bool = True,
        timeout: float | None = None,
    ) -> _Response: ...


# Literal hosts/networks that must never be reached unless allow_private=True.
_BLOCKED_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local incl. cloud metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]
_BLOCKED_HOSTNAMES = {"localhost", "metadata", "metadata.google.internal"}

# Kubernetes event.type -> normalized level
_TYPE_LEVEL = {"Normal": "info", "Warning": "warning"}


class SSRFError(ValueError):
    """Raised when a base_url host is blocked by the literal SSRF guard."""


def host_is_blocked(host: str) -> bool:
    """True if `host` is a literal private/loopback/metadata target (no DNS)."""
    h = host.strip("[]").lower()
    if h in _BLOCKED_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        # Not a literal IP; we do NOT resolve DNS. Only the explicit blocked
        # hostnames above are refused — any other name is allowed through.
        return False
    return any(ip in net for net in _BLOCKED_NETS)


def assert_url_allowed(base_url: str, *, allow_private: bool) -> None:
    """Fail-closed SSRF guard on the literal host of `base_url`."""
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise SSRFError(f"unsupported URL scheme: {parts.scheme!r}")
    host = parts.hostname or ""
    if not host:
        raise SSRFError(f"base_url has no host: {base_url!r}")
    if not allow_private and host_is_blocked(host):
        raise SSRFError(
            f"refusing internal host {host!r} (set allow_private=True for clusters)"
        )


def token_from_env(env_key: str | None) -> str | None:
    """Resolve the Bearer token strictly from the named environment variable."""
    if not env_key:
        return None
    return os.environ.get(env_key)


class K8sEventsSource:
    """Connector over the Kubernetes core/v1 Events API."""

    KIND = "events"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        transport: _Transport | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "k8s_events"))
        self._transport = transport
        self._base_url: str = str(self.config.get("base_url", "")).rstrip("/")
        self._allow_private: bool = bool(self.config.get("allow_private", False))
        self._token_env: str | None = self.config.get("token_env")
        self._ca_cert: str | bool = self.config.get("ca_cert", True)
        # Bound the query so we never approximate an infinite watch.
        self._limit: int = int(self.config.get("limit", 500))
        self._timeout_seconds: int = int(self.config.get("timeout_seconds", 30))
        self._namespace: str | None = self.config.get("namespace")
        # Validate the base_url host at construction time (fail-closed early).
        if self._base_url:
            assert_url_allowed(self._base_url, allow_private=self._allow_private)

    # -- helpers -------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        token = token_from_env(self._token_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _events_url(self) -> str:
        if self._namespace:
            ns = self._sanitize_segment(self._namespace)
            return f"{self._base_url}/api/v1/namespaces/{ns}/events"
        return f"{self._base_url}/api/v1/events"

    @staticmethod
    def _sanitize_segment(segment: str) -> str:
        """K8s names are RFC1123 labels; reject anything that could traverse."""
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,251}[a-z0-9])?", segment):
            raise ValueError(f"invalid kubernetes namespace: {segment!r}")
        return segment

    def _normalize(self, event: dict[str, Any]) -> dict[str, Any]:
        ev_type = event.get("type", "Normal")
        level = _TYPE_LEVEL.get(ev_type, "info")
        involved = event.get("involvedObject", {}) or {}
        source = event.get("source", {}) or {}
        metadata = event.get("metadata", {}) or {}
        ts = (
            event.get("lastTimestamp")
            or event.get("eventTime")
            or event.get("firstTimestamp")
            or metadata.get("creationTimestamp")
        )
        labels = {
            "namespace": involved.get("namespace") or metadata.get("namespace"),
            "kind": involved.get("kind"),
            "name": involved.get("name"),
            "reason": event.get("reason"),
            "component": source.get("component"),
        }
        return {
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": level,
            "message": event.get("message", ""),
            "value": event.get("count"),
            "labels": {k: v for k, v in labels.items() if v is not None},
            "raw": event,
        }

    # -- public API ----------------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Single bounded GET of cluster events; returns normalized records."""
        if self._transport is None:
            raise RuntimeError("K8sEventsSource requires an injected transport")
        spec = dict(spec or {})
        # Re-assert SSRF guard in case base_url changed via spec.
        base = str(spec.get("base_url", self._base_url)).rstrip("/")
        assert_url_allowed(base, allow_private=self._allow_private)
        params: dict[str, Any] = {
            "limit": int(spec.get("limit", self._limit)),
            # timeoutSeconds bounds the request server-side; NOT a watch.
            "timeoutSeconds": int(spec.get("timeout_seconds", self._timeout_seconds)),
        }
        field_selector = spec.get("field_selector")
        if field_selector:
            params["fieldSelector"] = str(field_selector)
        resp = self._transport.get(
            self._events_url(),
            headers=self._headers(),
            params=params,
            verify=self._ca_cert,
            timeout=self._timeout_seconds,
        )
        if resp.status_code != 200:
            return []
        payload = resp.json()
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [self._normalize(e) for e in items if isinstance(e, dict)]

    def health(self) -> dict[str, Any]:
        """Probe the events endpoint with limit=1. NEVER raises."""
        try:
            if self._transport is None:
                return {"ok": False, "detail": "no transport injected"}
            if not self._base_url:
                return {"ok": False, "detail": "no base_url configured"}
            resp = self._transport.get(
                self._events_url(),
                headers=self._headers(),
                params={"limit": 1},
                verify=self._ca_cert,
                timeout=self._timeout_seconds,
            )
            if resp.status_code == 200:
                return {"ok": True, "detail": "events API reachable"}
            return {
                "ok": False,
                "detail": f"events API returned HTTP {resp.status_code}",
            }
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"probe error: {type(exc).__name__}: {exc}"}
