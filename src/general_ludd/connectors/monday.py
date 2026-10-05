"""Monday.com issue-tracking connector.

Self-contained connector — imports nothing from sibling connector modules or a
shared base. It pulls Monday.com items via the GraphQL API at
``https://api.monday.com/v2`` and normalizes them into the gludd event
envelope.

Security posture:

* The Monday.com API token is read **only** from an environment variable named by
  ``config['token_env']`` (never hardcoded, never logged).
* The GraphQL endpoint is SSRF-guarded against private / loopback / link-local
  hosts.
* The token is passed as a raw ``Authorization`` header (no ``Bearer`` prefix).
* The injected HTTP transport is time-bounded.
* ``health()`` never raises.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

MONDAY_API_URL = "https://api.monday.com/v2"

_HEALTH_QUERY = "query { boards(limit: 1) { id name } }"

_ITEMS_PAGE_QUERY = """\
query Boards($ids: [ID!]!, $limit: Int!) {
  boards(ids: $ids) {
    id
    items_page(limit: $limit) {
      cursor
      items {
        id
        name
        created_at
        updated_at
        state
        group {
          id
          title
        }
      }
    }
  }
}"""

_NEXT_ITEMS_PAGE_QUERY = """\
query NextItemsPage($cursor: String!) {
  next_items_page(cursor: $cursor) {
    cursor
    items {
      id
      name
      created_at
      updated_at
      state
      group {
        id
        title
      }
    }
  }
}"""


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
        json: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:  # pragma: no cover - structural typing only
        """Execute a synchronous HTTP request."""
        ...


class MondaySource:
    """Monday.com issue-tracking source.

    Parameters
    ----------
    config:
        Mapping with keys:

        * ``board_ids`` (required): list of board IDs or a single ID string.
        * ``token_env`` (required): name of the env var holding the API token.
        * ``allow_private`` (optional): if True, skip SSRF guard on the API URL
          (default ``False``).
        * ``max_pages`` (optional): pagination bound (default 10).
        * ``timeout`` (optional): per-request timeout seconds (default 30).
        * ``mock_url`` (optional): override API URL for testing / SSRF tests.
    transport:
        Optional injected :class:`Transport`. Used for all HTTP calls.
    """

    KIND = "tasks"
    name = "monday"

    def __init__(
        self,
        config: dict[str, Any],
        transport: Transport | None = None,
    ) -> None:
        """Initialize the Monday source from config and an optional transport."""
        # -- board_ids ---------------------------------------------------
        board_ids = config.get("board_ids")
        if isinstance(board_ids, (int, str)):
            board_ids = [board_ids]
        if not board_ids or not isinstance(board_ids, (list, tuple)):
            raise ValueError("monday: 'board_ids' is required and must be non-empty")
        self.board_ids = [int(b) for b in board_ids]

        # -- token_env ---------------------------------------------------
        token_env = config.get("token_env")
        if not isinstance(token_env, str) or not token_env.strip():
            raise ValueError("monday: 'token_env' is required")
        self.token_env: str = token_env.strip()

        # -- optional config ---------------------------------------------
        self.allow_private = bool(config.get("allow_private", False))
        self.max_pages = int(config.get("max_pages", 10))
        self.timeout = float(config.get("timeout", 30.0))
        self.transport = transport

        # -- URL + SSRF --------------------------------------------------
        self.url = str(config.get("mock_url", MONDAY_API_URL)).strip()
        self._guard_ssrf(self.url)

    # -- internals -----------------------------------------------------------

    def _guard_ssrf(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"monday: unsupported URL scheme: {parts.scheme!r}")
        host = parts.hostname or ""
        if is_url_blocked(url, scheme_allowlist=("http", "https")):
            raise ValueError(f"monday: refusing private/loopback host {host!r}")

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(f"monday: token env var {self.token_env!r} is unset or empty")
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._token(),
            "Content-Type": "application/json",
        }

    def _normalize(self, item: dict[str, Any], board_id: int) -> dict[str, Any]:
        group_info = item.get("group") or {}
        return {
            "ts": item.get("updated_at") or item.get("created_at"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": item.get("state") or "unknown",
            "message": item.get("name"),
            "value": 1,
            "labels": {
                "board_id": board_id,
                "item_id": item.get("id"),
                "group": group_info.get("title"),
            },
            "raw": item,
        }

    # -- public API ----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the Monday.com API with a boards query; never raises."""
        try:
            if self.transport is None:
                return {"ok": False, "detail": "health check failed"}
            resp = self.transport(
                "POST",
                self.url,
                headers=self._headers(),
                json={"query": _HEALTH_QUERY},
                timeout=self.timeout,
            )
        except Exception:
            logger.warning("monday health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        status = getattr(resp, "status_code", 0)
        if 200 <= status < 300:
            return {"ok": True, "detail": f"monday reachable (HTTP {status})"}
        return {"ok": False, "detail": f"monday HTTP {status}"}

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch + normalize Monday.com items via GraphQL.

        Pagination follows Monday.com's ``items_page`` cursor up to
        ``max_pages`` per board. Accepts ``limit`` in the spec to restrict
        items per board per page.
        """
        spec = spec or {}
        limit = int(spec.get("limit", 100))

        if self.transport is None:
            raise RuntimeError("monday: no transport available")

        variables: dict[str, Any] = {
            "ids": [str(b) for b in self.board_ids],
            "limit": limit,
        }

        resp = self.transport(
            "POST",
            self.url,
            headers=self._headers(),
            json={"query": _ITEMS_PAGE_QUERY, "variables": variables},
            timeout=self.timeout,
        )
        status = getattr(resp, "status_code", 0)
        if not (200 <= status < 300):
            raise RuntimeError(f"monday: query failed HTTP {status}")

        body = resp.json()
        data = (body or {}).get("data") or {}
        boards = data.get("boards") or []

        out: list[dict[str, Any]] = []
        pending_pages: list[tuple[int, str]] = []
        for board_idx, board in enumerate(boards):
            if not isinstance(board, dict):
                continue
            board_id = self.board_ids[board_idx] if board_idx < len(self.board_ids) else None
            if board_id is None:
                continue
            page = board.get("items_page") or {}
            if not isinstance(page, dict):
                page = {}
            # Backward compatibility: the pinned contract also accepts the
            # legacy boards[].items shape without items_page pagination.
            items = page.get("items") or board.get("items") or []
            for item in items:
                if isinstance(item, dict):
                    out.append(self._normalize(item, board_id))
            initial_cursor = page.get("cursor")
            if isinstance(initial_cursor, str) and initial_cursor:
                pending_pages.append((board_id, initial_cursor))

        page_limit = max(1, self.max_pages)
        for board_id, first_cursor in pending_pages:
            cursor: str | None = first_cursor
            for _ in range(1, page_limit):
                if not cursor:
                    break
                resp = self.transport(
                    "POST",
                    self.url,
                    headers=self._headers(),
                    json={
                        "query": _NEXT_ITEMS_PAGE_QUERY,
                        "variables": {"cursor": cursor},
                    },
                    timeout=self.timeout,
                )
                status = getattr(resp, "status_code", 0)
                if not (200 <= status < 300):
                    raise RuntimeError(f"monday: query failed HTTP {status}")
                body = resp.json()
                data = (body or {}).get("data") or {}
                page = data.get("next_items_page") or {}
                if not isinstance(page, dict):
                    break
                items = page.get("items") or []
                for item in items:
                    if isinstance(item, dict):
                        out.append(self._normalize(item, board_id))
                next_cursor = page.get("cursor")
                cursor = next_cursor if isinstance(next_cursor, str) and next_cursor else None

        return out
