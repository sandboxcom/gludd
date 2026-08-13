"""Structural TDD tests for connectors/clickhouse_stats.py — ClickHouseStatsSource."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from general_ludd.connectors.clickhouse_stats import (
    _QUERIES,
    ClickhouseConfig,
    ClickhouseHealthResult,
    ClickhouseQuerySpec,
    ClickhouseRow,
    ClickHouseStatsSource,
    Executor,
    _num,
)

# ---------------------------------------------------------------------------
# Proof-of-existence: imports resolve, classes/functions/constants exist
# ---------------------------------------------------------------------------


def test_module_exports_all_typed_dicts():
    """Every TypedDict in the module is importable."""
    assert ClickhouseRow is not None
    assert ClickhouseConfig is not None
    assert ClickhouseHealthResult is not None
    assert ClickhouseQuerySpec is not None


def test_executor_type_alias():
    """Executor is a type alias for Callable[[str], Sequence[ClickhouseRow]]."""
    assert Executor == Callable[[str], Sequence[ClickhouseRow]]


def test__QUERIES_tuple_shape():
    """_QUERIES contains the 4 expected system-table entries."""
    tables = {t for t, _sql in _QUERIES}
    assert tables == {
        "system.metrics",
        "system.events",
        "system.asynchronous_metrics",
        "system.replicas",
    }


# ---------------------------------------------------------------------------
# ClickHouseStatsSource — instantiation and default config
# ---------------------------------------------------------------------------


class TestClickHouseStatsSourceInit:
    def test_no_args_uses_defaults(self):
        source = ClickHouseStatsSource()
        assert source.name == "clickhouse"
        assert source.KIND == "metrics"
        assert source._url == "http://localhost:8123"
        assert source._user == "default"
        assert source._password_env == "CLICKHOUSE_PASSWORD"
        assert source._executor is None

    def test_KIND_is_metrics(self):
        assert ClickHouseStatsSource.KIND == "metrics"

    def test_config_overrides_name(self):
        source = ClickHouseStatsSource(config={"name": "ch-prod"})
        assert source.name == "ch-prod"

    def test_config_overrides_url(self):
        source = ClickHouseStatsSource(config={"url": "https://ch.example.com:8443"})
        assert source._url == "https://ch.example.com:8443"

    def test_url_trailing_slash_stripped(self):
        source = ClickHouseStatsSource(config={"url": "http://localhost:8123/"})
        assert source._url == "http://localhost:8123"

    def test_config_overrides_user(self):
        source = ClickHouseStatsSource(config={"user": "admin"})
        assert source._user == "admin"

    def test_config_overrides_password_env(self):
        source = ClickHouseStatsSource(config={"password_env": "CH_PASS"})
        assert source._password_env == "CH_PASS"

    def test_injectable_executor_stored(self):
        def my_exec(sql: str) -> Sequence[ClickhouseRow]:
            return []

        source = ClickHouseStatsSource(executor=my_exec)
        assert source._executor is my_exec

    def test_iterable_cursor_is_adapted(self):
        class Cursor:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def execute(self, sql: str) -> None:
                self.calls.append(sql)

            def __iter__(self):
                return iter([{"metric": "Query", "value": "42"}])

        cursor = Cursor()
        source = ClickHouseStatsSource(cursor=cursor)

        records = source.query()

        assert len(cursor.calls) == 4
        assert len(records) == 3
        assert records[0]["message"] == "Query"
        assert records[0]["value"] == 42

    def test_executor_and_cursor_are_mutually_exclusive(self):
        with pytest.raises(ValueError, match="executor or cursor"):
            ClickHouseStatsSource(executor=lambda _: [], cursor=object())


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_no_executor_returns_ok_false(self):
        source = ClickHouseStatsSource()
        result = source.health()
        assert result["ok"] is False
        assert len(result["detail"]) > 0

    def test_health_with_success_executor(self):
        def ok_exec(sql: str) -> Sequence[ClickhouseRow]:
            return [{"metric": "1", "value": 1}]

        source = ClickHouseStatsSource(executor=ok_exec)
        result = source.health()
        assert result["ok"] is True
        assert result["detail"] == "ok"

    def test_health_executor_raises_returns_ok_false(self):
        def bad_exec(sql: str) -> Sequence[ClickhouseRow]:
            raise RuntimeError("boom")

        source = ClickHouseStatsSource(executor=bad_exec)
        result = source.health()
        assert result["ok"] is False
        assert result["detail"] == "probe failed"

    def test_health_result_is_typed_dict_shape(self):
        source = ClickHouseStatsSource()
        result = source.health()
        assert isinstance(result, dict)
        assert "ok" in result
        assert "detail" in result
        assert isinstance(result["ok"], bool)
        assert isinstance(result["detail"], str)


# ---------------------------------------------------------------------------
# query()
# ---------------------------------------------------------------------------


class TestQuery:
    def test_query_no_executor_returns_empty(self):
        source = ClickHouseStatsSource()
        records = source.query()
        assert records == []

    def test_query_with_metric_executor(self):
        def metric_exec(sql: str) -> Sequence[ClickhouseRow]:
            return [{"metric": "Query", "value": 42}]

        source = ClickHouseStatsSource(executor=metric_exec)
        records = source.query()
        assert len(records) >= 3  # 1 row per metric-table query; replicas yields 0 (no replica fields)
        # All records have the expected normalized shape
        for rec in records:
            assert "ts" in rec
            assert rec["source"] == "clickhouse"
            assert rec["kind"] == "metrics"

    def test_query_with_replica_executor(self):
        def replica_exec(sql: str) -> Sequence[ClickhouseRow]:
            if "replicas" in sql:
                return [
                    {
                        "database": "mydb",
                        "table": "mytable",
                        "is_readonly": 1,
                        "absolute_delay": 10,
                        "queue_size": 5,
                    }
                ]
            return []

        source = ClickHouseStatsSource(executor=replica_exec)
        records = source.query()
        assert len(records) == 2  # absolute_delay + queue_size
        delay_rec = [r for r in records if "absolute_delay" in r["message"]]
        queue_rec = [r for r in records if "queue_size" in r["message"]]
        assert len(delay_rec) == 1
        assert len(queue_rec) == 1
        assert delay_rec[0]["value"] == 10
        assert queue_rec[0]["value"] == 5
        assert delay_rec[0]["labels"]["table"] == "mydb.mytable"
        assert delay_rec[0]["labels"]["readonly"] is True

    def test_query_executor_failure_is_skipped_per_table(self):
        call_count = 0

        def flaky_exec(sql: str) -> Sequence[ClickhouseRow]:
            nonlocal call_count
            call_count += 1
            if "events" in sql:
                raise RuntimeError("events query failed")
            if "metrics" in sql:
                return [{"metric": "Query", "value": 1}]
            return []

        source = ClickHouseStatsSource(executor=flaky_exec)
        records = source.query()
        assert call_count == 4  # all 4 tables attempted
        assert len(records) >= 1  # metrics + async_metrics succeed, events fails

    def test_query_passes_spec_through(self):
        def tracking_exec(sql: str) -> Sequence[ClickhouseRow]:
            return []

        source = ClickHouseStatsSource(executor=tracking_exec)
        records = source.query(spec={"dummy": True})
        assert records == []


# ---------------------------------------------------------------------------
# _metric_records
# ---------------------------------------------------------------------------


class TestMetricRecords:
    def test_normalizes_metric_and_value(self):
        source = ClickHouseStatsSource()
        rows: list[ClickhouseRow] = [{"metric": "Query", "value": 42}]
        records = source._metric_records(rows, "system.metrics", 1000.0)
        assert len(records) == 1
        assert records[0]["message"] == "Query"
        assert records[0]["value"] == 42
        assert records[0]["ts"] == 1000.0
        assert records[0]["labels"]["table"] == "system.metrics"

    def test_skips_rows_without_metric(self):
        source = ClickHouseStatsSource()
        rows: list[ClickhouseRow] = [{}, {"value": 1}]
        records = source._metric_records(rows, "system.events", 0.0)
        assert records == []

    def test_includes_raw_row(self):
        source = ClickHouseStatsSource()
        rows: list[ClickhouseRow] = [{"metric": "foo", "value": 3.14}]
        records = source._metric_records(rows, "system.asynchronous_metrics", 0.0)
        assert records[0]["raw"] == {"metric": "foo", "value": 3.14}

    def test_status_is_ok(self):
        source = ClickHouseStatsSource()
        rows: list[ClickhouseRow] = [{"metric": "m", "value": 0}]
        records = source._metric_records(rows, "system.metrics", 0.0)
        assert records[0]["level_or_status"] == "ok"


# ---------------------------------------------------------------------------
# _replica_records
# ---------------------------------------------------------------------------


class TestReplicaRecords:
    def test_normalizes_delay_and_queue(self):
        source = ClickHouseStatsSource()
        rows: list[ClickhouseRow] = [
            {
                "database": "db1",
                "table": "tbl1",
                "is_readonly": 0,
                "absolute_delay": 3,
                "queue_size": 12,
            }
        ]
        records = source._replica_records(rows, "system.replicas", 2000.0)
        assert len(records) == 2
        msgs = {r["message"] for r in records}
        assert msgs == {"replicas.absolute_delay", "replicas.queue_size"}

        for r in records:
            assert r["labels"]["table"] == "db1.tbl1"
            assert r["labels"]["readonly"] is False

    def test_readonly_status(self):
        source = ClickHouseStatsSource()
        rows: list[ClickhouseRow] = [
            {
                "database": "db1",
                "table": "tbl1",
                "is_readonly": 1,
                "absolute_delay": 0,
                "queue_size": 0,
            }
        ]
        records = source._replica_records(rows, "system.replicas", 0.0)
        for r in records:
            assert r["level_or_status"] == "readonly"

    def test_missing_db_table_becomes_questionmark(self):
        source = ClickHouseStatsSource()
        rows: list[ClickhouseRow] = [
            {
                "is_readonly": 0,
                "absolute_delay": 0,
            }
        ]
        records = source._replica_records(rows, "system.replicas", 0.0)
        for r in records:
            assert r["labels"]["table"] == "?"

    def test_missing_absolute_delay_skipped(self):
        source = ClickHouseStatsSource()
        rows: list[ClickhouseRow] = [
            {
                "database": "db1",
                "table": "tbl1",
                "is_readonly": 0,
                "queue_size": 5,
            }
        ]
        records = source._replica_records(rows, "system.replicas", 0.0)
        msgs = {r["message"] for r in records}
        assert msgs == {"replicas.queue_size"}


# ---------------------------------------------------------------------------
# _num() — value normalization helper
# ---------------------------------------------------------------------------


class TestNum:
    def test_int_passthrough(self):
        assert _num(42) == 42
        assert _num(0) == 0
        assert _num(-1) == -1

    def test_float_passthrough(self):
        assert _num(3.14) == 3.14
        assert _num(0.0) == 0.0
        assert _num(-2.5) == -2.5

    def test_bool_returns_none(self):
        assert _num(True) is None
        assert _num(False) is None

    def test_str_int(self):
        assert _num("42") == 42
        assert _num("-7") == -7

    def test_str_float(self):
        assert _num("3.14") == 3.14
        assert _num("-0.5") == -0.5

    def test_str_scientific(self):
        assert _num("1e10") == 1e10
        assert _num("2.5E-3") == 2.5e-3

    def test_str_garbage_returns_none(self):
        assert _num("abc") is None
        assert _num("") is None

    def test_none_returns_none(self):
        assert _num(None) is None

    def test_arbitrary_object_returns_none(self):
        assert _num([]) is None
        assert _num({}) is None
        assert _num(object()) is None


# ---------------------------------------------------------------------------
# _record() — normalized record shape
# ---------------------------------------------------------------------------


class TestRecord:
    def test_record_shape(self):
        source = ClickHouseStatsSource()
        rec = source._record(
            1.0, "test_message", 42, {"label": "val"}, {"raw": "data"}
        )
        assert rec["ts"] == 1.0
        assert rec["source"] == "clickhouse"
        assert rec["kind"] == "metrics"
        assert rec["level_or_status"] == "ok"
        assert rec["message"] == "test_message"
        assert rec["value"] == 42
        assert rec["labels"] == {"label": "val"}
        assert rec["raw"] == {"raw": "data"}

    def test_record_value_none(self):
        source = ClickHouseStatsSource()
        rec = source._record(0.0, "m", None, {}, None)
        assert rec["value"] is None

    def test_record_custom_status(self):
        source = ClickHouseStatsSource()
        rec = source._record(0.0, "m", 1, {}, None, status="warning")
        assert rec["level_or_status"] == "warning"


# ---------------------------------------------------------------------------
# _get_executor wiring
# ---------------------------------------------------------------------------


class TestGetExecutor:
    def test_returns_injected_executor(self):
        def my_exec(sql: str) -> Sequence[ClickhouseRow]:
            return []

        source = ClickHouseStatsSource(executor=my_exec)
        assert source._get_executor() is my_exec

    def test_returns_none_when_no_executor_and_no_default(self):
        source = ClickHouseStatsSource()
        executor = source._get_executor()
        assert executor is None

    def test_caches_built_executor(self):
        source = ClickHouseStatsSource()
        e1 = source._get_executor()
        e2 = source._get_executor()
        assert e1 is e2  # same None reference across calls
