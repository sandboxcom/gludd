"""Structural tests for Redis stats connector."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.redis_stats import (
    RedisStatsSource,
    _section_for_field,
    _to_float,
    _utc_now_epoch,
)


def _canned(
    info: dict[str, Any] | None = None,
    slowlog: list[dict[str, Any]] | None = None,
) -> Any:
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
        assert _to_float(False) == 0.0

    def test_utc_now_epoch_is_float(self) -> None:
        ts = _utc_now_epoch()
        assert isinstance(ts, float)
        assert ts > 1700000000.0

    def test_section_for_field_memory(self) -> None:
        assert _section_for_field("used_memory") == "memory"
        assert _section_for_field("mem_fragmentation") == "memory"

    def test_section_for_field_stats(self) -> None:
        assert _section_for_field("total_commands") == "stats"
        assert _section_for_field("instantaneous_ops") == "stats"

    def test_section_for_field_clients(self) -> None:
        assert _section_for_field("connected_clients") == "clients"

    def test_section_for_field_replication(self) -> None:
        assert _section_for_field("repl_backlog") == "replication"

    def test_section_for_field_persistence(self) -> None:
        assert _section_for_field("rdb_changes") == "persistence"

    def test_section_for_field_unknown(self) -> None:
        assert _section_for_field("redis_version") == "server"


class TestContract:
    def test_kind(self) -> None:
        src = RedisStatsSource()
        assert src.KIND == "metrics"

    def test_name(self) -> None:
        src = RedisStatsSource()
        assert src.name == "redis_stats"


class TestQueryInfo:
    def test_numeric_fields_become_records(self) -> None:
        info = {"used_memory": 1048576, "redis_version": "7.2.0"}
        src = RedisStatsSource(executor=_canned(info=info))
        records = src.query("info")
        assert len(records) == 1
        assert records[0]["value"] == 1048576.0
        assert records[0]["labels"]["field"] == "used_memory"

    def test_section_labeling(self) -> None:
        info = {"used_memory": 100, "connected_clients": 1, "total_commands": 5}
        src = RedisStatsSource(executor=_canned(info=info))
        records = src.query("info")
        sections = {r["labels"]["section"] for r in records}
        assert "memory" in sections
        assert "clients" in sections
        assert "stats" in sections

    def test_record_keys(self) -> None:
        info = {"used_memory": 100}
        src = RedisStatsSource(executor=_canned(info=info))
        records = src.query("info")
        for r in records:
            assert set(r) == {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}

    def test_non_numeric_skipped(self) -> None:
        info = {"redis_version": "7.2.0", "redis_mode": "standalone"}
        src = RedisStatsSource(executor=_canned(info=info))
        assert src.query("info") == []

    def test_empty_info(self) -> None:
        src = RedisStatsSource(executor=_canned(info={}))
        assert src.query("info") == []


class TestQuerySlowlog:
    def test_slowlog_records(self) -> None:
        entries = [{"id": 1, "start_time": 1700000000, "duration": 5000, "command": "GET key"}]
        src = RedisStatsSource(executor=_canned(slowlog=entries))
        records = src.query("slowlog")
        assert len(records) == 1
        r = records[0]
        assert r["value"] == 5000.0
        assert r["labels"]["section"] == "slowlog"
        assert r["labels"]["id"] == "1"
        assert r["labels"]["command"] == "GET key"

    def test_slowlog_command_list(self) -> None:
        entries = [{"id": 1, "duration": 100, "command": ["GET", "key1"]}]
        src = RedisStatsSource(executor=_canned(slowlog=entries))
        records = src.query("slowlog")
        assert "GET key1" in records[0]["labels"]["command"]  # type: ignore[operator]

    def test_slowlog_command_none(self) -> None:
        entries = [{"id": 1, "duration": 100, "command": None}]
        src = RedisStatsSource(executor=_canned(slowlog=entries))
        records = src.query("slowlog")
        assert "command" not in records[0]["labels"]

    def test_slowlog_empty(self) -> None:
        src = RedisStatsSource(executor=_canned(slowlog=[]))
        assert src.query("slowlog") == []


class TestQueryErrors:
    def test_unknown_spec(self) -> None:
        src = RedisStatsSource()
        with pytest.raises(ValueError):
            src.query("bogus")

    def test_info_non_mapping(self) -> None:
        def _bad(cmd: str) -> Any:
            if cmd.strip().upper() == "INFO":
                return "not a mapping"
            return {}
        src = RedisStatsSource(executor=_bad)
        with pytest.raises(TypeError):
            src.query("info")

    def test_slowlog_non_sequence(self) -> None:
        def _bad(cmd: str) -> Any:
            if cmd.strip().upper() == "SLOWLOG GET":
                return "not a sequence"
            return {}
        src = RedisStatsSource(executor=_bad)
        with pytest.raises(TypeError):
            src.query("slowlog")


class TestHealth:
    def test_ok(self) -> None:
        src = RedisStatsSource(executor=_canned())
        r = src.health()
        assert r["ok"] is True

    def test_executor_init_fails(self) -> None:
        src = RedisStatsSource(config={"url_env": "MISSING_VAR"})
        r = src.health()
        assert r["ok"] is False

    def test_probe_fails(self) -> None:
        def _fail(cmd: str) -> Any:
            raise RuntimeError("down")
        src = RedisStatsSource(executor=_fail)
        r = src.health()
        assert r["ok"] is False


class TestConfig:
    def test_config_stored_as_dict(self) -> None:
        src = RedisStatsSource(config={"url_env": "REDIS_URL"})
        assert src.config["url_env"] == "REDIS_URL"

    def test_config_none_becomes_empty(self) -> None:
        src = RedisStatsSource()
        assert src.config == {}

    def test_resolve_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_REDIS_URL", "redis://localhost")
        src = RedisStatsSource(config={"url_env": "MY_REDIS_URL"})
        assert src._resolve_secret("url_env") == "redis://localhost"
