"""Self-contained Prometheus observability connector.

Reads metrics from a Prometheus HTTP API and normalizes every sample into a
flat record. The HTTP transport is *injectable* so the connector can be driven
entirely from mocked responses in tests (no real network, no DNS, no shell).

Design constraints honoured here:

* No imports from sibling connectors, the package ``__init__``, or any
  connector base class — this module stands alone.
* SSRF protection blocks loopback / private / link-local / cloud-metadata
  *literal* hosts on ``base_url``. We deliberately do **not** resolve DNS
  (so no DNS-rebinding surface and no network at construction time), and we
  **allow** ``http`` because Prometheus is frequently exposed on plain HTTP
  inside an allowlisted internal network.
* ``health()`` never raises. ``query()`` never raises: transport / protocol
  failures are surfaced as a single normalized error record.
* No ``shell=True`` and no subprocess use anywhere.

Record shape (one dict per sample)::

    {
        "ts": float,                 # unix seconds of the sample
        "source": str,               # connector name
        "kind": "metrics",
        "level_or_status": str,      # "" for data, "error" for failures
        "message": str,              # "<__name__>{label="v", ...}"
        "value": float,              # numeric sample value
        "labels": dict[str, str],    # metric labels (incl. __name__)
        "raw": Any,                  # original series/sample/payload
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

from general_ludd.connectors.normalize import sanitize_metric_value
from general_ludd.security.ssrf import is_url_blocked

# Injectable transport signature: (url, params, headers) -> (status, json)
HttpGet = Callable[..., "tuple[int, Any]"]

KIND = "metrics"

MAX_RESULT_SIZE = 10_000

# Hostnames that must never be reached, regardless of IP resolution.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
    }
)

_DEFAULT_TIMEOUT = 10.0


def _default_http_get(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """Real, time-bound stdlib transport used when none is injected.

    Matches the connector's ``(url, params, headers) -> (status, json)``
    contract. Only ``http``/``https`` are allowed and the request is bounded by
    an explicit timeout. The mocked tests never reach this path.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported url scheme: {parsed.scheme!r}")
    if params:
        sep = "&" if parsed.query else "?"
        url = f"{url}{sep}{urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
        status = int(resp.getcode() or 0)
        body = resp.read()
    parsed_body: Any = _json.loads(body) if body else {}
    return status, parsed_body


def _validate_base_url(base_url: str) -> str:
    """Reject SSRF-prone literal hosts; return a normalized base_url.

    Allows http and https only. Performs NO DNS resolution.
    """
    if not base_url or not isinstance(base_url, str):
        raise ValueError("base_url is required")
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(
            f"base_url blocked by SSRF policy (bad scheme, missing host, or internal/metadata address): {base_url!r}"
        )
    # Normalize: drop trailing slash so endpoint joins are clean.
    return base_url.rstrip("/")


def _fmt_labels(metric: dict[str, Any]) -> str:
    """Render ``__name__{k="v", ...}`` with sorted, non-name labels."""
    name = str(metric.get("__name__", ""))
    pairs = sorted((k, v) for k, v in metric.items() if k != "__name__")
    if not pairs:
        return name
    inner = ", ".join(f'{k}="{v}"' for k, v in pairs)
    return f"{name}{{{inner}}}"


