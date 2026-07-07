"""Self-contained Splunk Observability Cloud (SignalFx) connector.

Reads metric time series from the Splunk Observability Cloud API and normalizes
every datapoint into a flat record. Two read paths are supported:

* ``POST {base_url}/v2/signalflow`` — execute a SignalFlow program and stream
  back JSON datapoint messages (this connector consumes an already-collected
  list of those messages from the injected transport).
* ``GET  {base_url}/v1/timeserieswindow`` — fetch a window of datapoints for a
  set of metric time series (MTS).

The HTTP transport is *injectable* so the connector can be driven entirely from
mocked responses in tests (no real network, no DNS, no shell).

Design constraints honoured here:

* No imports from sibling connectors, the package ``__init__``, or any
  connector base class — this module stands alone.
* SSRF protection blocks loopback / private / link-local / cloud-metadata
  *literal* hosts on ``base_url``. We deliberately do **not** resolve DNS
  (so no DNS-rebinding surface and no network at construction time). Splunk
  Observability is SaaS, so ``https`` is the norm, but ``http`` is permitted
  for allowlisted non-internal hosts.
* The ``X-SF-Token`` access token comes from the environment
  (``token_env``) — never from inline config.
* ``health()`` never raises and returns ``{"ok", "detail"}``.
  ``query()`` never raises: transport / protocol failures are surfaced as a
  single normalized error record.
* No ``shell=True`` and no subprocess use anywhere.
* Every transport call is time-bound via an injected ``timeout``.

Record shape (one dict per datapoint)::

    {
        "ts": float,                 # unix seconds (SignalFx reports ms)
        "source": str,               # connector name
        "kind": "metrics",
        "level_or_status": str,      # "" for data, "error" for failures
        "message": str,              # metric name
        "value": float,              # numeric datapoint value
        "labels": dict[str, str],    # MTS dimensions
        "raw": Any,                  # original datapoint / message payload
    }
"""

from __future__ import annotations

import json as _json
import os
import time
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

from general_ludd.security.ssrf import is_url_blocked

# Injectable transport signature:
#   (method, url, params, headers, json, timeout) -> (status, json)
HttpRequest = Callable[..., "tuple[int, object]"]

KIND = "metrics"

_DEFAULT_TIMEOUT = 10.0


