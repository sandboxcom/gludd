"""Zendesk ticket-source connector.

Self-contained connector — imports nothing from sibling connector modules or a
shared base. It pulls ticket records via the ``https://{subdomain}.zendesk.com``
API and normalizes them into the gludd event envelope.

Security posture:

* The Zendesk email and API token are read **only** from environment variables
  named by ``config['email_env']`` and ``config['token_env']`` respectively
  (never hardcoded, never logged).
* ``subdomain`` is SSRF-guarded against literal private / loopback / link-local
  hosts unless ``config['allow_private']`` is explicitly truthy.
* The injected HTTP transport is time-bounded and never uses ``shell=True``.
* ``health()`` never raises.
* Authentication uses HTTP Basic Auth (base64-encoded ``email/token:token``).
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)


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
        timeout: float = 30.0,
    ) -> HttpResponse:# pragma: no cover - structural typing only
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


class ZendeskSource:
    """Zendesk ticket source.

    Parameters
    ----------
    config:
        Mapping with keys:

        * ``subdomain`` (required): the Zendesk subdomain, e.g.
          ``mycompany`` → ``https://mycompany.zendesk.com``.
        * ``email_env`` (required): name of the env var holding the user email
          (e.g. ``ZENDESK_EMAIL``).
        * ``token_env`` (required): name of the env var holding the API token
          (e.g. ``ZENDESK_TOKEN``).
        * ``allow_private`` (optional): opt-in to permit private/loopback hosts.
        * ``max_items`` (optional): maximum tickets per query (default 100).
        * ``timeout`` (optional): per-request timeout seconds (default 30).
    transport:
        Optional injected :class:`Transport`. Defaults to an httpx-backed one.
    """

    KIND = "tickets"
    name = "zendesk"

    def __init__(
        self,
        config: dict[str, Any],
        transport: Transport | None = None,
    ) -> None:
        self.config = dict(config)
        self.transport: Transport = transport or _default_transport

        subdomain = str(self.config.get("subdomain", "")).strip()
        if not subdomain:
            raise ValueError("zendesk: 'subdomain' is required")
        if subdomain.split(".", 1)[0].lower() in {"ftp", "file"}:
            raise ValueError("zendesk: unsupported URL scheme in subdomain")
        if not bool(self.config.get("allow_private", False)):
            probe_url = f"https://{subdomain}"
            has_explicit_host = subdomain.count(chr(46)) > 0 or subdomain.lower() == "localhost"
            if has_explicit_host and is_url_blocked(
                probe_url,
                scheme_allowlist=("http", "https"),
            ):
                raise ValueError(
                    f"zendesk: refusing private/loopback host {subdomain!r} "
                    "(set allow_private=True to override)"
                )
        self.subdomain = subdomain

        self.email_env = str(self.config.get("email_env", "")).strip()
        if not self.email_env:
            raise ValueError("zendesk: 'email_env' is required")

        self.token_env = str(self.config.get("token_env", "")).strip()
        if not self.token_env:
            raise ValueError("zendesk: 'token_env' is required")

        self.allow_private = bool(self.config.get("allow_private", False))
        self.max_items = int(self.config.get("max_items", 100))
        self.timeout = float(self.config.get("timeout", 30.0))

        self._base_url = f"https://{self.subdomain}.zendesk.com"
        self._guard_ssrf(self._base_url)
        self._org_host = (urlsplit(self._base_url).hostname or "").lower()

    # -- internals ---------------------------------------------------------

    def _guard_ssrf(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"zendesk: unsupported URL scheme: {parsed.scheme!r}")
        host = parsed.hostname or ""
        if not self.allow_private and is_url_blocked(url, scheme_allowlist=("http", "https")):
            raise ValueError(
                f"zendesk: refusing private/loopback host {host!r} "
                "(set allow_private=True to override)"
            )

    def _email(self) -> str:
        email = os.environ.get(self.email_env)
        if not email:
            raise RuntimeError(
                f"zendesk: email env var {self.email_env!r} is unset or empty"
            )
        return email

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"zendesk: token env var {self.token_env!r} is unset or empty"
            )
        return token

    def _auth_header(self) -> str:
        credentials = f"{self._email()}/token:{self._token()}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        return f"Basic {encoded}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._auth_header(),
            "Accept": "application/json",
        }

    def _normalize(self, ticket: dict[str, Any]) -> dict[str, Any]:
        ts = ticket.get("updated_at") or ticket.get("created_at")
        status = ticket.get("status", "")
        subject = ticket.get("subject") or ""
        description = ticket.get("description") or ""
        priority = ticket.get("priority") or ""
        ticket_type = ticket.get("type") or ""
        message = subject or description or ""
        return {
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": status,
            "message": message,
            "value": ticket.get("id"),
            "labels": {
                "subject": subject,
                "priority": priority,
                "type": ticket_type,
                "requester_id": ticket.get("requester_id"),
                "assignee_id": ticket.get("assignee_id"),
                "status": status,
            },
            "raw": ticket,
        }

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the Zendesk tickets API; never raises."""
        try:
            resp = self.transport(
                "GET",
                f"{self._base_url}/api/v2/tickets.json",
                headers=self._headers(),
                params={"per_page": "1"},
                timeout=self.timeout,
            )
        except Exception:
            logger.warning("zendesk health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        status = getattr(resp, "status_code", 0)
        if 200 <= status < 300:
            return {"ok": True, "detail": f"zendesk reachable (HTTP {status})"}
        return {"ok": False, "detail": f"zendesk HTTP {status}"}

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch + normalize Zendesk tickets.

        ``spec`` may contain ``sort_by``, ``sort_order``, ``status``, and
        ``per_page``. Paginates up to ``max_items`` total.
        """
        spec = spec or {}
        params: dict[str, str] = {}
        if spec.get("sort_by"):
            params["sort_by"] = str(spec["sort_by"])
        if spec.get("sort_order"):
            params["sort_order"] = str(spec["sort_order"])
        if spec.get("status"):
            params["status"] = str(spec["status"])

        per_page = int(spec.get("per_page", 25))
        url = f"{self._base_url}/api/v2/tickets.json"
        out: list[dict[str, Any]] = []

        while len(out) < self.max_items:
            remaining = self.max_items - len(out)
            params["per_page"] = str(min(per_page, remaining))

            resp = self.transport(
                "GET",
                url,
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )
            status = getattr(resp, "status_code", 0)
            if not (200 <= status < 300):
                raise RuntimeError(f"zendesk: query failed HTTP {status}")

            body = resp.json() or {}
            tickets = body.get("tickets", []) if isinstance(body, dict) else []
            if not isinstance(tickets, list):
                break

            for ticket in tickets:
                if isinstance(ticket, dict):
                    out.append(self._normalize(ticket))
                    if len(out) >= self.max_items:
                        break

            # Zendesk cursor-based pagination
            next_url = None
            if isinstance(body, dict):
                links = body.get("links") or {}
                if isinstance(links, dict):
                    next_url = links.get("next") or links.get("next_page")
            if not next_url or not isinstance(next_url, str):
                break

            # Guard next-URL host before trusting it
            host = (urlsplit(next_url).hostname or "").lower()
            if host != self._org_host:
                break
            url = next_url.rstrip(".json") + ".json" if next_url.endswith(".json") else next_url

        return out
