"""Rollbar error-events connector (KIND='logs').

Self-contained namespace-package module: it deliberately does NOT import any
sibling connector module, a shared ``base``/``__init__``, or third-party HTTP
clients at module scope. The HTTP transport is injected so tests can run against
a canned mock with no network.

Contract (shared across general_ludd.connectors.*):
  * class attr ``KIND`` in {logs, metrics, traces}
  * instance attr ``name``
  * ``__init__(config)`` — config-driven; secrets read from ``os.environ`` via
    a configured ``*_env`` key (never hardcoded)
  * literal-host SSRF block on ``base_url`` (no DNS): loopback, RFC-1918,
    link-local, and the cloud metadata address 169.254.169.254 are rejected
  * ``health() -> {'ok', 'detail'}`` never raises
  * ``query(spec) -> list[dict]`` normalized records with keys:
    ts, source, kind, level_or_status, message, value, labels, raw

"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.connectors._errors import ConnectorConfigError
from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked


@runtime_checkable
class HttpTransport(Protocol):
    """Injectable HTTP transport. ``requests``/``httpx`` clients satisfy this."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        params: dict[str, object] | None = ...,
        timeout: float | None = ...,
    ) -> HttpResponse: ...


def _assert_public_base_url(base_url: str) -> None:
    """Reject a ``base_url`` whose *literal* host is internal.

    No DNS resolution is performed (that would itself be an SSRF vector and is
    non-deterministic). The private/loopback/metadata decision is delegated to
    the canonical :func:`general_ludd.security.ssrf.is_url_blocked` so this
    connector can never drift from the single source of truth. An always-on
    scheme and present-host check is kept for a precise error message.
    """
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ConnectorConfigError(f"unsupported scheme: {parts.scheme!r}")
    if not parts.hostname:
        raise ConnectorConfigError("base_url has no host")
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ConnectorConfigError(
            f"base_url host is internal/loopback/metadata and is blocked: {base_url!r}"
        )


def _coerce_ts(value: object) -> object:
    """Pass timestamps through unchanged; Rollbar emits epoch seconds."""
    return value


class RollbarSource:
    """Read Rollbar items (error events) and normalize them to records."""

    KIND = "logs"
    DEFAULT_BASE_URL = "https://api.rollbar.com"

    def __init__(
        self,
        config: dict[str, object],
        transport: HttpTransport,
        *,
        environ: dict[str, str] | None = None,
    ) -> None:
        env = os.environ if environ is None else environ
        self.name = str(config.get("name", "rollbar"))
        self.base_url = str(config.get("base_url", self.DEFAULT_BASE_URL)).rstrip("/")
        _assert_public_base_url(self.base_url)
        token_env = config.get("token_env")
        if not token_env:
            raise ConnectorConfigError("config must set 'token_env' (name of env var holding the token)")
        token = env.get(str(token_env))
        if not token:
            raise ConnectorConfigError(f"environment variable {token_env!r} is unset or empty")
        self._token = token
        self._transport = transport
        self._timeout = float(str(config.get("timeout", 15.0)))

    def _headers(self) -> dict[str, str]:
        return {"X-Rollbar-Access-Token": self._token, "Accept": "application/json"}

    def health(self) -> dict[str, object]:
        """Probe the items endpoint; never raises."""
        try:
            resp = self._transport.request(
                "GET",
                f"{self.base_url}/api/1/items",
                headers=self._headers(),
                params={"page": 1},
                timeout=self._timeout,
            )
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"request failed: {exc}"}
        if 200 <= resp.status_code < 300:
            return {"ok": True, "detail": f"HTTP {resp.status_code}"}
        return {"ok": False, "detail": f"HTTP {resp.status_code}"}

    def query(self, spec: dict[str, object] | None = None) -> list[dict[str, object]]:
        spec = spec or {}
        params: dict[str, object] = {}
        if "status" in spec:
            params["status"] = spec["status"]
        if "environment" in spec:
            params["environment"] = spec["environment"]
        if "page" in spec:
            params["page"] = spec["page"]
        resp = self._transport.request(
            "GET",
            f"{self.base_url}/api/1/items",
            headers=self._headers(),
            params=params,
            timeout=self._timeout,
        )
        if not (200 <= resp.status_code < 300):
            raise ConnectorConfigError(f"rollbar query failed: HTTP {resp.status_code}")
        payload = resp.json()
        items = (payload or {}).get("result", {}).get("items", [])
        return [self._normalize(item) for item in items]

    def _normalize(self, item: dict[str, object]) -> dict[str, object]:
        return {
            "ts": _coerce_ts(item.get("last_occurrence_timestamp")),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": item.get("level"),
            "message": item.get("title"),
            "value": item.get("total_occurrences"),
            "labels": {
                "counter": item.get("counter"),
                "environment": item.get("environment"),
                "status": item.get("status"),
            },
            "raw": item,
        }
