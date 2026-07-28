"""Self-contained Thanos observability connector.

Thanos exposes a Prometheus-compatible HTTP API (``/api/v1/query`` and
``/api/v1/query_range``) in front of a federated set of Prometheus stores.
This connector reads PromQL results from that API and normalizes every sample
into a flat record. The HTTP transport is *injectable* so the connector can be
driven entirely from mocked responses in tests (no real network, no DNS, no
shell).

Design constraints honoured here:

* No imports from sibling connectors, the package ``__init__``, or any
  connector base class — this module stands alone.
* SSRF protection blocks loopback / private / link-local / cloud-metadata
  *literal* hosts on ``base_url``. We deliberately do **not** resolve DNS
  (so no DNS-rebinding surface and no network at construction time), and we
  **allow** ``http`` because Thanos query frontends are frequently exposed on
  plain HTTP inside an allowlisted internal network.
* ``health()`` never raises and returns ``{"ok", "detail"}``.
  ``query()`` never raises: transport / protocol failures are surfaced as a
  single normalized error record.
* No ``shell=True`` and no subprocess use anywhere.
* Every transport call is time-bound via an injected ``timeout``.

Record shape (one dict per sample)::

    {
        "ts": float,                 # unix seconds of the sample
        "source": str,               # connector name
        "kind": "metrics",
        "level_or_status": str,      # "" for data, "error" for failures
        "message": str,              # "<__name__>{label="v", ...}"
        "value": float,              # numeric sample value
        "labels": dict[str, str],    # metric labels (incl. __name__)
        "raw": object,                  # original series/sample/payload
    }
"""

from __future__ import annotations

import json as _json
import logging
import os
import time
from collections.abc import Callable
from typing import cast
from urllib.parse import urlsplit

import httpx

from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

# Injectable transport signature: (url, params, headers, timeout) -> (status, json)
HttpGet = Callable[..., "tuple[int, object]"]

KIND = "metrics"

_DEFAULT_TIMEOUT = 10.0


