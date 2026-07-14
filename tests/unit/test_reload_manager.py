"""Tests for reload.manager: ReloadManager, ReloadType, ReloadResult, ReloadStatus."""

from __future__ import annotations

from general_ludd.reload.manager import (
    ReloadManager,
    ReloadResult,
    ReloadStatus,
    ReloadType,
)


class TestReloadType:
    def test_members_exist(self):
        assert ReloadType.CONFIG.value == "config"
        assert ReloadType.PROMPTS.value == "prompts"
        assert ReloadType.RULES.value == "rules"
        assert ReloadType.WORKER_CODE.value == "worker_code"
        assert ReloadType.EVENT_LOOP_CODE.value == "event_loop_code"
        assert ReloadType.SCHEMA_MIGRATION.value == "schema_migration"

    def test_all_six_members(self):
        members = set(rt.value for rt in ReloadType)
        expected = {"config", "prompts", "rules", "worker_code", "event_loop_code", "schema_migration"}
        assert members == expected


class TestReloadResult:
    def test_construction(self):
        rr = ReloadResult(
            reload_id="abc123",
            reload_type=ReloadType.CONFIG,
            status="pending",
            message="test",
        )
        assert rr.reload_id == "abc123"
        assert rr.reload_type == ReloadType.CONFIG
        assert rr.status == "pending"
        assert rr.message == "test"
        assert rr.timestamp != ""


class TestReloadStatus:
    def test_construction(self):
        rs = ReloadStatus(
            reload_id="abc",
            type=ReloadType.PROMPTS,
            status="running",
            started_at="2025-01-01T00:00:00Z",
        )
        assert rs.reload_id == "abc"
        assert rs.type == ReloadType.PROMPTS
        assert rs.status == "running"
        assert rs.completed_at is None

    def test_with_completed_at(self):
        rs = ReloadStatus(
            reload_id="r1",
            type=ReloadType.RULES,
            status="success",
            started_at="t1",
            completed_at="t2",
        )
        assert rs.completed_at == "t2"


class TestReloadManager:
    def test_initial_state(self):
        manager = ReloadManager()
        assert manager._reload_store == {}

    def test_request_reload_creates_entry(self):
        manager = ReloadManager()
        result = manager.request_reload(ReloadType.CONFIG)
        assert result.status == "pending"
        assert result.reload_type == ReloadType.CONFIG
        assert result.reload_id in manager._reload_store

        entry = manager._reload_store[result.reload_id]
        assert entry["status"] == "pending"
        assert entry["reload_type"] == ReloadType.CONFIG

    def test_request_reload_with_config(self):
        manager = ReloadManager()
        config = {"key": "value"}
        result = manager.request_reload(ReloadType.PROMPTS, config=config)
        entry = manager._reload_store[result.reload_id]
        assert entry["config"] == config

    def test_request_reload_generates_unique_ids(self):
        manager = ReloadManager()
        r1 = manager.request_reload(ReloadType.CONFIG)
        r2 = manager.request_reload(ReloadType.CONFIG)
        assert r1.reload_id != r2.reload_id

    def test_execute_reload_updates_status(self):
        manager = ReloadManager()
        result = manager.request_reload(ReloadType.RULES)
        exec_result = manager.execute_reload(result.reload_id)
        assert exec_result.status == "no_op"
        assert "no-op" in exec_result.message.lower() or "not performed" in exec_result.message.lower()

        entry = manager._reload_store[result.reload_id]
        assert entry["status"] == "no_op"

    def test_execute_reload_unknown_id(self):
        manager = ReloadManager()
        result = manager.execute_reload("nonexistent")
        assert result.status == "failed"
        assert "Unknown" in result.message

    def test_rollback_updates_status(self):
        manager = ReloadManager()
        result = manager.request_reload(ReloadType.WORKER_CODE)
        rollback_result = manager.rollback(result.reload_id)
        assert rollback_result.status == "rolled_back"

        entry = manager._reload_store[result.reload_id]
        assert entry["status"] == "rolled_back"

    def test_rollback_unknown_id(self):
        manager = ReloadManager()
        result = manager.rollback("nonexistent")
        assert result.status == "failed"
        assert "Unknown" in result.message

    def test_get_reload_status_known(self):
        manager = ReloadManager()
        result = manager.request_reload(ReloadType.EVENT_LOOP_CODE)
        status = manager.get_reload_status(result.reload_id)
        assert status.reload_id == result.reload_id
        assert status.type == ReloadType.EVENT_LOOP_CODE
        assert status.status == "pending"

    def test_get_reload_status_unknown(self):
        manager = ReloadManager()
        status = manager.get_reload_status("unknown-id")
        assert status.reload_id == "unknown-id"
        assert status.status == "unknown"

    def test_execute_then_rollback(self):
        manager = ReloadManager()
        result = manager.request_reload(ReloadType.SCHEMA_MIGRATION)
        manager.execute_reload(result.reload_id)
        manager.rollback(result.reload_id)
        entry = manager._reload_store[result.reload_id]
        assert entry["status"] == "rolled_back"

    def test_multiple_reloads_maintained(self):
        manager = ReloadManager()
        r1 = manager.request_reload(ReloadType.CONFIG)
        r2 = manager.request_reload(ReloadType.PROMPTS)
        r3 = manager.request_reload(ReloadType.RULES)
        assert len(manager._reload_store) == 3
        assert r1.reload_id in manager._reload_store
        assert r2.reload_id in manager._reload_store
        assert r3.reload_id in manager._reload_store
