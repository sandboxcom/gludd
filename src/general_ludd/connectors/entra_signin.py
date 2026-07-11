"""Microsoft Entra ID (Azure AD) sign-in audit-log connector.

Self-contained connector — imports nothing from sibling connector modules or a
shared base. It pulls Entra sign-in logs via
``GET https://graph.microsoft.com/v1.0/auditLogs/signIns`` and normalizes them
into the gludd event envelope.

The Graph access token is acquired **externally** (e.g. via the client-
credentials flow run by an operator or a separate service) and provided through
an environment variable. This connector does **not** implement OAuth.

Security posture:

* The bearer token is read **only** from an environment variable named by
  ``config['token_env']`` (never hardcoded, never logged).
* ``base_url`` is SSRF-guarded against literal private / loopback / link-local
  hosts unless ``config['allow_private']`` is explicitly truthy.
* The injected HTTP transport is time-bounded and never uses ``shell=True``.
* ``health()`` never raises.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlparse, urlsplit

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://graph.microsoft.com/v1.0/auditLogs/signIns"


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
    ) -> HttpResponse:  # pragma: no cover - structural typing only
        ...


def _default_transport(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> HttpResponse:
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


class EntraSignInSource:
    """Microsoft Entra ID sign-in audit-log event source.

    Parameters
    ----------
    config:
        Mapping with keys:

        * ``token_env`` (required): name of the env var holding the externally
          acquired Graph bearer token.
        * ``base_url`` (optional): override the signIns endpoint (default the
          public Graph v1.0 endpoint).
        * ``allow_private`` (optional): opt-in to permit private/loopback hosts.
        * ``max_pages`` (optional): pagination bound (default 10).
        * ``timeout`` (optional): per-request timeout seconds (default 30).
    transport:
        Optional injected :class:`Transport`. Defaults to an httpx-backed one.
    """

    KIND = "events"
    name = "entra_signin"

    def __init__(
        self,
        config: dict[str, Any],
        transport: Transport | None = None,
    ) -> None:
        self.config = dict(config)
        self.transport: Transport = transport or _default_transport

        self.token_env = str(self.config.get("token_env", "")).strip()
        if not self.token_env:
            raise ValueError("entra_signin: 'token_env' is required")

        self.base_url = str(self.config.get("base_url", _DEFAULT_BASE)).strip().rstrip(
            "/"
        )
        if not self.base_url:
            raise ValueError("entra_signin: 'base_url' must be non-empty")

        self.allow_private = bool(self.config.get("allow_private", False))
        self.max_pages = int(self.config.get("max_pages", 10))
        self.timeout = float(self.config.get("timeout", 30.0))

        self._guard_ssrf(self.base_url)
        # Pin the configured host so a server-supplied @odata.nextLink cannot
        # redirect us off-endpoint (response-driven SSRF).
        self._base_host = (urlsplit(self.base_url).hostname or "").lower()

    # -- internals ---------------------------------------------------------

    def _guard_ssrf(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"entra_signin: unsupported URL scheme: {parsed.scheme!r}"
            )
        host = parsed.hostname or ""
        # Delegate the private/loopback + cloud-metadata NAME decision to the
        # canonical shared guard so this connector (and its @odata.nextLink
        # re-guard) can never drift weaker than the single source of truth.
        # Private hosts remain opt-in via ``allow_private``.
        if not self.allow_private and is_url_blocked(url):
            raise ValueError(
                f"entra_signin: refusing private/loopback host {host!r} "
                "(set allow_private=True to override)"
            )

    def _guard_next_url(self, url: str) -> None:
        """Re-guard a server-supplied pagination URL before fetching it.

        Applies the full SSRF scheme/private-host guard *and* pins the host to
        the configured ``base_url`` host so a malicious ``@odata.nextLink``
        cannot pivot to an internal metadata endpoint or a third party.
        """
        self._guard_ssrf(url)
        host = (urlsplit(url).hostname or "").lower()
        if host != self._base_host:
            raise ValueError(
                f"entra_signin: refusing cross-host next-page URL {host!r} "
                f"(pinned to {self._base_host!r})"
            )

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"entra_signin: token env var {self.token_env!r} is unset or empty"
            )
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/json",
        }

    @staticmethod
    def _status_label(status: Any) -> str:
        """Map Graph signIn status.errorCode to success/failure."""
        if not isinstance(status, dict):
            return "failure"
        code = status.get("errorCode")
        if code is None:
            return "failure"
        try:
            return "success" if int(code) == 0 else "failure"
        except (TypeError, ValueError):
            return "failure"

    def _normalize(self, record: dict[str, Any]) -> dict[str, Any]:
        status = record.get("status") or {}
        return {
            "ts": record.get("createdDateTime"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": self._status_label(status),
            "message": record.get("appDisplayName"),
            "value": 1,
            "labels": {
                "userPrincipalName": record.get("userPrincipalName"),
                "ipAddress": record.get("ipAddress"),
                "clientAppUsed": record.get("clientAppUsed"),
                "conditionalAccessStatus": record.get("conditionalAccessStatus"),
            },
            "raw": record,
        }

    @staticmethod
    def _records(body: Any) -> list[dict[str, Any]]:
        if not isinstance(body, dict):
            return []
        value = body.get("value")
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
        return []

    @staticmethod
    def _next_link(body: Any) -> str | None:
        if not isinstance(body, dict):
            return None
        link = body.get("@odata.nextLink")
        return str(link) if link else None

    @staticmethod
    def _split_url_params(url: str) -> tuple[str, dict[str, str]]:
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}{parts.path}"
        flat = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        return base, flat

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the endpoint with a 1-record page; never raises."""
        try:
            resp = self.transport(
                "GET",
                self.base_url,
                headers=self._headers(),
                params={"$top": "1"},
                timeout=self.timeout,
            )
        except Exception:  # health must never raise
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        status = getattr(resp, "status_code", 0)
        if 200 <= status < 300:
            return {"ok": True, "detail": f"entra reachable (HTTP {status})"}
        return {"ok": False, "detail": f"entra HTTP {status}"}

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch + normalize sign-in events.

        ``spec`` may contain ``filter`` (an OData ``$filter`` expression) and
        ``top`` (page size). Pagination follows ``@odata.nextLink`` up to
        ``max_pages``.
        """
        spec = spec or {}
        params: dict[str, str] = {}
        if spec.get("filter"):
            params["$filter"] = str(spec["filter"])
        if spec.get("top"):
            params["$top"] = str(spec["top"])

        url = self.base_url
        out: list[dict[str, Any]] = []

        for _ in range(max(1, self.max_pages)):
            resp = self.transport(
                "GET",
                url,
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )
            status = getattr(resp, "status_code", 0)
            if not (200 <= status < 300):
                raise RuntimeError(f"entra_signin: query failed HTTP {status}")
            body = resp.json()
            for record in self._records(body):
                out.append(self._normalize(record))
            link = self._next_link(body)
            if not link:
                break
            # Re-guard the server-supplied next URL BEFORE we trust/fetch it.
            self._guard_next_url(link)
            url, params = self._split_url_params(link)

        return out
