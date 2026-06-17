"""Okta System Log audit-event connector.

Self-contained connector — imports nothing from sibling connector modules or a
shared base. It pulls Okta System Log events via the
``GET {org_url}/api/v1/logs`` API and normalizes them into the gludd event
envelope.

Security posture:

* The Okta API token is read **only** from an environment variable named by
  ``config['token_env']`` (never hardcoded, never logged).
* ``org_url`` is SSRF-guarded against literal private / loopback / link-local
  hosts unless ``config['allow_private']`` is explicitly truthy.
* The injected HTTP transport is time-bounded and never uses ``shell=True``.
* ``health()`` never raises.
"""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlparse, urlsplit


@runtime_checkable
class Response(Protocol):
    """Minimal structural contract for an HTTP response object."""

    status_code: int

    def json(self) -> Any:  # pragma: no cover - structural typing only
        ...

    @property
    def headers(self) -> Any:  # pragma: no cover - structural typing only
        ...


@runtime_checkable
class Transport(Protocol):
    """Injectable, synchronous HTTP transport.

    A callable taking an HTTP method and URL plus keyword args and returning a
    :class:`Response`. The default implementation wraps :mod:`httpx`, but tests
    inject a fake so no network access ever occurs.
    """

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
# Matches an Okta System Log `Link: <url>; rel="next"` header entry.
_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"', re.IGNORECASE)


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
    )
    return resp


def _is_private_host(host: str) -> bool:
    """Return True if ``host`` is a literal private/loopback/link-local target.

    Only literal hostnames are evaluated; DNS is never resolved here (a literal
    SSRF block, as specified by the contract).
    """
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


class OktaSource:
    """Okta System Log audit-event source.

    Parameters
    ----------
    config:
        Mapping with keys:

        * ``org_url`` (required): the Okta org base URL, e.g.
          ``https://example.okta.com``.
        * ``token_env`` (required): name of the env var holding the API token.
        * ``allow_private`` (optional): opt-in to permit private/loopback hosts.
        * ``max_pages`` (optional): pagination bound (default 10).
        * ``timeout`` (optional): per-request timeout seconds (default 30).
    transport:
        Optional injected :class:`Transport`. Defaults to an httpx-backed one.
    """

    KIND = "events"
    name = "okta"

    def __init__(
        self,
        config: dict[str, Any],
        transport: Transport | None = None,
    ) -> None:
        self.config = dict(config)
        self.transport: Transport = transport or _default_transport

        org_url = str(self.config.get("org_url", "")).strip()
        if not org_url:
            raise ValueError("okta: 'org_url' is required")
        self.org_url = org_url.rstrip("/")

        self.token_env = str(self.config.get("token_env", "")).strip()
        if not self.token_env:
            raise ValueError("okta: 'token_env' is required")

        self.allow_private = bool(self.config.get("allow_private", False))
        self.max_pages = int(self.config.get("max_pages", 10))
        self.timeout = float(self.config.get("timeout", 30.0))

        self._guard_ssrf(self.org_url)
        # Pin the configured host so a server-supplied next-page URL cannot
        # redirect us off-org (response-driven SSRF).
        self._org_host = (urlsplit(self.org_url).hostname or "").lower()

    # -- internals ---------------------------------------------------------

    def _guard_ssrf(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"okta: unsupported URL scheme: {parsed.scheme!r}")
        host = parsed.hostname or ""
        if not self.allow_private and _is_private_host(host):
            raise ValueError(
                f"okta: refusing private/loopback host {host!r} "
                "(set allow_private=True to override)"
            )

    def _guard_next_url(self, url: str) -> None:
        """Re-guard a server-supplied pagination URL before fetching it.

        Applies the full SSRF scheme/private-host guard *and* pins the host to
        the configured ``org_url`` host so a malicious ``Link: rel="next"``
        header cannot pivot to an internal metadata endpoint or a third party.
        """
        self._guard_ssrf(url)
        host = (urlsplit(url).hostname or "").lower()
        if host != self._org_host:
            raise ValueError(
                f"okta: refusing cross-host next-page URL {host!r} "
                f"(pinned to {self._org_host!r})"
            )

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"okta: token env var {self.token_env!r} is unset or empty"
            )
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"SSWS {self._token()}",
            "Accept": "application/json",
        }

    @staticmethod
    def _next_link(resp: Response) -> str | None:
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
        match = _LINK_NEXT_RE.search(str(link))
        return match.group(1) if match else None

    @staticmethod
    def _split_url_params(url: str) -> tuple[str, dict[str, str]]:
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}{parts.path}"
        flat = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        return base, flat

    def _normalize(self, record: dict[str, Any]) -> dict[str, Any]:
        outcome = record.get("outcome") or {}
        actor = record.get("actor") or {}
        client = record.get("client") or {}
        return {
            "ts": record.get("published"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": outcome.get("result"),
            "message": record.get("displayMessage"),
            "value": 1,
            "labels": {
                "eventType": record.get("eventType"),
                "actor": actor.get("alternateId") or actor.get("displayName"),
                "client_ip": client.get("ipAddress"),
                "outcome": outcome.get("result"),
            },
            "raw": record,
        }

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the org with a 1-record request; never raises."""
        try:
            resp = self.transport(
                "GET",
                f"{self.org_url}/api/v1/logs",
                headers=self._headers(),
                params={"limit": "1"},
                timeout=self.timeout,
            )
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        status = getattr(resp, "status_code", 0)
        if 200 <= status < 300:
            return {"ok": True, "detail": f"okta reachable (HTTP {status})"}
        return {"ok": False, "detail": f"okta HTTP {status}"}

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch + normalize System Log events.

        ``spec`` may contain ``since`` / ``until`` (ISO-8601) and ``filter``
        (Okta SCIM-style filter expression). Pagination follows the ``Link``
        header up to ``max_pages``.
        """
        spec = spec or {}
        params: dict[str, str] = {}
        if spec.get("since"):
            params["since"] = str(spec["since"])
        if spec.get("until"):
            params["until"] = str(spec["until"])
        if spec.get("filter"):
            params["filter"] = str(spec["filter"])
        if spec.get("limit"):
            params["limit"] = str(spec["limit"])

        url = f"{self.org_url}/api/v1/logs"
        out: list[dict[str, Any]] = []
        next_params: dict[str, str] | None = params

        for _ in range(max(1, self.max_pages)):
            resp = self.transport(
                "GET",
                url,
                headers=self._headers(),
                params=next_params,
                timeout=self.timeout,
            )
            status = getattr(resp, "status_code", 0)
            if not (200 <= status < 300):
                raise RuntimeError(f"okta: query failed HTTP {status}")
            body = resp.json()
            if isinstance(body, list):
                for record in body:
                    if isinstance(record, dict):
                        out.append(self._normalize(record))
            link = self._next_link(resp)
            if not link:
                break
            # Re-guard the server-supplied next URL BEFORE we trust/fetch it.
            self._guard_next_url(link)
            url, next_params = self._split_url_params(link)

        return out
