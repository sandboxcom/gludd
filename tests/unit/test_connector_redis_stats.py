"""Structural TDD tests for RedisStatsSource connector.

Covers: class shape, constants, type aliases, TypedDicts, _to_float,
_section_for_field, _normalize_info, _normalize_slowlog, _record, query,
health, executor injection, and _utc_now_epoch.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

import pytest

from general_ludd.connectors.redis_stats import (
    _INFO_COMMAND,
    _SLOWLOG_COMMAND,
    _SPECS,
    Executor,
    RedisConfig,
    RedisInfo,
    RedisSlowlogEntry,
    RedisStatsSource,
    ReplyValue,
    _section_for_field,
    _to_float,
    _utc_now_epoch,
)

# =========================================================================== #
# Module-level constants
# =========================================================================== #

class TestConstants:
    def test_info_command_is_INFO(self) -> None:
        assert _INFO_COMMAND == "INFO"

    def test_slowlog_command_is_SLOWLOG_GET(self) -> None:
        assert _SLOWLOG_COMMAND == "SLOWLOG GET"

    def test_specs_tuple_contains_info_and_slowlog(self) -> None:
        assert isinstance(_SPECS, tuple)
        assert "info" in _SPECS
        assert "slowlog" in _SPECS
        assert len(_SPECS) == 2


# =========================================================================== #
# Type aliases and TypedDicts (import-and-attribute assertions — PROOF type)
# =========================================================================== #

class TestTypeDefinitions:
    def test_RedisInfo_is_Mapping_importable(self) -> None:
        assert RedisInfo is not None

    def test_RedisSlowlogEntry_is_TypedDict(self) -> None:
        entry: RedisSlowlogEntry = {"id": 1, "start_time": 0, "duration": 500, "command": "GET"}
        assert entry["id"] == 1
        assert entry["duration"] == 500

    def test_RedisConfig_is_TypedDict(self) -> None:
        cfg: RedisConfig = {"url_env": "REDIS_URL"}
        assert cfg["url_env"] == "REDIS_URL"

    def test_ReplyValue_union_type_accepts_mapping(self) -> None:
        val: ReplyValue = {"redis_version": "7.0"}
        assert isinstance(val, Mapping)

    def test_ReplyValue_union_type_accepts_sequence(self) -> None:
        val: ReplyValue = cast(ReplyValue, [{"id": 0, "duration": 1}])
        assert isinstance(val, Sequence)

    def test_ReplyValue_union_type_accepts_bool(self) -> None:
        val: ReplyValue = True
        assert val is True

    def test_Executor_is_callable(self) -> None:
        def fake_exec(cmd: str) -> ReplyValue:
            return True
        ex: Executor = fake_exec
        assert callable(ex)
        assert ex("PING") is True


# =========================================================================== #
# _utc_now_epoch
# =========================================================================== #

class TestUtcNowEpoch:
    def test_returns_float(self) -> None:
        ts = _utc_now_epoch()
        assert isinstance(ts, float)

    def test_is_recent(self) -> None:
        ts = _utc_now_epoch()
        now = __import__("time").time()
        assert now - ts < 10.0
        assert ts > 0


# =========================================================================== #
# _to_float
# =========================================================================== #

class TestToFloat:
    def test_none_returns_none(self) -> None:
        assert _to_float(None) is None

    def test_int_returns_float(self) -> None:
        assert _to_float(42) == 42.0
        assert isinstance(_to_float(42), float)

    def test_float_returns_same(self) -> None:
        assert _to_float(3.14) == 3.14

    def test_bool_True_returns_1_0(self) -> None:
        assert _to_float(True) == 1.0

    def test_bool_False_returns_0_0(self) -> None:
        assert _to_float(False) == 0.0

    def test_numeric_string(self) -> None:
        assert _to_float("3.14") == 3.14

    def test_int_string(self) -> None:
        assert _to_float("100") == 100.0

    def test_string_with_whitespace(self) -> None:
        assert _to_float("  99.5  ") == 99.5

    def test_garbage_string_returns_none(self) -> None:
        assert _to_float("nope") is None

    def test_empty_string_returns_none(self) -> None:
        assert _to_float("") is None

    def test_list_returns_none(self) -> None:
        assert _to_float([1, 2, 3]) is None

    def test_dict_returns_none(self) -> None:
        assert _to_float({"a": 1}) is None

    def test_negative_float(self) -> None:
        assert _to_float(-5.5) == -5.5

    def test_zero(self) -> None:
        assert _to_float(0) == 0.0
        assert _to_float(0.0) == 0.0

    def test_negative_int_string(self) -> None:
        assert _to_float("-10") == -10.0


# =========================================================================== #
# _section_for_field
# =========================================================================== #

class TestSectionForField:
    def test_used_memory_field_maps_to_memory(self) -> None:
        assert _section_for_field("used_memory") == "memory"

    def test_used_memory_rss_maps_to_memory(self) -> None:
        assert _section_for_field("used_memory_rss") == "memory"

    def test_used_memory_peak_maps_to_memory(self) -> None:
        assert _section_for_field("used_memory_peak") == "memory"

    def test_mem_fragmentation_maps_to_memory(self) -> None:
        assert _section_for_field("mem_fragmentation_ratio") == "memory"

    def test_rdb_changes_maps_to_persistence(self) -> None:
        assert _section_for_field("rdb_changes_since_last_save") == "persistence"

    def test_aof_enabled_maps_to_persistence(self) -> None:
        assert _section_for_field("aof_enabled") == "persistence"

    def test_connected_clients_maps_to_clients(self) -> None:
        assert _section_for_field("connected_clients") == "clients"

    def test_blocked_clients_maps_to_clients(self) -> None:
        assert _section_for_field("blocked_clients") == "clients"

    def test_total_connections_maps_to_stats(self) -> None:
        assert _section_for_field("total_connections_received") == "stats"

    def test_instantaneous_ops_maps_to_stats(self) -> None:
        assert _section_for_field("instantaneous_ops_per_sec") == "stats"

    def test_keyspace_hits_maps_to_stats(self) -> None:
        assert _section_for_field("keyspace_hits") == "stats"

    def test_keyspace_misses_maps_to_stats(self) -> None:
        assert _section_for_field("keyspace_misses") == "stats"

    def test_expired_keys_maps_to_stats(self) -> None:
        assert _section_for_field("expired_keys") == "stats"

    def test_evicted_keys_maps_to_stats(self) -> None:
        assert _section_for_field("evicted_keys") == "stats"

    def test_rejected_connections_maps_to_stats(self) -> None:
        assert _section_for_field("rejected_connections") == "stats"

    def test_total_net_input_bytes_is_stats(self) -> None:
        assert _section_for_field("total_net_input_bytes") == "stats"

    def test_repl_backlog_maps_to_replication(self) -> None:
        assert _section_for_field("repl_backlog_histlen") == "replication"

    def test_master_repl_offset_maps_to_replication(self) -> None:
        assert _section_for_field("master_repl_offset") == "replication"

    def test_connected_slaves_is_clients_not_replication(self) -> None:
        assert _section_for_field("connected_slaves") == "clients"

    def test_slave_field_maps_to_replication(self) -> None:
        assert _section_for_field("master_repl_offset") == "replication"
        assert _section_for_field("slave_repl_offset") == "replication"

    def test_redis_version_maps_to_server_default(self) -> None:
        assert _section_for_field("redis_version") == "server"

    def test_os_falls_to_server(self) -> None:
        assert _section_for_field("os") == "server"

    def test_uptime_in_seconds_falls_to_server(self) -> None:
        assert _section_for_field("uptime_in_seconds") == "server"

    def test_case_insensitive(self) -> None:
        assert _section_for_field("USED_MEMORY") == "memory"


# =========================================================================== #
# RedisStatsSource class shape
# =========================================================================== #

class TestRedisStatsSourceShape:
    def test_KIND_is_metrics(self) -> None:
        assert RedisStatsSource.KIND == "metrics"

    def test_name_is_redis_stats(self) -> None:
        assert RedisStatsSource.name == "redis_stats"

    def test_accepts_no_args(self) -> None:
        src = RedisStatsSource()
        assert isinstance(src.config, dict)
        assert src.config == {}
        assert src._executor is None

    def test_accepts_config_dict(self) -> None:
        src = RedisStatsSource(config={"url_env": "REDIS_URL"})
        assert src.config["url_env"] == "REDIS_URL"

    def test_accepts_executor(self) -> None:
        def fake(cmd: str) -> ReplyValue:
            return True
        src = RedisStatsSource(executor=fake)
        assert src._executor is fake

    def test_accepts_both_config_and_executor(self) -> None:
        def fake(cmd: str) -> ReplyValue:
            return True
        src = RedisStatsSource(config={"url_env": "REDIS_URL"}, executor=fake)
        assert src.config["url_env"] == "REDIS_URL"
        assert src._executor is fake

    def test_config_is_copied_not_aliased(self) -> None:
        orig: dict[str, object] = {"url_env": "X"}
        src = RedisStatsSource(config=orig)
        orig["url_env"] = "OVERWRITTEN"
        assert src.config["url_env"] == "X"


# =========================================================================== #
# RedisStatsSource._resolve_secret
# =========================================================================== #

class TestResolveSecret:
    def test_returns_none_when_key_missing(self) -> None:
        src = RedisStatsSource()
        assert src._resolve_secret("url_env") is None

    def test_returns_none_when_key_has_none_value(self) -> None:
        src = RedisStatsSource(config={"url_env": None})  # type: ignore[dict-item]
        assert src._resolve_secret("url_env") is None

    def test_returns_env_value_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_REDIS_URL", "redis://localhost:6379")
        src = RedisStatsSource(config={"url_env": "TEST_REDIS_URL"})
        assert src._resolve_secret("url_env") == "redis://localhost:6379"

    def test_returns_none_when_env_var_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MISSING_REDIS_URL", raising=False)
        src = RedisStatsSource(config={"url_env": "MISSING_REDIS_URL"})
        assert src._resolve_secret("url_env") is None


# =========================================================================== #
# RedisStatsSource._get_executor
# =========================================================================== #

class TestGetExecutor:
    def test_returns_injected_executor(self) -> None:
        def fake(cmd: str) -> ReplyValue:
            return True
        src = RedisStatsSource(executor=fake)
        assert src._get_executor() is fake

    def test_tries_default_when_none_missing_driver_raises(self) -> None:
        src = RedisStatsSource()
        with pytest.raises(RuntimeError, match="redis"):
            src._get_executor()

    def test_tries_default_with_valid_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_REDIS_URL", "redis://localhost:6379")
        pytest.importorskip("redis")
        src = RedisStatsSource(config={"url_env": "TEST_REDIS_URL"})
        ex = src._get_executor()
        assert callable(ex)


# =========================================================================== #
# RedisStatsSource.health()
# =========================================================================== #

class TestHealth:
    def test_ok_false_on_executor_init_failure(self) -> None:
        def bad_exec(_cmd: str) -> ReplyValue:
            raise RuntimeError("broken")
        src = RedisStatsSource(executor=bad_exec)

        result = src.health()
        assert result["ok"] is False
        assert result["detail"] == "probe failed"

    def test_ok_false_on_default_executor_init_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.delenv("REDIS_URL_ENV", raising=False)
        src = RedisStatsSource()

        result = src.health()
        assert result["ok"] is False

    def test_ok_true_on_success(self) -> None:
        def good_exec(cmd: str) -> ReplyValue:
            return True
        src = RedisStatsSource(executor=good_exec)

        result = src.health()
        assert result["ok"] is True
        assert result["detail"] == "redis reachable"

    def test_never_raises_on_executor_exception(self) -> None:
        def explode(_cmd: str) -> ReplyValue:
            raise RuntimeError("kaboom")
        src = RedisStatsSource(executor=explode)

        result = src.health()
        assert isinstance(result, dict)
        assert result["ok"] is False

    def test_never_raises_on_ping_value_error(self) -> None:
        def bad_ping(cmd: str) -> ReplyValue:
            raise ValueError("unsupported")
        src = RedisStatsSource(executor=bad_ping)

        result = src.health()
        assert result["ok"] is False

    def test_never_raises_on_executor_returns_non_bool(self) -> None:
        def weird_ping(_cmd: str) -> ReplyValue:
            return cast(ReplyValue, None)
        src = RedisStatsSource(executor=weird_ping)

        result = src.health()
        assert isinstance(result, dict)
        assert result["ok"] is True


# =========================================================================== #
# RedisStatsSource._record
# =========================================================================== #

class TestRecord:
    def test_returns_NormalizedRecord_shape(self) -> None:
        src = RedisStatsSource()
        rec = src._record(message="test", value=100.0, labels={"a": "1"}, raw={"x": 1})
        assert isinstance(rec, dict)
        assert rec["source"] == "redis_stats"
        assert rec["kind"] == "metrics"
        assert rec["message"] == "test"
        assert rec["value"] == 100.0
        assert rec["labels"] == {"a": "1"}
        assert rec["raw"] == {"x": 1}
        assert isinstance(rec["ts"], float)

    def test_none_labels_filtered_out(self) -> None:
        src = RedisStatsSource()
        rec = src._record(message="m", value=1.0, labels={"a": "keep", "b": None}, raw={})
        assert rec["labels"] == {"a": "keep"}

    def test_all_keys_present(self) -> None:
        src = RedisStatsSource()
        rec = src._record(message="m", value=None, labels={}, raw={})
        for key in ("ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"):
            assert key in rec

    def test_default_status_is_ok(self) -> None:
        src = RedisStatsSource()
        rec = src._record(message="m", value=1.0, labels={}, raw={})
        assert rec["level_or_status"] == "ok"

    def test_custom_status(self) -> None:
        src = RedisStatsSource()
        rec = src._record(message="m", value=1.0, labels={}, raw={}, status="warn")
        assert rec["level_or_status"] == "warn"

    def test_labels_coerced_to_str(self) -> None:
        src = RedisStatsSource()
        rec = src._record(message="m", value=1.0, labels={"id": 42}, raw={})
        assert rec["labels"] == {"id": "42"}

    def test_value_none(self) -> None:
        src = RedisStatsSource()
        rec = src._record(message="m", value=None, labels={}, raw={})
        assert rec["value"] is None

    def test_raw_is_copied(self) -> None:
        src = RedisStatsSource()
        raw: dict[str, object] = {"x": 1}
        rec = src._record(message="m", value=1.0, labels={}, raw=raw)
        raw["x"] = 99
        assert rec["raw"] == {"x": 1}


# =========================================================================== #
# RedisStatsSource._normalize_info
# =========================================================================== #

class TestNormalizeInfo:
    def test_empty_info_returns_empty_list(self) -> None:
        src = RedisStatsSource()
        result = src._normalize_info({})
        assert result == []

    def test_numeric_fields_become_records(self) -> None:
        src = RedisStatsSource()
        info: RedisInfo = {
            "used_memory": 1024,
            "connected_clients": 5,
        }
        result = src._normalize_info(info)
        assert len(result) == 2
        values = {r["message"]: r["value"] for r in result}
        assert values["info used_memory"] == 1024.0
        assert values["info connected_clients"] == 5.0

    def test_non_numeric_fields_skipped(self) -> None:
        src = RedisStatsSource()
        info: RedisInfo = {"redis_version": "7.0.0", "os": "Darwin"}
        result = src._normalize_info(info)
        assert result == []

    def test_section_labels_assigned(self) -> None:
        src = RedisStatsSource()
        info: RedisInfo = {"used_memory_rss": 4096, "redis_version": "7.0"}
        result = src._normalize_info(info)
        assert len(result) == 2
        sections = {r["labels"]["field"]: r["labels"]["section"] for r in result}
        assert sections["used_memory_rss"] == "memory"
        assert sections["redis_version"] == "server"

    def test_raw_contains_original_value(self) -> None:
        src = RedisStatsSource()
        info: RedisInfo = {"keyspace_hits": 1000}
        result = src._normalize_info(info)
        assert result[0]["raw"]["field"] == "keyspace_hits"
        assert result[0]["raw"]["value"] == 1000

    def test_kind_is_metrics(self) -> None:
        src = RedisStatsSource()
        info: RedisInfo = {"used_memory": 512}
        result = src._normalize_info(info)
        assert result[0]["kind"] == "metrics"


# =========================================================================== #
# RedisStatsSource._normalize_slowlog
# =========================================================================== #

class TestNormalizeSlowlog:
    def test_empty_returns_empty_list(self) -> None:
        src = RedisStatsSource()
        result = src._normalize_slowlog([])
        assert result == []

    def test_single_entry(self) -> None:
        src = RedisStatsSource()
        entry: RedisSlowlogEntry = {"id": 123, "duration": 5000, "command": "GET key"}
        result = src._normalize_slowlog([entry])
        assert len(result) == 1
        assert result[0]["message"] == "slowlog entry duration microseconds"
        assert result[0]["value"] == 5000.0
        assert result[0]["labels"]["section"] == "slowlog"
        assert result[0]["labels"]["id"] == "123"

    def test_command_list_joined(self) -> None:
        src = RedisStatsSource()
        entry: RedisSlowlogEntry = {
            "id": 1,
            "duration": 100,
            "command": ["GET", "mykey"],
        }
        result = src._normalize_slowlog([entry])
        assert result[0]["labels"]["command"] == "GET mykey"

    def test_command_none_not_in_labels(self) -> None:
        src = RedisStatsSource()
        entry: RedisSlowlogEntry = {"id": 2, "duration": 200}
        result = src._normalize_slowlog([entry])
        assert "command" not in result[0]["labels"]

    def test_command_empty_string_not_in_labels(self) -> None:
        src = RedisStatsSource()
        entry: RedisSlowlogEntry = {"id": 3, "duration": 300, "command": ""}
        result = src._normalize_slowlog([entry])
        assert "command" not in result[0]["labels"]

    def test_duration_none(self) -> None:
        src = RedisStatsSource()
        entry: RedisSlowlogEntry = {"id": 4}
        result = src._normalize_slowlog([entry])
        assert result[0]["value"] is None

    def test_multiple_entries(self) -> None:
        src = RedisStatsSource()
        entries: list[RedisSlowlogEntry] = [
            {"id": 1, "duration": 1000, "command": "SET k v"},
            {"id": 2, "duration": 2000, "command": ["GET", "k"]},
        ]
        result = src._normalize_slowlog(entries)
        assert len(result) == 2
        assert result[0]["labels"]["id"] == "1"
        assert result[1]["labels"]["id"] == "2"

    def test_raw_is_entry_dict(self) -> None:
        src = RedisStatsSource()
        entry: RedisSlowlogEntry = {"id": 5, "duration": 50, "command": "PING"}
        result = src._normalize_slowlog([entry])
        assert result[0]["raw"] == {"id": 5, "duration": 50, "command": "PING"}


# =========================================================================== #
# RedisStatsSource.query()
# =========================================================================== #

class TestQuerySpec:
    def test_defaults_to_info_when_none(self) -> None:
        def fake_exec(cmd: str) -> ReplyValue:
            if cmd == "INFO":
                return {"used_memory": 100}
            return True
        src = RedisStatsSource(executor=fake_exec)
        result = src.query()
        assert len(result) == 1
        assert result[0]["message"] == "info used_memory"

    def test_spec_info_runs_info_command(self) -> None:
        def fake_exec(cmd: str) -> ReplyValue:
            assert cmd == "INFO"
            return {"used_memory": 200}
        src = RedisStatsSource(executor=fake_exec)
        result = src.query("info")
        assert len(result) == 1

    def test_spec_slowlog_runs_slowlog_command(self) -> None:
        def fake_exec(cmd: str) -> ReplyValue:
            if cmd == "SLOWLOG GET":
                return [{"id": 1, "duration": 100, "command": "KEYS *"}]
            return True
        src = RedisStatsSource(executor=fake_exec)
        result = src.query("slowlog")
        assert len(result) == 1
        assert result[0]["message"] == "slowlog entry duration microseconds"

    def test_unknown_spec_raises_ValueError(self) -> None:
        def fake_exec(cmd: str) -> ReplyValue:
            return True
        src = RedisStatsSource(executor=fake_exec)
        with pytest.raises(ValueError, match="unknown spec"):
            src.query("bogus")

    def test_spec_whitespace_trimmed(self) -> None:
        def fake_exec(cmd: str) -> ReplyValue:
            return {"connected_clients": 3}
        src = RedisStatsSource(executor=fake_exec)
        result = src.query("  INFO  ")
        assert len(result) == 1

    def test_spec_uppercase_normalized(self) -> None:
        def fake_exec(cmd: str) -> ReplyValue:
            return {"connected_clients": 3}
        src = RedisStatsSource(executor=fake_exec)
        result = src.query("INFO")
        assert len(result) == 1

    def test_info_reply_not_mapping_raises_TypeError(self) -> None:
        def fake_exec(cmd: str) -> ReplyValue:
            return cast(ReplyValue, 42)
        src = RedisStatsSource(executor=fake_exec)
        with pytest.raises(TypeError, match="mapping"):
            src.query("info")

    def test_slowlog_reply_string_raises_TypeError(self) -> None:
        def fake_exec(cmd: str) -> ReplyValue:
            return cast(ReplyValue, "not a sequence")
        src = RedisStatsSource(executor=fake_exec)
        with pytest.raises(TypeError, match="sequence"):
            src.query("slowlog")

    def test_slowlog_reply_not_sequence_raises_TypeError(self) -> None:
        def fake_exec(cmd: str) -> ReplyValue:
            return cast(ReplyValue, {"nope": 1})
        src = RedisStatsSource(executor=fake_exec)
        with pytest.raises(TypeError, match="sequence"):
            src.query("slowlog")
