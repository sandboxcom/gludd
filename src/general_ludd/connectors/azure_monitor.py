"""Azure Monitor / Log Analytics observability connector.

Self-contained, duck-typed connector (no shared base class). Runs a KQL query
against the Azure Monitor Log Analytics query API and normalizes each result
table row into the project's flat observability record shape.

Contract (duck-typed, shared informally with sibling connectors):
  - class attr ``KIND`` in {logs, metrics, traces, infra, pipeline}
  - instance attr ``name``
  - ``__init__(config: dict)`` — config-driven; the Bearer token is read from
    ``os.environ`` by the configured ``token_env`` name (NEVER hardcoded).
  - ``health() -> dict`` with keys ``ok`` / ``detail`` — never raises.
  - ``query(spec) -> list[dict]`` of normalized records with keys
    ``ts, source, kind, level_or_status, message, value, labels, raw``.

Any caller-overridable ``base_url`` is validated with a literal-host SSRF block
(no DNS): loopback / RFC-1918 / link-local / 169.254.169.254 / metadata names
are rejected.

HTTP transport is injectable (config key ``transport``) so tests mock it with a
canned response and no network is touched.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Mapping
from urllib.parse import urlsplit

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

# Injectable transport contract: (method, url, headers, json_body, timeout) -> response.
# The response only needs ``.status_code: int`` and ``.json() -> Any`` (httpx.Response
# satisfies this; tests pass a tiny stub).
Transport = Callable[[str, str, Mapping[str, str], object, float], HttpResponse]


# --- literal-host SSRF block (NO DNS) -------------------------------------------------

_EXTRA_BLOCKED_HOST_NAMES: frozenset[str] = frozenset()


def _reject_if_internal(base_url: str) -> None:
    """Raise ValueError if base_url's literal host is internal/metadata."""
    if is_url_blocked(base_url):
        raise ValueError(f"refusing internal/metadata host: {base_url!r}")
    host = urlsplit(base_url).hostname or ""
    host_l = host.lower()
    if host_l in _EXTRA_BLOCKED_HOST_NAMES:
        raise ValueError(f"refusing internal/metadata host: {host!r}")


def _parse_ts(value: object) -> float | None:
    """Best-effort parse of an Azure ``TimeGenerated`` value into epoch seconds.

    Accepts ISO-8601 (optionally ``Z``-suffixed) strings and numeric epochs.
    Returns ``None`` when the value is missing or unparseable — never raises.
    """
    if value is None:
        return None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        iso = text.replace("Z", "+00:00") if text.endswith("Z") else text
        try:
            from datetime import datetime

            return datetime.fromisoformat(iso).timestamp()
        except ValueError:
            return None
    return None


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


