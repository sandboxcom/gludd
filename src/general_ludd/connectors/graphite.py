"""Graphite metrics connector (KIND='metrics').

GET {base_url}/render?target=&from=&format=json. Each Graphite series returns a
list of [value, epoch] datapoints; every datapoint becomes a normalized record.

Self-contained namespace-package module: no sibling/base/__init__ imports and no
module-level HTTP client; the transport is injected for mocked tests.

Normalized record keys: ts, source, kind, level_or_status, message, value,
labels, raw.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.connectors.normalize import sanitize_metric_value
from general_ludd.security.ssrf import is_url_blocked


@runtime_checkable
class HttpResponse(Protocol):
    status_code: int

    def json(self) -> Any: ...


@runtime_checkable
class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        params: dict[str, Any] | None = ...,
        timeout: float | None = ...,
    ) -> HttpResponse: ...


class ConnectorConfigError(ValueError):
    """Invalid config or a blocked base_url host."""


def _assert_public_base_url(base_url: str) -> None:
    """Reject a ``base_url`` whose *literal* host is internal.

    The private/loopback/metadata decision is delegated to the canonical
    :func:`general_ludd.security.ssrf.is_url_blocked` (no DNS) so this connector
    can never drift from the single source of truth. An always-on scheme and
    present-host check is kept for a precise error message.
    """
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ConnectorConfigError(f"unsupported scheme: {parts.scheme!r}")
    if not parts.hostname:
        raise ConnectorConfigError("base_url has no host")
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ConnectorConfigError(
            f"base_url host is internal/loopback/metadata and is blocked: {base_url!r}"
        )


class GraphiteSource:
    """Query Graphite's render API and normalize each datapoint to a record."""

    KIND = "metrics"

    def __init__(
        self,
        config: dict[str, Any],
        transport: HttpTransport,
        *,
        environ: dict[str, str] | None = None,
    ) -> None:
        env = os.environ if environ is None else environ
        self.name = str(config.get("name", "graphite"))
        base_url = config.get("base_url")
        if not base_url:
            raise ConnectorConfigError("config must set 'base_url'")
        self.base_url = str(base_url).rstrip("/")
        _assert_public_base_url(self.base_url)
        # Auth is optional for Graphite. If a token_env is configured it must
        # resolve; the value is sent as a Bearer header. Never hardcode secrets.
        self._auth_header: dict[str, str] = {}
        token_env = config.get("token_env")
        if token_env:
            token = env.get(str(token_env))
            if not token:
                raise ConnectorConfigError(f"environment variable {token_env!r} is unset or empty")
            self._auth_header = {"Authorization": f"Bearer {token}"}
        self._transport = transport
        self._timeout = float(config.get("timeout", 30.0))

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", **self._auth_header}

    def health(self) -> dict[str, Any]:
        try:
            resp = self._transport.request(
                "GET",
                f"{self.base_url}/render",
                headers=self._headers(),
                params={"target": "", "format": "json"},
                timeout=self._timeout,
            )
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"request failed: {exc}"}
        if 200 <= resp.status_code < 300:
            return {"ok": True, "detail": f"HTTP {resp.status_code}"}
        return {"ok": False, "detail": f"HTTP {resp.status_code}"}

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        spec = spec or {}
        target = spec.get("target")
        if not target:
            raise ConnectorConfigError("spec must set 'target'")
        params: dict[str, Any] = {"target": target, "format": "json"}
        if "from" in spec:
            params["from"] = spec["from"]
        if "until" in spec:
            params["until"] = spec["until"]
        resp = self._transport.request(
            "GET",
            f"{self.base_url}/render",
            headers=self._headers(),
            params=params,
            timeout=self._timeout,
        )
        if not (200 <= resp.status_code < 300):
            raise ConnectorConfigError(f"graphite query failed: HTTP {resp.status_code}")
        return self._normalize(resp.json())

    def _normalize(self, payload: Any) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        series_list = payload if isinstance(payload, list) else []
        for series in series_list:
            target = series.get("target")
            for point in series.get("datapoints", []):
                # Graphite datapoint is [value, epoch_seconds]; value may be null.
                value = sanitize_metric_value(point[0]) if len(point) > 0 else None
                ts = point[1] if len(point) > 1 else None
                records.append(
                    {
                        "ts": ts,
                        "source": self.name,
                        "kind": self.KIND,
                        "level_or_status": None,
                        "message": target,
                        "value": value,
                        "labels": {"target": target},
                        "raw": {"target": target, "datapoint": point},
                    }
                )
        return records
