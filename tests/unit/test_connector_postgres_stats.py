"""Structural TDD unit tests for postgres_stats connector.

Proof-level assertions: import check, class/function existence, attribute values,
TypedDict key sets, constant content, and function output type.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import Any, get_type_hints

import pytest

from general_ludd.connectors import postgres_stats as mod
from general_ludd.connectors.base import NormalizedRecord
from general_ludd.connectors.postgres_stats import (
    PostgresConfig,
    PostgresHealthResult,
    PostgresStatsSource,
    PgRow,
    _to_float,
    _utc_now_epoch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
Row = dict[str, Any]


def _canned(mapping: dict[str, Sequence[Row]]) -> Any:
    def _executor(query: str) -> Sequence[Row]:
        for needle, rows in mapping.items():
            if needle in query:
                return rows
        return []
    return _executor


# ---------------------------------------------------------------------------
# TypedDict shape proofs
# ---------------------------------------------------------------------------
class TestTypedDicts:
    """Prove TypedDicts exist and carry the expected key sets."""

    def test_postgres_config_keys(self) -> None:
        hints = get_type_hints(PostgresConfig, include_extras=False)
        assert "dsn_env" in hints
        assert hints["dsn_env"] is str

    def test_pg_row_keys(self) -> None:
        hints = get_type_hints(PgRow, include_extras=False)
        expected = {
            "state", "value", "datname", "application_name",
            "xact_commit", "xact_rollback", "blks_read", "blks_hit",
            "query_id", "calls",
        }
        assert set(hints) == expected

    def test_postgres_health_result_keys(self) -> None:
        hints = get_type_hints(PostgresHealthResult, include_extras=False)
        assert "ok" in hints
        assert "detail" in hints
        assert hints["ok"] is bool
        assert hints["detail"] is str


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------
class TestSQLConstants:
    """Prove the four SQL constants exist and are non-empty strings."""

    def test_sql_activity(self) -> None:
        assert isinstance(mod._SQL_ACTIVITY, str)
        assert "pg_stat_activity" in mod._SQL_ACTIVITY
        assert "GROUP BY" in mod._SQL_ACTIVITY

    def test_sql_replication(self) -> None:
        assert isinstance(mod._SQL_REPLICATION, str)
        assert "pg_stat_replication" in mod._SQL_REPLICATION

    def test_sql_database(self) -> None:
        assert isinstance(mod._SQL_DATABASE, str)
        assert "pg_stat_database" in mod._SQL_DATABASE
        assert "xact_commit" in mod._SQL_DATABASE

    def test_sql_statements(self) -> None:
        assert isinstance(mod._SQL_STATEMENTS, str)
        assert "pg_stat_statements" in mod._SQL_STATEMENTS
        assert "LIMIT 20" in mod._SQL_STATEMENTS

    def test_specs_covers_all_four(self) -> None:
        assert set(mod._SPECS) == {"activity", "replication", "database", "statements"}


# ---------------------------------------------------------------------------
# Class attribute proofs
# ---------------------------------------------------------------------------
class TestClassAttributes:
    """Prove PostgresStatsSource class-level attributes."""

    def test_kind_is_metrics(self) -> None:
        assert PostgresStatsSource.KIND == "metrics"

    def test_name_is_postgres_stats(self) -> None:
        assert PostgresStatsSource.name == "postgres_stats"

    def test_kind_is_class_variable_not_instance(self) -> None:
        assert hasattr(PostgresStatsSource, "KIND")
        src = PostgresStatsSource()
        assert src.KIND == "metrics"
        assert src.name == "postgres_stats"


# ---------------------------------------------------------------------------
# Constructor and config
# ---------------------------------------------------------------------------
class TestConstructor:
    """Prove __init__ accepts and stores config + executor."""

    def test_config_dict(self) -> None:
        src = PostgresStatsSource(config={"dsn_env": "PG_DSN"})
        assert isinstance(src.config, dict)
        assert src.config["dsn_env"] == "PG_DSN"

    def test_config_none_becomes_empty_dict(self) -> None:
        src = PostgresStatsSource()
        assert src.config == {}

    def test_config_defaults_to_dict(self) -> None:
        src = PostgresStatsSource(config={})
        assert isinstance(src.config, dict)

    def test_injectable_executor(self) -> None:
        def _ex(query: str) -> Sequence[PgRow]:
            return []
        src = PostgresStatsSource(executor=_ex)
        assert src._executor is _ex

    def test_executor_none_by_default(self) -> None:
        src = PostgresStatsSource()
        assert src._executor is None


# ---------------------------------------------------------------------------
# _utc_now_epoch
# ---------------------------------------------------------------------------
class TestUtcNowEpoch:
    """Prove _utc_now_epoch returns a reasonable float."""

    def test_returns_float(self) -> None:
        ts = _utc_now_epoch()
        assert isinstance(ts, float)

    def test_is_recent(self) -> None:
        ts = _utc_now_epoch()
        assert ts > 1_700_000_000.0  # Jan 2023

    def test_monotonic_across_calls(self) -> None:
        a = _utc_now_epoch()
        b = _utc_now_epoch()
        assert b >= a


# ---------------------------------------------------------------------------
# _to_float — exhaustive edge cases
# ---------------------------------------------------------------------------
class TestToFloat:
    """Prove _to_float handles every documented input type."""

    def test_none(self) -> None:
        assert _to_float(None) is None

    def test_int(self) -> None:
        assert _to_float(42) == 42.0
        assert _to_float(0) == 0.0
        assert _to_float(-7) == -7.0

    def test_float(self) -> None:
        assert _to_float(3.14) == 3.14
        assert _to_float(-0.5) == -0.5

    def test_bool_true(self) -> None:
        assert _to_float(True) == 1.0

    def test_bool_false(self) -> None:
        assert _to_float(False) == 0.0

    def test_str_valid(self) -> None:
        assert _to_float("42") == 42.0
        assert _to_float("  3.14  ") == 3.14
        assert _to_float("-1.5") == -1.5

    def test_str_garbage(self) -> None:
        assert _to_float("not-a-number") is None
        assert _to_float("") is None

    def test_object_garbage(self) -> None:
        assert _to_float(object()) is None
        assert _to_float([]) is None


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------
class TestHealth:
    """Prove the three health() return paths."""

    def test_ok_on_successful_probe(self) -> None:
        def _exec(query: str) -> Sequence[Row]:
            return [{"state": "active", "value": 1}]
        src = PostgresStatsSource(executor=_exec)
        r = src.health()
        assert r["ok"] is True
        assert isinstance(r["detail"], str)

    def test_ok_false_on_executor_init_failure(self) -> None:
        src = PostgresStatsSource(config={"dsn_env": "MISSING_VAR"})
        r = src.health()
        assert r["ok"] is False
        assert "executor init failed" in r["detail"]

    def test_ok_false_on_probe_failure(self) -> None:
        def _fail(query: str) -> Sequence[Row]:
            raise RuntimeError("down")
        src = PostgresStatsSource(executor=_fail)
        r = src.health()
        assert r["ok"] is False
        assert "probe failed" in r["detail"]

    def test_health_result_is_typed_dict_shape(self) -> None:
        def _exec(query: str) -> Sequence[Row]:
            return []
        src = PostgresStatsSource(executor=_exec)
        r = src.health()
        assert set(r) == {"ok", "detail"}

    def test_health_never_raises(self) -> None:
        src = PostgresStatsSource(config={"dsn_env": "MISSING_VAR"})
        try:
            src.health()
        except Exception:
            pytest.fail("health() must never raise")
        else:
            assert True


# ---------------------------------------------------------------------------
# query()
# ---------------------------------------------------------------------------
class TestQuerySpecs:
    """Prove query() accepts known specs, rejects unknown ones, and defaults."""

    def test_default_spec_is_activity(self) -> None:
        rows: list[Row] = [{"state": "active", "value": 1, "datname": "db"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        records = src.query()
        assert len(records) == 1

    def test_none_spec_defaults_to_activity(self) -> None:
        rows: list[Row] = [{"state": "idle", "value": 2, "datname": "db2"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        records = src.query(None)
        assert len(records) == 1

    def test_activity_spec(self) -> None:
        rows: list[Row] = [{"state": "active", "value": 1, "datname": "db"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        records = src.query("activity")
        assert len(records) == 1

    def test_activity_spec_uppercase(self) -> None:
        rows: list[Row] = [{"state": "active", "value": 1, "datname": "db"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        records = src.query("ACTIVITY")
        assert len(records) == 1

    def test_replication_spec(self) -> None:
        rows: list[Row] = [{"application_name": "s1", "value": 0.5}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_replication": rows}))
        records = src.query("replication")
        assert len(records) == 1

    def test_database_spec(self) -> None:
        rows: list[Row] = [
            {"datname": "db", "xact_commit": 1, "xact_rollback": 0, "blks_read": 2, "blks_hit": 3}
        ]
        src = PostgresStatsSource(executor=_canned({"pg_stat_database": rows}))
        records = src.query("database")
        assert len(records) == 4  # 4 metric fields per row

    def test_statements_spec(self) -> None:
        rows: list[Row] = [{"query_id": 1, "value": 25.0, "calls": 100}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_statements": rows}))
        records = src.query("statements")
        assert len(records) == 1

    def test_unknown_spec_raises_valueerror(self) -> None:
        src = PostgresStatsSource()
        with pytest.raises(ValueError, match="unknown spec"):
            src.query("bogus")

    def test_strips_and_lowers_spec(self) -> None:
        rows: list[Row] = [{"state": "active", "value": 1, "datname": "db"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        records = src.query("  ACTIVITY  ")
        assert len(records) == 1


# ---------------------------------------------------------------------------
# Normalization: activity
# ---------------------------------------------------------------------------
class TestNormalizeActivity:
    """Prove _normalize_activity creates records with datname/state labels."""

    def test_creates_datname_state_labels(self) -> None:
        rows: list[Row] = [{"state": "active", "value": 10, "datname": "mydb"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        records = src.query("activity")
        assert len(records) == 1
        rec = records[0]
        assert rec["labels"]["state"] == "active"
        assert rec["labels"]["datname"] == "mydb"

    def test_record_structure(self) -> None:
        rows: list[Row] = [{"state": "idle in transaction", "value": 3, "datname": "testdb"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        records = src.query("activity")
        rec = records[0]
        assert rec["source"] == "postgres_stats"
        assert rec["kind"] == "metrics"
        assert rec["message"] == "pg_stat_activity connection count"
        assert rec["value"] == 3.0
        assert rec["level_or_status"] == "ok"
        assert isinstance(rec["ts"], float)
        assert isinstance(rec["raw"], dict)

    def test_all_eight_normalized_record_keys(self) -> None:
        rows: list[Row] = [{"state": "idle", "value": 1, "datname": "z"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        records = src.query("activity")
        for r in records:
            assert set(r) == {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}


# ---------------------------------------------------------------------------
# Normalization: replication
# ---------------------------------------------------------------------------
class TestNormalizeReplication:
    """Prove _normalize_replication creates records with application_name label."""

    def test_creates_application_name_label(self) -> None:
        rows: list[Row] = [{"application_name": "standby1", "value": 0.5}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_replication": rows}))
        records = src.query("replication")
        assert len(records) == 1
        assert records[0]["labels"]["application_name"] == "standby1"
        assert records[0]["value"] == 0.5

    def test_handles_none_application_name(self) -> None:
        rows: list[Row] = [{"application_name": None, "value": 0.0}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_replication": rows}))
        records = src.query("replication")
        assert len(records) == 1
        assert "application_name" not in records[0]["labels"]

    def test_message_is_replication_specific(self) -> None:
        rows: list[Row] = [{"application_name": "s1", "value": 1.0}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_replication": rows}))
        records = src.query("replication")
        assert "replay lag" in records[0]["message"]


# ---------------------------------------------------------------------------
# Normalization: database
# ---------------------------------------------------------------------------
class TestNormalizeDatabase:
    """Prove _normalize_database emits one record per (field, row)."""

    def test_four_records_per_row(self) -> None:
        rows: list[Row] = [
            {"datname": "mydb", "xact_commit": 100, "xact_rollback": 5, "blks_read": 50, "blks_hit": 500}
        ]
        src = PostgresStatsSource(executor=_canned({"pg_stat_database": rows}))
        records = src.query("database")
        assert len(records) == 4

    def test_splits_by_metric_field(self) -> None:
        rows: list[Row] = [
            {"datname": "db1", "xact_commit": 10, "xact_rollback": 2, "blks_read": 3, "blks_hit": 40}
        ]
        src = PostgresStatsSource(executor=_canned({"pg_stat_database": rows}))
        records = src.query("database")
        fields = {r["labels"]["metric"] for r in records}
        assert fields == {"xact_commit", "xact_rollback", "blks_read", "blks_hit"}

    def test_datname_in_labels(self) -> None:
        rows: list[Row] = [
            {"datname": "prod", "xact_commit": 1, "xact_rollback": 0, "blks_read": 0, "blks_hit": 0}
        ]
        src = PostgresStatsSource(executor=_canned({"pg_stat_database": rows}))
        records = src.query("database")
        for r in records:
            assert r["labels"]["datname"] == "prod"

    def test_message_includes_field_name(self) -> None:
        rows: list[Row] = [
            {"datname": "db", "xact_commit": 1, "xact_rollback": 0, "blks_read": 0, "blks_hit": 0}
        ]
        src = PostgresStatsSource(executor=_canned({"pg_stat_database": rows}))
        records = src.query("database")
        for r in records:
            assert "pg_stat_database" in r["message"]
            assert r["labels"]["metric"] in r["message"]

    def test_skips_missing_fields(self) -> None:
        row: Row = {"datname": "partial", "xact_commit": 1}
        src = PostgresStatsSource(executor=_canned({"pg_stat_database": [row]}))
        records = src.query("database")
        assert len(records) == 1
        assert records[0]["labels"]["metric"] == "xact_commit"


# ---------------------------------------------------------------------------
# Normalization: statements
# ---------------------------------------------------------------------------
class TestNormalizeStatements:
    """Prove _normalize_statements creates records with query_id/calls labels."""

    def test_creates_query_id_calls_labels(self) -> None:
        rows: list[Row] = [{"query_id": 42, "value": 15.0, "calls": 300}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_statements": rows}))
        records = src.query("statements")
        assert len(records) == 1
        assert records[0]["labels"]["query_id"] == "42"
        assert records[0]["labels"]["calls"] == "300"
        assert records[0]["value"] == 15.0

    def test_message_is_statements_specific(self) -> None:
        rows: list[Row] = [{"query_id": 1, "value": 1.0, "calls": 1}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_statements": rows}))
        records = src.query("statements")
        assert "pg_stat_statements" in records[0]["message"]


# ---------------------------------------------------------------------------
# Executor plumbing
# ---------------------------------------------------------------------------
class TestExecutor:
    """Prove executor injection and delegation."""

    def test_injected_executor_is_used(self) -> None:
        call_log: list[str] = []
        def _exec(query: str) -> Sequence[Row]:
            call_log.append(query)
            return []
        src = PostgresStatsSource(executor=_exec)
        src.query()
        assert len(call_log) == 1
        assert "pg_stat_activity" in call_log[0]

    def test_get_executor_returns_injected(self) -> None:
        def _exec(query: str) -> Sequence[Row]:
            return []
        src = PostgresStatsSource(executor=_exec)
        assert src._get_executor() is _exec


# ---------------------------------------------------------------------------
# NormalizedRecord contract proofs
# ---------------------------------------------------------------------------
class TestNormalizedRecordContract:
    """Prove all records satisfy the NormalizedRecord TypedDict contract."""

    def test_activity_record_is_normalized_record(self) -> None:
        rows: list[Row] = [{"state": "a", "value": 1, "datname": "d"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        for rec in src.query("activity"):
            NormalizedRecord(**rec)

    def test_replication_record_is_normalized_record(self) -> None:
        rows: list[Row] = [{"application_name": "s", "value": 0.1}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_replication": rows}))
        for rec in src.query("replication"):
            NormalizedRecord(**rec)

    def test_database_record_is_normalized_record(self) -> None:
        rows: list[Row] = [
            {"datname": "d", "xact_commit": 1, "xact_rollback": 0, "blks_read": 0, "blks_hit": 0}
        ]
        src = PostgresStatsSource(executor=_canned({"pg_stat_database": rows}))
        for rec in src.query("database"):
            NormalizedRecord(**rec)

    def test_statements_record_is_normalized_record(self) -> None:
        rows: list[Row] = [{"query_id": 1, "value": 1.0, "calls": 1}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_statements": rows}))
        for rec in src.query("statements"):
            NormalizedRecord(**rec)

    def test_empty_query_returns_empty_list(self) -> None:
        src = PostgresStatsSource(executor=lambda _: [])
        assert src.query("activity") == []


# ---------------------------------------------------------------------------
# Import surface proof
# ---------------------------------------------------------------------------
class TestImportSurface:
    """Prove the public import surface matches the module's intended API."""

    def test_classes_importable(self) -> None:
        assert PostgresStatsSource is not None
        assert inspect.isclass(PostgresStatsSource)

    def test_typed_dicts_importable(self) -> None:
        assert PostgresConfig is not None
        assert PgRow is not None
        assert PostgresHealthResult is not None

    def test_helper_functions_importable(self) -> None:
        assert callable(_to_float)
        assert callable(_utc_now_epoch)

    def test_module_docstring(self) -> None:
        assert mod.__doc__ is not None
        assert "pg_stat_" in mod.__doc__
