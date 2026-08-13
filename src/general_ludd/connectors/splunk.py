"""Splunk observability connector.

Self-contained connector for querying a Splunk deployment over its REST API.

Design constraints (intentional, do not "simplify" away):

* No imports from ``general_ludd`` base classes, package ``__init__`` or other
  connectors -- this module is deliberately standalone so it can be vendored or
  tested in isolation.
* The HTTP transport is *injected*. Production callers pass a real client; tests
  pass a fake. We never construct a global/default network client at import time.
* SSRF protection: ``base_url`` is validated against its *literal* host only.
  We never resolve DNS (a DNS rebind could otherwise smuggle a public name onto a
  private address). Loopback, link-local, private, and cloud-metadata hosts are
  rejected outright.
* Bearer/HEC token is read from an environment variable named by the config; the
  token value never appears in the config dict and is never logged.
* No ``shell=True`` / subprocess use anywhere.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Protocol, cast, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.connectors._errors import SSRFError
from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

__all__ = ["HttpTransport", "SplunkSource"]

KIND = "logs"


class _TupleResponse:
    def __init__(self, status: object, body: object) -> None:
        self.status_code = int(status) if isinstance(status, int) else 0
        self._body = body

    def json(self) -> object:
        return self._body


def _invoke(transport: object, method: str, url: str, **kwargs: object) -> HttpResponse:
    fn = getattr(transport, method.lower(), None)
    if callable(fn):
        result = fn(url, **kwargs)
    elif callable(transport):
        result = transport(method, url, **kwargs)
    else:
        raise TypeError("transport must expose get/post or be callable")
    if isinstance(result, tuple) and len(result) == 2:
        return cast(HttpResponse, _TupleResponse(result[0], result[1]))
    return cast(HttpResponse, result)


@runtime_checkable
class HttpTransport(Protocol):
    """Injectable HTTP transport.

    Implementations must accept ``url``, ``headers``, optional ``params`` /
    ``data`` and a ``timeout`` and return an :class:`HttpResponse`.
    """

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object] | None = ...,
        timeout: float = ...,
    ) -> HttpResponse:  # pragma: no cover - structural typing only
        ...

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: dict[str, object] | None = ...,
        timeout: float = ...,
    ) -> HttpResponse:  # pragma: no cover - structural typing only
        ...


class CallableHttpTransport(Protocol):
    """Compact method-and-URL callback used by generated workflows."""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object] | None = ...,
        data: dict[str, object] | None = ...,
        timeout: float = ...,
    ) -> tuple[int, object]: ...


class _CallbackResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = int(status_code)
        self._body = body

    @property
    def text(self) -> str:
        return self._body if isinstance(self._body, str) else str(self._body)

    def json(self) -> object:
        return self._body


class _CallableTransportAdapter:
    def __init__(self, callback: CallableHttpTransport) -> None:
        self._callback = callback

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        status, body = self._callback(
            "GET",
            url,
            headers=headers,
            params=params,
            timeout=timeout,
        )
        return _CallbackResponse(status, body)

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: dict[str, object] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        status, body = self._callback(
            "POST",
            url,
            headers=headers,
            data=data,
            timeout=timeout,
        )
        return _CallbackResponse(status, body)


def _assert_safe_base_url(base_url: str) -> str:
    """Validate ``base_url`` against SSRF and return a normalized base (no trailing /).

    Only the *literal* host in the URL is inspected -- DNS is never resolved.
    """
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        parts = urlsplit(base_url)
        host = parts.hostname or ""
        raise SSRFError(f"forbidden host or address: {host!r}")
    return base_url.rstrip("/")


def _parse_time(value: object) -> str | None:
    """Best-effort parse of a Splunk ``_time`` field into ISO-8601 UTC text.

    Splunk emits ``_time`` either as an ISO string (``2026-06-12T...``) or as an
    epoch-seconds string/number. We normalize both to ISO-8601; unparseable
    values yield ``None`` rather than raising.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _dt.datetime.fromtimestamp(float(value), tz=_dt.UTC).isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Numeric epoch encoded as a string.
        try:
            epoch = float(text)
        except ValueError:
            epoch = None
        if epoch is not None:
            return _dt.datetime.fromtimestamp(epoch, tz=_dt.UTC).isoformat()
        # ISO-8601, possibly with a trailing ``Z``.
        iso = text.replace("Z", "+00:00")
        try:
            parsed = _dt.datetime.fromisoformat(iso)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.UTC)
        return parsed.astimezone(_dt.UTC).isoformat()
    return None


def _first(row: dict[str, object], *keys: str) -> object:
    """Return the first present, non-None value among ``keys``."""
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


