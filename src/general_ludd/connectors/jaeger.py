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
from typing import Any, ClassVar, Protocol, runtime_checkable

from general_ludd.connectors.normalize import sanitize_metric_value
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0


# --------------------------------------------------------------------------- #
# Transport protocol (structural; a fake or httpx both satisfy it)
# --------------------------------------------------------------------------- #
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
    """Thin default transport so the connector works with no DI in prod."""

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        import httpx  # local import: keep module import-light + optional dep

        return httpx.get(
            url,
            params=params,
            timeout=timeout if timeout is not None else _DEFAULT_TIMEOUT,
            headers=headers,
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
def _span_has_error(span: dict[str, Any]) -> bool:
    for tag in span.get("tags", []) or []:
        key = tag.get("key")
        val = tag.get("value")
        if key == "error" and val not in (False, "false", "False", 0, None):
            return True
        if key in ("otel.status_code", "status.code") and str(val).upper() in ("ERROR", "2"):
            return True
        if key == "http.status_code":
            try:
                if int(val) >= 500:
                    return True
            except (TypeError, ValueError):
                pass
    return False


class JaegerSource:
    """Tracing source backed by the Jaeger query API."""

    KIND: ClassVar[str] = "traces"

    def __init__(self, config: dict[str, Any], transport: HttpTransport | None = None) -> None:
        base_url = str(config.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("JaegerSource requires config['base_url']")
        _assert_public_base_url(base_url)

        self._base_url = base_url
        self.name: str = str(config.get("name") or "jaeger")
        self._timeout: float = float(config.get("timeout", _DEFAULT_TIMEOUT))
        self._token_env: str | None = config.get("token_env")
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
            resp = self._get("/api/services")
            code = getattr(resp, "status_code", 0)
            ok = 200 <= code < 300
            return {"ok": ok, "detail": f"GET /api/services -> {code}"}
        except Exception as exc:  # never raise
            logger.warning("jaeger health check failed: %s", exc)
            return {"ok": False, "detail": f"error: {exc}"}

    # -- query ------------------------------------------------------------ #
    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            trace_id = spec.get("trace_id")
            if trace_id:
                resp = self._get(f"/api/traces/{trace_id}")
            else:
                params: dict[str, Any] = {}
                for key in ("service", "operation", "lookback", "limit"):
                    if spec.get(key) is not None:
                        params[key] = spec[key]
                resp = self._get("/api/traces", params=params)

            code = getattr(resp, "status_code", 0)
            if not (200 <= code < 300):
                logger.warning("jaeger query non-2xx: %s", code)
                return []
            payload = resp.json()
        except Exception as exc:
            logger.warning("jaeger query failed: %s", exc)
            return []

        records: list[dict[str, Any]] = []
        for trace in payload.get("data", []) or []:
            processes = trace.get("processes", {}) or {}
            for span in trace.get("spans", []) or []:
                rec = self._normalize_span(span, processes)
                if rec is not None:
                    records.append(rec)
        return records

    def _normalize_span(
        self, span: dict[str, Any], processes: dict[str, Any]
    ) -> dict[str, Any] | None:
        try:
            trace_id = span["traceID"]
            span_id = span["spanID"]
            start_us = span["startTime"]
            duration = span.get("duration", 0)
            operation = span.get("operationName", "")
        except (KeyError, TypeError):
            return None

        proc = processes.get(span.get("processID", ""), {}) or {}
        service = proc.get("serviceName", "")

        return {
            "ts": float(start_us) / 1_000_000.0,  # microseconds -> seconds
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
            "raw": span,
        }
