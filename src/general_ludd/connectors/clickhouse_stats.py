"""ClickHouse observability connector — a *metrics* source over ``system.*``.

It reads the introspection tables ``system.metrics``, ``system.events``,
``system.asynchronous_metrics`` and ``system.replicas`` and normalizes each row
into a metric record.

Design (matches the package contract):

  - The query executor is INJECTABLE via ``executor=``. It is a callable
    ``(sql: str) -> Sequence[ClickhouseRow]`` returning result rows as dicts
    (the shape ClickHouse's ``JSONEachRow`` / ``FORMAT JSON`` produces). Tests
    inject a canned executor so neither a live server nor an HTTP round-trip is
    needed.
  - With no executor, a default one is built LAZILY: it issues
    ``GET {url}/?query=...`` with HTTP Basic auth via ``httpx`` (already a hard
    dependency). ``httpx`` is imported behind a guard so a missing client still
    yields ``health() -> "driver unavailable"`` rather than an import error.
  - The endpoint URL comes from ``config['url']`` (default
    ``http://localhost:8123``); the username from ``config['user']`` (default
    ``default``); the password from the env var named by ``config['password_env']``
    (default ``CLICKHOUSE_PASSWORD``). The password is never logged or stored in
    records.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, TypedDict, cast

if TYPE_CHECKING:
    from general_ludd.connectors.base import NormalizedRecord

logger = logging.getLogger(__name__)


class ClickhouseRow(TypedDict, total=False):
    """A single result row from a ClickHouse ``system.*`` query."""

    metric: str
    value: float | int | str
    database: str
    table: str
    is_readonly: int
    absolute_delay: float | int
    queue_size: float | int


class ClickhouseConfig(TypedDict, total=False):
    """Connector configuration for the ClickHouse source."""

    name: str
    url: str
    user: str
    password_env: str


class ClickhouseHealthResult(TypedDict, total=False):
    """Health probe outcome."""

    ok: bool
    detail: str


class ClickhouseQuerySpec(TypedDict, total=False):
    """Caller-supplied query selection (currently empty)."""

    pass


Executor = Callable[[str], Sequence[ClickhouseRow]]

_DRIVER_UNAVAILABLE = "driver unavailable"

# (table, value-selecting SQL). Each query yields rows the normalizer maps to
# records; the table name becomes a label so callers can group by source table.
_QUERIES: tuple[tuple[str, str], ...] = (
    ("system.metrics", "SELECT metric, value FROM system.metrics"),
    ("system.events", "SELECT event AS metric, value FROM system.events"),
    (
        "system.asynchronous_metrics",
        "SELECT metric, value FROM system.asynchronous_metrics",
    ),
    (
        "system.replicas",
        "SELECT database, table, is_readonly, absolute_delay, queue_size FROM system.replicas",
    ),
)


class ClickHouseStatsSource:
    """Normalize ClickHouse ``system.*`` rows into metric records."""

    KIND = "metrics"

    def __init__(
        self,
        config: ClickhouseConfig | None = None,
        executor: Executor | None = None,
        *,
        cursor: object | None = None,
    ) -> None:
        """Build the source from connector config; executor and cursor are mutually exclusive."""
        cfg: dict[str, object] = dict(config or {})
        self.name: str = str(cfg.get("name", "clickhouse"))
        self._config = cfg
        self._url: str = str(cfg.get("url", "http://localhost:8123")).rstrip("/")
        self._user: str = str(cfg.get("user", "default"))
        self._password_env: str = str(cfg.get("password_env", "CLICKHOUSE_PASSWORD"))
        self._executor: Executor | None
        if executor is not None and cursor is not None:
            raise ValueError("provide exactly one of executor or cursor, not both")
        if cursor is not None:

            def _cursor_executor(query: str) -> Sequence[ClickhouseRow]:
                cursor_obj = cast(Any, cursor)
                cursor_obj.execute(query)
                return list(cursor_obj)

            self._executor = _cursor_executor
        else:
            self._executor = executor
        self._driver_error: str | None = None

    # -- executor wiring ---------------------------------------------------

    def _get_executor(self) -> Executor | None:
        if self._executor is not None:
            return self._executor
        executor = self._build_default_executor()
        if executor is not None:
            self._executor = executor
        return executor

    def _build_default_executor(self) -> Executor | None:
        try:
            import httpx  # guarded; default HTTP transport
        except Exception as exc:  # pragma: no cover - guarded import
            self._driver_error = _DRIVER_UNAVAILABLE
            logger.debug("httpx import failed: %s", exc)
            return None

        from general_ludd.connectors.base import is_safe_endpoint

        if not is_safe_endpoint(self._url):
            self._driver_error = "unsafe endpoint"
            logger.debug("url rejected by SSRF guard: %s", self._url)
            return None
        password = os.environ.get(self._password_env, "")
        url = self._url
        user = self._user

        def _run(sql: str) -> Sequence[ClickhouseRow]:
            # FORMAT JSONEachRow returns one JSON object per line.
            query = sql + " FORMAT JSONEachRow"
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(
                    f"{url}/",
                    params={"query": query},
                    auth=(user, password),
                )
                resp.raise_for_status()
                rows: list[ClickhouseRow] = []
                for line in resp.text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        rows.append(cast("ClickhouseRow", parsed))
                return rows

        return _run

    # -- health ------------------------------------------------------------

    def health(self) -> ClickhouseHealthResult:
        """Probe the source. Never raises."""
        executor = self._get_executor()
        if executor is None:
            return {"ok": False, "detail": self._driver_error or _DRIVER_UNAVAILABLE}
        try:
            executor("SELECT 1 AS metric, 1 AS value")
        except Exception as exc:
            logger.warning(
                "clickhouse_stats probe failed: %s",
                type(exc).__name__,
                exc_info=False,
            )
            return {"ok": False, "detail": "probe failed"}
        return {"ok": True, "detail": "ok"}

    # -- query -------------------------------------------------------------

    def query(self, spec: ClickhouseQuerySpec | None = None) -> list[NormalizedRecord]:
        """Return normalized metric records from the ``system.*`` tables."""
        executor = self._get_executor()
        if executor is None:
            return []

        ts = time.time()
        out: list[NormalizedRecord] = []
        for table, sql in _QUERIES:
            try:
                rows = executor(sql)
            except Exception as exc:
                logger.debug("query %s failed: %s", table, exc)
                continue
            if table == "system.replicas":
                out.extend(self._replica_records(rows, table, ts))
            else:
                out.extend(self._metric_records(rows, table, ts))
        return out

    # -- normalization helpers --------------------------------------------

    def _record(
        self,
        ts: float,
        message: str,
        value: float | int | None,
        labels: dict[str, object],
        raw: object,
        status: str = "ok",
    ) -> NormalizedRecord:
        return {
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": status,
            "message": message,
            "value": value,
            "labels": labels,
            "raw": raw,
        }

    def _metric_records(self, rows: Sequence[ClickhouseRow], table: str, ts: float) -> list[NormalizedRecord]:
        out: list[NormalizedRecord] = []
        for row in rows:
            metric = row.get("metric")
            if metric is None:
                continue
            out.append(
                self._record(
                    ts,
                    str(metric),
                    _num(row.get("value")),
                    {"metric": str(metric), "table": table},
                    dict(row),
                )
            )
        return out

    def _replica_records(self, rows: Sequence[ClickhouseRow], table: str, ts: float) -> list[NormalizedRecord]:
        out: list[NormalizedRecord] = []
        for row in rows:
            db = str(row.get("database", ""))
            tbl = str(row.get("table", ""))
            qualified = f"{db}.{tbl}" if db or tbl else "?"
            readonly = bool(row.get("is_readonly", 0))
            for metric in ("absolute_delay", "queue_size"):
                if metric in row:
                    out.append(
                        self._record(
                            ts,
                            f"replicas.{metric}",
                            _num(row.get(metric)),
                            {"metric": metric, "table": qualified, "readonly": readonly},
                            dict(row),
                            status="readonly" if readonly else "ok",
                        )
                    )
        return out


def _num(value: object) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            if "." in value or "e" in value.lower():
                return float(value)
            return int(value)
        except ValueError:
            return None
    return None
