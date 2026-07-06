"""Jaeger tracing connector for gludd.

Self-contained source connector: it queries a Jaeger query-service HTTP API
and normalizes spans into gludd's cross-source record shape.

Design constraints (enforced here, not inherited):
  * No base/sibling imports — this module stands alone.
  * Injectable HTTP transport (no hidden global client); defaults to httpx.
  * SSRF guard rejects literal private/loopback/link-local/reserved hosts on
    ``base_url`` using a pure-literal parse (no DNS resolution).
  * ``health()`` never raises.
  * ``query()`` returns normalized ``list[dict]`` and is fail-soft.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import ClassVar, Protocol, TypedDict, cast, runtime_checkable

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.connectors.normalize import sanitize_metric_value
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0


# --------------------------------------------------------------------------- #
# Typed API-response shapes (Jaeger query-service JSON).
# --------------------------------------------------------------------------- #
class JaegerTag(TypedDict, total=False):
    """One tag on a Jaeger span or process."""

    key: str
    value: object
    type: str


class JaegerProcess(TypedDict, total=False):
    """A process entry referenced by a span via ``processID``."""

    serviceName: str
    tags: list[JaegerTag]


class JaegerSpan(TypedDict, total=False):
    """One span in a Jaeger trace.

    ``duration`` is int|float because Jaeger emits microseconds as an int, but
    a misbehaving exporter or JSON deserializer can surface it as a float; the
    numeric sanitizer tolerates both, and NaN/Inf are normalized to ``None``.
    """

    traceID: str
    spanID: str
    operationName: str
    startTime: int
    duration: int | float
    processID: str
    tags: list[JaegerTag]


class JaegerTrace(TypedDict, total=False):
    """One trace from ``/api/traces`` or ``/api/traces/{id}`` — the ``data[]`` item."""

    traceID: str
    spans: list[JaegerSpan]
    processes: dict[str, JaegerProcess]


class JaegerPayload(TypedDict, total=False):
    """Top-level Jaeger query-service response."""

    data: list[JaegerTrace]


class JaegerQuerySpec(TypedDict, total=False):
    """Caller-supplied query selection accepted by :meth:`JaegerSource.query`."""

    trace_id: str
    service: str
    operation: str
    lookback: str
    limit: int


# --------------------------------------------------------------------------- #
# Transport protocol (structural; a fake or httpx both satisfy it)
# --------------------------------------------------------------------------- #
@runtime_checkable
class HttpTransport(Protocol):
    def get(
        self,
        url: str,
        params: Mapping[str, str | int] | None = ...,
        timeout: float | None = ...,
        headers: Mapping[str, str] | None = ...,
    ) -> HttpResponse: ...


class _HttpxTransport:
    """Thin default transport so the connector works with no DI in prod."""

    def get(
        self,
        url: str,
        params: Mapping[str, str | int] | None = None,
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        import httpx  # local import: keep module import-light + optional dep

        return httpx.get(
            url,
            params=dict(params) if params else None,
            timeout=timeout if timeout is not None else _DEFAULT_TIMEOUT,
            headers=dict(headers) if headers else None,
            follow_redirects=False,
        )


# --------------------------------------------------------------------------- #
# SSRF literal-host guard (no DNS)
# --------------------------------------------------------------------------- #
def _assert_public_base_url(base_url: str) -> None:
    """Reject schemes other than http/https and literal internal hosts.

    Pure literal inspection — we never resolve DNS. A hostname (non-IP-literal)
    is allowed through, since a literal-host block by definition cannot vet a
    name without resolution; numeric/loopback/private literals are blocked.
    """
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(
            f"base_url blocked by SSRF policy (bad scheme, missing host, or internal/loopback/private): {base_url!r}"
        )


# --------------------------------------------------------------------------- #
# Span normalization helpers
# --------------------------------------------------------------------------- #
def _span_has_error(span: Mapping[str, object]) -> bool:
    """Return True if the span carries an error / 5xx tag.

    Defensive about the shape of ``tags``: a malformed exporter can omit the
    field or emit a non-list, in which case the span is treated as non-error.
    """
    tags_obj = span.get("tags")
    if not isinstance(tags_obj, list):
        return False
    for tag in tags_obj:
        if not isinstance(tag, Mapping):
            continue
        key = tag.get("key")
        val = tag.get("value")
        if key == "error" and val not in (False, "false", "False", 0, None):
            return True
        if key in ("otel.status_code", "status.code") and str(val).upper() in ("ERROR", "2"):
            return True
        if key == "http.status_code":
            try:
                if int(cast(int | float | str | bool, val)) >= 500:
                    return True
            except (TypeError, ValueError):
                pass
    return False


class JaegerSource:
    """Tracing source backed by the Jaeger query API."""

    KIND: ClassVar[str] = "traces"

    def __init__(
        self,
        config: Mapping[str, object],
        transport: HttpTransport | None = None,
    ) -> None:
        base_url = str(config.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("JaegerSource requires config['base_url']")
        _assert_public_base_url(base_url)

        self._base_url = base_url
        self.name: str = str(config.get("name") or "jaeger")
        self._timeout: float = float(cast(float | int | str | bool, config.get("timeout", _DEFAULT_TIMEOUT)))
        token_env_obj = config.get("token_env")
        self._token_env: str | None = str(token_env_obj) if token_env_obj is not None else None
        self._transport: HttpTransport = transport or _HttpxTransport()

    # -- auth ------------------------------------------------------------- #
    def _auth_header(self) -> dict[str, str]:
        if not self._token_env:
            return {}
        token = os.environ.get(self._token_env)
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    def _get(
        self,
        path: str,
        params: Mapping[str, str | int] | None = None,
    ) -> HttpResponse:
        url = f"{self._base_url}{path}"
        headers = self._auth_header()
        return self._transport.get(
            url,
            params=params,
            timeout=self._timeout,
            headers=headers or None,
        )

    # -- health ----------------------------------------------------------- #
    def health(self) -> dict[str, object]:
        try:
            resp = self._get("/api/services")
            code = int(getattr(resp, "status_code", 0))
            ok = 200 <= code < 300
            return {"ok": ok, "detail": f"GET /api/services -> {code}"}
        except Exception as exc:  # never raise
            logger.warning("jaeger health check failed: %s", exc)
            return {"ok": False, "detail": f"error: {exc}"}

    # -- query ------------------------------------------------------------ #
    def query(self, spec: JaegerQuerySpec | None = None) -> list[dict[str, object]]:
        try:
            trace_id = spec.get("trace_id") if spec else None
            if trace_id:
                resp = self._get(f"/api/traces/{trace_id}")
            else:
                params: dict[str, str | int] = {}
                if spec:
                    for key in ("service", "operation", "lookback", "limit"):
                        val = spec.get(key)
                        if val is not None:
                            params[key] = cast(str | int, val)
                resp = self._get("/api/traces", params=params)

            code = int(getattr(resp, "status_code", 0))
            if not (200 <= code < 300):
                logger.warning("jaeger query non-2xx: %s", code)
                return []
            payload = resp.json()
        except Exception as exc:
            logger.warning("jaeger query failed: %s", exc)
            return []

        records: list[dict[str, object]] = []
        if not isinstance(payload, Mapping):
            return records
        for trace in payload.get("data", []) or []:
            if not isinstance(trace, Mapping):
                continue
            processes_raw = trace.get("processes")
            processes: Mapping[str, object] = processes_raw if isinstance(processes_raw, Mapping) else {}
            for span in trace.get("spans", []) or []:
                if not isinstance(span, Mapping):
                    continue
                rec = self._normalize_span(span, processes)
                if rec is not None:
                    records.append(rec)
        return records

    def _normalize_span(
        self,
        span: Mapping[str, object],
        processes: Mapping[str, object],
    ) -> dict[str, object] | None:
        try:
            trace_id = span["traceID"]
            span_id = span["spanID"]
            start_us = span["startTime"]
        except KeyError:
            return None

        duration = span.get("duration", 0)
        operation = str(span.get("operationName", ""))

        proc_id = str(span.get("processID", ""))
        proc_obj = processes.get(proc_id, {})
        proc: Mapping[str, object] = proc_obj if isinstance(proc_obj, Mapping) else {}
        service = str(proc.get("serviceName", ""))

        try:
            ts_seconds = float(cast(int | float | str | bool, start_us)) / 1_000_000.0
        except (TypeError, ValueError):
            return None

        return {
            "ts": ts_seconds,  # microseconds -> seconds
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "error" if _span_has_error(span) else "ok",
            "message": f"{service} {operation}".strip(),
            "value": sanitize_metric_value(duration),
            "labels": {
                "trace_id": trace_id,
                "span_id": span_id,
                "service": service,
            },
            "raw": dict(span),
        }
