"""Unit tests for the Redis stats observability connector.

No real Redis is used: canned INFO mappings and SLOWLOG sequences are injected
through the executor and the normalized record shape is asserted.
Driver-unavailable health and the env-only credential contract are covered.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.redis_stats import RedisStatsSource


def _canned(info: dict[str, Any] | None = None, slowlog: list[dict[str, Any]] | None = None):
    info = info or {}
    slowlog = slowlog or []

    def _executor(command: str) -> Any:
        cmd = command.strip().upper()
        if cmd == "INFO":
            return info
        if cmd == "SLOWLOG GET":
            return slowlog
        if cmd == "PING":
            return True
        raise ValueError(command)

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
        src = RedisStatsSource()
        assert src.KIND == "metrics"
        assert src.name == "redis_stats"

    def test_info_numeric_fields_become_records(self) -> None:
        info = {
            "used_memory": 1048576,
            "connected_clients": 4,
            "redis_version": "7.2.0",  # non-numeric -> skipped
            "instantaneous_ops_per_sec": 250,
        }
        src = RedisStatsSource(executor=_canned(info=info))
        records = src.query("info")
        fields = {r["labels"]["field"]: r["value"] for r in records}
        assert fields == {
            "used_memory": 1048576.0,
            "connected_clients": 4.0,
            "instantaneous_ops_per_sec": 250.0,
        }
        for rec in records:
            assert set(rec) == _record_keys()
            assert rec["source"] == "redis_stats"
            assert rec["kind"] == "metrics"
            assert "section" in rec["labels"]

    def test_info_section_labeling(self) -> None:
        info = {
            "used_memory": 100,
            "connected_clients": 1,
            "total_commands_processed": 5,
        }
        src = RedisStatsSource(executor=_canned(info=info))
        by_field = {r["labels"]["field"]: r["labels"]["section"] for r in src.query("info")}
        assert by_field["used_memory"] == "memory"
        assert by_field["connected_clients"] == "clients"
        assert by_field["total_commands_processed"] == "stats"

    def test_slowlog_entries_become_records(self) -> None:
        slowlog = [
            {
                "id": 1,
                "start_time": 1700000000,
                "duration": 15000,
                "command": ["GET", "foo"],
            },
            {
                "id": 2,
                "start_time": 1700000001,
                "duration": 22000,
                "command": "SET bar baz",
            },
        ]
        src = RedisStatsSource(executor=_canned(slowlog=slowlog))
        records = src.query("slowlog")
        assert len(records) == 2
        first = records[0]
        assert first["value"] == 15000.0
        assert first["labels"]["section"] == "slowlog"
        assert first["labels"]["id"] == "1"
        assert first["labels"]["command"] == "GET foo"
        # string command path
        assert records[1]["labels"]["command"] == "SET bar baz"
        for rec in records:
            assert set(rec) == _record_keys()

    @pytest.mark.parametrize("poison", [float("nan"), float("inf"), float("-inf")])
    def test_nonfinite_value_coerces_to_none(self, poison: float) -> None:
        # A non-finite metric value from a flaky backend must be coerced to None
        # at the boundary (normalized_record policy), not passed through.
        slowlog = [{"id": 1, "duration": poison, "command": "GET foo"}]
        src = RedisStatsSource(executor=_canned(slowlog=slowlog))
        assert src.query("slowlog")[0]["value"] is None

    def test_ts_is_epoch_float_not_string(self) -> None:
        # ts must be an epoch float (or None), never an ISO string — a string ts
        # TypeErrors in the observability find()/_sort_by_ts path (math.isfinite).
        import math

        src = RedisStatsSource(executor=_canned(info={"used_memory": 1}))
        ts = src.query("info")[0]["ts"]
        assert isinstance(ts, float)
        assert math.isfinite(ts)

    def test_records_route_through_find_without_typeerror(self) -> None:
        # End-to-end guard: routing records through the observability facade's
        # find()/sort must not TypeError on the ts type.
        from general_ludd.connectors.base import Observability, SourceRegistry

        src = RedisStatsSource(executor=_canned(info={"used_memory": 1}))
        reg = SourceRegistry()
        reg.register(src)  # type: ignore[arg-type]
        obs = Observability(reg)
        results = obs.find({}, kinds=["metrics"])
        assert results
        assert all(isinstance(r["ts"], float) for r in results)

    def test_default_spec_is_info(self) -> None:
        src = RedisStatsSource(executor=_canned(info={"used_memory": 1}))
        assert src.query()

    def test_unknown_spec_raises(self) -> None:
        src = RedisStatsSource(executor=_canned())
        with pytest.raises(ValueError):
            src.query("keyspace")

    def test_info_non_mapping_reply_raises_typeerror(self) -> None:
        src = RedisStatsSource(executor=lambda c: [1, 2, 3])
        with pytest.raises(TypeError):
            src.query("info")

    def test_slowlog_non_sequence_reply_raises_typeerror(self) -> None:
        def _exec(command: str) -> Any:
            return {"not": "a list"}

        src = RedisStatsSource(executor=_exec)
        with pytest.raises(TypeError):
            src.query("slowlog")


class TestHealth:
    def test_health_ok(self) -> None:
        src = RedisStatsSource(executor=_canned())
        h = src.health()
        assert h["ok"] is True
        assert set(h) == {"ok", "detail"}

    def test_health_reports_failure_without_url(self) -> None:
        src = RedisStatsSource(config={})
        h = src.health()
        assert h["ok"] is False
        assert isinstance(h["detail"], str)

    def test_health_never_raises(self) -> None:
        def _boom(_c: str):
            raise RuntimeError("NOAUTH")

        src = RedisStatsSource(executor=_boom)
        h = src.health()
        assert h["ok"] is False
        # Raw exception text (which can embed creds/host) must not leak to
        # callers; only the static generic marker is surfaced.
        assert "NOAUTH" not in h["detail"]
        assert h["detail"] == "probe failed"


class TestNoHardcodedCredentials:
    def test_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDIS_URL_TEST", "redis://h:6379/0")
        src = RedisStatsSource(config={"url_env": "REDIS_URL_TEST"})
        assert src._resolve_secret("url_env") == "redis://h:6379/0"

    def test_no_url_literal_in_source(self) -> None:
        import inspect

        import general_ludd.connectors.redis_stats as mod

        text = inspect.getsource(mod)
        assert "redis://" not in text
        assert "password=" not in text

    def test_missing_env_returns_none(self) -> None:
        src = RedisStatsSource(config={"url_env": "UNSET_REDIS_URL_XYZ"})
        assert src._resolve_secret("url_env") is None
