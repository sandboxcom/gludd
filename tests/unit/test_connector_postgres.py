"""Unit tests for the PostgreSQL stats observability connector.

No real database is used: a canned in-memory executor is injected and the
normalized record shape is asserted. Driver-unavailable health and the
env-only credential contract are also covered.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from general_ludd.connectors.postgres_stats import PostgresStatsSource

Row = dict[str, Any]


def _canned(mapping: dict[str, Sequence[Row]]):
    """Build an executor that returns canned rows keyed by a SQL substring."""

    def _executor(query: str) -> Sequence[Row]:
        for needle, rows in mapping.items():
            if needle in query:
                return rows
        return []

    return _executor


def _record_keys() -> set[str]:
    return {
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    }


class TestContract:
    def test_class_attrs(self) -> None:
        src = PostgresStatsSource()
        assert src.KIND == "metrics"
        assert src.name == "postgres_stats"

    def test_records_have_normalized_schema(self) -> None:
        rows = [
            {"state": "active", "value": 3, "datname": "app"},
            {"state": "idle", "value": 7, "datname": "app"},
        ]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        records = src.query("activity")
        assert len(records) == 2
        for rec in records:
            assert set(rec) == _record_keys()
            assert rec["source"] == "postgres_stats"
            assert rec["kind"] == "metrics"

    def test_activity_value_and_labels(self) -> None:
        rows = [{"state": "active", "value": 5, "datname": "app"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        rec = src.query("activity")[0]
        assert rec["value"] == 5.0
        assert rec["labels"]["datname"] == "app"
        assert rec["labels"]["state"] == "active"
        assert rec["raw"] == rows[0]

    def test_replication_lag(self) -> None:
        rows = [{"application_name": "replica1", "value": 1.5}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_replication": rows}))
        rec = src.query("replication")[0]
        assert rec["value"] == 1.5
        assert rec["labels"]["application_name"] == "replica1"

    def test_database_fans_out_fields(self) -> None:
        rows = [
            {
                "datname": "app",
                "xact_commit": 100,
                "xact_rollback": 2,
                "blks_read": 50,
                "blks_hit": 900,
            }
        ]
        src = PostgresStatsSource(executor=_canned({"pg_stat_database": rows}))
        records = src.query("database")
        # One record per numeric field.
        metrics = {r["labels"]["metric"]: r["value"] for r in records}
        assert metrics == {
            "xact_commit": 100.0,
            "xact_rollback": 2.0,
            "blks_read": 50.0,
            "blks_hit": 900.0,
        }
        assert all(r["labels"]["datname"] == "app" for r in records)

    def test_statements_slow_queries(self) -> None:
        rows = [{"query_id": 42, "value": 250.0, "calls": 10}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_statements": rows}))
        rec = src.query("statements")[0]
        assert rec["value"] == 250.0
        assert rec["labels"]["query_id"] == "42"
        assert rec["labels"]["calls"] == "10"

    def test_default_spec_is_activity(self) -> None:
        rows = [{"state": "active", "value": 1, "datname": "app"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        assert src.query()  # no spec -> activity

    def test_unknown_spec_raises_valueerror(self) -> None:
        src = PostgresStatsSource(executor=_canned({}))
        with pytest.raises(ValueError):
            src.query("does-not-exist")

    def test_null_value_coerces_to_none(self) -> None:
        rows = [{"application_name": "r", "value": None}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_replication": rows}))
        assert src.query("replication")[0]["value"] is None

    @pytest.mark.parametrize("poison", [float("nan"), float("inf"), float("-inf")])
    def test_nonfinite_value_coerces_to_none(self, poison: float) -> None:
        # A non-finite value from a flaky backend must be coerced to None at the
        # boundary (normalized_record policy), not passed through uncoerced where
        # it would poison downstream aggregation/scoring.
        rows = [{"application_name": "r", "value": poison}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_replication": rows}))
        assert src.query("replication")[0]["value"] is None

    def test_ts_is_epoch_float_not_string(self) -> None:
        # ts must be an epoch float (or None), never an ISO string — a string ts
        # TypeErrors in the observability find()/_sort_by_ts path (math.isfinite).
        import math

        rows = [{"state": "active", "value": 1, "datname": "app"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        ts = src.query("activity")[0]["ts"]
        assert isinstance(ts, float)
        assert math.isfinite(ts)

    def test_records_route_through_find_without_typeerror(self) -> None:
        # End-to-end guard: routing records through the observability facade's
        # find()/sort must not TypeError on the ts type.
        from general_ludd.connectors.base import Observability, SourceRegistry

        rows = [{"state": "active", "value": 1, "datname": "app"}]
        src = PostgresStatsSource(executor=_canned({"pg_stat_activity": rows}))
        reg = SourceRegistry()
        reg.register(src)  # type: ignore[arg-type]
        obs = Observability(reg)
        results = obs.find({}, kinds=["metrics"])
        assert results
        assert all(isinstance(r["ts"], float) for r in results)


class TestHealth:
    def test_health_ok_with_injected_executor(self) -> None:
        src = PostgresStatsSource(executor=lambda q: [{"ok": 1}])
        h = src.health()
        assert h["ok"] is True
        assert set(h) == {"ok", "detail"}

    def test_health_driver_unavailable_when_no_executor(self) -> None:
        # No executor injected and no DSN configured -> default path errors but
        # health must report it without raising.
        src = PostgresStatsSource(config={})
        h = src.health()
        assert h["ok"] is False
        assert isinstance(h["detail"], str)

    def test_health_never_raises_on_probe_failure(self) -> None:
        def _boom(_q: str):
            raise RuntimeError("connection refused")

        src = PostgresStatsSource(executor=_boom)
        h = src.health()
        assert h["ok"] is False
        # Raw exception text (which can embed a DSN) must not leak to callers;
        # only the static generic marker is surfaced.
        assert "connection refused" not in h["detail"]
        assert h["detail"] == "probe failed"


class TestNoHardcodedCredentials:
    def test_secret_comes_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PG_DSN_TEST", "postgresql://u:p@h/db")
        src = PostgresStatsSource(config={"dsn_env": "PG_DSN_TEST"})
        assert src._resolve_secret("dsn_env") == "postgresql://u:p@h/db"

    def test_no_secret_literal_in_source(self) -> None:
        import inspect

        import general_ludd.connectors.postgres_stats as mod

        text = inspect.getsource(mod)
        # The module must not embed a password/connection literal.
        assert "password=" not in text.replace("password=password", "")
        assert "postgresql://" not in text

    def test_missing_env_returns_none(self) -> None:
        src = PostgresStatsSource(config={"dsn_env": "DEFINITELY_UNSET_VAR_XYZ"})
        assert src._resolve_secret("dsn_env") is None
