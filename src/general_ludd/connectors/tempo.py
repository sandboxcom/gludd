"""TempoSource — distributed-tracing connector for Grafana Tempo.

Self-contained: no base class, no sibling imports. Normalizes Tempo search
results (and optionally fetched full traces) into the common gludd record
shape. HTTP transport is injectable for fully mocked (no-network) testing.

Contract
--------
- ``KIND = "traces"`` class attribute; ``name`` instance attribute.
- ``__init__(config, transport=None)`` — config-driven; Bearer token read from
  the env var named by ``token_env`` (never a literal secret in config).
- ``base_url`` is SSRF-guarded by literal-host inspection (no DNS); private /
  internal hosts rejected unless ``allow_private`` is set.
- ``health()`` returns ``{"ok": bool, "detail": str}`` and never raises.
- ``query(spec)`` returns a list of normalized record dicts.

Tempo HTTP API
--------------
- ``GET {base_url}/api/search?tags=&start=&end=`` returns ``{"traces": [...]}``
  trace summaries (durationMs, rootServiceName, rootTraceName, traceID).
- ``GET {base_url}/api/search?q=<TraceQL>`` is used when ``spec["traceql"]`` is
  given (the same response shape).
- ``GET {base_url}/api/traces/{id}`` fetches the full trace; when
  ``spec["fetch_spans"]`` is true, one record is emitted per span instead of
  one per trace summary.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from typing import Protocol, cast, runtime_checkable

import httpx

from general_ludd.connectors._errors import SSRFError
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

__all__ = ["TempoSource"]


class _TempoResponse:
    """Minimal transport response: status code plus raw body bytes."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> object:
        return json.loads(self.body.decode("utf-8"))


@runtime_checkable
class HttpTransport(Protocol):
    """Injectable HTTP transport. The default uses httpx (no redirects)."""

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> _TempoResponse: ...


class _HttpxTransport:
    """Default time-bound transport backed by ``httpx`` (no shell, no redirects)."""

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> _TempoResponse:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            resp = client.get(url, headers=headers)
        return _TempoResponse(resp.status_code, resp.content)


