"""Self-contained Datadog observability connector.

Reads **logs** (Logs Search API v2) and **metrics** (Query Timeseries API v1)
from Datadog and normalizes every event / sample into a flat record. The HTTP
transport is *injectable* so the connector can be driven entirely from mocked
responses in tests — no real network, no DNS resolution, no shell.

Design constraints honoured here:

* No imports from sibling connectors, the package ``__init__``, or any
  connector base class — this module stands alone.
* SSRF protection blocks loopback / private / link-local / cloud-metadata
  *literal* hosts on ``site``. We deliberately do **not** resolve DNS (so no
  DNS-rebinding surface and no network at construction time). Only ``http`` /
  ``https`` schemes are accepted.
* Secrets are **never** hardcoded. ``api_key_env`` / ``app_key_env`` name the
  environment variables holding the Datadog API key and Application key; their
  values are read at call time into the ``DD-API-KEY`` / ``DD-APPLICATION-KEY``
  headers and are never stored on the instance.
* ``health()`` never raises. ``query()`` never raises: transport / protocol
  failures surface as a single normalized error record.
* Every request is time-bound (``timeout``). No ``shell=True`` / subprocess.

Record shape (one dict per event or metric point)::

    {
        "ts": Any,                   # event/sample timestamp (ms epoch, as-is)
        "source": str,               # connector name
        "kind": "logs" | "metrics",
        "level_or_status": str,      # log status, or "" for metric data
        "message": str,              # log message, or metric name
        "value": float | None,       # metric value (None for logs)
        "labels": dict[str, object],    # service/host/tags or tag_set+metric+scope
        "raw": Any,                  # original event / series payload
    }
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import cast
from urllib.parse import urlsplit

import httpx

from general_ludd.connectors.normalize import sanitize_metric_value
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

# Injectable transport signature:
#   (method, url, *, params, json, headers, timeout) -> (status, payload)
HttpRequest = Callable[..., "tuple[int, object]"]

KIND = "logs"

_DEFAULT_SITE = "https://api.datadoghq.com"
_DEFAULT_TIMEOUT = 10.0


def _default_http_request(
    method: str,
    url: str,
    *,
    params: dict[str, object] | None = None,
    json: object = None,
    headers: dict[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[int, object]:
    """Real, time-bound httpx transport used when none is injected.

    Matches the connector's
    ``(method, url, *, params, json, headers, timeout) -> (status, json)``
    contract. The request is bounded by an explicit timeout and redirects are
    disabled to prevent SSRF via redirect-to-metadata. The mocked tests never
    reach this path.
    """
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        resp = client.request(
            method.upper(),
            url,
            params=cast("dict[str, str | int | float | bool | None]", params),
            json=json,
            headers=headers or {},
        )
    try:
        parsed_body: object = resp.json() if resp.content else {}
    except ValueError:
        parsed_body = {}
    return resp.status_code, parsed_body


def _validate_site(site: str) -> str:
    """Reject SSRF-prone literal hosts; return a normalized site URL.

    Allows http and https only. Performs NO DNS resolution.
    """
    if not site or not isinstance(site, str):
        raise ValueError("site is required")
    if is_url_blocked(site, scheme_allowlist=("http", "https")):
        raise ValueError(
            f"site blocked by SSRF policy (bad scheme, missing host, or internal/metadata address): {site!r}"
        )
    return site.rstrip("/")


class DatadogSource:
    """An observability source backed by the Datadog HTTP APIs.

    ``spec['mode']`` selects ``"logs"`` (Logs Search v2, POST) or ``"metrics"``
    (Query Timeseries v1, GET).
    """

    KIND = KIND

    def __init__(
        self,
        config: dict[str, object],
        http_request: HttpRequest | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._site = _validate_site(str(config.get("site") or _DEFAULT_SITE))
        self._api_key_env = config.get("api_key_env")
        self._app_key_env = config.get("app_key_env")
        self._http_request = http_request or _default_http_request
        self._timeout = float(timeout)
        self.kind = KIND
        host = urlsplit(self._site).netloc
        self.name = config.get("name") or f"datadog:{host}"

    # -- helpers ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Build auth headers from env. Missing env vars are simply omitted."""
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._api_key_env:
            api_key = os.environ.get(str(self._api_key_env))
            if api_key:
                headers["DD-API-KEY"] = api_key
        if self._app_key_env:
            app_key = os.environ.get(str(self._app_key_env))
            if app_key:
                headers["DD-APPLICATION-KEY"] = app_key
        return headers

    def _error_record(self, message: str, raw: object) -> dict[str, object]:
        return {
            "ts": time.time(),
            "source": self.name,
            "kind": self.kind,
            "level_or_status": "error",
            "message": message,
            "value": None,
            "labels": {},
            "raw": raw,
        }

    @staticmethod
    def _is_2xx(status: int) -> bool:
        try:
            return 200 <= status < 300
        except (TypeError, ValueError):
            return False

    # -- public API -------------------------------------------------------

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Run a logs or metrics query; return normalized records.

        Never raises: transport / protocol failures become a single error
        record. An unknown ``mode`` is reported without touching the network.
        """
        mode = spec.get("mode")
        if mode == "logs":
            return self._query_logs(spec)
        if mode == "metrics":
            return self._query_metrics(spec)
        return [
            self._error_record(
                f"unsupported mode: {mode!r} (expected 'logs' or 'metrics')",
                {"spec": spec},
            )
        ]

    # -- logs -------------------------------------------------------------

    def _query_logs(self, spec: dict[str, object]) -> list[dict[str, object]]:
        url = f"{self._site}/api/v2/logs/events/search"
        body: dict[str, object] = {
            "filter": {
                "query": spec.get("query", "*"),
                "from": spec.get("from", "now-15m"),
                "to": spec.get("to", "now"),
            },
            "page": {"limit": spec.get("limit", 100)},
        }
        try:
            status, payload = self._http_request(
                "POST",
                url,
                params=None,
                json=body,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except Exception:  # surfaced as a record, never raised
            logger.warning("datadog transport error", exc_info=True)
            return [self._error_record("transport error", {"url": url})]

        if not self._is_2xx(status):
            return [
                self._error_record(
                    f"datadog logs returned status {status}", payload
                )
            ]
        if not isinstance(payload, dict):
            return [
                self._error_record(f"non-dict payload (status {status})", payload)
            ]

        records: list[dict[str, object]] = []
        for event in payload.get("data") or []:
            if not isinstance(event, dict):
                continue
            attrs = event.get("attributes") or {}
            records.append(
                {
                    "ts": attrs.get("timestamp"),
                    "source": self.name,
                    "kind": "logs",
                    "level_or_status": attrs.get("status", ""),
                    "message": attrs.get("message", ""),
                    "value": None,
                    "labels": {
                        "service": attrs.get("service"),
                        "host": attrs.get("host"),
                        "tags": attrs.get("tags", []),
                    },
                    "raw": event,
                }
            )
        return records

    # -- metrics ----------------------------------------------------------

    def _query_metrics(self, spec: dict[str, object]) -> list[dict[str, object]]:
        url = f"{self._site}/api/v1/query"
        params: dict[str, object] = {"query": spec.get("query", "")}
        if "from" in spec:
            params["from"] = spec["from"]
        if "to" in spec:
            params["to"] = spec["to"]
        try:
            status, payload = self._http_request(
                "GET",
                url,
                params=params,
                json=None,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except Exception:  # surfaced as a record, never raised
            logger.warning("datadog transport error", exc_info=True)
            return [self._error_record("transport error", {"url": url})]

        if not self._is_2xx(status):
            return [
                self._error_record(
                    f"datadog metrics returned status {status}", payload
                )
            ]
        if not isinstance(payload, dict):
            return [
                self._error_record(f"non-dict payload (status {status})", payload)
            ]

        records: list[dict[str, object]] = []
        for series in payload.get("series") or []:
            if not isinstance(series, dict):
                continue
            metric = series.get("metric", "")
            scope = series.get("scope", "")
            tag_set = list(series.get("tag_set") or [])
            labels: dict[str, object] = {
                "tags": tag_set,
                "metric": metric,
                "scope": scope,
            }
            for point in series.get("pointlist") or []:
                ts, raw_value = self._split_point(point)
                records.append(
                    {
                        "ts": ts,
                        "source": self.name,
                        "kind": "metrics",
                        "level_or_status": "",
                        "message": metric,
                        "value": self._to_float(raw_value),
                        "labels": dict(labels),
                        "raw": series,
                    }
                )
        return records

    @staticmethod
    def _split_point(point: object) -> tuple[object, object]:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            return point[0], point[1]
        return None, None

    @staticmethod
    def _to_float(value: object) -> float | None:
        return sanitize_metric_value(value)

    # -- health -----------------------------------------------------------

    def health(self) -> dict[str, object]:
        """Probe the Datadog key-validation endpoint. Never raises.

        Returns ``{'ok': bool, 'detail': str}``.
        """
        url = f"{self._site}/api/v1/validate"
        try:
            status, payload = self._http_request(
                "GET",
                url,
                params=None,
                json=None,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"transport error: {exc}"}

        valid = bool(payload.get("valid")) if isinstance(payload, dict) else False
        ok = self._is_2xx(status) and valid
        detail = (
            f"validated (status {status})"
            if ok
            else f"unhealthy (status {status}, valid={valid})"
        )
        return {"ok": ok, "detail": detail}
