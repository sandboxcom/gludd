"""AppDynamics APM connector (self-contained, no sibling imports).

A config-driven, fail-safe source that pulls metric-data from an AppDynamics
controller via its REST API and normalizes each metric value into a uniform
record shape.

Design contract (shared by all gludd APM connectors):

* ``KIND`` is one of ``{"metrics", "traces"}``; this source emits ``metrics``.
* ``name`` identifies the source instance.
* ``__init__(config)`` is fully config-driven. The bearer/OAuth token is read
  from the environment via a ``*_env`` key that names the variable — tokens are
  NEVER hardcoded and NEVER taken inline from the config dict.
* The ``base_url`` is validated with a *literal-host* SSRF guard: the host is
  parsed from the URL only (no DNS resolution) and rejected if it is loopback,
  RFC-1918 / unique-local private space, link-local, or a cloud metadata
  endpoint.
* ``health()`` returns ``{"ok": bool, "detail": str}`` and NEVER raises.
* ``query(spec)`` returns ``list[dict]`` of normalized records with the keys
  ``ts, source, kind, level_or_status, message, value, labels, raw``.
* The HTTP transport is injectable and every call is time-bounded.
  ``shell=True`` is never used.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote, urlencode, urlparse, urlsplit

from general_ludd.connectors._errors import ConnectorConfigError
from general_ludd.security.ssrf import is_url_blocked

KIND_METRICS = "metrics"
KIND_TRACES = "traces"
VALID_KINDS = frozenset({KIND_METRICS, KIND_TRACES})

DEFAULT_TIMEOUT = 30.0



@runtime_checkable
class HttpTransport(Protocol):
    """Minimal injectable HTTP transport returning ``(status_code, json_body)``."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        ...


def _validate_base_url(base_url: str) -> str:
    """Validate + normalize a base URL, applying the literal-host SSRF guard.

    The private/metadata decision is delegated to the canonical shared guard
    :func:`general_ludd.security.ssrf.is_url_blocked` so this connector cannot
    drift weaker than the single source of truth (no DNS resolution).
    """
    if not base_url or not isinstance(base_url, str):
        raise ConnectorConfigError("base_url must be a non-empty string")
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise ConnectorConfigError(f"base_url scheme must be http/https, got {parsed.scheme!r}")
    host = urlsplit(base_url).hostname or ""
    if not host:
        raise ConnectorConfigError(
            f"base_url host {host!r} is blocked (loopback/private/link-local/metadata)"
        )
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ConnectorConfigError(
            f"base_url host {host!r} is blocked (loopback/private/link-local/metadata)"
        )
    return base_url.rstrip("/")


def _resolve_secret(config: dict[str, Any], env_key_name: str) -> str:
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
    ) -> tuple[int, dict[str, Any]]:
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
        parsed: Any = json.loads(raw)
        return status, parsed if isinstance(parsed, dict) else {"data": parsed}


class AppDynamicsSource:
    """AppDynamics controller metric-data source."""

    KIND = KIND_METRICS

    def __init__(self, config: dict[str, Any], transport: HttpTransport | None = None) -> None:
        self.name: str = str(config.get("name", "appdynamics"))
        self.base_url: str = _validate_base_url(str(config.get("base_url", "")))
        self.application: str = str(config.get("application", config.get("app", "")))
        if not self.application:
            raise ConnectorConfigError("config['application'] is required")
        self._token: str = _resolve_secret(config, "token_env")
        self.timeout: float = float(config.get("timeout", DEFAULT_TIMEOUT))
        self._transport: HttpTransport = transport or _UrllibTransport()

    # -- internals ---------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return self._transport.get(url, headers=self._auth_headers(), timeout=self.timeout)

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe reachability + auth; never raises."""
        try:
            app = quote(self.application, safe="")
            status, _ = self._get(
                f"/controller/rest/applications/{app}/business-transactions",
                {"output": "JSON"},
            )
        except Exception as exc:  # health must never propagate
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Run a metric-data query; return normalized records."""
        metric_path = str(spec.get("metric_path", spec.get("metricPath", "")))
        if not metric_path:
            return []
        params: dict[str, str] = {
            "metric-path": metric_path,
            "output": "JSON",
            "time-range-type": str(spec.get("time_range_type", spec.get("timeRangeType", "BEFORE_NOW"))),
        }
        if spec.get("duration_in_mins") is not None:
            params["duration-in-mins"] = str(spec["duration_in_mins"])
        if spec.get("rollup") is not None:
            params["rollup"] = "true" if spec["rollup"] else "false"
        app = quote(self.application, safe="")
        status, body = self._get(
            f"/controller/rest/applications/{app}/metric-data",
            params,
        )
        if not (200 <= status < 300):
            return []
        return self._normalize(body)

    # -- normalization -----------------------------------------------------

    def _normalize(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        # The controller returns a JSON array; the urllib transport wraps a bare
        # list under {"data": [...]}, while an injected transport may pass the
        # list straight through under a conventional key.
        series_list = self._extract_series(body)
        out: list[dict[str, Any]] = []
        for series in series_list:
            if not isinstance(series, dict):
                continue
            metric_name = series.get("metricName", "")
            metric_path = series.get("metricPath", "")
            metric_id = series.get("metricId")
            for mv in series.get("metricValues", []):
                if not isinstance(mv, dict):
                    continue
                out.append(
                    {
                        "ts": _ms_to_epoch(mv.get("startTimeInMillis")),
                        "source": self.name,
                        "kind": KIND_METRICS,
                        "level_or_status": "ok",
                        "message": str(metric_name),
                        "value": mv.get("value"),
                        "labels": {
                            "metricName": metric_name,
                            "metricPath": metric_path,
                            "metricId": metric_id,
                        },
                        "raw": mv,
                    }
                )
        return out

    @staticmethod
    def _extract_series(body: dict[str, Any]) -> list[Any]:
        for key in ("data", "metrics", "result"):
            value = body.get(key)
            if isinstance(value, list):
                return value
        # A single metric object passed directly.
        if "metricValues" in body:
            return [body]
        return []


def _ms_to_epoch(ts_ms: Any) -> float | None:
    """Convert epoch milliseconds to epoch seconds; tolerate None/garbage."""
    if ts_ms is None:
        return None
    try:
        return float(ts_ms) / 1000.0
    except (TypeError, ValueError):
        return None
