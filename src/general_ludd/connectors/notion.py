"""Notion database page connector.

Self-contained connector — imports nothing from sibling connector modules or a
shared base. It pulls Notion database pages via the
``POST https://api.notion.com/v1/databases/{database_id}/query`` API and
normalizes them into the gludd event envelope.

Security posture:

* The Notion API token is read **only** from an environment variable named by
  ``config['token_env']`` (never hardcoded, never logged).
* The API base URL is SSRF-guarded against literal private / loopback / link-local
  hosts unless ``config['allow_private']`` is explicitly truthy.
* The injected HTTP transport is time-bounded and never uses ``shell=True``.
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
        json: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:  # pragma: no cover - structural typing only
        ...


API_BASE = "https://api.notion.com/v1"


def _default_transport(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> HttpResponse:
    """Default transport backed by httpx (imported lazily to stay optional)."""
    import httpx

    resp = httpx.request(
        method,
        url,
        headers=headers,
        json=json,
        timeout=timeout,
        follow_redirects=False,
    )
    return resp


class NotionSource:
    """Notion database page source.

    Parameters
    ----------
    config:
        Mapping with keys:

        * ``database_id`` (required): the Notion database UUID.
        * ``token_env`` (required): name of the env var holding the API token.
        * ``notion_version`` (optional): Notion-Version header value
          (default ``"2022-06-28"``).
        * ``allow_private`` (optional): opt-in to permit private/loopback hosts.
        * ``timeout`` (optional): per-request timeout seconds (default 30).
    transport:
        Optional injected :class:`Transport`. Defaults to an httpx-backed one.
    """

    KIND = "pages"
    name = "notion"

    def __init__(
        self,
        config: dict[str, Any],
        transport: Transport | None = None,
    ) -> None:
        self.config = dict(config)
        self.transport: Transport = transport or _default_transport

        database_id = str(self.config.get("database_id", "")).strip()
        if not database_id:
            raise ValueError("notion: 'database_id' is required")
        self.database_id = database_id

        self.token_env = str(self.config.get("token_env", "")).strip()
        if not self.token_env:
            raise ValueError("notion: 'token_env' is required")

        self.notion_version = str(
            self.config.get("notion_version", "2022-06-28")
        ).strip()

        self.allow_private = bool(self.config.get("allow_private", False))
        self.timeout = float(self.config.get("timeout", 30.0))

        api_base = str(
            self.config.get("api_base") or self.config.get("base_url") or API_BASE
        ).rstrip("/")
        self._query_url = f"{api_base}/databases/{self.database_id}/query"
        self._guard_ssrf(self._query_url)

    # -- internals ---------------------------------------------------------

    def _guard_ssrf(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"notion: unsupported URL scheme: {parsed.scheme!r}"
            )
        host = parsed.hostname or ""
        if not self.allow_private and is_url_blocked(
            url, scheme_allowlist=("http", "https")
        ):
            raise ValueError(
                f"notion: refusing private/loopback host {host!r} "
                "(set allow_private=True to override)"
            )

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"notion: token env var {self.token_env!r} is unset or empty"
            )
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Notion-Version": self.notion_version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _normalize(self, record: dict[str, Any]) -> dict[str, Any]:
        record.get("properties") or {}
        created = record.get("created_time", "")
        last_edited = record.get("last_edited_time", "")
        return {
            "ts": last_edited or created,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "info",
            "message": self._page_title(record),
            "value": 1,
            "labels": {
                "page_id": record.get("id"),
                "url": record.get("url"),
                "created_time": created,
                "last_edited_time": last_edited,
            },
            "raw": record,
        }

    @staticmethod
    def _page_title(record: dict[str, Any]) -> str:
        properties = record.get("properties") or {}
        for prop in properties.values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                title_items = prop.get("title") or []
                parts: list[str] = []
                for item in title_items:
                    if isinstance(item, dict):
                        parts.append(item.get("plain_text", ""))
                return " ".join(parts)
        return ""

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the database with a 1-page query; never raises."""
        try:
            resp = self.transport(
                "POST",
                self._query_url,
                headers=self._headers(),
                json={"page_size": 1},
                timeout=self.timeout,
            )
        except Exception:
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        status = getattr(resp, "status_code", 0)
        if 200 <= status < 300:
            return {"ok": True, "detail": f"notion reachable (HTTP {status})"}
        return {"ok": False, "detail": f"notion HTTP {status}"}

    def query(
        self, spec: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Fetch + normalize database pages.

        ``spec`` may contain ``start_cursor`` for cursor-based pagination
        and ``filter`` for Notion filter objects.
        """
        spec = spec or {}
        payload: dict[str, Any] = {
            "page_size": 100,
            "start_cursor": spec.get("start_cursor"),
        }

        if spec.get("filter"):
            payload["filter"] = spec["filter"]

        out: list[dict[str, Any]] = []
        has_more = True

        while has_more:
            resp = self.transport(
                "POST",
                self._query_url,
                headers=self._headers(),
                json=dict(payload),
                timeout=self.timeout,
            )
            status = getattr(resp, "status_code", 0)
            if not (200 <= status < 300):
                raise RuntimeError(f"notion: query failed HTTP {status}")

            try:
                body = resp.json()
            except Exception:
                body = {}

            results = body.get("results") or []
            for record in results:
                if isinstance(record, dict):
                    out.append(self._normalize(record))

            has_more = bool(body.get("has_more", False))
            next_cursor = body.get("next_cursor")
            if has_more and next_cursor:
                payload["start_cursor"] = next_cursor
            else:
                has_more = False

        return out
