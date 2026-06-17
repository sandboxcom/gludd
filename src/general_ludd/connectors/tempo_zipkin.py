"""Grafana Tempo + Zipkin tracing connector for gludd.

One class, ``TempoZipkinSource``, supporting two flavors selected per-query via
``spec['flavor']`` in {'tempo', 'zipkin'}:

  * tempo  -> GET {base_url}/api/traces/{traceID}     (OTLP/Tempo JSON, ns ts)
  * zipkin -> GET {base_url}/api/v2/traces?serviceName=&limit=
              (Zipkin v2 JSON: list of traces, each a list of spans, us ts)

Both flavors normalize spans into gludd's cross-source record shape with
trace_id/span_id/service labels for association, duration as ``value``, and an
error/ok status.

Design constraints (enforced here, not inherited):
  * No base/sibling imports — this module stands alone.
  * Injectable HTTP transport; defaults to httpx.
  * SSRF guard rejects literal private/loopback/link-local hosts (no DNS).
  * ``health()`` never raises; ``query()`` is fail-soft.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import Any, ClassVar, Protocol, runtime_checkable
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0
_ALLOWED_SCHEMES = ("http", "https")


@runtime_checkable
class HttpTransport(Protocol):
    def get(
        self,
        url: str,
        params: dict[str, Any] | None = ...,
        timeout: float | None = ...,
        headers: dict[str, str] | None = ...,
    ) -> Any: ...


class _HttpxTransport:
    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        import httpx

        return httpx.get(
            url,
            params=params,
            timeout=timeout if timeout is not None else _DEFAULT_TIMEOUT,
            headers=headers,
        )


# --------------------------------------------------------------------------- #
# SSRF literal-host guard (no DNS)
# --------------------------------------------------------------------------- #
def _assert_public_base_url(base_url: str) -> None:
    parts = urlsplit(base_url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError(f"disallowed scheme for base_url: {parts.scheme!r}")

    host = parts.hostname
    if not host:
        raise ValueError("base_url has no host")

    lowered = host.lower()
    if lowered in {"localhost", "localhost.localdomain", "ip6-localhost"}:
        raise ValueError(f"refusing internal host: {host!r}")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # DNS name; literal block does not vet names

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise ValueError(f"refusing internal IP literal: {host!r}")


# --------------------------------------------------------------------------- #
# Tempo (OTLP JSON) helpers
# --------------------------------------------------------------------------- #
def _otlp_attr_value(value: Any) -> Any:
    """Unwrap an OTLP AnyValue dict ({'stringValue': ...}) to a plain value."""
    if not isinstance(value, dict):
        return value
    for vkey in ("stringValue", "intValue", "boolValue", "doubleValue"):
        if vkey in value:
            return value[vkey]
    return value


def _otlp_service_name(resource: dict[str, Any]) -> str:
    for attr in resource.get("attributes", []) or []:
        if attr.get("key") == "service.name":
            return str(_otlp_attr_value(attr.get("value")))
    return ""


def _tempo_span_has_error(span: dict[str, Any]) -> bool:
    status = span.get("status", {}) or {}
    code = status.get("code")
    # OTLP status: 0=UNSET, 1=OK, 2=ERROR
    return code in (2, "2", "STATUS_CODE_ERROR")


# --------------------------------------------------------------------------- #
# Zipkin helpers
# --------------------------------------------------------------------------- #
def _zipkin_span_has_error(span: dict[str, Any]) -> bool:
    tags = span.get("tags", {}) or {}
    if "error" in tags:
        return True
    status = tags.get("http.status_code")
    if status is not None:
        try:
            if int(status) >= 500:
                return True
        except (TypeError, ValueError):
            pass
    return False


class TempoZipkinSource:
    """Tracing source for Grafana Tempo and Zipkin backends."""

    KIND: ClassVar[str] = "traces"

    def __init__(self, config: dict[str, Any], transport: HttpTransport | None = None) -> None:
        base_url = str(config.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("TempoZipkinSource requires config['base_url']")
        _assert_public_base_url(base_url)

        self._base_url = base_url
        self.name: str = str(config.get("name") or "tempo-zipkin")
        self._timeout: float = float(config.get("timeout", _DEFAULT_TIMEOUT))
        self._token_env: str | None = config.get("token_env")
        # default flavor used by health(); query() reads spec['flavor'] per call.
        self._flavor: str = str(config.get("flavor") or "zipkin").lower()
        self._transport: HttpTransport = transport or _HttpxTransport()

    # -- auth ------------------------------------------------------------- #
    def _auth_header(self) -> dict[str, str]:
        if not self._token_env:
            return {}
        token = os.environ.get(self._token_env)
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base_url}{path}"
        headers = self._auth_header()
        return self._transport.get(
            url,
            params=params,
            timeout=self._timeout,
            headers=headers or None,
        )

    # -- health ----------------------------------------------------------- #
    def health(self) -> dict[str, Any]:
        try:
            # zipkin: /api/v2/services ; tempo: /ready
            path = "/api/v2/services" if self._flavor == "zipkin" else "/ready"
            resp = self._get(path)
            code = getattr(resp, "status_code", 0)
            ok = 200 <= code < 300
            return {"ok": ok, "detail": f"GET {path} -> {code}"}
        except Exception as exc:
            logger.warning("tempo/zipkin health check failed: %s", exc)
            return {"ok": False, "detail": f"error: {exc}"}

    # -- query ------------------------------------------------------------ #
    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        flavor = str(spec.get("flavor") or self._flavor).lower()
        if flavor == "zipkin":
            return self._query_zipkin(spec)
        if flavor == "tempo":
            return self._query_tempo(spec)
        logger.warning("tempo/zipkin: unknown flavor %r", flavor)
        return []

    # -- zipkin ----------------------------------------------------------- #
    def _query_zipkin(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            params: dict[str, Any] = {}
            if spec.get("service") is not None:
                params["serviceName"] = spec["service"]
            if spec.get("limit") is not None:
                params["limit"] = spec["limit"]
            resp = self._get("/api/v2/traces", params=params)
            code = getattr(resp, "status_code", 0)
            if not (200 <= code < 300):
                logger.warning("zipkin query non-2xx: %s", code)
                return []
            traces = resp.json()
        except Exception as exc:
            logger.warning("zipkin query failed: %s", exc)
            return []

        records: list[dict[str, Any]] = []
        for trace in traces or []:
            for span in trace or []:
                rec = self._normalize_zipkin_span(span)
                if rec is not None:
                    records.append(rec)
        return records

    def _normalize_zipkin_span(self, span: dict[str, Any]) -> dict[str, Any] | None:
        try:
            trace_id = span["traceId"]
            span_id = span["id"]
            ts_us = span["timestamp"]
            duration = span.get("duration", 0)
            name = span.get("name", "")
        except (KeyError, TypeError):
            return None

        endpoint = span.get("localEndpoint", {}) or {}
        service = endpoint.get("serviceName", "")

        return {
            "ts": float(ts_us) / 1_000_000.0,  # microseconds -> seconds
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "error" if _zipkin_span_has_error(span) else "ok",
            "message": f"{service} {name}".strip(),
            "value": duration,
            "labels": {
                "trace_id": trace_id,
                "span_id": span_id,
                "service": service,
            },
            "raw": span,
        }

    # -- tempo ------------------------------------------------------------ #
    def _query_tempo(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        trace_id = spec.get("trace_id")
        if not trace_id:
            logger.warning("tempo query requires spec['trace_id']")
            return []
        try:
            resp = self._get(f"/api/traces/{trace_id}")
            code = getattr(resp, "status_code", 0)
            if not (200 <= code < 300):
                logger.warning("tempo query non-2xx: %s", code)
                return []
            payload = resp.json()
        except Exception as exc:
            logger.warning("tempo query failed: %s", exc)
            return []

        records: list[dict[str, Any]] = []
        for batch in payload.get("batches", []) or []:
            service = _otlp_service_name(batch.get("resource", {}) or {})
            for scope in batch.get("scopeSpans", []) or []:
                for span in scope.get("spans", []) or []:
                    rec = self._normalize_tempo_span(span, service)
                    if rec is not None:
                        records.append(rec)
        return records

    def _normalize_tempo_span(
        self, span: dict[str, Any], service: str
    ) -> dict[str, Any] | None:
        try:
            trace_id = span["traceId"]
            span_id = span["spanId"]
            start_ns = int(span["startTimeUnixNano"])
            name = span.get("name", "")
        except (KeyError, TypeError, ValueError):
            return None

        end_ns_raw = span.get("endTimeUnixNano")
        try:
            duration_us = (int(end_ns_raw) - start_ns) // 1_000 if end_ns_raw is not None else 0
        except (TypeError, ValueError):
            duration_us = 0

        return {
            "ts": float(start_ns) / 1_000_000_000.0,  # nanoseconds -> seconds
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "error" if _tempo_span_has_error(span) else "ok",
            "message": f"{service} {name}".strip(),
            "value": duration_us,
            "labels": {
                "trace_id": trace_id,
                "span_id": span_id,
                "service": service,
            },
            "raw": span,
        }
