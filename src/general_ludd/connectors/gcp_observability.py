"""Self-contained GCP observability connector.

``GcpObservabilitySource`` reads from two Google Cloud APIs and normalizes the
results into a common record shape so downstream code can treat logs and
metrics uniformly:

* ``mode='logs'``    -> Cloud Logging ``entries.list``
  (POST ``https://logging.googleapis.com/v2/entries:list``)
* ``mode='metrics'`` -> Cloud Monitoring ``timeSeries.list``
  (GET ``https://monitoring.googleapis.com/v3/projects/{project}/timeSeries``)

Design constraints (security + testability):

* **Injectable transport.** The HTTP client is passed in via ``transport`` so
  tests can supply canned payloads and no real network call is ever made. The
  transport must expose ``request(method, url, *, headers, params, json_body,
  timeout)`` returning an object with ``.status`` and ``.json()``.
* **Bearer auth from env.** The token is read from the environment variable
  named by ``config['token_env']`` (or an injected client/token), never
  hard-coded.
* **SSRF literal-host block.** Endpoint hostnames are validated against a
  literal denylist (loopback, link-local, RFC-1918 private ranges, the cloud
  metadata host) at construction time *without DNS resolution*. There is no
  name lookup, so a hostile DNS answer cannot be used to bypass the check.
* **Time-bound.** Every request is issued with an explicit timeout.
* ``health()`` never raises; it returns ``{'ok': bool, 'detail': str}``.

This module intentionally imports nothing from sibling connector modules and
defines no shared base class — it is standalone.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

DEFAULT_LOGGING_ENDPOINT = "https://logging.googleapis.com/v2/entries:list"
DEFAULT_MONITORING_BASE = "https://monitoring.googleapis.com/v3"
DEFAULT_TIMEOUT = 15.0


@runtime_checkable
class HttpTransport(Protocol):
    """Injectable HTTP transport contract."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object] | None = ...,
        json_body: dict[str, object] | None = ...,
        timeout: float,
    ) -> HttpResponse: ...


def _require_safe_endpoint(url: str) -> str:
    """Validate an endpoint URL, rejecting internal/SSRF targets. No DNS.

    The private/metadata decision is delegated to the canonical shared guard
    :func:`general_ludd.security.ssrf.is_url_blocked` so this connector cannot
    drift weaker than the single source of truth. The URL is returned UNCHANGED
    (no rstrip) — callers build endpoint paths off the exact string.
    """

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported endpoint scheme: {url!r}")
    host = parts.hostname
    if not host:
        raise ValueError(f"endpoint has no host: {url!r}")
    if is_url_blocked(url, scheme_allowlist=("http", "https")):
        raise ValueError(f"refusing internal/SSRF endpoint: {url!r}")
    return url