def _guard_base_url(base_url: str, *, allow_private: bool) -> str:
    if not allow_private and is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise SSRFError(f"blocked private/internal host: {base_url!r}")
    # Scheme check still applies even when allow_private is True.
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise SSRFError(f"unsupported url scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise SSRFError(f"no host in base_url: {base_url!r}")
    return base_url.rstrip("/")


class TempoSource:
    """Normalizing connector for the Grafana Tempo HTTP API."""

    KIND = "traces"

    def __init__(self, config: dict[str, object], transport: HttpTransport | None = None) -> None:
        """Build the source from connector config and select the transport."""
        self.name: str = str(config.get("name", "tempo"))
        self.allow_private: bool = bool(config.get("allow_private", False))
        self.base_url: str = _guard_base_url(
            str(config.get("base_url", "https://tempo.example.com")), allow_private=self.allow_private
        )
        self.timeout: float = float(cast("float | int | str", config.get("timeout", 10.0)))
        self.default_tags: str | None = str(config["tags"]) if config.get("tags") is not None else None
        self.default_start: int | None = (
            int(cast("int | str", config["start"])) if config.get("start") is not None else None
        )
        self.default_end: int | None = int(cast("int | str", config["end"])) if config.get("end") is not None else None

        token_env = config.get("token_env")
        self._token: str | None = os.environ.get(str(token_env)) if token_env else None

        transport_impl: HttpTransport
        if callable(transport) and not hasattr(transport, "get"):

            def _legacy_get(url: str, *, headers: dict[str, str], timeout: float) -> _TempoResponse:
                result = transport("GET", url, headers=headers or {})
                status, payload = result if isinstance(result, tuple) and len(result) == 2 else (0, {})
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                return _TempoResponse(int(status), body)

            transport_impl = cast(HttpTransport, type("LegacyTransport", (), {"get": staticmethod(_legacy_get)})())
        else:
            transport_impl = transport if transport is not None else _HttpxTransport()
        self._transport = transport_impl

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _get(self, path: str, params: dict[str, object]) -> _TempoResponse:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        return self._transport.get(url, headers=self._headers(), timeout=self.timeout)

    def health(self) -> dict[str, object]:
        """Probe ``/api/search`` with a bounded window. Never raises."""
        try:
            resp = self._get("/api/search", {"limit": 1})
        except Exception:
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        if resp.status >= 400:
            return {"ok": False, "detail": f"http {resp.status}"}
        try:
            resp.json()
        except Exception:
            logger.warning("health check failed (invalid json)", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        return {"ok": True, "detail": "reachable"}

    def query(self, spec: dict[str, object] | None = None) -> list[dict[str, object]]:
        """Search traces and emit normalized records.

        Default: one record per trace summary. When ``spec["fetch_spans"]`` is
        truthy, each trace is fetched via ``/api/traces/{id}`` and one record is
        emitted per span.
        """
        spec = spec or {}
        traceql = spec.get("traceql")
        if traceql is not None:
            params: dict[str, object] = {"q": str(traceql)}
        else:
            params = {
                "tags": spec.get("tags", self.default_tags),
                "start": spec.get("start", self.default_start),
                "end": spec.get("end", self.default_end),
            }
        if spec.get("limit") is not None:
            params["limit"] = spec["limit"]

        resp = self._get("/api/search", params)
        payload = resp.json()
        summaries = payload.get("traces", []) if isinstance(payload, dict) else []

        if spec.get("fetch_spans"):
            records: list[dict[str, object]] = []
            for summary in summaries:
                if isinstance(summary, dict):
                    records.extend(self._fetch_and_normalize_spans(summary))
            return records

        return [self._normalize_summary(summary) for summary in summaries if isinstance(summary, dict)]

    def _normalize_summary(self, summary: dict[str, object]) -> dict[str, object]:
        trace_id = str(summary.get("traceID", ""))
        service = str(summary.get("rootServiceName", ""))
        message = str(summary.get("rootTraceName", ""))
        labels = {
            "service": service,
            "trace_id": trace_id,
            "span_id": "",
            "status": "ok",
        }
        return {
            "ts": summary.get("startTimeUnixNano"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "ok",
            "message": message,
            "value": summary.get("durationMs"),
            "labels": labels,
            "raw": summary,
        }

    def _fetch_and_normalize_spans(self, summary: dict[str, object]) -> list[dict[str, object]]:
        trace_id = str(summary.get("traceID", ""))
        if not trace_id:
            return []
        safe_id = urllib.parse.quote(trace_id, safe="")
        resp = self._get(f"/api/traces/{safe_id}", {})
        payload = resp.json()
        records: list[dict[str, object]] = []
        for batch in self._iter_batches(payload):
            resource = batch.get("resource", {})
            service = self._resource_service(resource)
            for scoped in self._iter_scope_spans(batch):
                spans_raw = scoped.get("spans", [])
                spans: list[dict[str, object]] = spans_raw if isinstance(spans_raw, list) else []
                for span in spans:
                    if isinstance(span, dict):
                        records.append(self._normalize_span(span, service, trace_id))
        return records

    @staticmethod
    def _iter_batches(payload: object) -> list[dict[str, object]]:
        if isinstance(payload, dict):
            batches_raw = payload.get("batches")
            if isinstance(batches_raw, list):
                return [b for b in batches_raw if isinstance(b, dict)]
        return []

    @staticmethod
    def _iter_scope_spans(batch: dict[str, object]) -> list[dict[str, object]]:
        scoped_raw = batch.get("scopeSpans") or batch.get("instrumentationLibrarySpans") or []
        scoped: list[dict[str, object]] = scoped_raw if isinstance(scoped_raw, list) else []
        return scoped

    @staticmethod
    def _resource_service(resource: object) -> str:
        if not isinstance(resource, dict):
            return ""
        for attr in resource.get("attributes", []):
            if isinstance(attr, dict) and attr.get("key") == "service.name":
                value = attr.get("value", {})
                if isinstance(value, dict):
                    return str(value.get("stringValue", ""))
        return ""

    def _normalize_span(self, span: dict[str, object], service: str, trace_id: str) -> dict[str, object]:
        name = str(span.get("name", ""))
        span_id = str(span.get("spanId", span.get("spanID", "")))
        status_obj = span.get("status") or {}
        status_code = str(status_obj.get("code", "")) if isinstance(status_obj, dict) else ""
        is_error = status_code in {"2", "STATUS_CODE_ERROR", "ERROR"}
        status = "error" if is_error else (status_code or "ok")
        start = span.get("startTimeUnixNano")
        end = span.get("endTimeUnixNano")
        duration_ms: float | None = None
        try:
            if start is not None and end is not None:
                duration_ms = (int(cast("int | str", end)) - int(cast("int | str", start))) / 1_000_000
        except (TypeError, ValueError):
            duration_ms = None
        labels = {
            "service": service,
            "trace_id": trace_id,
            "span_id": span_id,
            "status": status,
        }
        return {
            "ts": start,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "error" if is_error else "ok",
            "message": name,
            "value": duration_ms,
            "labels": labels,
            "raw": span,
        }
