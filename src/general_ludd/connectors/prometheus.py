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
        "raw": object,               # original series/sample/payload
    }
"""

from __future__ import annotations

import json as _json
import logging
import os
import time
import urllib.request
from collections.abc import Callable, Mapping
from typing import TypedDict, cast
from urllib.parse import urlencode, urlsplit

from general_ludd.connectors.normalize import sanitize_metric_value
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

# Injectable transport signature: (url, params, headers) -> (status, json-body).
# The json body is ``object`` because the wire payload is only narrowable at
# runtime via ``isinstance``; callers treat it as ``Mapping[str, object]`` after
# a structural check.
HttpGet = Callable[..., "tuple[int, object]"]

KIND = "metrics"

MAX_RESULT_SIZE = 10_000

_DEFAULT_TIMEOUT = 10.0


# --------------------------------------------------------------------------- #
# Typed API-response shapes (Prometheus query API JSON).
# --------------------------------------------------------------------------- #
class PromMetric(TypedDict, total=False):
    """The ``metric`` block on a Prometheus vector/matrix series.

    Label values are strings in Prometheus; ``__name__`` carries the metric
    name and is rendered specially by :func:`_fmt_labels`.
    """

    __name__: str


class PromVectorSeries(TypedDict, total=False):
    """One series in a vector result: ``{"metric": {...}, "value": [ts, "val"]}``."""

    metric: dict[str, str]
    value: list[object]


class PromMatrixSeries(TypedDict, total=False):
    """One series in a matrix result: ``{"metric": {...}, "values": [[ts, "val"], ...]}``."""

    metric: dict[str, str]
    values: list[list[object]]


class PromData(TypedDict, total=False):
    """The ``data`` block of a Prometheus query response."""

    resultType: str
    result: list[object]


class PromResponse(TypedDict, total=False):
    """Top-level Prometheus query API response envelope."""

    status: str
    data: PromData
    error: str
    errorType: str
    warnings: list[str]


class PromQuerySpec(TypedDict, total=False):
    """Caller-supplied query spec accepted by :meth:`PrometheusSource.query`.

    Range mode is selected when ``start``/``end``/``step`` are all present;
    otherwise an instant query is issued. ``start``/``end``/``step``/``time``
    are ``object`` because Prometheus accepts epoch floats, RFC3339 strings,
    or duration strings depending on the field.
    """

    promql: str
    start: object
    end: object
    step: object
    time: object


def _default_http_get(
    url: str,
    params: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, object]:
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
    parsed_body: object = _json.loads(body) if body else {}
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


def _fmt_labels(metric: Mapping[str, str]) -> str:
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
        config: Mapping[str, object],
        http_get: HttpGet | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        base_url = config.get("base_url", "")
        self._base_url = _validate_base_url(str(base_url))
        token_env_obj = config.get("token_env")
        self._token_env: str | None = str(token_env_obj) if token_env_obj is not None else None
        self._http_get = http_get or _default_http_get
        self._timeout = float(timeout)
        self.kind = KIND
        # A stable, human-readable name derived from the validated host.
        host = urlsplit(self._base_url).netloc
        name_obj = config.get("name")
        self.name = str(name_obj) if name_obj else f"prometheus:{host}"

    # -- helpers ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token_env:
            token = os.environ.get(self._token_env)
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
        self,
        metric: Mapping[str, str],
        ts: object,
        raw_value: object,
        raw: object,
    ) -> dict[str, object]:
        value = sanitize_metric_value(raw_value)
        try:
            ts_f = float(cast(int | float | str | bool, ts))
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

    def query(self, spec: PromQuerySpec) -> list[dict[str, object]]:
        """Run an instant or range PromQL query; return normalized records.

        Range mode is selected when ``start``/``end``/``step`` are present.
        Never raises: failures become a single error record.
        """
        promql = spec.get("promql")
        if not promql:
            return [self._error_record("missing 'promql' in spec", {"spec": dict(spec)})]

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

        url = f"{self._base_url}{endpoint}"
        try:
            status, payload = self._http_get(
                url, params=params, headers=self._headers()
            )
        except Exception:  # surfaced as a record, never raised
            # Never echo str(exc) into the record: it can embed the target URL
            # or auth detail. Log the real error; keep a stable generic marker.
            logger.warning("prometheus transport error", exc_info=True)
            return [self._error_record("transport error", {"url": url})]

        return self._normalize(status, payload)

    def _normalize(self, status: int, payload: object) -> list[dict[str, object]]:
        if not isinstance(payload, Mapping):
            return [self._error_record(f"non-dict payload (status {status})", payload)]

        if payload.get("status") != "success":
            err = payload.get("error") or f"prometheus returned status {status}"
            return [self._error_record(str(err), payload)]

        data_obj = payload.get("data") or {}
        data: Mapping[str, object] = data_obj if isinstance(data_obj, Mapping) else {}
        result_type_obj = data.get("resultType")
        result_type = result_type_obj if isinstance(result_type_obj, str) else None
        result_obj = data.get("result")
        result: list[object] = result_obj if isinstance(result_obj, list) else []

        records: list[dict[str, object]] = []

        if result_type == "vector":
            for series in result:
                if len(records) >= MAX_RESULT_SIZE:
                    records.append(
                        self._error_record(
                            f"truncated: result exceeded {MAX_RESULT_SIZE} records",
                            {"truncated": True},
                        )
                    )
                    break
                if not isinstance(series, Mapping):
                    continue
                metric_obj = series.get("metric", {})
                metric: Mapping[str, str] = metric_obj if isinstance(metric_obj, Mapping) else {}
                value_obj = series.get("value", [0.0, "0"])
                value_pair = value_obj if isinstance(value_obj, list) else [0.0, "0"]
                ts = value_pair[0] if len(value_pair) > 0 else 0.0
                raw_value = value_pair[1] if len(value_pair) > 1 else "0"
                records.append(self._sample_record(metric, ts, raw_value, series))

        elif result_type == "matrix":
            outer_break = False
            for series in result:
                if outer_break:
                    break
                if not isinstance(series, Mapping):
                    continue
                metric_obj = series.get("metric", {})
                metric = metric_obj if isinstance(metric_obj, Mapping) else {}
                values_obj = series.get("values", [])
                values = values_obj if isinstance(values_obj, list) else []
                for pair in values:
                    if not isinstance(pair, list):
                        continue
                    if len(records) >= MAX_RESULT_SIZE:
                        records.append(
                            self._error_record(
                                f"truncated: result exceeded {MAX_RESULT_SIZE} records",
                                {"truncated": True},
                            )
                        )
                        outer_break = True
                        break
                    ts = pair[0] if len(pair) > 0 else 0.0
                    raw_value = pair[1] if len(pair) > 1 else "0"
                    records.append(self._sample_record(metric, ts, raw_value, series))

        elif result_type == "scalar":
            # result is [ts, "value"]
            pair = result if isinstance(result, list) and result else [0.0, "0"]
            ts = pair[0] if len(pair) > 0 else 0.0
            raw_value = pair[1] if len(pair) > 1 else "0"
            records.append(self._sample_record({}, ts, raw_value, {"scalar": result}))

        else:
            return [
                self._error_record(
                    f"unsupported resultType: {result_type!r}", payload
                )
            ]

        return records

    def health(self) -> dict[str, object]:
        """Return a health dict. Never raises.

        Uses the cheap ``query=1`` instant query as a liveness probe (works on
        every Prometheus regardless of whether ``/-/healthy`` is exposed).
        """
        url = f"{self._base_url}/api/v1/query"
        try:
            status, payload = self._http_get(
                url, params={"query": "1"}, headers=self._headers()
            )
        except Exception:  # health must never raise
            logger.warning("prometheus health check failed", exc_info=True)
            return {"ok": False, "source": self.name, "error": "health check failed"}

        ok = (
            isinstance(payload, Mapping)
            and payload.get("status") == "success"
            and 200 <= int(status) < 300
        )
        result: dict[str, object] = {"ok": ok, "source": self.name, "status": status}
        if not ok:
            if isinstance(payload, Mapping):
                result["error"] = str(
                    payload.get("error") or f"unhealthy (status {status})"
                )
            else:
                result["error"] = f"unhealthy (status {status})"
        return result
