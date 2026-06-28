"""Cloudflare audit-log connector.

Self-contained connector — imports nothing from sibling connector modules or a
shared base. It pulls Cloudflare account audit logs via
``GET https://api.cloudflare.com/client/v4/accounts/{id}/audit_logs`` (or a
caller-supplied ``base_url`` for zone-scoped logs) and normalizes them into the
gludd event envelope.

Security posture:

* The Cloudflare API token is read **only** from an environment variable named
  by ``config['token_env']`` (never hardcoded, never logged).
* ``base_url`` is SSRF-guarded against literal private / loopback / link-local
  hosts unless ``config['allow_private']`` is explicitly truthy.
* The injected HTTP transport is time-bounded and never uses ``shell=True``.
* ``health()`` never raises.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

_DEFAULT_API = "https://api.cloudflare.com/client/v4"


@runtime_checkable
class Response(Protocol):
    """Minimal structural contract for an HTTP response object."""

    status_code: int

    def json(self) -> Any:  # pragma: no cover - structural typing only
        ...


@runtime_checkable
class Transport(Protocol):
    """Injectable, synchronous HTTP transport."""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> Response:  # pragma: no cover - structural typing only
        ...


_PRIVATE_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)


def _default_transport(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> Response:
    """Default transport backed by httpx (imported lazily to stay optional)."""
    import httpx

    resp = httpx.request(
        method,
        url,
        headers=headers,
        params=params,
        timeout=timeout,
        follow_redirects=False,
    )
    return resp


def _is_private_host(host: str) -> bool:
    """Return True if ``host`` is a literal private/loopback/link-local target."""
    bare = host.strip().strip("[]").lower()
    if not bare:
        return True
    if bare in _PRIVATE_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(bare)
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
    )


class CloudflareSource:
    """Cloudflare audit-log event source.

    Parameters
    ----------
    config:
        Mapping with keys:

        * ``account_id`` (required unless ``base_url`` is supplied): the
          Cloudflare account id whose audit logs to read.
        * ``token_env`` (required): name of the env var holding the API token.
        * ``base_url`` (optional): override the full audit-log endpoint (e.g.
          for zone-scoped logs); also overrides the API host.
        * ``api_url`` (optional): override the API root (default the public
          Cloudflare v4 API). Useful for self-hosted gateways.
        * ``allow_private`` (optional): opt-in to permit private/loopback hosts.
        * ``max_pages`` (optional): pagination bound (default 10).
        * ``per_page`` (optional): page size (default 100).
        * ``timeout`` (optional): per-request timeout seconds (default 30).
    transport:
        Optional injected :class:`Transport`. Defaults to an httpx-backed one.
    """

    KIND = "events"
    name = "cloudflare"

    def __init__(
        self,
        config: dict[str, Any],
        transport: Transport | None = None,
    ) -> None:
        self.config = dict(config)
        self.transport: Transport = transport or _default_transport

        self.token_env = str(self.config.get("token_env", "")).strip()
        if not self.token_env:
            raise ValueError("cloudflare: 'token_env' is required")

        self.allow_private = bool(self.config.get("allow_private", False))
        self.max_pages = int(self.config.get("max_pages", 10))
        self.per_page = int(self.config.get("per_page", 100))
        self.timeout = float(self.config.get("timeout", 30.0))

        base_url = str(self.config.get("base_url", "")).strip()
        if base_url:
            self.base_url = base_url.rstrip("/")
        else:
            account_id = str(self.config.get("account_id", "")).strip()
            if not account_id:
                raise ValueError(
                    "cloudflare: 'account_id' is required when 'base_url' is unset"
                )
            api_url = str(self.config.get("api_url", _DEFAULT_API)).strip().rstrip("/")
            self.base_url = f"{api_url}/accounts/{account_id}/audit_logs"

        self._guard_ssrf(self.base_url)

    # -- internals ---------------------------------------------------------

    def _guard_ssrf(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"cloudflare: unsupported URL scheme: {parsed.scheme!r}"
            )
        host = parsed.hostname or ""
        if not self.allow_private and _is_private_host(host):
            raise ValueError(
                f"cloudflare: refusing private/loopback host {host!r} "
                "(set allow_private=True to override)"
            )

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"cloudflare: token env var {self.token_env!r} is unset or empty"
            )
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/json",
        }

    def _normalize(self, record: dict[str, Any]) -> dict[str, Any]:
        action = record.get("action") or {}
        actor = record.get("actor") or {}
        resource = record.get("resource") or {}
        ts = record.get("when") or record.get("timestamp")
        return {
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": action.get("result"),
            "message": action.get("type"),
            "value": 1,
            "labels": {
                "actor_email": actor.get("email"),
                "resource_type": resource.get("type"),
                "zone": (record.get("zone") or {}).get("name")
                if isinstance(record.get("zone"), dict)
                else record.get("zone"),
                "client_ip": record.get("actor_ip") or actor.get("ip"),
            },
            "raw": record,
        }

    @staticmethod
    def _result_records(body: Any) -> list[dict[str, Any]]:
        if not isinstance(body, dict):
            return []
        result = body.get("result")
        if isinstance(result, list):
            return [r for r in result if isinstance(r, dict)]
        return []

    @staticmethod
    def _total_pages(body: Any) -> int:
        if not isinstance(body, dict):
            return 1
        info = body.get("result_info")
        if isinstance(info, dict):
            try:
                return max(1, int(info.get("total_pages", 1)))
            except (TypeError, ValueError):
                return 1
        return 1

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the endpoint with a 1-record page; never raises."""
        try:
            resp = self.transport(
                "GET",
                self.base_url,
                headers=self._headers(),
                params={"per_page": "1", "page": "1"},
                timeout=self.timeout,
            )
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        status = getattr(resp, "status_code", 0)
        if 200 <= status < 300:
            return {"ok": True, "detail": f"cloudflare reachable (HTTP {status})"}
        return {"ok": False, "detail": f"cloudflare HTTP {status}"}

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch + normalize audit-log events.

        ``spec`` may contain ``since`` / ``before`` (ISO-8601) and a free-form
        ``direction``. Pagination uses Cloudflare's ``result_info.total_pages``,
        bounded by ``max_pages``.
        """
        spec = spec or {}
        base_params: dict[str, str] = {"per_page": str(self.per_page)}
        if spec.get("since"):
            base_params["since"] = str(spec["since"])
        if spec.get("before"):
            base_params["before"] = str(spec["before"])
        if spec.get("direction"):
            base_params["direction"] = str(spec["direction"])

        out: list[dict[str, Any]] = []
        total_pages = 1
        page = 1
        while page <= max(1, self.max_pages):
            params = dict(base_params)
            params["page"] = str(page)
            resp = self.transport(
                "GET",
                self.base_url,
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )
            status = getattr(resp, "status_code", 0)
            if not (200 <= status < 300):
                raise RuntimeError(f"cloudflare: query failed HTTP {status}")
            body = resp.json()
            for record in self._result_records(body):
                out.append(self._normalize(record))
            if page == 1:
                total_pages = self._total_pages(body)
            if page >= total_pages:
                break
            page += 1

        return out
