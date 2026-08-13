"""Structural tests for connectors/cassandra_stats.py — CassandraStatsSource."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from general_ludd.connectors.cassandra_stats import (
    CassandraRow,
    CassandraStatsSource,
    _num,
    _parse_labels,
    _parse_prometheus,
    _split_sample,
)


class TestParseLabels:
    def test_single_pair(self):
        labels = _parse_labels('key1="val1"')
        assert labels == {"key1": "val1"}

    def test_multiple_pairs(self):
        labels = _parse_labels('key1="v1",key2="v2"')
        assert labels == {"key1": "v1", "key2": "v2"}

    def test_pair_without_equals_skipped(self):
        labels = _parse_labels("badpairs,")
        assert labels == {}

    def test_empty_string(self):
        labels = _parse_labels("")
        assert labels == {}


class TestSplitSample:
    def test_with_labels(self):
        name, labels, value = _split_sample('cassandra_table_read_latency{keyspace="ks1",table="tb1"} 0.5')
        assert name == "cassandra_table_read_latency"
        assert labels == {"keyspace": "ks1", "table": "tb1"}
        assert value == 0.5

    def test_without_labels(self):
        name, labels, value = _split_sample("metric_name 42")
        assert name == "metric_name"
        assert labels == {}
        assert value == 42.0

    def test_malformed_line(self):
        name, _labels, _value = _split_sample("only_one_part")
        assert name is None

    def test_nan_value_returns_none(self):
        name, _labels, value = _split_sample("metric NaN")
        assert name == "metric"
        assert value is None


class TestParsePrometheus:
    def test_filters_by_command_fragment(self):
        text = """# HELP compaction_tasks Pending compactions
compaction_pending_tasks 3
threadpool_active_tasks 5
other_metric 99
"""
        rows = _parse_prometheus(text, "compactionstats")
        assert len(rows) == 1
        assert rows[0]["metric"] == "compaction_pending_tasks"

    def test_skips_comments(self):
        text = "# just a comment\nsome_metric 42\n"
        rows = _parse_prometheus(text, "some")
        assert len(rows) == 1
        assert rows[0]["metric"] == "some_metric"

    def test_parses_labels(self):
        text = 'table_read_latency{keyspace="ks1",table="tb1"} 1.5\n'
        rows = _parse_prometheus(text, "table")
        assert len(rows) == 1
        assert rows[0]["keyspace"] == "ks1"
        assert rows[0]["table"] == "tb1"

    def test_non_matching_fragment_returns_empty(self):
        text = "unrelated_metric 10\n"
        rows = _parse_prometheus(text, "compactionstats")
        assert rows == []


class TestNum:
    def test_int(self):
        assert _num(42) == 42

    def test_float(self):
        assert _num(3.14) == 3.14

    def test_bool_returns_none(self):
        assert _num(True) is None

    def test_none_returns_none(self):
        assert _num(None) is None


class TestCassandraStatsSource:
    def _make_rows(self, rows: Sequence[CassandraRow]) -> callable:
        def executor(command: str) -> Sequence[CassandraRow]:
            return list(rows)
        return executor

    def test_constructs_with_defaults(self):
        src = CassandraStatsSource()
        assert src.name == "cassandra"
        assert src.KIND == "metrics"

    def test_constructs_with_custom_name(self):
        src = CassandraStatsSource({"name": "my-cass"})
        assert src.name == "my-cass"

    def test_adapts_iterable_database_cursor(self):
        class Cursor:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def execute(self, command: str) -> None:
                self.calls.append(command)

            def __iter__(self):
                return iter([{"metric": "read_latency", "value": 1.5}])

        cursor = Cursor()
        src = CassandraStatsSource(cursor=cursor)

        records = src.query()

        assert cursor.calls == ["compactionstats", "tablestats", "tpstats"]
        assert len(records) == 3
        assert records[0]["message"] == "read_latency"
        assert records[0]["value"] == 1.5

    def test_rejects_executor_and_cursor_together(self):
        with pytest.raises(ValueError, match="executor or cursor"):
            CassandraStatsSource(executor=self._make_rows([]), cursor=object())

    def test_health_ok_when_executor_works(self):
        rows: list[CassandraRow] = [{"metric": "tpstats_active", "value": 5}]
        src = CassandraStatsSource(executor=self._make_rows(rows))
        h = src.health()
        assert h["ok"] is True

    def test_health_fails_when_executor_none(self):
        src = CassandraStatsSource()
        src._driver_error = "driver unavailable"
        h = src.health()
        assert h["ok"] is False

    def test_health_fails_when_probe_raises(self):
        def bad_executor(command: str) -> Sequence[CassandraRow]:
            raise RuntimeError("down")
        src = CassandraStatsSource(executor=bad_executor)
        h = src.health()
        assert h["ok"] is False

    def test_query_returns_records(self):
        rows: list[CassandraRow] = [
            {"metric": "compaction_pending", "value": 3, "keyspace": "ks1"},
            {"metric": "compaction_completed", "value": 1, "keyspace": "ks1"},
        ]
        src = CassandraStatsSource(executor=self._make_rows(rows))
        records = src.query()
        assert len(records) >= 1
        for rec in records:
            assert rec["kind"] == "metrics"
            assert rec["source"] == "cassandra"
            assert "ts" in rec
            assert "labels" in rec
            assert "keyspace" in rec["labels"]

    def test_query_no_executor_returns_empty(self):
        src = CassandraStatsSource()
        src._driver_error = "driver unavailable"
        records = src.query()
        assert records == []

    def test_query_command_failure_does_not_crash(self):
        def partial_executor(command: str) -> Sequence[CassandraRow]:
            if command == "compactionstats":
                raise RuntimeError("timeout")
            return [{"metric": "table_read", "value": 42}]
        src = CassandraStatsSource(executor=partial_executor)
        records = src.query()
        assert len(records) >= 1

    def test_rows_to_records_skips_missing_metric(self):
        rows: list[CassandraRow] = [{"value": 10}]
        src = CassandraStatsSource(executor=self._make_rows(rows))
        executor = src._get_executor()
        records = src._rows_to_records(executor("tpstats"), "tpstats", 100.0)
        assert records == []

    def test_executor_injected_skips_default(self):
        def my_exec(command: str) -> Sequence[CassandraRow]:
            return [{"metric": "custom", "value": 99}]
        src = CassandraStatsSource(executor=my_exec)
        assert src._executor is my_exec
        assert src._get_executor() is my_exec

    def test_executor_none_when_not_set(self):
        src = CassandraStatsSource()
        assert src._executor is None

    def test_get_executor_returns_injected(self):
        def my_exec(command: str) -> Sequence[CassandraRow]:
            return []
        src = CassandraStatsSource(executor=my_exec)
        ex = src._get_executor()
        assert ex is my_exec
