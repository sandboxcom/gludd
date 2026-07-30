"""Self-contained SigNoz observability connector.

``SigNozSource`` queries a SigNoz instance's ``query_range`` API for traces /
spans (and can carry metrics) and normalizes each span to the common record
shape shared by every gludd observability source::

    {
        "ts": float,             # event time in epoch seconds
        "source": str,           # connector name ("signoz")
        "kind": str,             # "traces"
        "level_or_status": str,  # span statusCode / error flag
        "message": str,          # "<serviceName> <operation>"
        "value": float | None,   # durationNano
        "labels": dict,          # {trace_id, span_id, service.name, durationNano}
        "raw": object,              # the original span object
    }

The module is deliberately standalone: it imports nothing from a connector
base class, the package ``__init__``, or any sibling connector. The HTTP
transport is injected so tests run with zero network I/O.

Security posture:

* SSRF: the configured ``base_url`` host is checked against a literal
  blocklist (loopback / private / link-local / cloud-metadata) with NO DNS
  resolution. Internal hosts are rejected at construction time.
* Auth is read from an environment variable named by ``config["token_env"]``;
  no token is ever hard-coded or logged.
* Every request is time-bound by an explicit timeout and a caller-supplied
  start/end window. No ``shell=True``; no subprocess at all.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, cast, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

__all__ = ["SigNozSource"]

# Default request timeout (seconds) — every call is time-bound.
_DEFAULT_TIMEOUT = 15.0


@runtime_checkable
class _Transport(Protocol):
    """Minimal injectable HTTP transport.

    object object exposing this ``request`` signature can be injected. Returning a
    ``(status_code, json_body)`` tuple keeps the connector free of any concrete
    HTTP library dependency and makes mocking trivial.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        json: object | None = ...,
        params: dict[str, object] | None = ...,
        timeout: float | None = ...,
    ) -> tuple[int, object]: ...


class _CallableTransport(Protocol):
    """Compact method-and-URL callback used by generated workflows."""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        json: object | None = ...,
        params: dict[str, object] | None = ...,
        timeout: float | None = ...,
    ) -> tuple[int, object]: ...


class _CallableTransportAdapter:
    def __init__(self, callback: _CallableTransport) -> None:
        self._callback = callback

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: object | None = None,
        params: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, object]:
        return self._callback(
            method,
            url,
            headers=headers,
            json=json,
            params=params,
            timeout=timeout,
        )


def _validate_base_url(base_url: str) -> str:
    """Validate scheme + host and return the normalized base URL.

    Raises ``ValueError`` for non-http(s) schemes or blocked literal hosts. The
    private/metadata decision is delegated to the canonical shared guard
    :func:`general_ludd.security.ssrf.is_url_blocked` so this connector cannot
    drift weaker than the single source of truth (no DNS resolution).
    """
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"SigNozSource: unsupported URL scheme: {parts.scheme!r}")
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ValueError(f"SigNozSource: refusing internal/blocked host: {host!r}")
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(f"SigNozSource: refusing internal/blocked host: {host!r}")
    return base_url.rstrip("/")


