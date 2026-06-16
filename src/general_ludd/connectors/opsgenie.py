"""Opsgenie incident-source connector.

Self-contained: no imports from sibling connectors or a shared base. Pulls
alerts from the Opsgenie REST API and normalizes them into the incident record
shape used across gludd's incident sources.

SECURITY NOTES:
  - The API key is read ONLY from the environment variable named by
    ``config['token_env']`` (default ``OPSGENIE_API_KEY``). It is never accepted
    inline, never hardcoded, and never written to any record, label, log line,
    or raised error.
  - ``base_url`` may be overridden (e.g. EU region or test stub). Any override is
    SSRF-guarded; private/loopback/link-local/non-public hosts are rejected
    unless ``allow_private=True``.
  - HTTP requests are time-bound and never use a shell.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.opsgenie.com"
_DEFAULT_TOKEN_ENV = "OPSGENIE_API_KEY"
_DEFAULT_TIMEOUT = 15.0
_DEFAULT_LIMIT = 100


class OpsgenieSource:
    """Incident source backed by the Opsgenie REST API.

    GET https://api.opsgenie.com/v2/alerts?query=&limit=
    Header: ``Authorization: GenieKey <token>``
    """

    KIND = "incidents"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        config = dict(config or {})
        self.name: str = str(config.get("name", "opsgenie"))
        self._token_env: str = str(config.get("token_env", _DEFAULT_TOKEN_ENV))
        self._timeout: float = float(config.get("timeout", _DEFAULT_TIMEOUT))
        self._limit: int = int(config.get("limit", _DEFAULT_LIMIT))
        self._allow_private: bool = bool(config.get("allow_private", False))
        self._query: str = str(config.get("query", ""))

        base_url = str(config.get("base_url", _DEFAULT_BASE_URL))
        if base_url.rstrip("/") != _DEFAULT_BASE_URL:
            _guard_ssrf(base_url, allow_private=self._allow_private)
        self._base_url: str = base_url.rstrip("/")
        self._transport = transport

    # -- secrets -----------------------------------------------------------

    def _token(self) -> str:
        token = os.environ.get(self._token_env)
        if not token:
            raise _MissingToken(
                f"environment variable {self._token_env} is not set"
            )
        return token

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout, transport=self._transport)

    # -- health ------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return ``{'ok': bool, 'detail': str}``. Never raises."""
        try:
            token = self._token()
        except _MissingToken as exc:
            return {"ok": False, "detail": str(exc)}
        try:
            with self._client() as client:
                resp = client.get(
                    f"{self._base_url}/v2/alerts",
                    headers=_auth_header(token),
                    params={"limit": 1},
                )
            if resp.status_code == 200:
                return {"ok": True, "detail": "opsgenie reachable"}
            return {
                "ok": False,
                "detail": f"unexpected status {resp.status_code}",
            }
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": _safe_err(exc)}

    # -- query -------------------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch alerts and normalize to incident records."""
        spec = dict(spec or {})
        params: dict[str, Any] = {"limit": int(spec.get("limit", self._limit))}
        query = str(spec.get("query", self._query))
        if query:
            params["query"] = query

        token = self._token()
        with self._client() as client:
            resp = client.get(
                f"{self._base_url}/v2/alerts",
                headers=_auth_header(token),
                params=params,
            )
            resp.raise_for_status()
            payload = resp.json()

        alerts = payload.get("data", []) if isinstance(payload, dict) else []
        return [self._normalize(alert) for alert in alerts]

    def _normalize(self, alert: dict[str, Any]) -> dict[str, Any]:
        status = alert.get("status")
        priority = alert.get("priority")
        return {
            "ts": alert.get("createdAt"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": status or priority,
            "message": alert.get("message"),
            "value": None,
            "labels": {
                "priority": priority,
                "owner": alert.get("owner"),
                "team": (alert.get("ownerTeamId") or alert.get("team")),
                "tinyId": alert.get("tinyId"),
                "acknowledged": alert.get("acknowledged"),
            },
            "raw": alert,
        }


# ---------------------------------------------------------------------------
# helpers (module-private; not shared with sibling connectors)
# ---------------------------------------------------------------------------


class _MissingToken(RuntimeError):
    """Raised internally when the configured token env var is absent."""


def _auth_header(token: str) -> dict[str, str]:
    return {
        "Authorization": f"GenieKey {token}",
        "Accept": "application/json",
    }


def _safe_err(exc: Exception) -> str:
    """Best-effort error string that never leaks the request URL/credentials."""
    return f"{type(exc).__name__}"


def _guard_ssrf(base_url: str, *, allow_private: bool) -> None:
    """Reject base URLs whose host is private/loopback unless opted in."""
    if allow_private:
        return
    host = urlsplit(base_url).hostname
    if not host:
        raise ValueError("base_url has no host")

    candidates: list[Any] = []
    try:
        candidates.append(ipaddress.ip_address(host))
    except ValueError:
        lowered = host.lower()
        if lowered in {"localhost"} or lowered.endswith(
            (".localhost", ".local", ".internal", ".lan", ".intranet", ".corp")
        ):
            raise ValueError(
                f"base_url host {host!r} is an internal name "
                "(set allow_private=True to override)"
            ) from None
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return
        for info in infos:
            addr = str(info[4][0])
            try:
                candidates.append(ipaddress.ip_address(addr.split("%")[0]))
            except ValueError:
                continue

    for ip in candidates:
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(
                f"base_url host {host!r} resolves to a non-public address "
                "(set allow_private=True to override)"
            )
