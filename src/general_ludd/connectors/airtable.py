"""Airtable record-list connector.

Self-contained connector — imports nothing from sibling connector modules or a
shared base. It pulls Airtable table records via the
``GET {api_url}/v0/{base_id}/{table_name}`` API and normalizes them into the
gludd record envelope.

Security posture:

* The Airtable API token is read **only** from an environment variable named by
  ``config['token_env']`` (never hardcoded, never logged).
* ``api_url`` is SSRF-guarded against literal private / loopback / link-local
  hosts unless ``config['allow_private']`` is explicitly truthy.
* The injected HTTP transport is time-bounded and never uses ``shell=True``.
* ``health()`` never raises.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse, urlsplit

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
    ) -> HttpResponse:
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


class AirtableSource:
    """Airtable table record source.

    Parameters
    ----------
    config:
        Mapping with keys:

        * ``base_id`` (required): the Airtable base ID, e.g. ``appXXXXXXXXXXXXXX``.
        * ``table_name`` (required): the table name or ID, e.g. ``tblXXXXXXXXXXXXXX``.
        * ``token_env`` (required): name of the env var holding the API token.
        * ``api_url`` (optional): base Airtable API URL (default
          ``https://api.airtable.com``).
        * ``allow_private`` (optional): opt-in to permit private/loopback hosts.
        * ``max_pages`` (optional): pagination bound (default 10).
        * ``timeout`` (optional): per-request timeout seconds (default 30).
    transport:
        Optional injected :class:`Transport`. Defaults to an httpx-backed one.
    """

    KIND = "records"
    name = "airtable"

    def __init__(
        self,
        config: dict[str, Any],
        transport: Transport | None = None,
    ) -> None:
        self.config = dict(config)
        self.transport: Transport = transport or _default_transport

        base_id = str(self.config.get("base_id", "")).strip()
        if not base_id:
            raise ValueError("airtable: 'base_id' is required")
        self.base_id = base_id

        table_name = str(self.config.get("table_name", "")).strip()
        if not table_name:
            raise ValueError("airtable: 'table_name' is required")
        self.table_name = table_name

        self.token_env = str(self.config.get("token_env", "")).strip()
        if not self.token_env:
            raise ValueError("airtable: 'token_env' is required")

        self.api_url = str(self.config.get("api_url", "https://api.airtable.com")).rstrip("/")
        self.allow_private = bool(self.config.get("allow_private", False))
        self.max_pages = int(self.config.get("max_pages", 10))
        self.timeout = float(self.config.get("timeout", 30.0))

        self._guard_ssrf(self.api_url)
        self._api_host = (urlsplit(self.api_url).hostname or "").lower()

    # -- internals ---------------------------------------------------------

    @property
    def _table_url(self) -> str:
        return f"{self.api_url}/v0/{self.base_id}/{self.table_name}"

    def _guard_ssrf(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"airtable: unsupported URL scheme: {parsed.scheme!r}")
        host = parsed.hostname or ""
        if not self.allow_private and is_url_blocked(url, scheme_allowlist=("http", "https")):
            raise ValueError(
                f"airtable: refusing private/loopback host {host!r} "
                "(set allow_private=True to override)"
            )

    def _guard_next_url(self, url: str) -> None:
        self._guard_ssrf(url)
        host = (urlsplit(url).hostname or "").lower()
        if host != self._api_host:
            raise ValueError(
                f"airtable: refusing cross-host next-page URL {host!r} "
                f"(pinned to {self._api_host!r})"
            )

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"airtable: token env var {self.token_env!r} is unset or empty"
            )
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/json",
        }

    def _normalize(self, record: dict[str, Any]) -> dict[str, Any]:
        fields = record.get("fields") or {}
        return {
            "ts": record.get("createdTime"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": None,
            "message": None,
            "value": 1,
            "labels": {
                "record_id": record.get("id"),
            },
            "raw": record,
        }

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the table with a 1-record request; never raises."""
        try:
            resp = self.transport(
                "GET",
                self._table_url,
                headers=self._headers(),
                params={"maxRecords": "1"},
                timeout=self.timeout,
            )
        except Exception:
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        status = getattr(resp, "status_code", 0)
        if 200 <= status < 300:
            return {"ok": True, "detail": f"airtable reachable (HTTP {status})"}
        return {"ok": False, "detail": f"airtable HTTP {status}"}

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch + normalize table records.

        ``spec`` may contain ``filterByFormula``, ``sort``, ``view``,
        ``cellFormat``, ``timeZone``, ``userLocale``, and ``maxRecords``.
        Pagination follows the ``offset`` field in the response body up to
        ``max_pages``.
        """
        spec = spec or {}
        params: dict[str, str] = {}
        known = {
            "filterByFormula", "sort", "view", "cellFormat",
            "timeZone", "userLocale", "maxRecords",
        }
        for key in known:
            if key in spec:
                params[key] = str(spec[key])

        url = self._table_url
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
                raise RuntimeError(f"airtable: query failed HTTP {status}")
            body = resp.json()
            if isinstance(body, dict):
                records = body.get("records", [])
                if isinstance(records, list):
                    for record in records:
                        if isinstance(record, dict):
                            out.append(self._normalize(record))
                offset = body.get("offset")
                if not offset:
                    break
                params = {"offset": str(offset)}
            else:
                break

        return out
