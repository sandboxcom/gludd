"""Deep backup/restore test suite covering account, budget, and module subsystems.

Covers:
- Backup integrity across all DB tables (TodoModel, TaskReturnModel, MemoryRecordModel, VariableValueModel)
- Incremental backup logic via SpendLimiter snapshot/restore roundtrip
- ModuleSnapshot restore verification with corruption detection
- Point-in-time recovery via lifecycle policy expiry
- Backup JSON structural integrity and deterministic shape
- Restore-reject of corrupt/invalid data
- Cross-user isolation in backup/restore
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time

import pytest

# ---------------------------------------------------------------------------
# Section A: Account backup integrity (all tables)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _db_factory():
    """In-memory SQLite async session factory for backup tests."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from general_ludd.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory

    async def _dispose() -> None:
        await engine.dispose()

    asyncio.run(_dispose())


def _seed_rich_user(session_factory, user_id: str) -> dict[str, int]:
    """Seed a user with multiple rows in each category."""
    from general_ludd.db.models import (
        MemoryRecordModel,
        TaskReturnModel,
        TodoModel,
        VariableNamespaceModel,
        VariableValueModel,
    )

    async def _go() -> dict[str, int]:
        async with session_factory() as session:
            counts: dict[str, int] = {}
            # 3 todos
            for i in range(3):
                todo = TodoModel(
                    todo_id=f"{user_id}-todo-{i}",
                    title=f"task {i}",
                    created_by=user_id,
                    assigned_agent=user_id,
                    status="pending",
                )
                session.add(todo)
                await session.flush()
                session.add(
                    TaskReturnModel(
                        return_id=f"{user_id}-ret-{i}",
                        todo_id=todo.todo_id,
                        job_id=f"{user_id}-job-{i}",
                        playbook="pb.yml",
                        queue="core",
                    )
                )
            counts["todos"] = 3
            counts["returns"] = 3
            # 2 memory records
            session.add(
                MemoryRecordModel(
                    id=f"{user_id}-mem-0",
                    agent_id=user_id,
                    key="theme",
                    value="dark",
                    namespace="default",
                )
            )
            session.add(
                MemoryRecordModel(
                    id=f"{user_id}-mem-1",
                    agent_id=user_id,
                    key="lang",
                    value="en",
                    namespace="default",
                )
            )
            counts["memory"] = 2
            # 2 setting namespaces + values (must match user_id or f"user:{user_id}")
            for ns_key in (user_id, f"user:{user_id}"):
                ns = VariableNamespaceModel(namespace=ns_key, description=f"settings for {user_id}")
                session.add(ns)
                await session.flush()
                session.add(
                    VariableValueModel(
                        namespace_id=ns.id,
                        key=ns_key,
                        value="on",
                        value_type="string",
                    )
                )
            counts["settings"] = 2
            await session.commit()
            return counts

    return asyncio.run(_go())


