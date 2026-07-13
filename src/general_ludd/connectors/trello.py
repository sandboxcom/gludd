"""Trello task-board connector.

Self-contained connector — imports nothing from sibling connector modules or a
shared base. It fetches Trello cards via the REST API at
``https://api.trello.com/1`` and normalizes them into the gludd event envelope.

Security posture:

* The Trello API key and token are read **only** from environment variables
  named by ``config['key_env']`` and ``config['token_env']`` (never hardcoded,
  never logged).
* The base URL is SSRF-guarded against private / loopback / link-local hosts.
* The injected HTTP transport is time-bounded.
* ``health()`` never raises.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

TRELLO_BASE_URL = "https://api.trello.com/1"


@runtime_checkable
class Transport(Protocol):
    """Injectable, synchronous HTTP transport.

    A callable taking an HTTP method and URL plus keyword args and returning a
    :class:`HttpResponse`. The default implementation wraps :mod:`httpx`, but
    tests inject a fake so no network access ever occurs.
    """

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse: ...


def _default_transport(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
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
        json=json,
        timeout=timeout,
        follow_redirects=False,
    )
    return resp


class TrelloSource:
    """Trello task source.

    Parameters
    ----------
    config:
        Mapping with keys:

        * ``board_id`` (required): Trello board ID.
        * ``key_env`` (required): name of the env var holding the API key.
        * ``token_env`` (required): name of the env var holding the API token.
        * ``base_url`` (optional): override base URL (default
          ``https://api.trello.com/1``).
        * ``allow_private`` (optional): allow private IPs (default False).
        * ``max_pages`` (optional): pagination bound (default 10).
        * ``timeout`` (optional): per-request timeout seconds (default 30.0).
    transport:
        Optional injected :class:`Transport`. Defaults to an httpx-backed one.
    """

    KIND = "tasks"
    name = "trello"

    def __init__(
        self,
        config: dict[str, Any],
        transport: Transport | None = None,
    ) -> None:
        self.config = dict(config)
        self.transport: Transport = transport or _default_transport

        self.board_id = str(self.config.get("board_id", "")).strip()
        if not self.board_id:
            raise ValueError("trello: 'board_id' is required")

        self.key_env = str(self.config.get("key_env", "TRELLO_KEY")).strip()
        self.token_env = str(self.config.get("token_env", "TRELLO_TOKEN")).strip()

        self.allow_private = bool(self.config.get("allow_private", False))
        self.max_pages = int(self.config.get("max_pages", 10))
        self.page_size = int(self.config.get("page_size", 50))
        self.timeout = float(self.config.get("timeout", 30.0))

        self.base_url = str(self.config.get("base_url", TRELLO_BASE_URL)).strip()
        self._guard_ssrf(self.base_url)

    # -- internals ---------------------------------------------------------

    def _guard_ssrf(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"trello: unsupported URL scheme: {parsed.scheme!r}"
            )
        if self.allow_private:
            return
        if is_url_blocked(url, scheme_allowlist=("http", "https")):
            host = parsed.hostname or ""
            raise ValueError(
                f"trello: refusing private/loopback host {host!r}"
            )

    def _api_key(self) -> str:
        token = os.environ.get(self.key_env)
        if not token:
            raise RuntimeError(
                f"trello: API key env var {self.key_env!r} is unset or empty"
            )
        return token

    def _api_token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"trello: API token env var {self.token_env!r} is unset or empty"
            )
        return token

    def _auth_params(self) -> dict[str, str]:
        return {"key": self._api_key(), "token": self._api_token()}

    def _normalize(self, card: dict[str, Any]) -> dict[str, Any]:
        due_str = card.get("due")
        closed = bool(card.get("closed", False))

        if closed:
            level_or_status = "closed"
        elif due_str:
            try:
                due_dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
                delta = due_dt - datetime.now(timezone.utc)
                level_or_status = "due_soon" if delta.total_seconds() < 259200 else "open"
            except (ValueError, TypeError):
                level_or_status = "open"
        else:
            level_or_status = "open"

        return {
            "ts": card.get("dateLastActivity"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": level_or_status,
            "message": card.get("name"),
            "value": 1,
            "labels": {
                "id": card.get("id"),
                "idList": card.get("idList"),
                "due": card.get("due"),
                "closed": closed,
            },
            "raw": card,
        }

    def _cards_url(self, spec: dict[str, Any]) -> str:
        list_id = spec.get("list_id")
        if list_id:
            return f"{self.base_url}/lists/{list_id}/cards"
        return f"{self.base_url}/boards/{self.board_id}/cards"

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the Trello API board endpoint; never raises."""
        try:
            resp = self.transport(
                "GET",
                f"{self.base_url}/boards/{self.board_id}",
                headers={"Accept": "application/json"},
                params={**self._auth_params(), "cards": "none"},
                timeout=self.timeout,
            )
        except Exception:
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        status = getattr(resp, "status_code", 0)
        if 200 <= status < 300:
            return {"ok": True, "detail": f"trello reachable (HTTP {status})"}
        return {"ok": False, "detail": f"trello HTTP {status}"}

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch + normalize Trello cards via REST API.

        Pagination follows ``before`` / ``limit`` up to ``max_pages``.
        """
        spec = spec or {}
        out: list[dict[str, Any]] = []
        before: str | None = None

        for _ in range(max(1, self.max_pages)):
            params = {**self._auth_params(), "limit": self.page_size}
            if before:
                params["before"] = before

            resp = self.transport(
                "GET",
                self._cards_url(spec),
                headers={"Accept": "application/json"},
                params=params,
                timeout=self.timeout,
            )
            status = getattr(resp, "status_code", 0)
            if not (200 <= status < 300):
                raise RuntimeError(f"trello: query failed HTTP {status}")

            body = resp.json()
            if not isinstance(body, list):
                break

            for card in body:
                if isinstance(card, dict):
                    out.append(self._normalize(card))

            if len(body) < self.page_size:
                break

            last = body[-1]
            if isinstance(last, dict):
                before = last.get("id")
            if not before:
                break

        return out
