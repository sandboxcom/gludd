"""Structural TDD tests for MongoDB stats connector."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from general_ludd.connectors.mongodb_stats import (
    MongoConfig,
    MongoDbStatsSource,
    MongoMember,
    MongoOpTime,
    MongoQuerySpec,
    MongoRecord,
    MongoReplSetDoc,
    MongoServerStatusDoc,
    _as_mapping,
    _member_optime,
    _num,
)


# ---------------------------------------------------------------------------
# TypedDict existence
# ---------------------------------------------------------------------------

class TestTypedDicts:
    def test_mongo_config_fields(self) -> None:
        assert "name" in MongoConfig.__optional_keys__
        assert "uri_env" in MongoConfig.__optional_keys__

    def test_mongo_query_spec_fields(self) -> None:
        assert hasattr(MongoQuerySpec, "__optional_keys__")

    def test_mongo_record_fields(self) -> None:
        assert "ts" in MongoRecord.__required_keys__
        assert "source" in MongoRecord.__required_keys__
        assert "kind" in MongoRecord.__required_keys__
        assert "level_or_status" in MongoRecord.__required_keys__
        assert "message" in MongoRecord.__required_keys__
        assert "value" in MongoRecord.__required_keys__
        assert "labels" in MongoRecord.__required_keys__
        assert "raw" in MongoRecord.__required_keys__

    def test_mongo_op_time_fields(self) -> None:
        assert "ts" in MongoOpTime.__optional_keys__

    def test_mongo_member_fields(self) -> None:
        assert "stateStr" in MongoMember.__optional_keys__
        assert "optimeDate" in MongoMember.__optional_keys__
        assert "optime" in MongoMember.__optional_keys__
        assert "optime_seconds" in MongoMember.__optional_keys__

    def test_mongo_server_status_doc_fields(self) -> None:
        assert "connections" in MongoServerStatusDoc.__optional_keys__
        assert "opcounters" in MongoServerStatusDoc.__optional_keys__
        assert "wiredTiger" in MongoServerStatusDoc.__optional_keys__

    def test_mongo_repl_set_doc_fields(self) -> None:
        assert "members" in MongoReplSetDoc.__optional_keys__


# ---------------------------------------------------------------------------
# _num helper
# ---------------------------------------------------------------------------

class TestNum:
    def test_int_returns_int(self) -> None:
        assert _num(42) == 42

    def test_float_returns_float(self) -> None:
        assert _num(3.14) == 3.14

    def test_bool_returns_none(self) -> None:
        assert _num(True) is None

    def test_bool_false_returns_none(self) -> None:
        assert _num(False) is None

    def test_str_returns_none(self) -> None:
        assert _num("42") is None

    def test_none_returns_none(self) -> None:
        assert _num(None) is None

    def test_list_returns_none(self) -> None:
        assert _num([1, 2, 3]) is None

    def test_dict_returns_none(self) -> None:
        assert _num({"a": 1}) is None

    def test_zero_int_returns_zero(self) -> None:
        assert _num(0) == 0

    def test_negative_int_returns_negative(self) -> None:
        assert _num(-5) == -5

    def test_zero_float_returns_zero(self) -> None:
        assert _num(0.0) == 0.0


# ---------------------------------------------------------------------------
# _as_mapping helper
# ---------------------------------------------------------------------------

class TestAsMapping:
    def test_dict_returns_dict(self) -> None:
        d = {"a": 1}
        assert _as_mapping(d) is d

    def test_none_returns_empty_mapping(self) -> None:
        result = _as_mapping(None)
        assert isinstance(result, Mapping)
        assert not result

    def test_falsy_zero_int_returns_empty_mapping(self) -> None:
        result = _as_mapping(0)
        assert isinstance(result, Mapping)

    def test_empty_string_returns_empty_mapping(self) -> None:
        result = _as_mapping("")
        assert isinstance(result, Mapping)

    def test_empty_dict_returns_empty_dict(self) -> None:
        d: dict[str, object] = {}
        result = _as_mapping(d)
        assert isinstance(result, Mapping)
        assert not result


# ---------------------------------------------------------------------------
# _member_optime helper
# ---------------------------------------------------------------------------

class TestMemberOptime:
    def test_optime_date_primary(self) -> None:
        members: list[MongoMember] = [
            {"stateStr": "PRIMARY", "optimeDate": 1710000000},
        ]
        assert _member_optime(members, want_primary=True) == 1710000000.0

    def test_optime_date_int_returns_float(self) -> None:
        members: list[MongoMember] = [
            {"stateStr": "SECONDARY", "optimeDate": 1710000000},
        ]
        assert _member_optime(members, want_primary=False) == 1710000000.0

    def test_optime_ts_from_subdoc(self) -> None:
        members: list[MongoMember] = [
            {"stateStr": "SECONDARY", "optime": {"ts": 1710000050}},
        ]
        assert _member_optime(members, want_primary=False) == 1710000050.0

    def test_optime_seconds_field(self) -> None:
        members: list[MongoMember] = [
            {"stateStr": "SECONDARY", "optime_seconds": 1710000100},
        ]
        assert _member_optime(members, want_primary=False) == 1710000100.0

    def test_primary_searches_for_primary_state(self) -> None:
        members: list[MongoMember] = [
            {"stateStr": "SECONDARY", "optimeDate": 1710000000},
            {"stateStr": "PRIMARY", "optimeDate": 1710000050},
        ]
        assert _member_optime(members, want_primary=True) == 1710000050.0

    def test_primary_not_found_returns_none(self) -> None:
        members: list[MongoMember] = [
            {"stateStr": "SECONDARY", "optimeDate": 1710000000},
        ]
        assert _member_optime(members, want_primary=True) is None

    def test_no_optime_fields_primary_returns_none(self) -> None:
        members: list[MongoMember] = [
            {"stateStr": "PRIMARY", "name": "node1"},
        ]
        assert _member_optime(members, want_primary=True) is None

    def test_no_optime_fields_nonprimary_returns_none(self) -> None:
        members: list[MongoMember] = [
            {"stateStr": "SECONDARY", "name": "node1"},
        ]
        assert _member_optime(members, want_primary=False) is None

    def test_empty_members_returns_none(self) -> None:
        assert _member_optime([], want_primary=False) is None

    def test_empty_members_primary_returns_none(self) -> None:
        assert _member_optime([], want_primary=True) is None

    def test_multiple_secondaries_first_wins(self) -> None:
        members: list[MongoMember] = [
            {"stateStr": "SECONDARY", "optimeDate": 1710000030},
            {"stateStr": "SECONDARY", "optimeDate": 1710000060},
        ]
        assert _member_optime(members, want_primary=False) == 1710000030.0

    def test_optime_priority_order(self) -> None:
        # optimeDate wins over optime.ts and optime_seconds
        members: list[MongoMember] = [
            {
                "stateStr": "SECONDARY",
                "optimeDate": 1710000010,
                "optime": {"ts": 1710000020},
                "optime_seconds": 1710000030,
            },
        ]
        assert _member_optime(members, want_primary=False) == 1710000010.0

    def test_optime_ts_over_optime_seconds(self) -> None:
        # optime.ts wins over optime_seconds when optimeDate absent
        members: list[MongoMember] = [
            {
                "stateStr": "SECONDARY",
                "optime": {"ts": 1710000020},
                "optime_seconds": 1710000030,
            },
        ]
        assert _member_optime(members, want_primary=False) == 1710000020.0


# ---------------------------------------------------------------------------
# MongoDbStatsSource — default config
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_kind_is_metrics(self) -> None:
        src = MongoDbStatsSource()
        assert src.KIND == "metrics"

    def test_default_name(self) -> None:
        src = MongoDbStatsSource()
        assert src.name == "mongodb"

    def test_default_uri_env(self) -> None:
        src = MongoDbStatsSource()
        assert src._uri_env == "MONGODB_URI"

    def test_custom_name(self) -> None:
        src = MongoDbStatsSource(config={"name": "my_mongo"})
        assert src.name == "my_mongo"

    def test_custom_uri_env(self) -> None:
        src = MongoDbStatsSource(config={"uri_env": "MY_MONGO_URI"})
        assert src._uri_env == "MY_MONGO_URI"

    def test_config_none_uses_defaults(self) -> None:
        src = MongoDbStatsSource(config=None)
        assert src.name == "mongodb"
        assert src._uri_env == "MONGODB_URI"


# ---------------------------------------------------------------------------
# Executor wiring
# ---------------------------------------------------------------------------

class TestExecutorWiring:
    def test_get_executor_returns_none_when_no_executor_and_no_env(self) -> None:
        src = MongoDbStatsSource()
        assert src._get_executor() is None

    def test_get_executor_returns_injected_executor(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {"ok": 1}
        src = MongoDbStatsSource(executor=fake_exec)
        retrieved = src._get_executor()
        assert retrieved is fake_exec

    def test_get_executor_stores_built_executor(self) -> None:
        """After first successful _get_executor, subsequent calls return the stored one."""
        call_count = 0

        def fake_exec(command: str) -> Mapping[str, object]:
            nonlocal call_count
            call_count += 1
            return {"ok": 1}

        src = MongoDbStatsSource(executor=fake_exec)
        src._get_executor()
        src._get_executor()
        assert call_count == 0  # injectable executor is never called during _get_executor


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------

class TestHealth:
    def test_no_executor_returns_not_ok(self) -> None:
        src = MongoDbStatsSource()
        result = src.health()
        assert result["ok"] is False
        assert "detail" in result

    def test_no_executor_detail_mentions_driver(self) -> None:
        src = MongoDbStatsSource()
        result = src.health()
        assert isinstance(result["detail"], str)

    def test_successful_probe_returns_ok(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {"ok": 1}
        src = MongoDbStatsSource(executor=fake_exec)
        result = src.health()
        assert result["ok"] is True
        assert result["detail"] == "ok"

    def test_probe_failure_returns_not_ok(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            raise RuntimeError("boom")
        src = MongoDbStatsSource(executor=fake_exec)
        result = src.health()
        assert result["ok"] is False
        assert result["detail"] == "serverStatus failed"

    def test_probe_connection_error_returns_not_ok(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            raise ConnectionError("timeout")
        src = MongoDbStatsSource(executor=fake_exec)
        result = src.health()
        assert result["ok"] is False

    def test_health_never_raises(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            raise RecursionError("deep")
        src = MongoDbStatsSource(executor=fake_exec)
        result = src.health()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# query() — no executor
# ---------------------------------------------------------------------------

class TestQueryNoExecutor:
    def test_no_executor_returns_empty(self) -> None:
        src = MongoDbStatsSource()
        result = src.query()
        assert result == []

    def test_no_executor_with_spec_returns_empty(self) -> None:
        src = MongoDbStatsSource()
        result = src.query(MongoQuerySpec())
        assert result == []


# ---------------------------------------------------------------------------
# _server_status_records
# ---------------------------------------------------------------------------

class TestServerStatusRecords:
    def test_connections_extracted(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {
                "connections": {
                    "current": 10,
                    "available": 50000,
                    "active": 3,
                },
            }
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._server_status_records(fake_exec, 1000.0)

        conn_records = [r for r in records if r["labels"]["section"] == "connections"]
        assert len(conn_records) == 3
        messages = {r["message"] for r in conn_records}
        assert messages == {"connections.current", "connections.available", "connections.active"}

    def test_connections_values_are_numeric(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {"connections": {"current": 15}}
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._server_status_records(fake_exec, 1000.0)

        rec = records[0]
        assert rec["value"] == 15
        assert isinstance(rec["value"], int)

    def test_opcounters_extracted(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {
                "opcounters": {
                    "insert": 100,
                    "query": 200,
                    "update": 50,
                },
            }
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._server_status_records(fake_exec, 1000.0)

        op_records = [r for r in records if r["labels"]["section"] == "opcounters"]
        assert len(op_records) == 3
        messages = {r["message"] for r in op_records}
        assert messages == {"opcounters.insert", "opcounters.query", "opcounters.update"}

    def test_wiredtiger_cache_extracted(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {
                "wiredTiger": {
                    "cache": {
                        "bytes currently in the cache": 104857600,
                        "maximum bytes configured": 1073741824,
                    },
                },
            }
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._server_status_records(fake_exec, 1000.0)

        wt_records = [r for r in records if r["labels"]["section"] == "wiredTiger"]
        assert len(wt_records) == 2
        messages = {r["message"] for r in wt_records}
        assert messages == {
            "wiredTiger.cache.bytes currently in the cache",
            "wiredTiger.cache.maximum bytes configured",
        }

    def test_record_structure(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {"connections": {"current": 5}}
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._server_status_records(fake_exec, 1000.0)

        r = records[0]
        assert r["ts"] == 1000.0
        assert r["source"] == "mongodb"
        assert r["kind"] == "metrics"
        assert r["level_or_status"] == "ok"
        assert "message" in r
        assert "value" in r
        assert "labels" in r
        assert "raw" in r

    def test_executor_failure_returns_empty(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            raise RuntimeError("fail")
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._server_status_records(fake_exec, 1000.0)
        assert records == []

    def test_missing_sections_returns_empty(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {}
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._server_status_records(fake_exec, 1000.0)
        assert records == []

    def test_none_connections_handled(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {"connections": None}
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._server_status_records(fake_exec, 1000.0)
        assert records == []


# ---------------------------------------------------------------------------
# _current_op_records
# ---------------------------------------------------------------------------

class TestCurrentOpRecords:
    def test_counts_inprog_length(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {"inprog": [{}, {}, {}]}
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._current_op_records(fake_exec, 1000.0)
        assert len(records) == 1
        assert records[0]["value"] == 3
        assert records[0]["message"] == "currentOp.active"
        assert records[0]["labels"]["section"] == "currentOp"

    def test_empty_inprog_returns_zero(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {"inprog": []}
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._current_op_records(fake_exec, 1000.0)
        assert records[0]["value"] == 0

    def test_missing_inprog_returns_zero(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {}
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._current_op_records(fake_exec, 1000.0)
        assert records[0]["value"] == 0

    def test_none_inprog_returns_zero(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {"inprog": None}
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._current_op_records(fake_exec, 1000.0)
        assert records[0]["value"] == 0

    def test_executor_failure_returns_empty(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            raise RuntimeError("fail")
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._current_op_records(fake_exec, 1000.0)
        assert records == []


# ---------------------------------------------------------------------------
# _repl_status_records
# ---------------------------------------------------------------------------

class TestReplStatusRecords:
    def test_oplog_lag_computed(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {
                "members": [
                    {"stateStr": "PRIMARY", "optimeDate": 1710000060},
                    {"stateStr": "SECONDARY", "optimeDate": 1710000000},
                ],
            }
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._repl_status_records(fake_exec, 1000.0)
        # order: PRIMARY first (want_primary for reference), then SECONDARY
        # SECONDARY lag = 60 seconds
        secondary_rec = [r for r in records if "SECONDARY" in str(r["labels"].get("state"))]
        assert len(secondary_rec) == 1
        assert secondary_rec[0]["value"] == 60.0

    def test_oplog_lag_zero_when_equal(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {
                "members": [
                    {"stateStr": "PRIMARY", "optimeDate": 1710000000},
                    {"stateStr": "SECONDARY", "optimeDate": 1710000000},
                ],
            }
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._repl_status_records(fake_exec, 1000.0)
        secondary_rec = [r for r in records if "SECONDARY" in str(r["labels"].get("state"))]
        assert secondary_rec[0]["value"] == 0.0

    def test_lag_floor_at_zero(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {
                "members": [
                    {"stateStr": "PRIMARY", "optimeDate": 1710000000},
                    {"stateStr": "SECONDARY", "optimeDate": 1710000060},
                ],
            }
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._repl_status_records(fake_exec, 1000.0)
        secondary_rec = [r for r in records if "SECONDARY" in str(r["labels"].get("state"))]
        assert secondary_rec[0]["value"] == 0.0

    def test_missing_members_returns_empty(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {}
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._repl_status_records(fake_exec, 1000.0)
        assert records == []

    def test_none_members_returns_empty(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {"members": None}
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._repl_status_records(fake_exec, 1000.0)
        assert records == []

    def test_empty_members_returns_empty(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {"members": []}
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._repl_status_records(fake_exec, 1000.0)
        assert records == []

    def test_member_name_and_state_in_labels(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {
                "members": [
                    {"stateStr": "PRIMARY", "name": "node1:27017", "optimeDate": 1710000000},
                ],
            }
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._repl_status_records(fake_exec, 1000.0)
        assert records[0]["labels"]["member"] == "node1:27017"
        assert records[0]["labels"]["state"] == "PRIMARY"

    def test_status_uses_state_lowercase(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {
                "members": [
                    {"stateStr": "PRIMARY", "name": "n1", "optimeDate": 1710000000},
                ],
            }
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._repl_status_records(fake_exec, 1000.0)
        assert records[0]["level_or_status"] == "primary"

    def test_executor_failure_returns_empty(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            raise RuntimeError("fail")
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._repl_status_records(fake_exec, 1000.0)
        assert records == []

    def test_member_without_optime_has_none_lag(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {
                "members": [
                    {"stateStr": "PRIMARY", "optimeDate": 1710000000},
                    {"stateStr": "SECONDARY", "name": "lagging"},
                ],
            }
        src = MongoDbStatsSource(executor=fake_exec)
        records = src._repl_status_records(fake_exec, 1000.0)
        secondary = [r for r in records if "SECONDARY" in str(r["labels"].get("state"))]
        assert len(secondary) == 1
        assert secondary[0]["value"] is None


# ---------------------------------------------------------------------------
# query() — integration through all three record methods
# ---------------------------------------------------------------------------

class TestQueryFull:
    def test_query_returns_all_sections(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            docs: dict[str, object] = {
                "serverStatus": {
                    "connections": {"current": 5},
                    "opcounters": {"insert": 10},
                    "wiredTiger": {"cache": {"bytes currently in the cache": 512}},
                },
                "currentOp": {"inprog": [{}, {}]},
                "replSetGetStatus": {
                    "members": [
                        {"stateStr": "PRIMARY", "name": "p", "optimeDate": 1710000000},
                    ],
                },
            }
            return docs.get(command, {})

        src = MongoDbStatsSource(executor=fake_exec)
        records = src.query()

        sections = {r["labels"]["section"] for r in records}
        assert "connections" in sections
        assert "opcounters" in sections
        assert "wiredTiger" in sections
        assert "currentOp" in sections
        assert "replication" in sections
        # 1 conn + 1 opcounter + 1 wiredTiger + 1 currentOp + 1 repl = 5
        assert len(records) == 5

    def test_query_custom_source_name(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {"connections": {"current": 1}}

        src = MongoDbStatsSource(config={"name": "my_mongo"}, executor=fake_exec)
        records = src.query()
        assert records[0]["source"] == "my_mongo"


# ---------------------------------------------------------------------------
# MongoDbStatsSource — class attributes and instantiation
# ---------------------------------------------------------------------------

class TestInstantiation:
    def test_source_is_instantiable(self) -> None:
        MongoDbStatsSource()

    def test_source_is_instantiable_with_config(self) -> None:
        MongoDbStatsSource(config={"name": "test"})

    def test_source_is_instantiable_with_executor(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {}
        MongoDbStatsSource(executor=fake_exec)

    def test_source_is_instantiable_with_both(self) -> None:
        def fake_exec(command: str) -> Mapping[str, object]:
            return {}
        MongoDbStatsSource(config={"name": "test"}, executor=fake_exec)

    def test_config_not_mutated_externally(self) -> None:
        config: dict[str, object] = {"name": "original"}
        src = MongoDbStatsSource(config=config)
        config.clear()
        assert src.name == "original"

    def test_kind_is_class_attribute_not_instance(self) -> None:
        assert MongoDbStatsSource.KIND == "metrics"
        src = MongoDbStatsSource()
        assert src.KIND == "metrics"