class GcpObservabilitySource:
    """Query GCP Cloud Logging and Cloud Monitoring with normalized output."""

    KIND = "gcp_observability"

    def __init__(
        self,
        config: dict[str, object],
        *,
        transport: HttpTransport,
        token: str | None = None,
    ) -> None:
        self._transport = transport
        self.project = str(config.get("project", ""))
        self.name = str(config.get("name", f"gcp:{self.project}"))
        self._timeout = float(str(config.get("timeout", DEFAULT_TIMEOUT)))
        self._order_by = str(config.get("order_by", "timestamp desc"))

        # Endpoints are validated (literal SSRF block) at construction time, so a
        # misconfigured/internal endpoint fails fast and closed before any I/O.
        self._logging_endpoint = _require_safe_endpoint(
            str(config.get("logging_endpoint", DEFAULT_LOGGING_ENDPOINT))
        )
        monitoring_base = _require_safe_endpoint(
            str(config.get("monitoring_base", DEFAULT_MONITORING_BASE))
        )
        self._monitoring_endpoint = (
            f"{monitoring_base}/projects/{self.project}/timeSeries"
        )

        # Secret resolution: explicit injected token wins; otherwise read the env
        # var named by ``token_env``. The token itself is never stored in config.
        if token is not None:
            self._token = token
        else:
            token_env = str(config.get("token_env", "GCP_TOKEN"))
            resolved = os.environ.get(token_env)
            if not resolved:
                raise ValueError(
                    f"missing Bearer token: env var {token_env!r} is unset"
                )
            self._token = resolved

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _check_status(self, resp: HttpResponse) -> None:
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"GCP API returned HTTP {resp.status_code}")

    # ------------------------------------------------------------------ #
    # health
    # ------------------------------------------------------------------ #
    def health(self) -> dict[str, object]:
        """Minimal monitoring call; never raises. Returns {'ok', 'detail'}."""

        try:
            resp = self._transport.request(
                "GET",
                self._monitoring_endpoint,
                headers=self._headers(),
                params={"pageSize": 1},
                timeout=self._timeout,
            )
            self._check_status(resp)
        except Exception as exc:  # health must never propagate to the caller
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "detail": "monitoring reachable"}

    # ------------------------------------------------------------------ #
    # query dispatch
    # ------------------------------------------------------------------ #
    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        mode = spec.get("mode")
        if mode == "logs":
            return self._query_logs(spec)
        if mode == "metrics":
            return self._query_metrics(spec)
        raise ValueError(f"unknown query mode: {mode!r} (expected logs|metrics)")

    # ------------------------------------------------------------------ #
    # logs mode
    # ------------------------------------------------------------------ #
    def _query_logs(self, spec: dict[str, object]) -> list[dict[str, object]]:
        body: dict[str, object] = {
            "resourceNames": [f"projects/{self.project}"],
            "orderBy": str(spec.get("order_by", self._order_by)),
            "pageSize": int(str(spec.get("page_size", 100))),
        }
        if "filter" in spec and spec["filter"] is not None:
            body["filter"] = str(spec["filter"])

        resp = self._transport.request(
            "POST",
            self._logging_endpoint,
            headers=self._headers(),
            json_body=body,
            timeout=self._timeout,
        )
        self._check_status(resp)
        payload = resp.json()
        entries = payload.get("entries") or []
        return [self._normalize_log_entry(e) for e in entries]

    def _normalize_log_entry(self, entry: dict[str, object]) -> dict[str, object]:
        message = entry.get("textPayload")
        if message is None:
            json_payload = entry.get("jsonPayload")
            if isinstance(json_payload, dict):
                message = json_payload.get("message")
        if message is None:
            message = ""

        resource_raw = entry.get("resource") or {}
        resource: dict[str, object] = resource_raw if isinstance(resource_raw, dict) else {}
        labels: dict[str, object] = {
            "resource.type": resource.get("type"),
            "logName": entry.get("logName"),
        }
        # Surface trace id so log records can be associated with a request/trace.
        if entry.get("trace") is not None:
            labels["trace"] = entry["trace"]

        return {
            "ts": entry.get("timestamp"),
            "source": self.name,
            "kind": "logs",
            "level_or_status": entry.get("severity"),
            "message": message,
            "value": None,
            "labels": labels,
            "raw": entry,
        }

    # ------------------------------------------------------------------ #
    # metrics mode
    # ------------------------------------------------------------------ #
    def _query_metrics(self, spec: dict[str, object]) -> list[dict[str, object]]:
        params: dict[str, object] = {}
        if "filter" in spec and spec["filter"] is not None:
            params["filter"] = str(spec["filter"])
        if spec.get("interval_start") is not None:
            params["interval.startTime"] = str(spec["interval_start"])
        if spec.get("interval_end") is not None:
            params["interval.endTime"] = str(spec["interval_end"])
        if spec.get("page_size") is not None:
            params["pageSize"] = int(str(spec["page_size"]))

        resp = self._transport.request(
            "GET",
            self._monitoring_endpoint,
            headers=self._headers(),
            params=params,
            timeout=self._timeout,
        )
        self._check_status(resp)
        payload = resp.json()
        series = payload.get("timeSeries") or []

        rows: list[dict[str, object]] = []
        for ts_obj in series:
            rows.extend(self._normalize_time_series(ts_obj))
        return rows

    def _normalize_time_series(
        self, ts_obj: dict[str, object]
    ) -> list[dict[str, object]]:
        metric_raw = ts_obj.get("metric") or {}
        metric: dict[str, object] = metric_raw if isinstance(metric_raw, dict) else {}
        resource_raw = ts_obj.get("resource") or {}
        resource: dict[str, object] = resource_raw if isinstance(resource_raw, dict) else {}
        metric_type = metric.get("type", "")

        labels: dict[str, object] = {}
        metric_labels = metric.get("labels")
        if isinstance(metric_labels, dict):
            labels.update(metric_labels)
        resource_labels = resource.get("labels")
        if isinstance(resource_labels, dict):
            labels.update(resource_labels)

        points = ts_obj.get("points")
        if not isinstance(points, list):
            return []
        rows: list[dict[str, object]] = []
        for point in points:
            interval = point.get("interval") or {}
            rows.append(
                {
                    "ts": interval.get("endTime"),
                    "source": self.name,
                    "kind": "metrics",
                    "level_or_status": None,
                    "message": metric_type,
                    "value": _point_value(point.get("value") or {}),
                    "labels": labels,
                    "raw": ts_obj,
                }
            )
        return rows


def _point_value(value: dict[str, object]) -> float | None:
    """Extract a scalar float from a Cloud Monitoring typed value object."""

    for key in ("doubleValue", "int64Value", "boolValue"):
        if key in value and value[key] is not None:
            raw = value[key]
            if isinstance(raw, bool):
                return float(str(raw))
            return float(str(raw))
    dist = value.get("distributionValue")
    if isinstance(dist, dict) and dist.get("mean") is not None:
        return float(str(dist["mean"]))
    return None
