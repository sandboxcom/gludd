"""Dynatrace APM connector (self-contained, no sibling imports).

A config-driven, fail-safe source that pulls metrics and incident ("problem")
records from a Dynatrace environment over its v2 REST API and normalizes them
into a uniform record shape.

Design contract (shared by all gludd APM connectors):

* ``KIND`` is one of ``{"metrics", "traces"}``; this source emits ``metrics``.
* ``name`` identifies the source instance.
* ``__init__(config)`` is fully config-driven. Secrets are read from the
  environment via a ``*_env`` key that names the variable — tokens are NEVER
  hardcoded and NEVER taken inline from the config dict.
* The ``base_url`` is validated with a *literal-host* SSRF guard: the host is
  parsed from the URL only (no DNS resolution) and rejected if it is loopback,
  RFC-1918 / unique-local private space, link-local, or a known cloud
  metadata endpoint.
* ``health()`` returns ``{"ok": bool, "detail": str}`` and NEVER raises.
* ``query(spec)`` returns ``list[dict]`` of normalized records with the keys
  ``ts, source, kind, level_or_status, message, value, labels, raw``.
* The HTTP transport is injectable (for tests / alternate clients) and every
  call is time-bounded. ``shell=True`` is never used.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.connectors._errors import ConnectorConfigError
from general_ludd.security.ssrf import is_url_blocked

KIND_METRICS = "metrics"
KIND_TRACES = "traces"
VALID_KINDS = frozenset({KIND_METRICS, KIND_TRACES})

DEFAULT_TIMEOUT = 30.0



@runtime_checkable
class HttpTransport(Protocol):
    """Minimal injectable HTTP transport.

    An implementation performs a GET and returns ``(status_code, json_body)``.
    The default transport wraps :mod:`urllib`; tests inject a fake.
    """

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, object]]:
        ...


def _validate_base_url(base_url: str) -> str:
    """Validate + normalize a base URL, applying the literal-host SSRF guard."""
    if not base_url or not isinstance(base_url, str):
        raise ConnectorConfigError("base_url must be a non-empty string")
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        host = urlsplit(base_url).hostname or ""
        raise ConnectorConfigError(
            f"base_url host {host!r} is blocked (loopback/private/link-local/metadata)"
        )
    return base_url.rstrip("/")


def _resolve_secret(config: dict[str, object], env_key_name: str) -> str:
    """Read a secret strictly from the environment named by ``config[env_key_name]``."""
    env_var = config.get(env_key_name)
    if not env_var or not isinstance(env_var, str):
        raise ConnectorConfigError(f"config[{env_key_name!r}] must name an environment variable")
    value = os.environ.get(env_var)
    if not value:
        raise ConnectorConfigError(f"environment variable {env_var!r} is unset or empty")
    return value


class _UrllibTransport:
    """Default transport backed by urllib (stdlib only, time-bounded)."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, object]]:
        import json
        import urllib.error
        import urllib.request

        # base_url scheme is vetted to http/https upstream in _validate_base_url.
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                status = int(resp.status)
        except urllib.error.HTTPError as exc:  # pragma: no cover - exercised via injected transport
            body = exc.read()
            try:
                return int(exc.code), json.loads(body or b"{}")
            except ValueError:
                return int(exc.code), {}
        if not raw:
            return status, {}
        parsed: object = json.loads(raw)
        return status, parsed if isinstance(parsed, dict) else {"data": parsed}


class DynatraceSource:
    """Dynatrace metrics + problems source."""

    KIND = KIND_METRICS

    def __init__(self, config: dict[str, object], transport: HttpTransport | None = None) -> None:
        self.name: str = str(config.get("name", "dynatrace"))
        self.base_url: str = _validate_base_url(str(config.get("base_url", "")))
        self._token: str = _resolve_secret(config, "token_env")
        self.timeout: float = float(str(config.get("timeout", DEFAULT_TIMEOUT)))
        self._transport: HttpTransport = transport or _UrllibTransport()

    # -- internals ---------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Api-Token {self._token}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
        from urllib.parse import urlencode

        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return self._transport.get(url, headers=self._auth_headers(), timeout=self.timeout)

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, object]:
        """Probe reachability + auth; never raises."""
        try:
            status, _ = self._get("/api/v2/metrics", {"pageSize": "1"})
        except Exception as exc:  # health must never propagate
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Run a metrics query (and optional problems pull); return normalized records."""
        records: list[dict[str, object]] = []
        records.extend(self._query_metrics(spec))
        if spec.get("include_problems"):
            records.extend(self._query_problems(spec))
        return records

    # -- metrics -----------------------------------------------------------

    def _query_metrics(self, spec: dict[str, object]) -> list[dict[str, object]]:
        selector = str(spec.get("metric_selector", spec.get("metricSelector", "")))
        if not selector:
            return []
        params = {"metricSelector": selector}
        if spec.get("from") is not None:
            params["from"] = str(spec["from"])
        if spec.get("to") is not None:
            params["to"] = str(spec["to"])
        status, body = self._get("/api/v2/metrics/query", params)
        if not (200 <= status < 300):
            return []
        return self._normalize_metrics(body)

    def _normalize_metrics(self, body: dict[str, object]) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        raw_results = body.get("result", [])
        if not isinstance(raw_results, list):
            return []
        for series_result in raw_results:
            if not isinstance(series_result, dict):
                continue
            metric_id = series_result.get("metricId", "")
            for data_point in series_result.get("data", []):
                if not isinstance(data_point, dict):
                    continue
                dimensions = data_point.get("dimensions") or []
                dim_map = data_point.get("dimensionMap") or {}
                labels: dict[str, object] = {"dimensions": dimensions}
                if dim_map:
                    labels.update(dim_map)
                timestamps = data_point.get("timestamps") or []
                values = data_point.get("values") or []
                for ts_ms, value in zip(timestamps, values, strict=False):
                    out.append(
                        {
                            "ts": _ms_to_epoch(ts_ms),
                            "source": self.name,
                            "kind": KIND_METRICS,
                            "level_or_status": "ok",
                            "message": metric_id,
                            "value": value,
                            "labels": dict(labels),
                            "raw": data_point,
                        }
                    )
        return out

    # -- problems / incidents ----------------------------------------------

    def _query_problems(self, spec: dict[str, object]) -> list[dict[str, object]]:
        params: dict[str, str] = {}
        if spec.get("from") is not None:
            params["from"] = str(spec["from"])
        if spec.get("to") is not None:
            params["to"] = str(spec["to"])
        status, body = self._get("/api/v2/problems", params or None)
        if not (200 <= status < 300):
            return []
        out: list[dict[str, object]] = []
        raw_problems = body.get("problems", [])
        if not isinstance(raw_problems, list):
            return []
        for problem in raw_problems:
            if not isinstance(problem, dict):
                continue
            out.append(
                {
                    "ts": _ms_to_epoch(problem.get("startTime")),
                    "source": self.name,
                    "kind": KIND_METRICS,
                    "level_or_status": str(problem.get("status", "")).lower() or "unknown",
                    "message": str(problem.get("title", problem.get("displayId", ""))),
                    "value": None,
                    "labels": {
                        "severityLevel": problem.get("severityLevel"),
                        "impactLevel": problem.get("impactLevel"),
                        "problemId": problem.get("problemId"),
                    },
                    "raw": problem,
                }
            )
        return out


def _ms_to_epoch(ts_ms: object) -> float | None:
    """Convert epoch milliseconds to epoch seconds; tolerate None/garbage."""
    if ts_ms is None:
        return None
    try:
        return float(str(ts_ms)) / 1000.0
    except (TypeError, ValueError):
        return None
