"""JaegerSource — distributed-tracing connector for Jaeger.

Self-contained: no base class, no sibling imports. Normalizes Jaeger trace
spans into the common gludd record shape. HTTP transport is injectable so the
connector is fully testable with a mocked transport (no network).

Contract
--------
- ``KIND = "traces"`` class attribute; ``name`` instance attribute.
- ``__init__(config, transport=None)`` — config-driven; secrets read from
  ``*_env`` config keys (env var *names*, never literal secrets).
- ``base_url`` is SSRF-guarded by literal-host inspection (no DNS): private,
  loopback, link-local and reserved hosts are rejected unless
  ``allow_private`` is set.
- ``health()`` returns ``{"ok": bool, "detail": str}`` and never raises.
- ``query(spec)`` returns a list of normalized record dicts.

Jaeger HTTP API
---------------
- ``GET {base_url}/api/traces?service=&lookback=&limit=`` returns traces, each
  with a ``spans`` list and a ``processes`` map (process id -> service).
- ``GET {base_url}/api/services`` lists known services (used by ``health``).

One record is emitted per span.
"""

from __future__ import annotations

import ipaddress
import json
import os
import urllib.parse
import urllib.request
from typing import Any, Protocol, runtime_checkable

__all__ = ["HttpResponse", "JaegerSource", "SsrfError"]


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


def _host_is_private(host: str) -> bool:
    """True if *host* is a literal private/internal address (no DNS lookup).

    Hostnames that are not IP literals are treated as public — we never resolve
    DNS (that would itself be an SSRF vector and is non-deterministic).
    """
    bare = host.strip("[]")
    try:
        ip = ipaddress.ip_address(bare)
    except ValueError:
        lowered = host.lower()
        return lowered in {"localhost", "localhost.localdomain"}
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _guard_base_url(base_url: str, *, allow_private: bool) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise SsrfError(f"unsupported url scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise SsrfError(f"no host in base_url: {base_url!r}")
    if not allow_private and _host_is_private(host):
        raise SsrfError(f"blocked private/internal host: {host!r}")
    return base_url.rstrip("/")


class JaegerSource:
    """Normalizing connector for the Jaeger HTTP API."""

    KIND = "traces"

    def __init__(self, config: dict[str, Any], transport: HttpTransport | None = None) -> None:
        self.name: str = str(config.get("name", "jaeger"))
        self.allow_private: bool = bool(config.get("allow_private", False))
        self.base_url: str = _guard_base_url(
            str(config["base_url"]), allow_private=self.allow_private
        )
        self.timeout: float = float(config.get("timeout", 10.0))
        self.default_service: str | None = (
            str(config["service"]) if config.get("service") is not None else None
        )
        self.default_lookback: str = str(config.get("lookback", "1h"))
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
        """Probe ``/api/services``. Never raises; returns ok/detail."""
        try:
            resp = self._get("/api/services", {})
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        if resp.status >= 400:
            return {"ok": False, "detail": f"http {resp.status}"}
        try:
            payload = resp.json()
        except Exception as exc:
            return {"ok": False, "detail": f"invalid json: {exc}"}
        services = payload.get("data") if isinstance(payload, dict) else None
        count = len(services) if isinstance(services, list) else 0
        return {"ok": True, "detail": f"{count} services"}

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch traces and emit one normalized record per span."""
        spec = spec or {}
        params = {
            "service": spec.get("service", self.default_service),
            "lookback": spec.get("lookback", self.default_lookback),
            "limit": spec.get("limit", self.default_limit),
        }
        resp = self._get("/api/traces", params)
        payload = resp.json()
        traces = payload.get("data", []) if isinstance(payload, dict) else []
        records: list[dict[str, Any]] = []
        for trace in traces:
            if not isinstance(trace, dict):
                continue
            processes = trace.get("processes", {})
            for span in trace.get("spans", []):
                if isinstance(span, dict):
                    records.append(self._normalize_span(span, processes))
        return records

    @staticmethod
    def _service_name(process_id: Any, processes: dict[str, Any]) -> str:
        proc = processes.get(str(process_id)) if isinstance(processes, dict) else None
        if isinstance(proc, dict):
            return str(proc.get("serviceName", ""))
        return ""

    def _normalize_span(self, span: dict[str, Any], processes: dict[str, Any]) -> dict[str, Any]:
        tags = {
            str(t.get("key")): t.get("value")
            for t in span.get("tags", [])
            if isinstance(t, dict)
        }
        is_error = tags.get("error") is True or str(tags.get("error")).lower() == "true"
        status = str(tags.get("status", "")) or ("error" if is_error else "ok")
        service = self._service_name(span.get("processID"), processes)
        operation = str(span.get("operationName", ""))
        span_id = str(span.get("spanID", ""))
        trace_id = str(span.get("traceID", ""))
        labels = {
            "service": service,
            "operation": operation,
            "span_id": span_id,
            "trace_id": trace_id,
            "status": status,
        }
        return {
            "ts": span.get("startTime"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "error" if is_error else "ok",
            "message": operation,
            "value": span.get("duration"),
            "labels": labels,
            "raw": span,
        }