def _default_http_request(
    method: str,
    url: str,
    *,
    params: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    json: object = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[int, object]:
    """Real, time-bound stdlib transport used when none is injected.

    Matches the connector's
    ``(method, url, *, params, headers, json, timeout) -> (status, json)``
    contract. Only ``http``/``https`` are allowed; the request is bounded by an
    explicit timeout. The mocked tests never reach this path.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported url scheme: {parsed.scheme!r}")
    if params:
        sep = "&" if parsed.query else "?"
        url = f"{url}{sep}{urlencode(params, doseq=True)}"
    data = _json.dumps(json).encode("utf-8") if json is not None else None
    req = urllib.request.Request(
        url, data=data, headers=headers or {}, method=method.upper()
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = int(resp.getcode() or 0)
        body = resp.read()
    parsed_body: object = _json.loads(body) if body else {}
    return status, parsed_body


def _validate_base_url(base_url: str) -> str:
    if not base_url or not isinstance(base_url, str):
        raise ValueError("base_url is required")

    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parts.scheme!r} (only http/https)")

    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ValueError("base_url has no host")

    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(f"blocked internal/metadata address: {host!r}")

    return base_url.rstrip("/")


def _ms_to_seconds(ts: Any) -> float:
    """SignalFx timestamps are unix milliseconds; convert to seconds."""
    try:
        ts_f = float(ts)
    except (TypeError, ValueError):
        return 0.0
    # Heuristic: anything plausibly in ms (>1e12) gets divided.
    if ts_f > 1e12:
        return ts_f / 1000.0
    return ts_f


class SplunkObservabilitySource:
    """A metrics source backed by Splunk Observability Cloud (SignalFx)."""

    KIND = KIND

    def __init__(
        self,
        config: dict[str, object],
        http_request: HttpRequest | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        base_url = str(config.get("base_url", ""))
        self._base_url = _validate_base_url(base_url)
        # Access token is resolved from the environment only.
        token_env_raw = config.get("token_env")
        self._token_env = str(token_env_raw) if isinstance(token_env_raw, str) else None
        self._http_request = http_request or _default_http_request
        self._timeout = float(timeout)
        self.kind = KIND
        host = urlsplit(self._base_url).netloc
        self.name = config.get("name") or f"splunk-o11y:{host}"

    # -- helpers ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._token_env:
            token = os.environ.get(self._token_env)
            if token:
                headers["X-SF-Token"] = token
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

    def _datapoint_record(
        self,
        metric: str,
        dimensions: dict[str, object],
        ts: object,
        raw_value: object,
        raw: object,
    ) -> dict[str, object]:
        try:
            value = float(str(raw_value))
        except (TypeError, ValueError):
            value = 0.0
        labels = {str(k): str(v) for k, v in (dimensions or {}).items()}
        return {
            "ts": _ms_to_seconds(ts),
            "source": self.name,
            "kind": KIND,
            "level_or_status": "",
            "message": str(metric),
            "value": value,
            "labels": labels,
            "raw": raw,
        }

    # -- public API -------------------------------------------------------

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Run a SignalFlow program or a timeserieswindow fetch.

        ``spec["program"]`` selects the SignalFlow POST path; otherwise a
        ``spec["metric"]`` (with ``start``/``end``) drives the timeserieswindow
        GET path. Never raises: failures become a single error record.
        """
        if spec.get("program"):
            return self._query_signalflow(spec)
        if spec.get("metric") or spec.get("query"):
            return self._query_timeserieswindow(spec)
        return [
            self._error_record(
                "spec must provide 'program' (signalflow) or 'metric'/'query'"
                " (timeserieswindow)",
                {"spec": spec},
            )
        ]

    def _query_signalflow(self, spec: dict[str, object]) -> list[dict[str, object]]:
        url = f"{self._base_url}/v2/signalflow"
        body: dict[str, object] = {"program": spec["program"]}
        for key in ("start", "stop", "resolution", "maxDelay", "immediate"):
            if key in spec:
                body[key] = spec[key]
        try:
            status, payload = self._http_request(
                "POST",
                url,
                params=None,
                headers=self._headers(),
                json=body,
                timeout=self._timeout,
            )
        except Exception as exc:  # surfaced as a record, never raised
            return [self._error_record(f"transport error: {exc}", {"url": url})]
        return self._normalize_signalflow(status, payload)

    def _query_timeserieswindow(self, spec: dict[str, object]) -> list[dict[str, object]]:
        url = f"{self._base_url}/v1/timeserieswindow"
        params: dict[str, object] = {}
        query = spec.get("query") or spec.get("metric")
        if query:
            params["query"] = query
        for key in ("startMs", "endMs", "start", "end", "resolution"):
            if key in spec:
                params[key] = spec[key]
        try:
            status, payload = self._http_request(
                "GET",
                url,
                params=params,
                headers=self._headers(),
                json=None,
                timeout=self._timeout,
            )
        except Exception as exc:  # surfaced as a record, never raised
            return [self._error_record(f"transport error: {exc}", {"url": url})]
        return self._normalize_timeserieswindow(status, payload)

    # -- normalization ----------------------------------------------------

    def _normalize_signalflow(
        self, status: int, payload: object
    ) -> list[dict[str, object]]:
        if not (200 <= int(status) < 300):
            err = (
                payload.get("message")
                if isinstance(payload, dict)
                else f"signalflow status {status}"
            )
            return [self._error_record(str(err or f"status {status}"), payload)]

        # SignalFlow streams a list of typed messages; we care about
        # "data" messages plus the "metadata" messages that describe each TSID.
        if not isinstance(payload, list):
            return [
                self._error_record("signalflow payload is not a message list", payload)
            ]

        # First pass: collect metadata (tsId -> {metric, dimensions}).
        meta: dict[str, dict[str, object]] = {}
        for msg in payload:
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "metadata":
                tsid = str(msg.get("tsId", ""))
                props = msg.get("properties", {}) or {}
                metric = props.get("sf_metric", props.get("metric", ""))
                dims = {
                    k: v
                    for k, v in props.items()
                    if isinstance(k, str) and not k.startswith("sf_")
                }
                meta[tsid] = {"metric": metric, "dimensions": dims}

        records: list[dict[str, object]] = []
        for msg in payload:
            if not isinstance(msg, dict) or msg.get("type") != "data":
                continue
            logical_ts = msg.get("logicalTimestampMs", msg.get("timestamp"))
            for dp in msg.get("data", []) or []:
                tsid = str(dp.get("tsId", ""))
                info = meta.get(tsid, {})
                metric_val = info.get("metric", tsid)
                dims_val = info.get("dimensions", {})
                records.append(
                    self._datapoint_record(
                        str(metric_val),
                        dims_val if isinstance(dims_val, dict) else {},
                        logical_ts,
                        dp.get("value"),
                        dp,
                    )
                )
        return records

    def _normalize_timeserieswindow(
        self, status: int, payload: object
    ) -> list[dict[str, object]]:
        if not isinstance(payload, dict):
            return [self._error_record(f"non-dict payload (status {status})", payload)]

        if not (200 <= int(status) < 300):
            err = payload.get("message") or f"timeserieswindow status {status}"
            return [self._error_record(str(err), payload)]

        # Shape: {"data": {tsId: [[tsMs, value], ...]}, "metadata": {tsId: {...}}}
        data = payload.get("data") or {}
        metadata = payload.get("metadata") or {}

        records: list[dict[str, object]] = []
        for tsid, points in data.items():
            md = metadata.get(tsid, {}) if isinstance(metadata, dict) else {}
            metric = ""
            dims: dict[str, object] = {}
            if isinstance(md, dict):
                metric = md.get("sf_metric", md.get("metric", tsid))
                dims = {
                    k: v
                    for k, v in md.items()
                    if isinstance(k, str) and not k.startswith("sf_")
                }
            for point in points or []:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    ts, raw_value = point[0], point[1]
                else:
                    ts, raw_value = 0, point
                records.append(
                    self._datapoint_record(
                        metric or tsid, dims, ts, raw_value, point
                    )
                )
        return records

    def health(self) -> dict[str, object]:
        """Return ``{"ok", "detail"}``. Never raises.

        Probes the lightweight ``/v2/metric`` metadata endpoint (token-gated)
        as a liveness/credential check.
        """
        url = f"{self._base_url}/v2/metric"
        try:
            status, payload = self._http_request(
                "GET",
                url,
                params={"limit": 1},
                headers=self._headers(),
                json=None,
                timeout=self._timeout,
            )
        except Exception as exc:  # health must never raise
            return {
                "ok": False,
                "detail": f"transport error: {exc}",
                "source": self.name,
            }

        ok = 200 <= int(status) < 300
        if ok:
            detail = f"ok (status {status})"
        elif isinstance(payload, dict):
            detail = str(payload.get("message") or f"unhealthy (status {status})")
        else:
            detail = f"unhealthy (status {status})"
        return {"ok": ok, "detail": detail, "source": self.name, "status": status}
