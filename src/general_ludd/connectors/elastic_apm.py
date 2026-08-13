"""Elastic APM observability connector.

Self-contained read-only source that pulls APM spans/transactions out of an
Elasticsearch / Elastic APM endpoint and normalizes them into the project's
flat record shape. No base/sibling imports — everything this module needs is
defined here so it can be dropped in and tested in isolation.

Contract
--------
* ``KIND == "traces"``.
* ``name`` attribute identifies the source instance.
* ``__init__(config)`` is fully config-driven; secrets are read from the
  environment via ``*_env`` keys (never stored inline in config).
* ``base_url`` is checked with a *literal-host* SSRF guard: a URL whose host is
  a private / loopback / link-local / reserved IP literal is rejected. No DNS
  resolution is performed (a hostname is allowed through — we only refuse
  obviously-internal literal addresses, fail-closed on parse errors).
* ``health()`` returns ``{"ok": bool, "detail": str}`` and never raises.
* ``query(spec)`` returns a list of normalized ``dict`` records with the keys
  ``ts, source, kind, level_or_status, message, value, labels, raw``.
* The HTTP transport is injectable for testing; the default uses ``httpx``.
* All network calls are time-bound. No ``shell=True`` / subprocess use.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlsplit

from general_ludd.connectors._errors import SSRFError
from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

KIND = "traces"


class _TupleResponse:
    def __init__(self, status: object, body: object) -> None:
        self.status_code = int(status) if isinstance(status, int) else 0
        self._body = body

    def json(self) -> object:
        return self._body


def _coerce_response(value: object) -> HttpResponse:
    if isinstance(value, tuple) and len(value) == 2:
        return cast(HttpResponse, _TupleResponse(value[0], value[1]))
    return cast(HttpResponse, value)


# Injectable transport signature: (method, url, *, headers, params, json,
# timeout) -> HttpResponse. Kept identical across connectors so one mock works
# for all three.
Transport = Callable[..., HttpResponse]
TransportInput = Callable[..., HttpResponse | tuple[int, object]]


class _CallbackResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = int(status_code)
        self._body = body

    @property
    def text(self) -> str:
        return self._body if isinstance(self._body, str) else str(self._body)

    def json(self) -> object:
        return self._body


class _CompatibleTransport:
    def __init__(self, callback: TransportInput) -> None:
        self._callback = callback

    def __call__(self, *args: object, **kwargs: object) -> HttpResponse:
        result = self._callback(*args, **kwargs)
        if isinstance(result, tuple):
            status, body = result
            return _CallbackResponse(status, body)
        return result


def _validate_base_url(base_url: str) -> str:
    """Fail-closed SSRF guard on a base URL's literal host.

    The private/metadata decision is delegated to the canonical shared guard
    :func:`general_ludd.security.ssrf.is_url_blocked` so this connector cannot
    drift weaker than the single source of truth (no DNS resolution).
    """
    if not isinstance(base_url, str) or not base_url:
        raise SSRFError("base_url must be a non-empty string")
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise SSRFError(f"base_url scheme must be http/https, got {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise SSRFError(f"base_url has no host: {base_url!r}")
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise SSRFError(f"base_url host {host!r} is a blocked internal address")
    return base_url.rstrip("/")


def _default_transport(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: Any | None = None,
    timeout: float = 30.0,
) -> HttpResponse:
    import httpx

    resp = httpx.request(
        method,
        url,
        headers=headers,
        params=params,
        json=json,
        timeout=timeout,
    )
    return resp  # httpx.Response satisfies the HttpResponse protocol


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class ElasticApmSource:
    """Read APM spans/transactions from Elasticsearch and normalize them."""

    KIND = KIND

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: TransportInput | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        env = environ if environ is not None else os.environ
        self.name: str = str(config.get("name", "elastic_apm"))
        self.base_url: str = _validate_base_url(str(config.get("base_url", "")))
        self.index: str = str(config.get("index", "apm-*"))
        self.timeout: float = float(config.get("timeout", 30.0))
        self._transport: Transport = _CompatibleTransport(
            transport or _default_transport
        )

        # Secrets are resolved from the environment via *_env indirection only.
        self._bearer: str | None = None
        self._api_key: str | None = None
        bearer_env = config.get("bearer_token_env")
        api_key_env = config.get("api_key_env")
        if bearer_env:
            self._bearer = env.get(str(bearer_env))
        if api_key_env:
            self._api_key = env.get(str(api_key_env))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"ApiKey {self._api_key}"
        elif self._bearer:
            headers["Authorization"] = f"Bearer {self._bearer}"
        return headers

    def _search_url(self) -> str:
        return f"{self.base_url}/{self.index}/_search"

    def health(self) -> dict[str, Any]:
        """Probe the cluster root; never raises."""
        try:
            resp = _coerce_response(self._transport(
                "GET",
                f"{self.base_url}/",
                headers=self._headers(),
                timeout=self.timeout,
            ))
        except Exception:  # health() must never raise
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        ok = 200 <= resp.status_code < 300
        return {"ok": ok, "detail": f"HTTP {resp.status_code}"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Run an ES search and normalize APM transactions/spans to records."""
        body: dict[str, Any] = spec.get("body") or {"query": {"match_all": {}}}
        if "size" not in body and spec.get("size") is not None:
            body["size"] = int(spec["size"])
        resp = _coerce_response(self._transport(
            "POST",
            self._search_url(),
            headers=self._headers(),
            json=body,
            timeout=self.timeout,
        ))
        payload = resp.json() or {}
        hits = (payload.get("hits") or {}).get("hits") or []
        if not hits and isinstance(payload.get("data"), list):
            # Accept compact gateway responses that expose documents directly.
            hits = [
                item if isinstance(item, dict) and "_source" in item else {"_source": item}
                for item in payload["data"]
                if isinstance(item, dict)
            ]
        return [self._normalize_hit(hit) for hit in hits]

    def _normalize_hit(self, hit: dict[str, Any]) -> dict[str, Any]:
        src = hit.get("_source") or {}
        trace = src.get("trace") or {}
        span = src.get("span") or {}
        txn = src.get("transaction") or {}
        service = src.get("service") or {}
        error = src.get("error") or {}
        event = src.get("event") or {}

        # Duration is reported in microseconds by APM; prefer span then txn.
        duration_us = None
        if isinstance(span.get("duration"), dict):
            duration_us = _as_float(span["duration"].get("us"))
        if duration_us is None and isinstance(txn.get("duration"), dict):
            duration_us = _as_float(txn["duration"].get("us"))

        outcome = event.get("outcome") or txn.get("result")
        is_error = bool(error) or outcome == "failure"
        status = "error" if is_error else (str(outcome) if outcome else "ok")

        message = (
            error.get("message")
            or txn.get("name")
            or span.get("name")
            or src.get("message")
            or ""
        )

        labels: dict[str, Any] = {}
        if trace.get("id"):
            labels["trace_id"] = trace["id"]
        if span.get("id"):
            labels["span_id"] = span["id"]
        if txn.get("id"):
            labels["transaction_id"] = txn["id"]
        if service.get("name"):
            labels["service"] = service["name"]
        if span.get("type"):
            labels["span_type"] = span["type"]

        return {
            "ts": src.get("@timestamp"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": status,
            "message": str(message),
            "value": duration_us,
            "labels": labels,
            "raw": hit,
        }