class PrometheusSource:
    """A metrics source backed by the Prometheus HTTP API."""

    KIND = KIND

    def __init__(
        self,
        config: dict[str, Any],
        http_get: HttpGet | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        base_url = config.get("base_url", "")
        self._base_url = _validate_base_url(base_url)
        self._token_env = config.get("token_env")
        self._http_get = http_get or _default_http_get
        self._timeout = float(timeout)
        self.kind = KIND
        # A stable, human-readable name derived from the validated host.
        host = urlsplit(self._base_url).netloc
        self.name = config.get("name") or f"prometheus:{host}"

    # -- helpers ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token_env:
            token = os.environ.get(self._token_env)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _error_record(self, message: str, raw: Any) -> dict[str, Any]:
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
        self, metric: dict[str, Any], ts: Any, raw_value: Any, raw: Any
    ) -> dict[str, Any]:
        value = sanitize_metric_value(raw_value)
        try:
            ts_f = float(ts)
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

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Run an instant or range PromQL query; return normalized records.

        Range mode is selected when ``start``/``end``/``step`` are present.
        Never raises: failures become a single error record.
        """
        promql = spec.get("promql")
        if not promql:
            return [self._error_record("missing 'promql' in spec", {"spec": spec})]

        is_range = any(k in spec for k in ("start", "end", "step"))
        if is_range:
            endpoint = "/api/v1/query_range"
            params: dict[str, Any] = {"query": promql}
            for key in ("start", "end", "step"):
                if key in spec:
                    params[key] = spec[key]
        else:
            endpoint = "/api/v1/query"
            params = {"query": promql}
            if "time" in spec:
                params["time"] = spec["time"]

        url = f"{self._base_url}{endpoint}"
        try:
            status, payload = self._http_get(
                url, params=params, headers=self._headers()
            )
        except Exception as exc:  # surfaced as a record, never raised
            return [self._error_record(f"transport error: {exc}", {"url": url})]

        return self._normalize(status, payload)

    def _normalize(self, status: int, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return [self._error_record(f"non-dict payload (status {status})", payload)]

        if payload.get("status") != "success":
            err = payload.get("error") or f"prometheus returned status {status}"
            return [self._error_record(str(err), payload)]

        data = payload.get("data") or {}
        result_type = data.get("resultType")
        result = data.get("result")

        records: list[dict[str, Any]] = []

        if result_type == "vector":
            for series in result or []:
                if len(records) >= MAX_RESULT_SIZE:
                    records.append(
                        self._error_record(
                            f"truncated: result exceeded {MAX_RESULT_SIZE} records",
                            {"truncated": True},
                        )
                    )
                    break
                metric = series.get("metric", {})
                ts, raw_value = series.get("value", [0.0, "0"])
                records.append(self._sample_record(metric, ts, raw_value, series))

        elif result_type == "matrix":
            outer_break = False
            for series in result or []:
                if outer_break:
                    break
                metric = series.get("metric", {})
                for ts, raw_value in series.get("values", []):
                    if len(records) >= MAX_RESULT_SIZE:
                        records.append(
                            self._error_record(
                                f"truncated: result exceeded {MAX_RESULT_SIZE} records",
                                {"truncated": True},
                            )
                        )
                        outer_break = True
                        break
                    records.append(self._sample_record(metric, ts, raw_value, series))

        elif result_type == "scalar":
            # result is [ts, "value"]
            ts, raw_value = (result or [0.0, "0"])[:2]
            records.append(self._sample_record({}, ts, raw_value, {"scalar": result}))

        else:
            return [
                self._error_record(
                    f"unsupported resultType: {result_type!r}", payload
                )
            ]

        return records

    def health(self) -> dict[str, Any]:
        """Return a health dict. Never raises.

        Uses the cheap ``query=1`` instant query as a liveness probe (works on
        every Prometheus regardless of whether ``/-/healthy`` is exposed).
        """
        url = f"{self._base_url}/api/v1/query"
        try:
            status, payload = self._http_get(
                url, params={"query": "1"}, headers=self._headers()
            )
        except Exception as exc:  # health must never raise
            return {"ok": False, "source": self.name, "error": str(exc)}

        ok = (
            isinstance(payload, dict)
            and payload.get("status") == "success"
            and 200 <= int(status) < 300
        )
        result: dict[str, Any] = {"ok": ok, "source": self.name, "status": status}
        if not ok:
            if isinstance(payload, dict):
                result["error"] = str(
                    payload.get("error") or f"unhealthy (status {status})"
                )
            else:
                result["error"] = f"unhealthy (status {status})"
        return result
