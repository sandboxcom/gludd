"""ZipkinSource — distributed-tracing connector for Zipkin.

Self-contained: no base class, no sibling imports. Normalizes Zipkin v2 spans
into the common gludd record shape. HTTP transport is injectable for fully
mocked (no-network) testing.

Contract
--------
- ``KIND = "traces"`` class attribute; ``name`` instance attribute.
- ``__init__(config, transport=None)`` — config-driven; secrets read from
  ``*_env`` config keys (env var *names*, never literal secrets).
- ``base_url`` is SSRF-guarded by literal-host inspection (no DNS); private /
  internal hosts rejected unless ``allow_private`` is set.
- ``health()`` returns ``{"ok": bool, "detail": str}`` and never raises.
- ``query(spec)`` returns a list of normalized record dicts.

Zipkin HTTP API
---------------
- ``GET {base_url}/api/v2/traces?serviceName=&lookback=&limit=`` returns a list
  of traces; each trace is a list of v2 spans.

One record is emitted per span. Zipkin span ``duration`` is in microseconds.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Protocol, runtime_checkable

from general_ludd.security.ssrf import is_url_blocked

__all__ = ["HttpResponse", "SsrfError", "ZipkinSource"]


class SsrfError(ValueError):
    """Raised when ``base_url`` targets a blocked (private/internal) host."""


class HttpResponse:
    """Minimal transport response: status code plus raw body bytes."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


@runtime_checkable
class HttpTransport(Protocol):
    """Injectable HTTP transport. The default uses stdlib urllib."""

    def get(
        self, url: str, *, headers: dict[str, str], timeout: float
    ) -> HttpResponse: ...


class _UrllibTransport:
    """Default time-bound transport backed by ``urllib.request`` (no shell)."""

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> HttpResponse:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body: bytes = resp.read()
            status = int(getattr(resp, "status", 200) or 200)
            return HttpResponse(status, body)


def _guard_base_url(base_url: str, *, allow_private: bool) -> str:
    if not allow_private and is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise SsrfError(f"blocked private/internal host: {base_url!r}")
    # Scheme check still applies even when allow_private is True.
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise SsrfError(f"unsupported url scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise SsrfError(f"no host in base_url: {base_url!r}")
    return base_url.rstrip("/")


class ZipkinSource:
    """Normalizing connector for the Zipkin v2 HTTP API."""

    KIND = "traces"

    def __init__(self, config: dict[str, Any], transport: HttpTransport | None = None) -> None:
        self.name: str = str(config.get("name", "zipkin"))
        self.allow_private: bool = bool(config.get("allow_private", False))
        self.base_url: str = _guard_base_url(
            str(config["base_url"]), allow_private=self.allow_private
        )
        self.timeout: float = float(config.get("timeout", 10.0))
        self.default_service: str | None = (
            str(config["service_name"]) if config.get("service_name") is not None else None
        )
        self.default_lookback: int | str = config.get("lookback", 3600000)
        self.default_limit: int = int(config.get("limit", 20))

        token_env = config.get("token_env")
        self._token: str | None = os.environ.get(str(token_env)) if token_env else None

        self._transport: HttpTransport = transport if transport is not None else _UrllibTransport()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _get(self, path: str, params: dict[str, Any]) -> HttpResponse:
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}, doseq=True
        )
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        return self._transport.get(url, headers=self._headers(), timeout=self.timeout)

    def health(self) -> dict[str, Any]:
        """Probe the traces endpoint with limit=1. Never raises."""
        try:
            resp = self._get("/api/v2/traces", {"limit": 1})
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        if resp.status >= 400:
            return {"ok": False, "detail": f"http {resp.status}"}
        try:
            resp.json()
        except Exception as exc:
            return {"ok": False, "detail": f"invalid json: {exc}"}
        return {"ok": True, "detail": "reachable"}

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch traces and emit one normalized record per span."""
        spec = spec or {}
        params = {
            "serviceName": spec.get("service_name", self.default_service),
            "lookback": spec.get("lookback", self.default_lookback),
            "limit": spec.get("limit", self.default_limit),
        }
        resp = self._get("/api/v2/traces", params)
        payload = resp.json()
        records: list[dict[str, Any]] = []
        if not isinstance(payload, list):
            return records
        for trace in payload:
            if not isinstance(trace, list):
                continue
            for span in trace:
                if isinstance(span, dict):
                    records.append(self._normalize_span(span))
        return records

    def _normalize_span(self, span: dict[str, Any]) -> dict[str, Any]:
        local_endpoint = span.get("localEndpoint") or {}
        service = (
            str(local_endpoint.get("serviceName", "")) if isinstance(local_endpoint, dict) else ""
        )
        name = str(span.get("name", ""))
        span_id = str(span.get("id", ""))
        trace_id = str(span.get("traceId", ""))
        span_kind = str(span.get("kind", ""))
        tags = span.get("tags") or {}
        is_error = isinstance(tags, dict) and "error" in tags
        labels = {
            "service": service,
            "trace_id": trace_id,
            "span_id": span_id,
            "kind": span_kind,
        }
        return {
            "ts": span.get("timestamp"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "error" if is_error else "ok",
            "message": name,
            "value": span.get("duration"),
            "labels": labels,
            "raw": span,
        }