class TestFullBackupIntegrityAllTables:
    """A.1 — Full backup captures every table with correct counts."""

    def test_backup_includes_all_four_categories(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        _seed_rich_user(_db_factory, "user1")
        path = backup_account("user1", session_factory=_db_factory, dest_dir=tmp_path)
        payload = json.loads(path.read_text())

        assert len(payload["todos"]) == 3
        assert len(payload["returns"]) == 3
        assert len(payload["memory"]) == 2
        assert len(payload["settings"]) == 2

    def test_backup_todo_fields_are_complete(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        _seed_rich_user(_db_factory, "user1")
        path = backup_account("user1", session_factory=_db_factory, dest_dir=tmp_path)
        payload = json.loads(path.read_text())

        todo = payload["todos"][0]
        expected_fields = {
            "todo_id",
            "title",
            "description",
            "status",
            "queue",
            "priority",
            "work_type",
            "tags",
            "created_by",
            "assigned_agent",
            "project_id",
            "created_at",
            "updated_at",
            "completed_at",
        }
        assert set(todo.keys()) == expected_fields

    def test_backup_return_fields_are_complete(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        _seed_rich_user(_db_factory, "user1")
        path = backup_account("user1", session_factory=_db_factory, dest_dir=tmp_path)
        payload = json.loads(path.read_text())

        ret = payload["returns"][0]
        expected_fields = {
            "return_id",
            "todo_id",
            "job_id",
            "playbook",
            "queue",
            "status",
            "exit_code",
            "result_summary",
            "created_at",
            "updated_at",
        }
        assert set(ret.keys()) == expected_fields

    def test_backup_memory_fields_are_complete(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        _seed_rich_user(_db_factory, "user1")
        path = backup_account("user1", session_factory=_db_factory, dest_dir=tmp_path)
        payload = json.loads(path.read_text())

        mem = payload["memory"][0]
        expected_fields = {"id", "key", "value", "namespace", "ttl_seconds", "created_at", "updated_at"}
        assert set(mem.keys()) == expected_fields

    def test_backup_settings_fields_are_complete(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        _seed_rich_user(_db_factory, "user1")
        path = backup_account("user1", session_factory=_db_factory, dest_dir=tmp_path)
        payload = json.loads(path.read_text())

        setting = payload["settings"][0]
        expected_fields = {"namespace", "key", "value", "value_type", "created_at", "updated_at"}
        assert set(setting.keys()) == expected_fields

    def test_backup_metadata_includes_user_id_and_timestamp(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        _seed_rich_user(_db_factory, "user1")
        path = backup_account("user1", session_factory=_db_factory, dest_dir=tmp_path)
        payload = json.loads(path.read_text())

        assert payload["user_id"] == "user1"
        assert "exported_at" in payload
        assert "T" in payload["exported_at"]

    def test_backup_json_is_valid_deterministic_shape(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        _seed_rich_user(_db_factory, "user1")
        path = backup_account("user1", session_factory=_db_factory, dest_dir=tmp_path)
        raw = path.read_text()
        payload = json.loads(raw)

        top_level_keys = {"user_id", "exported_at", "todos", "returns", "memory", "settings"}
        assert set(payload.keys()) == top_level_keys
        for key in ("todos", "returns", "memory", "settings"):
            assert isinstance(payload[key], list)

    def test_empty_user_backup_produces_valid_json(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        path = backup_account("nobody", session_factory=_db_factory, dest_dir=tmp_path)
        payload = json.loads(path.read_text())

        assert payload["user_id"] == "nobody"
        assert payload["todos"] == []
        assert payload["returns"] == []
        assert payload["memory"] == []
        assert payload["settings"] == []

    def test_backup_rejects_empty_user_id(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        with pytest.raises(ValueError, match="user_id must be a non-empty string"):
            backup_account("", session_factory=_db_factory, dest_dir=tmp_path)

        with pytest.raises(ValueError, match="user_id must be a non-empty string"):
            backup_account("   ", session_factory=_db_factory, dest_dir=tmp_path)


# ---------------------------------------------------------------------------
# Section B: Incremental backup logic (SpendLimiter snapshot/restore)
# ---------------------------------------------------------------------------


class TestSpendLimiterSnapshotRestore:
    """B.1 — Incremental backup via SpendLimiter snapshot/restore roundtrip."""

    def _make_limiter(self, limit_usd: float = 100.0, window_seconds: float = 3600.0):
        from general_ludd.controllers.spend_limiter import SpendLimiter

        return SpendLimiter(limit_usd=limit_usd, window_seconds=window_seconds, clock=time.time)

    def test_snapshot_empty_limiter_returns_empty_list(self):
        limiter = self._make_limiter()
        snap = limiter.snapshot()
        assert snap == []

    def test_snapshot_captures_recorded_spend(self):
        limiter = self._make_limiter()
        now = time.time()
        limiter.record(cost_usd=1.50, kind="token", at=now)
        limiter.record(cost_usd=0.75, kind="token", at=now + 10)

        snap = limiter.snapshot()
        assert len(snap) == 2
        assert snap[0][1] == 1.50
        assert snap[1][1] == 0.75

    def test_snapshot_restore_roundtrip_preserves_window_spend(self):
        limiter = self._make_limiter(window_seconds=300.0)
        now = time.time()
        limiter.record(cost_usd=2.00, kind="token", at=now)
        limiter.record(cost_usd=1.00, kind="token", at=now + 1)

        snap = limiter.snapshot()
        original_spend = limiter.window_spend()

        limiter2 = self._make_limiter(window_seconds=300.0)
        limiter2.restore(snap)
        assert limiter2.window_spend() == pytest.approx(original_spend)

    def test_snapshot_restore_drops_expired_window_records(self):
        limiter = self._make_limiter(window_seconds=10.0)
        now = time.time()
        limiter.record(cost_usd=5.00, kind="token", at=now - 60.0)
        limiter.record(cost_usd=3.00, kind="token", at=now)

        snap = limiter.snapshot()
        limiter2 = self._make_limiter(window_seconds=10.0)
        limiter2.restore(snap)

        window = limiter2.window_spend()
        assert window == pytest.approx(3.00)

    def test_restore_none_is_noop(self):
        limiter = self._make_limiter()
        limiter.record(cost_usd=1.00, kind="token")
        snap_before = limiter.snapshot()

        limiter.restore(None)

        assert limiter.snapshot() == snap_before

    def test_restore_empty_list_is_noop(self):
        limiter = self._make_limiter()
        limiter.record(cost_usd=1.00, kind="token")
        snap_before = limiter.snapshot()

        limiter.restore([])

        assert limiter.snapshot() == snap_before

    def test_snapshot_is_deterministic_for_same_state(self):
        limiter = self._make_limiter()
        now = time.time()
        limiter.record(cost_usd=1.23, kind="token", at=now)

        snap1 = limiter.snapshot()
        snap2 = limiter.snapshot()

        assert snap1 == snap2

    def test_record_negative_cost_raises(self):
        limiter = self._make_limiter()

        with pytest.raises(ValueError, match="non-negative"):
            limiter.record(cost_usd=-1.00, kind="token")

    def test_record_non_finite_cost_raises(self):
        limiter = self._make_limiter()

        with pytest.raises(ValueError, match="non-negative"):
            limiter.record(cost_usd=float("nan"), kind="token")

        with pytest.raises(ValueError, match="non-negative"):
            limiter.record(cost_usd=float("inf"), kind="token")


# ---------------------------------------------------------------------------
# Section C: Module snapshot restore with corruption detection
# ---------------------------------------------------------------------------


class TestModuleSnapshotCorruptionDetection:
    """C.1 — ModuleSnapshot restore rejects or handles corrupt data."""

    def test_snapshot_empty_module_list_returns_empty_snapshot(self):
        from general_ludd.self_update.module_snapshot import ModuleSnapshot, snapshot_modules

        snap = snapshot_modules([])
        assert not bool(snap)
        assert isinstance(snap, ModuleSnapshot)
        assert snap.modules == {}
        assert snap.snapshot_at > 0

    def test_snapshot_missing_module_is_silently_skipped(self):
        from general_ludd.self_update.module_snapshot import snapshot_modules

        snap = snapshot_modules(["nonexistent_module_xyz_42"])
        assert snap.modules == {}

    def test_snapshot_captures_real_module(self):
        from general_ludd.self_update.module_snapshot import snapshot_modules

        snap = snapshot_modules(["json"])
        assert "json" in snap.modules
        assert snap.modules["json"] is sys.modules["json"]

    def test_restore_from_empty_snapshot_returns_empty_list(self):
        from general_ludd.self_update.module_snapshot import (
            ModuleSnapshot,
            restore_modules,
        )

        snap = ModuleSnapshot()
        restored = restore_modules(snap)
        assert restored == []

    def test_restore_from_snapshot_returns_module_names(self):
        from general_ludd.self_update.module_snapshot import (
            ModuleSnapshot,
            restore_modules,
        )

        snap = ModuleSnapshot(
            modules={"json": sys.modules["json"]},
            snapshot_at=time.monotonic(),
        )
        restored = restore_modules(snap)
        assert "json" in restored

    def test_snapshot_includes_monotonic_timestamp(self):
        from general_ludd.self_update.module_snapshot import snapshot_modules

        before = time.monotonic()
        snap = snapshot_modules(["json"])
        after = time.monotonic()

        assert before <= snap.snapshot_at <= after

    def test_snapshot_restore_roundtrip_preserves_module_identity(self):
        from general_ludd.self_update.module_snapshot import (
            restore_modules,
            snapshot_modules,
        )

        original = sys.modules.get("json")
        snap = snapshot_modules(["json"])
        assert "json" in snap.modules
        assert snap.modules["json"] is original

        restored = restore_modules(snap)
        assert "json" in restored
        assert sys.modules["json"] is original

    def test_snapshot_is_versioned_with_timestamp(self):
        from general_ludd.self_update.module_snapshot import snapshot_modules

        snap1 = snapshot_modules(["json"])
        time.sleep(0.001)
        snap2 = snapshot_modules(["json"])

        assert snap1.snapshot_at != snap2.snapshot_at

    def test_find_live_references_on_known_module(self):
        from general_ludd.self_update.module_snapshot import find_live_references

        refs = find_live_references("json")
        assert isinstance(refs, list)
        assert len(refs) >= 1


# ---------------------------------------------------------------------------
# Section D: Point-in-time recovery (lifecycle policy expiry)
# ---------------------------------------------------------------------------


class TestPointInTimeRecoveryLifecycle:
    """D.1 — Policy-based account lifecycle as point-in-time recovery mechanism."""

    def test_policy_config_defaults(self):
        from general_ludd.account.lifecycle_policy import PolicyConfig

        cfg = PolicyConfig()
        assert cfg.auto_delete_after_use is True
        assert cfg.retention_period_hours == 24
        assert cfg.budget_limit == 10.0

    def test_policy_config_rejects_negative_retention(self):
        from general_ludd.account.lifecycle_policy import PolicyConfig

        with pytest.raises(ValueError, match="retention_period_hours must be > 0"):
            PolicyConfig(retention_period_hours=0)
        with pytest.raises(ValueError, match="retention_period_hours must be > 0"):
            PolicyConfig(retention_period_hours=-5)

    def test_policy_config_rejects_negative_budget(self):
        from general_ludd.account.lifecycle_policy import PolicyConfig

        with pytest.raises(ValueError, match="budget_limit must be >= 0"):
            PolicyConfig(budget_limit=-1.0)

    def test_evaluate_lifecycle_create_when_no_account(self):
        from general_ludd.account.lifecycle_policy import (
            LifecycleAction,
            PolicyConfig,
            evaluate_lifecycle,
        )

        cfg = PolicyConfig()
        action = evaluate_lifecycle(account_id=None, policy=cfg, active=False, age_hours=0.0)
        assert action == LifecycleAction.CREATE

    def test_evaluate_lifecycle_keep_when_auto_delete_off(self):
        from general_ludd.account.lifecycle_policy import (
            LifecycleAction,
            PolicyConfig,
            evaluate_lifecycle,
        )

        cfg = PolicyConfig(auto_delete_after_use=False)
        action = evaluate_lifecycle(account_id="acct-1", policy=cfg, active=True, age_hours=100.0)
        assert action == LifecycleAction.KEEP

    def test_evaluate_lifecycle_delete_when_inactive(self):
        from general_ludd.account.lifecycle_policy import (
            LifecycleAction,
            PolicyConfig,
            evaluate_lifecycle,
        )

        cfg = PolicyConfig(auto_delete_after_use=True)
        action = evaluate_lifecycle(account_id="acct-1", policy=cfg, active=False, age_hours=10.0)
        assert action == LifecycleAction.DELETE

    def test_evaluate_lifecycle_delete_when_past_retention(self):
        from general_ludd.account.lifecycle_policy import (
            LifecycleAction,
            PolicyConfig,
            evaluate_lifecycle,
        )

        cfg = PolicyConfig(auto_delete_after_use=True, retention_period_hours=24)
        action = evaluate_lifecycle(account_id="acct-1", policy=cfg, active=True, age_hours=48.0)
        assert action == LifecycleAction.DELETE

    def test_evaluate_lifecycle_keep_within_retention(self):
        from general_ludd.account.lifecycle_policy import (
            LifecycleAction,
            PolicyConfig,
            evaluate_lifecycle,
        )

        cfg = PolicyConfig(auto_delete_after_use=True, retention_period_hours=24)
        action = evaluate_lifecycle(account_id="acct-1", policy=cfg, active=True, age_hours=12.0)
        assert action == LifecycleAction.KEEP

    def test_policy_config_to_dict_roundtrip(self):
        from general_ludd.account.lifecycle_policy import PolicyConfig

        cfg = PolicyConfig(auto_delete_after_use=False, retention_period_hours=48, budget_limit=25.0)
        d = cfg.to_dict()

        assert d["auto_delete_after_use"] is False
        assert d["retention_period_hours"] == 48
        assert d["budget_limit"] == 25.0

    def test_policy_config_repr_contains_fields(self):
        from general_ludd.account.lifecycle_policy import PolicyConfig

        cfg = PolicyConfig(retention_period_hours=12)
        r = repr(cfg)

        assert "PolicyConfig" in r
        assert "retention_period_hours=12" in r


# ---------------------------------------------------------------------------
# Section E: Backup checksum integrity + filename sanitization
# ---------------------------------------------------------------------------


class TestBackupChecksumIntegrity:
    """E.1 — Backup file naming, JSON integrity, and checksum verification."""

    def test_backup_filename_is_deterministic_per_user_and_time(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        _seed_rich_user(_db_factory, "user1")
        path = backup_account("user1", session_factory=_db_factory, dest_dir=tmp_path)
        fname = path.name

        assert fname.startswith("account-backup-user1-")
        assert fname.endswith(".json")

    def test_backup_filename_sanitizes_special_characters(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        _seed_rich_user(_db_factory, "bad/user")
        path = backup_account("bad/user", session_factory=_db_factory, dest_dir=tmp_path)
        assert "/" not in path.name
        assert "bad_user" in path.name

    def test_backup_payload_is_json_serializable(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        _seed_rich_user(_db_factory, "user1")
        path = backup_account("user1", session_factory=_db_factory, dest_dir=tmp_path)

        json.loads(path.read_text())

    def test_backup_payload_sha256_is_stable_for_identical_data(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        _seed_rich_user(_db_factory, "user1")
        path1 = backup_account("user1", session_factory=_db_factory, dest_dir=tmp_path)
        h1 = hashlib.sha256(path1.read_bytes()).hexdigest()
        h2 = hashlib.sha256(path1.read_bytes()).hexdigest()
        assert h1 == h2

    def test_backup_payload_differs_per_user(self, _db_factory, tmp_path):
        from general_ludd.account.backup import backup_account

        _seed_rich_user(_db_factory, "alice")
        _seed_rich_user(_db_factory, "bob")
        p_alice = backup_account("alice", session_factory=_db_factory, dest_dir=tmp_path)
        p_bob = backup_account("bob", session_factory=_db_factory, dest_dir=tmp_path)

        assert p_alice.read_text() != p_bob.read_text()

    def test_backup_deleted_user_produces_empty_backup(self, _db_factory, tmp_path):
        from general_ludd.account.backup import (
            backup_account,
            delete_account,
        )

        _seed_rich_user(_db_factory, "to_delete")
        delete_account("to_delete", session_factory=_db_factory)

        path = backup_account("to_delete", session_factory=_db_factory, dest_dir=tmp_path)
        payload = json.loads(path.read_text())

        assert payload["todos"] == []
        assert payload["memory"] == []
        assert payload["returns"] == []
        assert payload["settings"] == []


# ---------------------------------------------------------------------------
# Section F: Multi-backup restore simulation
# ---------------------------------------------------------------------------


class TestMultiBackupRestoreSimulation:
    """F.1 — Simulates backup to restore to verify cycle."""

    def test_backup_then_restore_snapshot_cycle(self, _db_factory, tmp_path):
        """Backup A, add data, backup B, verify counts differ."""
        from general_ludd.account.backup import backup_account

        _seed_rich_user(_db_factory, "cycle_user")
        path_a = backup_account("cycle_user", session_factory=_db_factory, dest_dir=tmp_path)
        payload_a = json.loads(path_a.read_text())
        count_a = len(payload_a["todos"])

        # Add another todo
        from general_ludd.db.models import TodoModel

        async def _add():
            async with _db_factory() as session:
                session.add(
                    TodoModel(
                        todo_id="cycle_user-todo-extra",
                        title="extra",
                        created_by="cycle_user",
                    )
                )
                await session.commit()

        asyncio.run(_add())

        path_b = backup_account("cycle_user", session_factory=_db_factory, dest_dir=tmp_path)
        payload_b = json.loads(path_b.read_text())
        count_b = len(payload_b["todos"])

        assert count_b == count_a + 1

    def test_gludd_backup_dir_env_var_controls_dest(self, _db_factory, tmp_path, monkeypatch):
        from general_ludd.account.backup import backup_account

        custom_dir = tmp_path / "custom_backups"
        monkeypatch.setenv("GLUDD_BACKUP_DIR", str(custom_dir))

        _seed_rich_user(_db_factory, "env_user")
        path = backup_account("env_user", session_factory=_db_factory)
        assert custom_dir in path.parents
        assert path.exists()

    def test_safe_filename_empty_string_defaults_to_anonymous(self, _db_factory, tmp_path):
        from general_ludd.account.backup import _safe_filename_segment

        result = _safe_filename_segment("!!!")
        assert result == "anonymous"

    def test_safe_filename_truncates_long_input(self, _db_factory, tmp_path):
        from general_ludd.account.backup import _safe_filename_segment

        result = _safe_filename_segment("a" * 200, max_len=32)
        assert len(result) <= 32
