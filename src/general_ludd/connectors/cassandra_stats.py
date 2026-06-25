"""Cassandra observability connector — a *metrics* source.

It collects pending compactions, read/write latency and hints by way of an
INJECTED executor that returns already-parsed ``nodetool``-style rows, or by
scraping a JMX-exporter Prometheus endpoint.

Design (matches the package contract):

  - The executor is INJECTABLE via ``executor=``. It is a callable
    ``(command: str) -> list[Mapping[str, Any]]`` where ``command`` is a logical
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
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from general_ludd.connectors.normalize import sanitize_metric_value

logger = logging.getLogger(__name__)

Executor = Callable[[str], "Sequence[Mapping[str, Any]]"]

_DRIVER_UNAVAILABLE = "driver unavailable"

# Logical metric groups the executor is asked for, in collection order.
_COMMANDS: tuple[str, ...] = ("compactionstats", "tablestats", "tpstats")


class CassandraStatsSource:
    """Normalize Cassandra nodetool/JMX rows into metric records."""

    KIND = "metrics"

    def __init__(self, config: Mapping[str, Any] | None = None, executor: Executor | None = None) -> None:
        cfg: dict[str, Any] = dict(config or {})
        self.name: str = str(cfg.get("name", "cassandra"))
        self._config = cfg
        self._jmx_url: str = str(cfg.get("jmx_url", "http://localhost:7070/metrics"))
        self._token_env: str = str(cfg.get("token_env", "CASSANDRA_JMX_TOKEN"))
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

        def _run(command: str) -> Sequence[Mapping[str, Any]]:
            # The JMX exporter publishes ALL metrics at one endpoint; we fetch
            # once and let the parser map Prometheus samples to logical rows.
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                return _parse_prometheus(resp.text, command)

        return _run

    # -- health ------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the source. Never raises."""
        executor = self._get_executor()
        if executor is None:
            return {"ok": False, "detail": self._driver_error or _DRIVER_UNAVAILABLE}
        try:
            executor("tpstats")
        except Exception as exc:
            return {"ok": False, "detail": f"probe failed: {exc}"}
        return {"ok": True, "detail": "ok"}

    # -- query -------------------------------------------------------------

    def query(self, spec: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        executor = self._get_executor()
        if executor is None:
            return []

        ts = time.time()
        out: list[dict[str, Any]] = []
        for command in _COMMANDS:
            try:
                rows = executor(command)
            except Exception as exc:
                logger.debug("command %s failed: %s", command, exc)
                continue
            out.extend(self._rows_to_records(rows, command, ts))
        return out

    # -- normalization helpers --------------------------------------------

    def _record(
        self,
        ts: float,
        message: str,
        value: float | int | None,
        labels: dict[str, Any],
        raw: Any,
        status: str = "ok",
    ) -> dict[str, Any]:
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

    def _rows_to_records(
        self, rows: Sequence[Mapping[str, Any]], command: str, ts: float
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            metric = row.get("metric")
            if metric is None:
                continue
            labels = {
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


def _num(value: Any) -> float | int | None:
    # Unified NaN policy: every numeric metric value (executor-injected rows
    # included) is routed through sanitize_metric_value, so NaN/Inf/bool/
    # unparseable all collapse to None. 0.0 stays a real sample.
    return sanitize_metric_value(value)


def _parse_prometheus(text: str, command: str) -> list[Mapping[str, Any]]:
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

    rows: list[Mapping[str, Any]] = []
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
