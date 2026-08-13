"""VictoriaMetrics connector.

Self-contained read-only source that queries the VictoriaMetrics
PromQL-compatible HTTP API (``/api/v1/query`` and ``/api/v1/query_range``) and
normalizes the Prometheus-style result into the project's flat record shape.
No base/sibling imports.

Contract
--------
* ``KIND == "metrics"``.
* ``name`` attribute identifies the source instance.
* ``__init__(config)`` is config-driven; an optional bearer token is read from
  the environment via ``token_env`` (never inline).
* ``base_url`` is checked with a *literal-host* SSRF guard (private/loopback/
  link-local/reserved IP literals rejected; no DNS).
* ``health()`` returns ``{"ok": bool, "detail": str}`` and never raises.
* ``query(spec)`` returns normalized ``dict`` records with the keys
  ``ts, source, kind, level_or_status, message, value, labels, raw``. The
  metric ``__name__`` label becomes the record ``message``.
* The HTTP transport is injectable; the default uses ``httpx``. Time-bound,
  no ``shell=True``.

"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import cast
from urllib.parse import urlsplit

from general_ludd.connectors._errors import SSRFError
from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

KIND = "metrics"


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
    """Fail-closed SSRF guard on a base URL's literal host."""
    if not isinstance(base_url, str) or not base_url:
        raise SSRFError("base_url must be a non-empty string")
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        parts = urlsplit(base_url)
        host = parts.hostname or ""
        if parts.scheme not in ("http", "https"):
            raise SSRFError(f"base_url scheme must be http/https, got {parts.scheme!r}")
        if not host:
            raise SSRFError(f"base_url has no host: {base_url!r}")
        raise SSRFError(f"base_url host {host!r} is a blocked internal address")
    return base_url.rstrip("/")


def _default_transport(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str | int | float | bool | None] | None = None,
    json: dict[str, object] | None = None,
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
        follow_redirects=False,
    )
    return resp


def _as_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(cast("float | int | str", value))
    except (TypeError, ValueError):
        # Prometheus encodes +Inf/-Inf/NaN as strings; treat as non-numeric.
        return None


class VictoriaMetricsSource:
    """Query the VictoriaMetrics PromQL API and normalize the result."""

    KIND = KIND

    def __init__(
        self,
        config: dict[str, object],
        *,
        transport: TransportInput | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        env = environ if environ is not None else os.environ
        self.name: str = str(config.get("name", "victoriametrics"))
        self.base_url: str = _validate_base_url(str(config.get("base_url", "")))
        self.timeout: float = float(cast("float | int | str", config.get("timeout", 30.0)))
        self._transport: Transport = _CompatibleTransport(
            transport or _default_transport
        )

        # Optional bearer token resolved from the environment via *_env only.
        self._token: str | None = None
        token_env = config.get("token_env")
        if token_env:
            self._token = env.get(str(token_env))

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def health(self) -> dict[str, object]:
        """Probe the VictoriaMetrics /health endpoint; never raises."""
        try:
            resp = _coerce_response(self._transport(
                "GET",
                f"{self.base_url}/health",
                headers=self._headers(),
                timeout=self.timeout,
            ))
        except Exception as exc:  # health() must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        ok = 200 <= resp.status_code < 300
        return {"ok": ok, "detail": f"HTTP {resp.status_code}"}

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Run an instant or range PromQL query and normalize results."""
        promql = str(spec.get("query", ""))
        is_range = bool(spec.get("start") or spec.get("end") or spec.get("step"))
        params: dict[str, object] = {"query": promql}
        if is_range:
            path = "/api/v1/query_range"
            for key in ("start", "end", "step"):
                if spec.get(key) is not None:
                    params[key] = spec[key]
        else:
            path = "/api/v1/query"
            if spec.get("time") is not None:
                params["time"] = spec["time"]
        resp = _coerce_response(self._transport(
            "GET",
            f"{self.base_url}{path}",
            headers=self._headers(),
            params=params,
            timeout=self.timeout,
        ))
        payload = resp.json() or {}
        data = payload.get("data") or {}
        result = data.get("result") or []
        result_type = data.get("resultType")
        records: list[dict[str, object]] = []
        for series in result:
            records.extend(self._normalize_series(series, result_type))
        return records

    def _normalize_series(
        self, series: dict[str, object], result_type: object
    ) -> list[dict[str, object]]:
        metric = cast(dict[str, object], series.get("metric") or {})
        name = metric.pop("__name__", "") if "__name__" in metric else ""
        labels = metric  # remaining labels after extracting the metric name

        samples: list[list[object]] = []
        if series.get("value") is not None:
            samples.append(cast(list[object], series["value"]))  # instant vector: [ts, "val"]
        if series.get("values"):
            samples.extend(cast(list[list[object]], series["values"]))  # matrix: [[ts, "val"], ...]

        records: list[dict[str, object]] = []
        for sample in samples:
            ts = sample[0] if len(sample) > 0 else None
            raw_val = sample[1] if len(sample) > 1 else None
            records.append(
                {
                    "ts": _as_float(ts),
                    "source": self.name,
                    "kind": self.KIND,
                    "level_or_status": "ok",
                    "message": str(name),
                    "value": _as_float(raw_val),
                    "labels": labels,
                    "raw": series,
                }
            )
        return records
