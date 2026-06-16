"""Unit tests for ClickHouseStatsSource — canned executor, no real server."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from general_ludd.connectors.clickhouse_stats import ClickHouseStatsSource

_ROWS: dict[str, list[dict[str, Any]]] = {
    "SELECT metric, value FROM system.metrics": [
        {"metric": "Query", "value": 3},
        {"metric": "TCPConnection", "value": 7},
    ],
    "SELECT event AS metric, value FROM system.events": [
        {"metric": "SelectQuery", "value": 12345},
    ],
    "SELECT metric, value FROM system.asynchronous_metrics": [
        {"metric": "LoadAverage1", "value": 0.42},
    ],
    (
        "SELECT database, table, is_readonly, absolute_delay, "
        "queue_size FROM system.replicas"
    ): [
        {
            "database": "analytics",
            "table": "events",
            "is_readonly": 0,
            "absolute_delay": 5,
            "queue_size": 2,
        },
        {
            "database": "analytics",
            "table": "users",
            "is_readonly": 1,
            "absolute_delay": 99,
            "queue_size": 0,
        },
    ],
}


def _canned_executor(sql: str) -> Sequence[Mapping[str, Any]]:
    return _ROWS.get(sql, [])


def test_contract_attrs() -> None:
    src = ClickHouseStatsSource({"name": "ch-1"}, executor=_canned_executor)
    assert src.KIND == "metrics"
    assert src.name == "ch-1"


def test_query_normalizes_records() -> None:
    src = ClickHouseStatsSource({"name": "ch-1"}, executor=_canned_executor)
    records = src.query({})
    assert records
    required = {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}
    for rec in records:
        assert required <= set(rec)
        assert rec["kind"] == "metrics"
        assert rec["source"] == "ch-1"


def test_system_metrics_values_and_labels() -> None:
    src = ClickHouseStatsSource({}, executor=_canned_executor)
    records = src.query({})
    by = {(r["message"], r["labels"]["table"]): r for r in records}

    assert by[("Query", "system.metrics")]["value"] == 3
    assert by[("SelectQuery", "system.events")]["value"] == 12345
    assert by[("LoadAverage1", "system.asynchronous_metrics")]["value"] == 0.42
    assert by[("Query", "system.metrics")]["labels"]["metric"] == "Query"


def test_replica_records() -> None:
    src = ClickHouseStatsSource({}, executor=_canned_executor)
    records = src.query({})
    replicas = [r for r in records if r["labels"]["table"].startswith("analytics.")]

    events_delay = next(
        r for r in replicas if r["message"] == "replicas.absolute_delay" and r["labels"]["table"] == "analytics.events"
    )
    assert events_delay["value"] == 5
    assert events_delay["level_or_status"] == "ok"

    users_delay = next(
        r for r in replicas if r["message"] == "replicas.absolute_delay" and r["labels"]["table"] == "analytics.users"
    )
    assert users_delay["value"] == 99
    assert users_delay["level_or_status"] == "readonly"
    assert users_delay["labels"]["readonly"] is True


def test_numeric_string_coercion() -> None:
    def exec_strings(sql: str) -> Sequence[Mapping[str, Any]]:
        if sql == "SELECT metric, value FROM system.metrics":
            return [{"metric": "Query", "value": "42"}]
        return []

    src = ClickHouseStatsSource({}, executor=exec_strings)
    rec = next(r for r in src.query({}) if r["message"] == "Query")
    assert rec["value"] == 42


def test_health_ok_with_executor() -> None:
    src = ClickHouseStatsSource({}, executor=_canned_executor)
    assert src.health()["ok"] is True


def test_health_never_raises_on_bad_executor() -> None:
    def boom(_sql: str) -> Sequence[Mapping[str, Any]]:
        raise RuntimeError("connection refused")

    src = ClickHouseStatsSource({}, executor=boom)
    health = src.health()
    assert health["ok"] is False
    assert "probe failed" in health["detail"]
    assert src.query({}) == []


def test_no_hardcoded_credentials() -> None:
    import inspect

    from general_ludd.connectors import clickhouse_stats

    text = inspect.getsource(clickhouse_stats)
    # The only password reference allowed is via the env-var indirection.
    assert "password_env" in text
    assert 'password = "' not in text.lower()
    assert "shell=True" not in text.replace(" ", "")
    assert "import subprocess" not in text
