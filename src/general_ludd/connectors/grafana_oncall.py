"""Grafana OnCall incident-source connector.

Self-contained: no imports from sibling connectors or a shared base. Pulls
alert groups from a Grafana OnCall instance and normalizes them into the
incident record shape used across gludd's incident sources.

SECURITY NOTES:
  - The API token is read ONLY from the environment variable named by
    ``config['token_env']`` (default ``GRAFANA_ONCALL_TOKEN``). It is never
    accepted inline, never hardcoded, and never written to any record, label,
    log line, or raised error.
  - ``base_url`` is REQUIRED (Grafana OnCall is instance-specific; there is no
    universal public host). It is SSRF-guarded: private/loopback/link-local/
    non-public hosts are rejected unless ``allow_private=True``. Self-hosted
    on-prem instances therefore require an explicit ``allow_private`` opt-in.
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

_DEFAULT_TOKEN_ENV = "GRAFANA_ONCALL_TOKEN"
_DEFAULT_TIMEOUT = 15.0
_DEFAULT_LIMIT = 100


class GrafanaOnCallSource:
    """Incident source backed by the Grafana OnCall API.

    GET {base_url}/api/v1/alert_groups
    Header: ``Authorization: <token>`` (raw token, no scheme prefix)
    """

    KIND = "incidents"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        config = dict(config or {})
        self.name: str = str(config.get("name", "grafana_oncall"))
        self._token_env: str = str(config.get("token_env", _DEFAULT_TOKEN_ENV))
        self._timeout: float = float(config.get("timeout", _DEFAULT_TIMEOUT))
        self._limit: int = int(config.get("limit", _DEFAULT_LIMIT))
        self._allow_private: bool = bool(config.get("allow_private", False))

        base_url = config.get("base_url")
        if not base_url:
            raise ValueError("GrafanaOnCallSource requires config['base_url']")
        base_url = str(base_url)
        # base_url is always a caller-supplied override here -> always guarded.
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
                    f"{self._base_url}/api/v1/alert_groups",
                    headers=_auth_header(token),
                    params={"perpage": 1},
                )
            if resp.status_code == 200:
                return {"ok": True, "detail": "grafana oncall reachable"}
            return {
                "ok": False,
                "detail": f"unexpected status {resp.status_code}",
            }
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": _safe_err(exc)}

    # -- query -------------------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch alert groups and normalize to incident records."""
        spec = dict(spec or {})
        params: dict[str, Any] = {"perpage": int(spec.get("limit", self._limit))}
        if spec.get("state"):
            params["state"] = str(spec["state"])

        token = self._token()
        with self._client() as client:
            resp = client.get(
                f"{self._base_url}/api/v1/alert_groups",
                headers=_auth_header(token),
                params=params,
            )
            resp.raise_for_status()
            payload = resp.json()

        groups: list[Any]
        if isinstance(payload, dict):
            raw = payload.get("results")
            if raw is None:
                raw = payload.get("data")
            groups = raw if isinstance(raw, list) else []
        elif isinstance(payload, list):
            groups = payload
        else:
            groups = []
        return [self._normalize(group) for group in groups]

    def _normalize(self, group: dict[str, Any]) -> dict[str, Any]:
        state = group.get("state")
        return {
            "ts": group.get("created_at"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": state,
            "message": group.get("title"),
            "value": None,
            "labels": {
                "integration": group.get("integration"),
                "team": group.get("team"),
                "state": state,
                "acknowledged_by": group.get("acknowledged_by"),
            },
            "raw": group,
        }


# ---------------------------------------------------------------------------
# helpers (module-private; not shared with sibling connectors)
# ---------------------------------------------------------------------------


class _MissingToken(RuntimeError):
    """Raised internally when the configured token env var is absent."""


def _auth_header(token: str) -> dict[str, str]:
    return {
        "Authorization": token,
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
        except OSError as exc:
            # Fail closed: an unresolvable host must not be silently allowed
            # (a DNS failure could mask an internal/rebinding target). Match
            # the cilium_hubble / nomad connectors' fail-closed posture.
            raise ValueError(
                f"base_url host {host!r} could not be resolved "
                "(set allow_private=True to override)"
            ) from exc
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