def _default_http_get(
    url: str,
    params: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[int, object]:
    """Real, time-bound stdlib transport used when none is injected.

    Matches the ``(url, params, headers, timeout) -> (status, json)`` contract.
    Only ``http``/``https`` are allowed; the request is bounded by an explicit
    timeout. The mocked tests never reach this path.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported url scheme: {parsed.scheme!r}")
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        resp = client.get(
            url,
            params=cast("dict[str, str | int | float | bool | None]", params),
            headers=headers or {},
        )
    status = int(resp.status_code)
    body = resp.content
    parsed_body: object = _json.loads(body) if body else {}
    return status, parsed_body


def _validate_base_url(base_url: str) -> str:
    """Reject SSRF-prone literal hosts; return a normalized base_url.

    Allows http and https only. Performs NO DNS resolution.
    """
    if not base_url or not isinstance(base_url, str):
        raise ValueError("base_url is required")

    parts = urlsplit(base_url)
    # Always-on scheme + present-host check (explicit message; also independent
    # of the SSRF host decision below).
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parts.scheme!r} (only http/https)")

    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ValueError("base_url has no host")

    # Private/loopback/metadata literal decision delegates to the canonical
    # SSRF guard so this connector's blocklist can never drift.
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(f"blocked internal/metadata address: {host!r}")

    # Normalize: drop trailing slash so endpoint joins are clean.
    return base_url.rstrip("/")


def _fmt_labels(metric: dict[str, object]) -> str:
    """Render ``__name__{k="v", ...}`` with sorted, non-name labels."""
    name = str(metric.get("__name__", ""))
    pairs = sorted((k, v) for k, v in metric.items() if k != "__name__")
    if not pairs:
        return name
    inner = ", ".join(f'{k}="{v}"' for k, v in pairs)
    return f"{name}{{{inner}}}"


class ThanosSource:
    """A metrics source backed by the Thanos (Prometheus-compatible) HTTP API."""

    KIND = KIND

    def __init__(
        self,
        config: dict[str, object],
        http_get: HttpGet | None = None,
        *,
        transport: HttpGet | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if http_get is not None and transport is not None:
            raise ValueError("provide http_get or transport, not both")
        base_url = config.get("base_url", "")
        self._base_url = _validate_base_url(str(base_url))
        # Bearer token is optional (Thanos may sit behind an auth proxy).
        self._token_env = config.get("token_env")
        self._http_get = http_get or transport or _default_http_get
        self._timeout = float(timeout)
        self.kind = KIND
        host = urlsplit(self._base_url).netloc
        self.name = config.get("name") or f"thanos:{host}"

    # -- helpers ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token_env:
            token = os.environ.get(str(self._token_env))
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _error_record(self, message: str, raw: object) -> dict[str, object]:
        return {
            "ts": time.time(),
            "source": self.name,
            "kind": KIND,
            "level_or_status": "error",
            "message": message,
            "value": 0.0,
            "labels": {},
            "raw": raw,
        }

    def _sample_record(
        self, metric: dict[str, object], ts: object, raw_value: object, raw: object
    ) -> dict[str, object]:
        try:
            value = float(cast("float | int | str", raw_value))
        except (TypeError, ValueError):
            value = 0.0
        try:
            ts_f = float(cast("float | int | str", ts))
        except (TypeError, ValueError):
            ts_f = 0.0
        labels = {str(k): str(v) for k, v in metric.items()}
        return {
            "ts": ts_f,
            "source": self.name,
            "kind": KIND,
            "level_or_status": "",
            "message": _fmt_labels(metric),
            "value": value,
            "labels": labels,
            "raw": raw,
        }

    # -- public API -------------------------------------------------------

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Run an instant or range PromQL query; return normalized records.

        Range mode is selected when ``start``/``end``/``step`` are present.
        Never raises: failures become a single error record.
        """
        promql = spec.get("promql") or spec.get("query")
        if not promql:
            return [self._error_record("missing 'promql' in spec", {"spec": spec})]

        is_range = any(k in spec for k in ("start", "end", "step"))
        if is_range:
            endpoint = "/api/v1/query_range"
            params: dict[str, object] = {"query": promql}
            for key in ("start", "end", "step"):
                if key in spec:
                    params[key] = spec[key]
        else:
            endpoint = "/api/v1/query"
            params = {"query": promql}
            if "time" in spec:
                params["time"] = spec["time"]

        # Thanos-specific tuning knobs (deduplication, partial-response).
        for key in ("dedup", "partial_response"):
            if key in spec:
                params[key] = spec[key]

        url = f"{self._base_url}{endpoint}"
        try:
            status, payload = self._http_get(
                url, params=params, headers=self._headers(), timeout=self._timeout
            )
        except Exception as exc:  # surfaced as a record, never raised
            logger.warning("thanos transport error", exc_info=True)
            return [self._error_record(f"transport error: {type(exc).__name__}", {"url": url})]

        return self._normalize(status, payload)

    def _normalize(self, status: int, payload: object) -> list[dict[str, object]]:
        if not isinstance(payload, dict):
            return [self._error_record(f"non-dict payload (status {status})", payload)]

        if payload.get("status") != "success":
            err = payload.get("error") or f"thanos returned status {status}"
            return [self._error_record(str(err), payload)]

        data = payload.get("data") or {}
        result_type = data.get("resultType")
        result = data.get("result")

        records: list[dict[str, object]] = []

        if result_type == "vector":
            for series in result or []:
                metric = series.get("metric", {})
                ts, raw_value = series.get("value", [0.0, "0"])
                records.append(self._sample_record(metric, ts, raw_value, series))

        elif result_type == "matrix":
            for series in result or []:
                metric = series.get("metric", {})
                for ts, raw_value in series.get("values", []):
                    records.append(self._sample_record(metric, ts, raw_value, series))

        elif result_type == "scalar":
            ts, raw_value = (result or [0.0, "0"])[:2]
            records.append(self._sample_record({}, ts, raw_value, {"scalar": result}))

        else:
            return [
                self._error_record(
                    f"unsupported resultType: {result_type!r}", payload
                )
            ]

        return records

    def health(self) -> dict[str, object]:
        """Return ``{"ok", "detail"}``. Never raises.

        Uses the cheap ``query=1`` instant query as a liveness probe (works on
        every Thanos query API regardless of optional health endpoints).
        """
        url = f"{self._base_url}/api/v1/query"
        try:
            status, payload = self._http_get(
                url,
                params={"query": "1"},
                headers=self._headers(),
                timeout=self._timeout,
            )
        except Exception:  # health must never raise
            logger.warning("health check failed", exc_info=True)
            return {
                "ok": False,
                "detail": "health check failed",
                "source": self.name,
            }

        ok = (
            isinstance(payload, dict)
            and payload.get("status") == "success"
            and 200 <= int(status) < 300
        )
        if ok:
            detail = f"ok (status {status})"
        elif isinstance(payload, dict):
            detail = str(payload.get("error") or f"unhealthy (status {status})")
        else:
            detail = f"unhealthy (status {status})"
        return {"ok": ok, "detail": detail, "source": self.name, "status": status}