class SigNozSource:
    """Read-only SigNoz traces (and metrics) source.

    Args:
        config: mapping with ``base_url`` (public SigNoz URL) and ``token_env``
            (name of the env var holding the API token).
        transport: injected HTTP transport implementing ``_Transport``.
        timeout: per-request timeout in seconds.
    """

    KIND = "traces"
    SECONDARY_KINDS = ("metrics",)

    def __init__(
        self,
        config: dict[str, object],
        *,
        transport: _Transport | _CallableTransport,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.name = "signoz"
        self._base_url = _validate_base_url(str(config.get("base_url", "")))
        self._token_env = str(config.get("token_env", ""))
        if callable(getattr(transport, "request", None)):
            self._transport = cast(_Transport, transport)
        elif callable(transport):
            self._transport = _CallableTransportAdapter(transport)
        else:
            raise TypeError("transport must provide request or be callable")
        self._timeout = float(timeout)

    # -- internals ---------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Build auth headers from the env token (empty if unset)."""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = os.environ.get(self._token_env, "") if self._token_env else ""
        if token:
            # SigNoz accepts the API key via the SIGNOZ-API-KEY header; we also
            # send a bearer for proxy setups. Neither value is logged.
            headers["SIGNOZ-API-KEY"] = token
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _coerce_ts(raw_span: dict[str, object]) -> float:
        """Derive epoch-seconds start time from a span's various time fields."""
        for key in ("startTimeUnixNano", "startTimeNano", "timestampNano"):
            val = raw_span.get(key)
            if isinstance(val, (int, float)) and val:
                return float(val) / 1_000_000_000.0
        # SigNoz sometimes returns micro/milli/seconds; best-effort fallbacks.
        for key, divisor in (("startTimeUnixMicro", 1_000_000.0), ("startTime", 1.0)):
            val = raw_span.get(key)
            if isinstance(val, (int, float)) and val:
                return float(val) / divisor
        return 0.0

    @staticmethod
    def _coerce_status(raw_span: dict[str, object]) -> str:
        """Map status code / error flag to a level_or_status string."""
        if raw_span.get("hasError") is True or raw_span.get("error") is True:
            return "error"
        code = raw_span.get("statusCode", raw_span.get("status_code"))
        if code is None:
            return ""
        # OTel span status code 2 == ERROR.
        if code in (2, "2", "ERROR", "Error"):
            return "error"
        if code in (0, "0", "UNSET", "OK", "Ok"):
            return "ok"
        return str(code)

    @staticmethod
    def _iter_spans(payload: object) -> list[dict[str, object]]:
        """Extract span dicts from a SigNoz query_range payload.

        Tolerates the common v3/v4 shapes: ``data.result[].list[].data`` and a
        flat ``data.result[]`` list of spans.
        """
        spans: list[dict[str, object]] = []
        if not isinstance(payload, dict):
            return spans
        data = payload.get("data")
        if not isinstance(data, dict):
            return spans
        result = data.get("result")
        if not isinstance(result, list):
            return spans
        for series in result:
            if not isinstance(series, dict):
                continue
            rows = series.get("list")
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        inner = row.get("data")
                        spans.append(inner if isinstance(inner, dict) else row)
            else:
                # Flat span shape.
                spans.append(series)
        return spans

    def _normalize_span(self, raw_span: dict[str, object]) -> dict[str, object]:
        trace_id = str(
            raw_span.get("traceID", raw_span.get("trace_id", raw_span.get("traceId", "")))
        )
        span_id = str(
            raw_span.get("spanID", raw_span.get("span_id", raw_span.get("spanId", "")))
        )
        service = str(
            raw_span.get("serviceName", raw_span.get("service.name", raw_span.get("service", "")))
        )
        operation = str(raw_span.get("name", raw_span.get("operation", raw_span.get("op", ""))))
        duration = raw_span.get("durationNano", raw_span.get("duration_nano"))
        value: float | None
        try:
            value = float(cast("float | int | str", duration)) if duration is not None else None
        except (TypeError, ValueError):
            value = None
        message = " ".join(part for part in (service, operation) if part) or "(span)"
        return {
            "ts": self._coerce_ts(raw_span),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": self._coerce_status(raw_span),
            "message": message,
            "value": value,
            "labels": {
                "trace_id": trace_id,
                "span_id": span_id,
                "service.name": service,
                "durationNano": duration,
            },
            "raw": raw_span,
        }

    # -- public API --------------------------------------------------------

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Run a SigNoz query_range request and return normalized records.

        ``spec`` carries the (time-bound) query window plus an optional raw
        SigNoz ``query`` body. Required keys: ``start`` and ``end`` (epoch
        seconds). object caller-supplied ``payload`` is forwarded verbatim.
        """
        start = spec.get("start")
        end = spec.get("end")
        body: dict[str, object] = cast(dict[str, object], spec.get("payload") or {})
        # Express the bounded window in the body (SigNoz expects ms).
        if start is not None:
            body.setdefault("start", int(float(cast("float | int | str", start)) * 1000))
        if end is not None:
            body.setdefault("end", int(float(cast("float | int | str", end)) * 1000))
        if "query" in spec:
            body.setdefault("query", spec["query"])
        url = f"{self._base_url}/api/v3/query_range"
        status_code, payload = self._transport.request(
            "POST",
            url,
            headers=self._auth_headers(),
            json=body,
            timeout=self._timeout,
        )
        if status_code >= 400:
            return []
        return [self._normalize_span(span) for span in self._iter_spans(payload)]

    def health(self) -> dict[str, object]:
        """Probe the SigNoz version endpoint. Never raises."""
        url = f"{self._base_url}/api/v1/version"
        try:
            status_code, payload = self._transport.request(
                "GET",
                url,
                headers=self._auth_headers(),
                timeout=self._timeout,
            )
        except Exception:  # health must never raise
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "source": self.name, "error": "health check failed"}
        ok = 200 <= status_code < 300
        result: dict[str, object] = {"ok": ok, "source": self.name, "status_code": status_code}
        if isinstance(payload, dict) and "version" in payload:
            result["version"] = payload["version"]
        if not ok:
            result["error"] = f"unhealthy status {status_code}"
        return result
