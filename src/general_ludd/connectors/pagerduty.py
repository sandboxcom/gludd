"""PagerDuty incident-source connector.

Self-contained: no imports from sibling connectors or a shared base. Pulls
incidents from the PagerDuty REST API and normalizes them into the incident
record shape used across gludd's incident sources.

SECURITY NOTES:
  - The API token is read ONLY from the environment variable named by
    ``config['token_env']`` (default ``PAGERDUTY_TOKEN``). It is never accepted
    inline in config, never hardcoded, and never written to any record, label,
    log line, or raised error message.
  - ``base_url`` may be overridden in config (e.g. for an EU/region endpoint or a
    test stub). Any override is SSRF-guarded: hosts that resolve to / look like
    private, loopback, link-local, or non-public addresses are rejected unless
    ``allow_private=True`` is explicitly set in config.
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

_DEFAULT_BASE_URL = "https://api.pagerduty.com"
_DEFAULT_TOKEN_ENV = "PAGERDUTY_TOKEN"
_DEFAULT_TIMEOUT = 15.0
_DEFAULT_LIMIT = 100


class PagerDutySource:
    """Incident source backed by the PagerDuty REST API.

    GET https://api.pagerduty.com/incidents?since=&until=&statuses[]=
    Header: ``Authorization: Token token=<token>``
    """

    KIND = "incidents"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        config = dict(config or {})
        self.name: str = str(config.get("name", "pagerduty"))
        self._token_env: str = str(config.get("token_env", _DEFAULT_TOKEN_ENV))
        self._timeout: float = float(config.get("timeout", _DEFAULT_TIMEOUT))
        self._limit: int = int(config.get("limit", _DEFAULT_LIMIT))
        self._allow_private: bool = bool(config.get("allow_private", False))
        self._statuses: list[str] = list(
            config.get("statuses", ["triggered", "acknowledged"])
        )

        base_url = str(config.get("base_url", _DEFAULT_BASE_URL))
        if base_url.rstrip("/") != _DEFAULT_BASE_URL:
            # Only validate when the caller overrode the literal default host.
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
                    f"{self._base_url}/incidents",
                    headers=_auth_header(token),
                    params={"limit": 1},
                )
            if resp.status_code == 200:
                return {"ok": True, "detail": "pagerduty reachable"}
            return {
                "ok": False,
                "detail": f"unexpected status {resp.status_code}",
            }
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": _safe_err(exc)}

    # -- query -------------------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch incidents and normalize to incident records."""
        spec = dict(spec or {})
        params: dict[str, Any] = {"limit": int(spec.get("limit", self._limit))}
        if spec.get("since"):
            params["since"] = str(spec["since"])
        if spec.get("until"):
            params["until"] = str(spec["until"])
        statuses = list(spec.get("statuses", self._statuses))
        if statuses:
            params["statuses[]"] = statuses

        token = self._token()
        with self._client() as client:
            resp = client.get(
                f"{self._base_url}/incidents",
                headers=_auth_header(token),
                params=params,
            )
            resp.raise_for_status()
            payload = resp.json()

        incidents = payload.get("incidents", []) if isinstance(payload, dict) else []
        return [self._normalize(inc) for inc in incidents]

    def _normalize(self, inc: dict[str, Any]) -> dict[str, Any]:
        service = (inc.get("service") or {}).get("summary")
        policy = (inc.get("escalation_policy") or {}).get("summary")
        assignments = inc.get("assignments") or []
        assignee = None
        if assignments:
            assignee = (assignments[0].get("assignee") or {}).get("summary")
        status = inc.get("status")
        urgency = inc.get("urgency")
        return {
            "ts": inc.get("created_at"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": status or urgency,
            "message": inc.get("title"),
            "value": None,
            "labels": {
                "service": service,
                "urgency": urgency,
                "escalation_policy": policy,
                "incident_number": inc.get("incident_number"),
                "assignee": assignee,
            },
            "raw": inc,
        }


# ---------------------------------------------------------------------------
# helpers (module-private; not shared with sibling connectors)
# ---------------------------------------------------------------------------


class _MissingToken(RuntimeError):
    """Raised internally when the configured token env var is absent."""


def _auth_header(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Token token={token}",
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
        # Hostname, not a literal IP. Block obvious internal names outright, then
        # best-effort resolve to catch hostnames that point at private targets.
        # DNS failures are non-fatal (offline/test env): a name that cannot be
        # resolved is left to the network layer rather than blocking a public host.
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