class SplunkSource:
    """Query a Splunk deployment and normalize results to gludd log records.

    Parameters
    ----------
    config:
        Mapping with ``base_url`` and ``token_env`` keys. ``token_env`` names the
        environment variable holding the HEC/Bearer token.
    transport:
        Injected HTTP client implementing :class:`HttpTransport`.
    name:
        Optional human-readable source name (defaults to the base_url host).
    timeout:
        Per-request timeout in seconds (default 30).
    env:
        Optional environment mapping (defaults to ``os.environ``) -- handy in
        tests so we never have to mutate the real process environment.
    """

    kind = KIND

    def __init__(
        self,
        config: dict[str, object],
        *,
        transport: HttpTransport | CallableHttpTransport,
        name: str | None = None,
        timeout: float = 30.0,
        env: dict[str, str] | None = None,
    ) -> None:
        base_url = config.get("base_url")
        if not isinstance(base_url, str) or not base_url:
            raise ValueError("config.base_url is required")
        token_env = config.get("token_env")
        if not isinstance(token_env, str) or not token_env:
            raise ValueError("config.token_env is required")

        # SSRF gate runs at construction so a bad URL never even reaches health().
        self._base_url = _assert_safe_base_url(base_url)
        self._token_env = token_env
        if callable(getattr(transport, "get", None)) and callable(
            getattr(transport, "post", None)
        ):
            self._transport = cast(HttpTransport, transport)
        elif callable(transport):
            self._transport = _CallableTransportAdapter(transport)
        else:
            raise TypeError("transport must provide get/post or be callable")
        self._timeout = float(timeout)
        self._env = env if env is not None else dict(os.environ)
        parsed_host = urlsplit(self._base_url).hostname or self._base_url
        self.name = name or parsed_host

    # -- auth ---------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        token = self._env.get(self._token_env)
        if not token:
            raise ValueError(f"missing token in env var {self._token_env!r}")
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    # -- health -------------------------------------------------------------

    def health(self) -> dict[str, object]:
        """Probe ``/services/server/info``; never raises.

        Returns a dict ``{"ok": bool, "name": str, "kind": str, ...}``.
        """
        result: dict[str, object] = {"ok": False, "name": self.name, "kind": self.kind}
        try:
            headers = self._auth_headers()
            resp = _invoke(
                self._transport,
                "GET",
                f"{self._base_url}/services/server/info",
                headers=headers,
                params={"output_mode": "json"},
                timeout=self._timeout,
            )
        except Exception:  # health must never raise
            # repr(exc) can embed the base URL / token-env detail; log it for
            # operators and return a generic message to the caller instead.
            logger.warning("splunk health check failed", exc_info=True)
            result["error"] = "splunk health check failed"
            return result

        status = getattr(resp, "status_code", None)
        result["status_code"] = status
        if status == 200:
            result["ok"] = True
        elif status in (401, 403):
            result["error"] = "authentication failed"
        else:
            result["error"] = f"unexpected status {status}"
        return result

    # -- query --------------------------------------------------------------

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Run a oneshot Splunk search and return normalized records.

        ``spec`` keys:
          * ``search`` (required) -- the SPL search string.
          * ``earliest`` / ``latest`` -- optional time bounds.
          * ``count`` -- optional max rows (0 = unlimited per Splunk).
        """
        search = spec.get("search")
        if not isinstance(search, str) or not search.strip():
            raise ValueError("spec.search is required")

        data: dict[str, object] = {
            "search": search,
            "output_mode": "json",
            "exec_mode": "oneshot",
        }
        for key in ("earliest", "latest", "count"):
            if spec.get(key) is not None:
                data[key] = spec[key]

        headers = self._auth_headers()
        resp = _invoke(
            self._transport,
            "POST",
            f"{self._base_url}/services/search/jobs",
            headers=headers,
            data=data,
            timeout=self._timeout,
        )
        status = getattr(resp, "status_code", None)
        if status != 200:
            raise RuntimeError(f"splunk search failed with status {status}")

        payload = resp.json()
        rows = self._extract_rows(payload)
        return [self._normalize(row) for row in rows]

    @staticmethod
    def _extract_rows(payload: object) -> list[dict[str, object]]:
        """Pull the result rows out of a oneshot JSON payload."""
        if isinstance(payload, dict):
            results = payload.get("results")
            if isinstance(results, list):
                return [r for r in results if isinstance(r, dict)]
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        return []

    def _normalize(self, row: dict[str, object]) -> dict[str, object]:
        """Map a Splunk result row onto the gludd normalized record shape."""
        ts = _parse_time(_first(row, "_time", "time"))
        level_or_status = _first(row, "level", "severity", "status")
        message = _first(row, "_raw", "message", "msg")
        value = _first(row, "value", "count", "metric_value")
        labels = {
            "index": _first(row, "index"),
            "host": _first(row, "host"),
            "source": _first(row, "source"),
            "sourcetype": _first(row, "sourcetype"),
        }
        # Drop labels that were absent so consumers see a clean map.
        labels = {k: v for k, v in labels.items() if v is not None}
        return {
            "ts": ts,
            "source": self.name,
            "kind": self.kind,
            "level_or_status": level_or_status,
            "message": message,
            "value": value,
            "labels": labels,
            "raw": row,
        }
