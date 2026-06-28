"""Unit tests for the MySQL stats observability connector.

No real database is used: canned rows are injected through the executor and the
normalized record shape is asserted. Driver-unavailable health and the env-only
credential contract are also covered.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from general_ludd.connectors.mysql_stats import MysqlStatsSource

Row = dict[str, Any]


def _canned(mapping: dict[str, Sequence[Row]]):
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
        src = MysqlStatsSource()
        assert src.KIND == "metrics"
        assert src.name == "mysql_stats"

    def test_global_status_numeric_only(self) -> None:
        rows = [
            {"Variable_name": "Threads_connected", "Value": "12"},
            {"Variable_name": "Uptime", "Value": "3600"},
            {"Variable_name": "version", "Value": "8.0.0"},  # non-numeric -> skip
        ]
        src = MysqlStatsSource(executor=_canned({"SHOW GLOBAL STATUS": rows}))
        records = src.query("status")
        names = {r["labels"]["variable"]: r["value"] for r in records}
        assert names == {"Threads_connected": 12.0, "Uptime": 3600.0}
        for rec in records:
            assert set(rec) == _record_keys()
            assert rec["source"] == "mysql_stats"
            assert rec["kind"] == "metrics"

    def test_performance_schema(self) -> None:
        rows = [
            {
                "event_name": "statement/sql/select",
                "count_star": 100,
                "sum_timer_wait": 5000,
            }
        ]
        src = MysqlStatsSource(executor=_canned({"performance_schema": rows}))
        rec = src.query("performance")[0]
        assert rec["value"] == 5000.0
        assert rec["labels"]["event_name"] == "statement/sql/select"
        assert rec["labels"]["count_star"] == "100"

    def test_replica_status_running(self) -> None:
        rows = [
            {
                "Replica_IO_Running": "Yes",
                "Seconds_Behind_Source": 0,
                "Source_Host": "primary",
            }
        ]
        src = MysqlStatsSource(executor=_canned({"SHOW REPLICA STATUS": rows}))
        rec = src.query("replica")[0]
        assert rec["value"] == 0.0
        assert rec["level_or_status"] == "ok"
        assert rec["labels"]["source_host"] == "primary"

    def test_replica_status_degraded_when_not_running(self) -> None:
        rows = [
            {
                "Replica_IO_Running": "No",
                "Seconds_Behind_Source": None,
                "Source_Host": "primary",
            }
        ]
        src = MysqlStatsSource(executor=_canned({"SHOW REPLICA STATUS": rows}))
        rec = src.query("replica")[0]
        assert rec["level_or_status"] == "degraded"
        assert rec["value"] is None

    def test_legacy_slave_field_names(self) -> None:
        rows = [
            {
                "Slave_IO_Running": "Yes",
                "Seconds_Behind_Master": 4,
                "Master_Host": "old-primary",
            }
        ]
        src = MysqlStatsSource(executor=_canned({"SHOW REPLICA STATUS": rows}))
        rec = src.query("replica")[0]
        assert rec["value"] == 4.0
        assert rec["labels"]["source_host"] == "old-primary"

    def test_default_spec_is_status(self) -> None:
        rows = [{"Variable_name": "Uptime", "Value": "1"}]
        src = MysqlStatsSource(executor=_canned({"SHOW GLOBAL STATUS": rows}))
        assert src.query()

    def test_unknown_spec_raises(self) -> None:
        src = MysqlStatsSource(executor=_canned({}))
        with pytest.raises(ValueError):
            src.query("nope")


class TestHealth:
    def test_health_ok(self) -> None:
        src = MysqlStatsSource(executor=lambda q: [{"1": 1}])
        h = src.health()
        assert h["ok"] is True
        assert set(h) == {"ok", "detail"}

    def test_health_reports_failure_without_credentials(self) -> None:
        src = MysqlStatsSource(config={})
        h = src.health()
        assert h["ok"] is False
        assert isinstance(h["detail"], str)

    def test_health_never_raises(self) -> None:
        def _boom(_q: str):
            raise RuntimeError("access denied")

        src = MysqlStatsSource(executor=_boom)
        h = src.health()
        assert h["ok"] is False
        # Redacted: the raw exception detail must NOT leak into the client-facing
        # health response (it is logged server-side instead).
        assert h["detail"] == "probe failed"
        assert "access denied" not in h["detail"]


class TestNoHardcodedCredentials:
    def test_user_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MYSQL_USER_TEST", "observer")
        src = MysqlStatsSource(config={"user_env": "MYSQL_USER_TEST"})
        assert src._resolve_secret("user_env") == "observer"

    def test_no_password_literal_in_source(self) -> None:
        import inspect

        import general_ludd.connectors.mysql_stats as mod

        text = inspect.getsource(mod)
        # Only the env-driven assignment may appear, no literal password value.
        assert 'password="' not in text
        assert "mysql://" not in text

    def test_missing_env_returns_none(self) -> None:
        src = MysqlStatsSource(config={"user_env": "UNSET_MYSQL_USER_XYZ"})
        assert src._resolve_secret("user_env") is None
