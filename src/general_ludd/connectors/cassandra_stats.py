"""Cassandra observability connector — a *metrics* source.

It collects pending compactions, read/write latency and hints by way of an
INJECTED executor that returns already-parsed ``nodetool``-style rows, or by
scraping a JMX-exporter Prometheus endpoint.

Design (matches the package contract):

  - The executor is INJECTABLE via ``executor=``. It is a callable
    ``(command: str) -> list[CassandraRow]`` where ``command`` is a logical
    metric group (``"compactionstats"``, ``"tablestats"``, ``"tpstats"``) and the
    return value is a list of normalized row dicts. The connector NEVER shells
    out to ``nodetool`` itself, and never spawns a subprocess: the executor owns
    transport, so tests inject canned rows and run with no Cassandra at all.
  - With no executor, a default one is built LAZILY that scrapes a JMX-exporter
    endpoint over HTTP (``httpx``, already a hard dependency) and turns Prometheus
    text samples into rows. ``httpx`` is imported behind a guard so a missing
    client yields ``health() -> "driver unavailable"``.
  - The JMX endpoint URL comes from ``config['jmx_url']`` (default
    ``http://localhost:7070/metrics``). If a bearer token is needed it is read
    from the env var named by ``config['token_env']`` (default
    ``CASSANDRA_JMX_TOKEN``); it is never logged or embedded in records.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Sequence
from typing import Any, TypedDict, cast

from general_ludd.connectors.normalize import sanitize_metric_value

logger = logging.getLogger(__name__)


class CassandraRow(TypedDict, total=False):
    """One normalized row returned by the executor for a logical metric group."""

    metric: str
    value: float | int | str
    keyspace: str
    table: str


class CassandraConfig(TypedDict, total=False):
    """Connector config accepted by :class:`CassandraStatsSource`."""

    name: str
    jmx_url: str
    token_env: str


class CassandraQuerySpec(TypedDict, total=False):
    """Query selector (currently ignored — present for API compatibility)."""


class CassandraRecord(TypedDict):
    """Normalized metric record emitted by :meth:`CassandraStatsSource.query`."""

    ts: float
    source: str
    kind: str
    level_or_status: str
    message: str
    value: float | int | None
    labels: dict[str, object]
    raw: object


class CassandraHealthResult(TypedDict, total=False):
    """Health probe outcome."""

    ok: bool
    detail: str


Executor = Callable[[str], Sequence[CassandraRow]]

_DRIVER_UNAVAILABLE = "driver unavailable"

# Logical metric groups the executor is asked for, in collection order.
_COMMANDS: tuple[str, ...] = ("compactionstats", "tablestats", "tpstats")


class CassandraStatsSource:
    """Normalize Cassandra nodetool/JMX rows into metric records."""

    KIND = "metrics"

    def __init__(
        self,
        config: CassandraConfig | None = None,
        executor: Executor | None = None,
        *,
        cursor: object | None = None,
    ) -> None:
        """Build the source from connector config; executor and cursor are mutually exclusive."""
        if executor is not None and cursor is not None:
            raise ValueError("provide exactly one of executor or cursor, not both")
        cfg = dict(config or {})
        self.name: str = str(cfg.get("name", "cassandra"))
        self._config = cfg
        self._jmx_url: str = str(cfg.get("jmx_url", "http://localhost:7070/metrics"))
        self._token_env: str = str(cfg.get("token_env", "CASSANDRA_JMX_TOKEN"))
        self._executor: Executor | None
        if cursor is not None:

            def _cursor_executor(command: str) -> Sequence[CassandraRow]:
                cursor_obj = cast(Any, cursor)
                cursor_obj.execute(command)
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
            import httpx  # guarded; default JMX-scrape transport
        except Exception as exc:  # pragma: no cover - guarded import
            self._driver_error = _DRIVER_UNAVAILABLE
            logger.debug("httpx import failed: %s", exc)
            return None

        from general_ludd.connectors.base import is_safe_endpoint

        if not is_safe_endpoint(self._jmx_url):
            self._driver_error = "unsafe endpoint"
            logger.debug("jmx_url rejected by SSRF guard: %s", self._jmx_url)
            return None
        url = self._jmx_url
        token = os.environ.get(self._token_env)
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        def _run(command: str) -> Sequence[CassandraRow]:
            # The JMX exporter publishes ALL metrics at one endpoint; we fetch
            # once and let the parser map Prometheus samples to logical rows.
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                return _parse_prometheus(resp.text, command)

        return _run

    # -- health ------------------------------------------------------------

    def health(self) -> CassandraHealthResult:
        """Probe the source. Never raises."""
        executor = self._get_executor()
        if executor is None:
            return {"ok": False, "detail": self._driver_error or _DRIVER_UNAVAILABLE}
        try:
            executor("tpstats")
        except Exception:
            logger.warning("cassandra_stats probe failed", exc_info=True)
            return {"ok": False, "detail": "probe failed"}
        return {"ok": True, "detail": "ok"}

    # -- query -------------------------------------------------------------

    def query(self, spec: CassandraQuerySpec | None = None) -> list[CassandraRecord]:
        """Return normalized metric records for each logical command group."""
        executor = self._get_executor()
        if executor is None:
            return []

        ts = time.time()
        out: list[CassandraRecord] = []
        seen: set[tuple[object, object, object, object]] = set()
        for command in _COMMANDS:
            try:
                rows = executor(command)
            except Exception as exc:
                logger.debug("command %s failed: %s", command, exc)
                continue
            for record in self._rows_to_records(rows, command, ts):
                labels = record["labels"]
                key = (
                    record["message"],
                    record["value"],
                    labels.get("keyspace"),
                    labels.get("table"),
                    command,
                )
                if key in seen:
                    continue
                seen.add(key)
                out.append(record)
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
    ) -> CassandraRecord:
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

    def _rows_to_records(self, rows: Sequence[CassandraRow], command: str, ts: float) -> list[CassandraRecord]:
        out: list[CassandraRecord] = []
        for row in rows:
            metric = row.get("metric")
            if metric is None:
                continue
            labels: dict[str, object] = {
                "keyspace": str(row.get("keyspace", "")),
                "table": str(row.get("table", "")),
                "command": command,
            }
            out.append(
                self._record(
                    ts,
                    str(metric),
                    _num(row.get("value")),
                    labels,
                    dict(row),
                )
            )
        return out


def _num(value: object) -> float | int | None:
    # Unified NaN policy: every numeric metric value (executor-injected rows
    # included) is routed through sanitize_metric_value, so NaN/Inf/bool/
    # unparseable all collapse to None. 0.0 stays a real sample.
    return sanitize_metric_value(value)


def _parse_prometheus(text: str, command: str) -> list[CassandraRow]:
    """Map Prometheus-text JMX samples into logical nodetool-style rows.

    Only samples whose name contains a fragment associated with ``command`` are
    kept, so each logical group returns just its relevant metrics. Label sets in
    ``{...}`` are parsed into keyspace/table when present.
    """
    fragment = {
        "compactionstats": "compaction",
        "tablestats": "table",
        "tpstats": "threadpool",
    }.get(command, command)

    rows: list[CassandraRow] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if fragment not in line:
            continue
        name, labels, value = _split_sample(line)
        if name is None or value is None:
            continue
        rows.append(
            {
                "metric": name,
                "value": value,
                "keyspace": labels.get("keyspace", ""),
                "table": labels.get("table", ""),
            }
        )
    return rows


def _split_sample(line: str) -> tuple[str | None, dict[str, str], float | None]:
    """Parse a single Prometheus text line ``name{labels} value``."""
    if "{" in line:
        name, rest = line.split("{", 1)
        label_part, _, value_part = rest.rpartition("}")
        labels = _parse_labels(label_part)
    else:
        parts = line.split()
        if len(parts) < 2:
            return None, {}, None
        name, value_part, labels = parts[0], parts[1], {}
    value_part = value_part.strip().split()[0] if value_part.strip() else ""
    value = sanitize_metric_value(value_part)
    return name.strip() or None, labels, value


def _parse_labels(label_part: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for pair in label_part.split(","):
        if "=" not in pair:
            continue
        key, _, val = pair.partition("=")
        labels[key.strip()] = val.strip().strip('"')
    return labels
