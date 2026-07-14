"""Structural tests for PostgreSQL stats connector."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import pytest

from general_ludd.connectors.postgres_stats import PostgresStatsSource, _to_float, _utc_now_epoch


Row = dict[str, Any]


def _canned(mapping: dict[str, Sequence[Row]]) -> Any:
    def _executor(query: str) -> Sequence[Row]:
        for needle, rows in mapping.items():
            if needle in query:
                return rows
        return []
    return _executor


class TestHelpers:
    def test_to_float_int(self) -> None:
        assert _to_float(42) == 42.0

    def test_to_float_float(self) -> None:
        assert _to_float(3.14) == 3.14

    def test_to_float_str(self) -> None:
        assert _to_float("42") == 42.0

    def test_to_float_none(self) -> None:
        assert _to_float(None) is None

    def test_to_float_invalid_str(self) -> None:
        assert _to_float("not-a-number") is None

    def test_to_float_bool(self) -> None:
        assert _to_float(True) == 1.0

    def test_utc_now_epoch(self) -> None:
        ts = _utc_now_epoch()
        assert isinstance(ts, float)
        assert ts > 1700000000.0


class TestContract:
    def test_kind(self) -> None:
        src = PostgresStatsSource()
        assert src.KIND == "metrics"

    def test_name(self) -> None:
        src = PostgresStatsSource()
        assert src.name == "postgres_stats"


class TestQueryActivity:
    def test_activity_rows(self) -> None:
        rows: list[Row] = [{"state": "active", "value": 10, "datname": "mydb"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        records = src.query("activity")
        assert len(records) == 1
        r = records[0]
        assert r["value"] == 10.0
        assert r["labels"]["state"] == "active"
        assert r["labels"]["datname"] == "mydb"
        assert r["source"] == "postgres_stats"
        assert r["kind"] == "metrics"

    def test_record_keys(self) -> None:
        rows: list[Row] = [{"state": "idle", "value": 5, "datname": "db1"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        records = src.query("activity")
        for r in records:
            assert set(r) == {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}

    def test_activity_empty(self) -> None:
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": []}))
        assert src.query("activity") == []


class TestQueryReplication:
    def test_replication_rows(self) -> None:
        rows: list[Row] = [{"application_name": "standby1", "value": 0.5}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_replication": rows}))
        records = src.query("replication")
        assert len(records) == 1
        assert records[0]["value"] == 0.5
        assert records[0]["labels"]["application_name"] == "standby1"

    def test_replication_empty(self) -> None:
        src = PostgresStatsSource(executor=_canned({"pg_stat_replication": []}))
        assert src.query("replication") == []


class TestQueryDatabase:
    def test_database_rows(self) -> None:
        rows: list[Row] = [{"datname": "mydb", "xact_commit": 100, "xact_rollback": 5, "blks_read": 50, "blks_hit": 500}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_database": rows}))
        records = src.query("database")
        assert len(records) == 4
        fields = {r["labels"]["metric"] for r in records}
        assert fields == {"xact_commit", "xact_rollback", "blks_read", "blks_hit"}

    def test_database_empty(self) -> None:
        src = PostgresStatsSource(executor=_canned({"pg_stat_database": []}))
        assert src.query("database") == []


class TestQueryStatements:
    def test_statements_rows(self) -> None:
        rows: list[Row] = [{"query_id": 1, "value": 25.0, "calls": 100}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_statements": rows}))
        records = src.query("statements")
        assert len(records) == 1
        r = records[0]
        assert r["value"] == 25.0
        assert r["labels"]["query_id"] == "1"
        assert r["labels"]["calls"] == "100"

    def test_statements_empty(self) -> None:
        src = PostgresStatsSource(executor=_canned({"pg_stat_statements": []}))
        assert src.query("statements") == []


class TestQueryErrors:
    def test_unknown_spec(self) -> None:
        src = PostgresStatsSource()
        with pytest.raises(ValueError):
            src.query("bogus")

    def test_default_is_activity(self) -> None:
        rows: list[Row] = [{"state": "active", "value": 1, "datname": "db"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        records = src.query()
        assert len(records) == 1


class TestHealth:
    def test_ok(self) -> None:
        def _exec(query: str) -> Sequence[Row]:
            return [{"state": "active", "value": 1}]
        src = PostgresStatsSource(executor=_exec)
        r = src.health()
        assert r["ok"] is True

    def test_executor_init_fails(self) -> None:
        src = PostgresStatsSource(config={"dsn_env": "MISSING_VAR"})
        r = src.health()
        assert r["ok"] is False

    def test_probe_fails(self) -> None:
        def _fail(query: str) -> Sequence[Row]:
            raise RuntimeError("down")
        src = PostgresStatsSource(executor=_fail)
        r = src.health()
        assert r["ok"] is False


class TestConfig:
    def test_config_stored(self) -> None:
        src = PostgresStatsSource(config={"dsn_env": "PG_DSN"})
        assert src.config["dsn_env"] == "PG_DSN"

    def test_config_none_becomes_empty(self) -> None:
        src = PostgresStatsSource()
        assert src.config == {}

    def test_resolve_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_PG_DSN", "postgres://localhost/db")
        src = PostgresStatsSource(config={"dsn_env": "MY_PG_DSN"})
        assert src._resolve_secret("dsn_env") == "postgres://localhost/db"
