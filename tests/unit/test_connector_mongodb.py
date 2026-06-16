"""Unit tests for MongoDbStatsSource — canned executor, no real database."""

from __future__ import annotations

from typing import Any

from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

# A canned serverStatus / currentOp / replSetGetStatus fixture.
_SERVER_STATUS = {
    "connections": {"current": 12, "available": 988, "active": 5},
    "opcounters": {"insert": 100, "query": 2000, "update": 30},
    "wiredTiger": {
        "cache": {
            "bytes currently in the cache": 5_000_000,
            "maximum bytes configured": 10_000_000,
        }
    },
}
_CURRENT_OP = {"inprog": [{"op": "query"}, {"op": "insert"}]}
_REPL_STATUS = {
    "members": [
        {"name": "node-a:27017", "stateStr": "PRIMARY", "optimeDate": 1000.0},
        {"name": "node-b:27017", "stateStr": "SECONDARY", "optimeDate": 996.5},
    ]
}


def _canned_executor(command: str) -> dict[str, Any]:
    return {
        "serverStatus": _SERVER_STATUS,
        "currentOp": _CURRENT_OP,
        "replSetGetStatus": _REPL_STATUS,
    }[command]


def test_contract_attrs() -> None:
    src = MongoDbStatsSource({"name": "mongo-1"}, executor=_canned_executor)
    assert src.KIND == "metrics"
    assert src.name == "mongo-1"


def test_query_normalizes_records() -> None:
    src = MongoDbStatsSource({"name": "mongo-1"}, executor=_canned_executor)
    records = src.query({})

    assert records, "expected metric records"
    required = {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}
    for rec in records:
        assert required <= set(rec), f"missing keys in {rec}"
        assert rec["source"] == "mongo-1"
        assert rec["kind"] == "metrics"


def test_connection_and_opcounter_values() -> None:
    src = MongoDbStatsSource({}, executor=_canned_executor)
    records = src.query({})
    by_msg = {r["message"]: r for r in records}

    assert by_msg["connections.current"]["value"] == 12
    assert by_msg["connections.current"]["labels"]["section"] == "connections"
    assert by_msg["opcounters.query"]["value"] == 2000
    assert by_msg["wiredTiger.cache.bytes currently in the cache"]["value"] == 5_000_000


def test_current_op_active_count() -> None:
    src = MongoDbStatsSource({}, executor=_canned_executor)
    records = src.query({})
    active = next(r for r in records if r["message"] == "currentOp.active")
    assert active["value"] == 2


def test_replication_oplog_lag() -> None:
    src = MongoDbStatsSource({}, executor=_canned_executor)
    records = src.query({})
    lags = [r for r in records if r["message"] == "replication.oplog_lag_seconds"]
    by_member = {r["labels"]["member"]: r for r in lags}

    # Primary lag is 0; secondary lag = 1000 - 996.5 = 3.5s.
    assert by_member["node-a:27017"]["value"] == 0.0
    assert by_member["node-b:27017"]["value"] == 3.5
    assert by_member["node-b:27017"]["level_or_status"] == "secondary"


def test_health_ok_with_executor() -> None:
    src = MongoDbStatsSource({}, executor=_canned_executor)
    health = src.health()
    assert health["ok"] is True


def test_health_driver_unavailable_without_env(monkeypatch: Any) -> None:
    monkeypatch.delenv("MONGODB_URI", raising=False)
    src = MongoDbStatsSource({})  # no executor, no env -> cannot build default
    health = src.health()
    assert health["ok"] is False
    assert "MONGODB_URI" in health["detail"] or health["detail"] == "driver unavailable"
    # query() must also fail soft (empty), never raise.
    assert src.query({}) == []


def test_health_never_raises_on_bad_executor() -> None:
    def boom(_command: str) -> dict[str, Any]:
        raise RuntimeError("connection refused")

    src = MongoDbStatsSource({}, executor=boom)
    health = src.health()
    assert health["ok"] is False
    assert "serverStatus failed" in health["detail"]
    # query() swallows per-command errors and returns [].
    assert src.query({}) == []


def test_no_hardcoded_credentials() -> None:
    import inspect

    from general_ludd.connectors import mongodb_stats

    text = inspect.getsource(mongodb_stats)
    lowered = text.lower()
    assert "password" not in lowered or "password_env" in lowered
    assert "mongodb://" not in lowered  # no embedded connection string
    assert "shell=True" not in text.replace(" ", "")
    assert "import subprocess" not in text