class AzureMonitorSource:
    """Log Analytics (Azure Monitor) KQL query source.

    ``KIND`` is ``"logs"`` (this source also surfaces metric-style numeric
    columns via the record ``value`` field).
    """

    KIND = "logs"

    DEFAULT_BASE_URL = "https://api.loganalytics.io"

    def __init__(self, config: dict[str, object]) -> None:
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")

        self.name: str = str(config.get("name", "azure-monitor"))
        self._workspace_id: str = str(config.get("workspace_id", "")).strip()
        if not self._workspace_id:
            raise ValueError("config['workspace_id'] is required")

        self._token_env: str = str(config.get("token_env", "")).strip()
        if not self._token_env:
            raise ValueError("config['token_env'] is required")

        self._base_url: str = str(config.get("base_url", self.DEFAULT_BASE_URL)).rstrip("/")
        # SSRF: validate any (possibly overridden) endpoint by literal host, no DNS.
        _reject_if_internal(self._base_url)

        self._timeout: float = float(str(config.get("timeout", 30.0)))
        self._default_timespan: str = str(config.get("timespan", "PT1H"))
        _mc = config.get("message_columns", ("Message", "RenderedDescription", "ResultDescription"))
        if isinstance(_mc, (str, bytes)):
            _mc = (_mc,)
        if not isinstance(_mc, tuple):
            _mc = tuple(_mc) if isinstance(_mc, Iterable) else ()
        self._message_columns: tuple[str, ...] = _mc

        transport = config.get("transport")
        if transport is None:
            transport = _default_transport
        if not callable(transport):
            raise TypeError("config['transport'] must be callable")
        self._transport: Transport = transport

    # -- internals ---------------------------------------------------------------------

    def _token(self) -> str:
        token = os.environ.get(self._token_env)
        if not token:
            raise RuntimeError(f"missing bearer token in env var {self._token_env!r}")
        return token

    def _query_url(self) -> str:
        return f"{self._base_url}/v1/workspaces/{self._workspace_id}/query"

    def _post_kql(self, kql: str, timespan: str) -> object:
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = {"query": kql, "timespan": timespan}
        resp = self._transport("POST", self._query_url(), headers, body, self._timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"log analytics query failed: HTTP {resp.status_code}")
        return resp.json()

    def _pick_message(self, row: Mapping[str, object]) -> object:
        for col in self._message_columns:
            if col in row and row[col] is not None:
                return row[col]
        return None

    def _normalize_table(self, table: Mapping[str, object]) -> list[dict[str, object]]:
        columns: object = table.get("columns") or []
        if not isinstance(columns, list):
            return []
        col_names = [str(c.get("name")) for c in columns if isinstance(c, Mapping)]
        records: list[dict[str, object]] = []
        raw_rows: object = table.get("rows") or []
        if not isinstance(raw_rows, list):
            return []
        for raw_row in raw_rows:
            row: dict[str, object] = dict(zip(col_names, raw_row, strict=False))
            level = row.get("Level")
            if level is None:
                level = row.get("SeverityLevel")
            message = self._pick_message(row)
            reserved = set(self._message_columns) | {
                "TimeGenerated",
                "Level",
                "SeverityLevel",
                "Value",
            }
            labels = {k: v for k, v in row.items() if k not in reserved}
            value = _coerce_float(row.get("Value")) if "Value" in row else None
            records.append(
                {
                    "ts": _parse_ts(row.get("TimeGenerated")),
                    "source": self.name,
                    "kind": self.KIND,
                    "level_or_status": level,
                    "message": message,
                    "value": value,
                    "labels": labels,
                    "raw": row,
                }
            )
        return records

    def _normalize(self, payload: object) -> list[dict[str, object]]:
        if not isinstance(payload, Mapping):
            return []
        records: list[dict[str, object]] = []
        for table in payload.get("tables") or []:
            if isinstance(table, Mapping):
                records.extend(self._normalize_table(table))
        return records

    # -- public API --------------------------------------------------------------------

    def query(self, spec: object) -> list[dict[str, object]]:
        """Run a KQL query and return normalized records.

        ``spec`` may be a raw KQL string or a mapping with ``query`` (KQL) and an
        optional ``timespan`` (ISO-8601 duration or ``start/end`` range).
        """
        if isinstance(spec, str):
            kql = spec
            timespan = self._default_timespan
        elif isinstance(spec, Mapping):
            kql = str(spec.get("query", ""))
            timespan = str(spec.get("timespan", self._default_timespan))
        else:
            raise TypeError("spec must be a KQL string or a mapping with 'query'")
        if not kql.strip():
            raise ValueError("empty KQL query")
        return self._normalize(self._post_kql(kql, timespan))

    def health(self) -> dict[str, object]:
        """Trivial ``print 1`` KQL probe. Never raises."""
        try:
            payload = self._post_kql("print 1", "PT5M")
        except Exception:  # health must never raise
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        rows = 0
        if isinstance(payload, Mapping):
            for table in payload.get("tables") or []:
                if isinstance(table, Mapping):
                    rows += len(table.get("rows") or [])
        return {"ok": True, "detail": f"print 1 returned {rows} row(s)"}


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    json_body: object,
    timeout: float,
) -> HttpResponse:
    """Default httpx-backed transport (no shell, time-bound). Imported lazily."""
    import httpx

    resp = httpx.request(
        method,
        url,
        headers=dict(headers),
        json=json_body,
        timeout=timeout,
        follow_redirects=False,
    )
    return resp
