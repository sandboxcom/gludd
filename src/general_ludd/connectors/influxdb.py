"""InfluxDB metrics connector (KIND='metrics').

Supports two query dialects, selected by config ``dialect``:
  * ``flux``    -> POST {base_url}/api/v2/query   (Token auth, body = Flux)
  * ``influxql``-> GET  {base_url}/query          (Token auth, params q/db)

Self-contained namespace-package module: no sibling/base/__init__ imports and no
module-level HTTP client; the transport is injected for mocked tests.

Normalized record keys: ts, source, kind, level_or_status, message, value,
labels, raw.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit


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
        data: Any = ...,
        timeout: float | None = ...,
    ) -> HttpResponse: ...


class ConnectorConfigError(ValueError):
    """Invalid config or a blocked base_url host."""


_METADATA_IP = ipaddress.ip_address("169.254.169.254")


def _assert_public_base_url(base_url: str) -> None:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ConnectorConfigError(f"unsupported scheme: {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise ConnectorConfigError("base_url has no host")
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise ConnectorConfigError("base_url host is loopback (localhost)")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if ip == _METADATA_IP:
        raise ConnectorConfigError("base_url host is the cloud metadata address")
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
        raise ConnectorConfigError(f"base_url host is an internal address: {ip}")


class InfluxDbSource:
    """Query InfluxDB (Flux or InfluxQL) and normalize series points."""

    KIND = "metrics"

    def __init__(
        self,
        config: dict[str, Any],
        transport: HttpTransport,
        *,
        environ: dict[str, str] | None = None,
    ) -> None:
        env = os.environ if environ is None else environ
        self.name = str(config.get("name", "influxdb"))
        base_url = config.get("base_url")
        if not base_url:
            raise ConnectorConfigError("config must set 'base_url'")
        self.base_url = str(base_url).rstrip("/")
        _assert_public_base_url(self.base_url)
        self.dialect = str(config.get("dialect", "flux")).lower()
        if self.dialect not in ("flux", "influxql"):
            raise ConnectorConfigError(f"unsupported dialect: {self.dialect!r}")
        token_env = config.get("token_env")
        if not token_env:
            raise ConnectorConfigError("config must set 'token_env' (name of env var holding the token)")
        token = env.get(str(token_env))
        if not token:
            raise ConnectorConfigError(f"environment variable {token_env!r} is unset or empty")
        self._token = token
        self.org = config.get("org")
        self.database = config.get("database")
        self._transport = transport
        self._timeout = float(config.get("timeout", 30.0))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self._token}", "Accept": "application/json"}

    def health(self) -> dict[str, Any]:
        try:
            resp = self._transport.request(
                "GET",
                f"{self.base_url}/health",
                headers=self._headers(),
                timeout=self._timeout,
            )
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"request failed: {exc}"}
        if 200 <= resp.status_code < 300:
            return {"ok": True, "detail": f"HTTP {resp.status_code}"}
        return {"ok": False, "detail": f"HTTP {resp.status_code}"}

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        spec = spec or {}
        resp = self._query_flux(spec) if self.dialect == "flux" else self._query_influxql(spec)
        if not (200 <= resp.status_code < 300):
            raise ConnectorConfigError(f"influxdb query failed: HTTP {resp.status_code}")
        return self._normalize(resp.json())

    def _query_flux(self, spec: dict[str, Any]) -> HttpResponse:
        flux = spec.get("flux")
        if not flux:
            raise ConnectorConfigError("flux dialect requires spec['flux']")
        params: dict[str, Any] = {}
        if self.org:
            params["org"] = self.org
        return self._transport.request(
            "POST",
            f"{self.base_url}/api/v2/query",
            headers={**self._headers(), "Content-Type": "application/vnd.flux"},
            params=params,
            data=flux,
            timeout=self._timeout,
        )

    def _query_influxql(self, spec: dict[str, Any]) -> HttpResponse:
        q = spec.get("q") or spec.get("influxql")
        if not q:
            raise ConnectorConfigError("influxql dialect requires spec['q']")
        params: dict[str, Any] = {"q": q}
        db = spec.get("db") or self.database
        if db:
            params["db"] = db
        return self._transport.request(
            "GET",
            f"{self.base_url}/query",
            headers=self._headers(),
            params=params,
            timeout=self._timeout,
        )

    def _normalize(self, payload: Any) -> list[dict[str, Any]]:
        if self.dialect == "flux":
            return self._normalize_flux(payload)
        return self._normalize_influxql(payload)

    def _normalize_flux(self, payload: Any) -> list[dict[str, Any]]:
        # Canonical normalized Flux shape: a list of tables, each with records
        # carrying _time/_value/_measurement and arbitrary tag columns.
        records: list[dict[str, Any]] = []
        tables = (payload or {}).get("tables", []) if isinstance(payload, dict) else []
        for table in tables:
            for row in table.get("records", []):
                values = row.get("values", row)
                measurement = values.get("_measurement")
                tags = {
                    k: v
                    for k, v in values.items()
                    if not k.startswith("_") and k not in ("result", "table")
                }
                records.append(
                    {
                        "ts": values.get("_time"),
                        "source": self.name,
                        "kind": self.KIND,
                        "level_or_status": None,
                        "message": measurement,
                        "value": values.get("_value"),
                        "labels": tags,
                        "raw": row,
                    }
                )
        return records

    def _normalize_influxql(self, payload: Any) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        results = (payload or {}).get("results", []) if isinstance(payload, dict) else []
        for result in results:
            for series in result.get("series", []):
                measurement = series.get("name")
                tags = series.get("tags", {}) or {}
                columns = series.get("columns", [])
                try:
                    time_idx = columns.index("time")
                except ValueError:
                    time_idx = None
                value_cols = [i for i, c in enumerate(columns) if c != "time"]
                for point in series.get("values", []):
                    ts = point[time_idx] if time_idx is not None else None
                    value = point[value_cols[0]] if value_cols else None
                    records.append(
                        {
                            "ts": ts,
                            "source": self.name,
                            "kind": self.KIND,
                            "level_or_status": None,
                            "message": measurement,
                            "value": value,
                            "labels": dict(tags),
                            "raw": {"series": measurement, "columns": columns, "point": point},
                        }
                    )
        return records
