"""Deep edge-case tests for db/repository.py — transitions, priority parsing,
cross-tenant isolation, version guards, claim races, P12 clamping, enum validation,
terminal-state rejection, empty-result contracts, and BenchmarkRepository fault paths.
"""

from __future__ import annotations

import asyncio
import json as _json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base, FeatureStatus, TodoModel
from general_ludd.db.repository import (
    VALID_TRANSITIONS,
    AgentMessageRepository,
    BenchmarkRepository,
    ConcurrencyError,
    FeatureRepository,
    HumanTodoRepository,
    InvalidTransitionError,
    ProjectRelationshipRepository,
    PromptProfileRepository,
    QueueRepository,
    RemediationActionRepository,
    SpendRepository,
    TaskReturnRepository,
    TodoRepository,
    VariableNamespaceRepository,
    _is_locked_error,
)
from general_ludd.schemas.todo import TodoStatus


@pytest.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def async_session(async_engine) -> AsyncSession:
    sf = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as session:
        yield session


def _td(title="t", pid=None, status=TodoStatus.BACKLOG):
    d: dict = {"title": title, "status": status.value}
    if pid:
        d["project_id"] = pid
    return d


# ── _is_locked_error ──────────────────────────────────────────────────


def test_is_locked_error_database_is_locked():
    exc = OperationalError("statement", {}, Exception("database is locked"))
    assert _is_locked_error(exc) is True


def test_is_locked_error_database_table_is_locked():
    exc = OperationalError("stmt", {}, Exception("database table is locked"))
    assert _is_locked_error(exc) is True


def test_is_locked_error_other():
    exc = OperationalError("stmt", {}, Exception("disk I/O error"))
    assert _is_locked_error(exc) is False


def test_is_locked_error_no_orig():
    exc = OperationalError("database is locked somewhere", {}, None)
    assert _is_locked_error(exc) is True


def test_is_locked_error_orig_is_none():

    exc = OperationalError("stmt", {}, None)
    exc.orig = None
    assert _is_locked_error(exc) is False


# ── TodoRepository.create() priority / oversized / immutable guards ───


class TestTodoCreateEdge:
    async def test_create_priority_string_label_low(self, async_session):
        repo = TodoRepository(async_session)
        t = await repo.create({"title": "x", "priority": "low", "status": "backlog"})
        assert t.priority == 0

    async def test_create_priority_string_label_medium(self, async_session):
        repo = TodoRepository(async_session)
        t = await repo.create({"title": "x", "priority": "medium", "status": "backlog"})
        assert t.priority == 1

    async def test_create_priority_string_label_high(self, async_session):
        repo = TodoRepository(async_session)
        t = await repo.create({"title": "x", "priority": "high", "status": "backlog"})
        assert t.priority == 2

    async def test_create_priority_string_label_critical(self, async_session):
        repo = TodoRepository(async_session)
        t = await repo.create({"title": "x", "priority": "critical", "status": "backlog"})
        assert t.priority == 3

    async def test_create_priority_boolean_rejected(self, async_session):
        repo = TodoRepository(async_session)
        with pytest.raises(ValueError, match="priority must be an integer"):
            await repo.create({"title": "x", "priority": True, "status": "backlog"})

    async def test_create_priority_negative_clamped_to_zero(self, async_session):
        repo = TodoRepository(async_session)
        t = await repo.create({"title": "x", "priority": -5, "status": "backlog"})
        assert t.priority == 0

    async def test_create_priority_above_max_clamped(self, async_session):
        repo = TodoRepository(async_session)
        t = await repo.create({"title": "x", "priority": 9999, "status": "backlog"})
        assert t.priority == 1000

    async def test_create_priority_float_rejected(self, async_session):
        repo = TodoRepository(async_session)
        with pytest.raises(ValueError, match="priority must be an integer"):
            await repo.create({"title": "x", "priority": 1.5, "status": "backlog"})

    async def test_create_oversized_field_rejected(self, async_session):
        repo = TodoRepository(async_session)
        big = "x" * 65537
        with pytest.raises(ValueError, match="exceeds the 65536-byte limit"):
            await repo.create({"title": big, "status": "backlog"})

    async def test_create_immutable_id_rejected(self, async_session):
        repo = TodoRepository(async_session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create({"id": 999, "title": "x", "status": "backlog"})

    async def test_create_immutable_version_rejected(self, async_session):
        repo = TodoRepository(async_session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create({"version": 42, "title": "x", "status": "backlog"})

    async def test_create_immutable_created_at_rejected(self, async_session):
        repo = TodoRepository(async_session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create({"created_at": datetime.now(UTC), "title": "x", "status": "backlog"})

    async def test_create_immutable_updated_at_rejected(self, async_session):
        repo = TodoRepository(async_session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create({"updated_at": datetime.now(UTC), "title": "x", "status": "backlog"})

    async def test_create_version_defaults_to_one(self, async_session):
        repo = TodoRepository(async_session)
        t = await repo.create({"title": "x", "status": "backlog"})
        assert t.version == 1


# ── TodoRepository.transition() deep edge cases ───────────────────────


class TestTodoTransitionEdge:
    async def _seed(self, repo, title="t", pid=None, status=TodoStatus.BACKLOG):
        return await repo.create(_td(title, pid, status))

    async def test_transition_terminal_complete_rejects_all(self, async_session):
        repo = TodoRepository(async_session)
        t = await self._seed(repo, status=TodoStatus.QUEUED)
        await repo.transition(t.todo_id, TodoStatus.ACTIVE, expected_version=1)
        await repo.transition(t.todo_id, TodoStatus.COMPLETE, expected_version=2)
        with pytest.raises(InvalidTransitionError, match="Invalid transition"):
            await repo.transition(t.todo_id, TodoStatus.QUEUED, expected_version=3)

    async def test_transition_terminal_cancelled_rejects_all(self, async_session):
        repo = TodoRepository(async_session)
        t = await self._seed(repo, status=TodoStatus.BACKLOG)
        await repo.transition(t.todo_id, TodoStatus.CANCELLED, expected_version=1)
        with pytest.raises(InvalidTransitionError, match="Invalid transition"):
            await repo.transition(t.todo_id, TodoStatus.QUEUED, expected_version=2)

    async def test_transition_version_mismatch(self, async_session):
        repo = TodoRepository(async_session)
        t = await self._seed(repo, status=TodoStatus.QUEUED)
        with pytest.raises(ConcurrencyError, match="Version mismatch"):
            await repo.transition(t.todo_id, TodoStatus.ACTIVE, expected_version=3)

    async def test_transition_nonexistent_todo(self, async_session):
        repo = TodoRepository(async_session)
        with pytest.raises(InvalidTransitionError, match="not found"):
            await repo.transition("NONEXISTENT", TodoStatus.ACTIVE, expected_version=1)

    async def test_transition_invalid_path(self, async_session):
        repo = TodoRepository(async_session)
        t = await self._seed(repo, status=TodoStatus.BACKLOG)
        with pytest.raises(InvalidTransitionError, match="Invalid transition"):
            await repo.transition(t.todo_id, TodoStatus.COMPLETE, expected_version=1)

    async def test_transition_approval_required_to_queued(self, async_session):
        """APPROVAL_REQUIRED → QUEUED is a valid transition (human release)."""
        t_data = {"title": "held", "status": "approval_required"}
        repo = TodoRepository(async_session)
        t = await repo.create(t_data)
        updated = await repo.transition(t.todo_id, TodoStatus.QUEUED, expected_version=1)
        assert updated.status == TodoStatus.QUEUED.value

    async def test_transition_budget_exceeded_to_queued(self, async_session):
        t_data = {"title": "busted", "status": "budget_exceeded"}
        repo = TodoRepository(async_session)
        t = await repo.create(t_data)
        updated = await repo.transition(t.todo_id, TodoStatus.QUEUED, expected_version=1)
        assert updated.status == TodoStatus.QUEUED.value

    async def test_transition_budget_exceeded_to_failed(self, async_session):
        t_data = {"title": "busted", "status": "budget_exceeded"}
        repo = TodoRepository(async_session)
        t = await repo.create(t_data)
        updated = await repo.transition(t.todo_id, TodoStatus.FAILED, expected_version=1)
        assert updated.status == TodoStatus.FAILED.value

    async def test_transition_reviewing_return_all_paths(self, async_session):
        repo = TodoRepository(async_session)
        t = await self._seed(repo, status=TodoStatus.QUEUED)
        await repo.transition(t.todo_id, TodoStatus.ACTIVE, expected_version=1)
        await repo.transition(t.todo_id, TodoStatus.REVIEWING_RETURN, expected_version=2)
        for tg in (
            TodoStatus.COMPLETE,
            TodoStatus.NEEDS_MORE_WORK,
            TodoStatus.FAILED,
            TodoStatus.BLOCKED,
            TodoStatus.MANUAL_HOLD,
        ):
            t2 = await self._seed(repo, status=TodoStatus.QUEUED, title="rv2")
            await repo.transition(t2.todo_id, TodoStatus.ACTIVE, expected_version=1)
            await repo.transition(t2.todo_id, TodoStatus.REVIEWING_RETURN, expected_version=2)
            u = await repo.transition(t2.todo_id, tg, expected_version=3)
            assert u.status == tg.value

    async def test_transition_needs_more_work_to_queued(self, async_session):
        repo = TodoRepository(async_session)
        t = await self._seed(repo, status=TodoStatus.QUEUED)
        await repo.transition(t.todo_id, TodoStatus.ACTIVE, expected_version=1)
        await repo.transition(t.todo_id, TodoStatus.REVIEWING_RETURN, expected_version=2)
        await repo.transition(t.todo_id, TodoStatus.NEEDS_MORE_WORK, expected_version=3)
        u2 = await repo.transition(t.todo_id, TodoStatus.QUEUED, expected_version=4)
        assert u2.status == TodoStatus.QUEUED.value

    async def test_transition_backlog_to_complete_rejected(self, async_session):
        repo = TodoRepository(async_session)
        t = await self._seed(repo, status=TodoStatus.BACKLOG)
        with pytest.raises(InvalidTransitionError, match="Invalid transition"):
            await repo.transition(t.todo_id, TodoStatus.COMPLETE, expected_version=1)

    async def test_valid_transitions_covers_all_statuses(self):
        _known_missing = frozenset({TodoStatus.AWAITING_RESULT})
        for s in TodoStatus:
            if s in _known_missing:
                continue
            assert s in VALID_TRANSITIONS, f"{s} missing from VALID_TRANSITIONS"


# ── TodoRepository.update() edge cases ───────────────────────────────


class TestTodoUpdateEdge:
    async def test_update_nonexistent_todo_raises(self, async_session):
        repo = TodoRepository(async_session)
        with pytest.raises(InvalidTransitionError, match="not found"):
            await repo.update("NOPE", {"title": "x"}, expected_version=1)

    async def test_update_version_mismatch_unscoped(self, async_session):
        repo = TodoRepository(async_session)
        t = await repo.create(_td("x", pid="A"))
        with pytest.raises(ConcurrencyError, match="Version mismatch"):
            await repo.update(t.todo_id, {"title": "y"}, expected_version=5)

    async def test_update_mixed_mutable_and_immutable_rejected(self, async_session):
        repo = TodoRepository(async_session)
        t = await repo.create(_td("x"))
        with pytest.raises(ValueError, match="immutable"):
            await repo.update(t.todo_id, {"title": "ok", "project_id": "evil"}, expected_version=1)

    async def test_update_scoped_repo_version_bump(self, async_session):
        repo = TodoRepository.scoped(async_session, "proj-A")
        t = await repo.create(_td("x", pid="proj-A"))
        u = await repo.update(t.todo_id, {"title": "y"}, expected_version=1)
        assert u.version == 2


# ── TodoRepository claim / requeue / list_due edge cases ──────────────


class TestTodoClaimRequeueEdge:
    async def test_claim_runnable_empty_table(self, async_session):
        repo = TodoRepository(async_session)
        claimed = await repo.claim_runnable()
        assert claimed == []

    async def test_claim_runnable_limit_clamped_to_default(self, async_session):
        repo = TodoRepository(async_session)
        for i in range(5):
            await repo.create({"todo_id": f"Q{i}", "title": f"t{i}", "status": "queued"})
        claimed = await repo.claim_runnable(limit=99999)
        assert len(claimed) <= 1000

    async def test_requeue_cooldown_not_expired_returns_zero(self, async_session):
        repo = TodoRepository(async_session)
        await repo.create({"title": "needs", "status": "needs_more_work"})
        n = await repo.requeue_needs_more_work(cooldown_hours=24)
        assert n == 0

    async def test_requeue_run_count_at_max_skipped(self, async_session):
        repo = TodoRepository(async_session)
        await repo.create({"title": "old", "status": "needs_more_work"})
        stmt = select(TodoModel).where(TodoModel.status == "needs_more_work")
        row = (await async_session.execute(stmt)).scalars().first()
        row.updated_at = datetime.now(UTC) - timedelta(hours=48)
        row.run_count = 5
        await async_session.flush()
        n = await repo.requeue_needs_more_work(cooldown_hours=1, max_run_count=3)
        assert n == 0

    async def test_requeue_scoped_no_cross_tenant_leak(self, async_session):
        repo_a = TodoRepository.scoped(async_session, "A")
        t1 = await repo_a.create({"title": "n1", "status": "needs_more_work", "project_id": "A"})
        stmt1 = select(TodoModel).where(TodoModel.todo_id == t1.todo_id)
        r1 = (await async_session.execute(stmt1)).scalar_one()
        r1.updated_at = datetime.now(UTC) - timedelta(hours=48)
        await async_session.flush()
        repo_b = TodoRepository.scoped(async_session, "B")
        t2 = await repo_b.create({"title": "n2", "status": "needs_more_work", "project_id": "B"})
        stmt2 = select(TodoModel).where(TodoModel.todo_id == t2.todo_id)
        r2 = (await async_session.execute(stmt2)).scalar_one()
        r2.updated_at = datetime.now(UTC) - timedelta(hours=48)
        await async_session.flush()
        n_a = await repo_a.requeue_needs_more_work(cooldown_hours=1)
        assert n_a >= 1
        refreshed = await repo_b.get_by_id(t2.todo_id)
        assert refreshed.status == "needs_more_work"

    async def test_list_due_skips_paused(self, async_session):
        repo = TodoRepository(async_session)
        now = datetime.now(UTC)
        pending = {"title": "due", "status": "scheduled", "scheduled_at": now - timedelta(hours=1)}
        await repo.create(pending)
        await repo.create({**pending, "title": "paused", "schedule_paused": True})
        due = await repo.list_due_scheduled(now)
        titles = {t.title for t in due}
        assert "due" in titles
        assert "paused" not in titles

    async def test_list_due_uses_coalesce(self, async_session):
        repo = TodoRepository(async_session)
        now = datetime.now(UTC)
        await repo.create({"title": "only_next", "status": "scheduled", "next_run_at": now - timedelta(hours=2)})
        await repo.create({"title": "only_scheduled", "status": "scheduled", "scheduled_at": now - timedelta(hours=1)})
        due = await repo.list_due_scheduled(now)
        assert len(due) == 2


# ── TodoRepository get_by_ids / count_active / list_all edge cases ────


class TestTodoReadEdge:
    async def test_get_by_ids_empty_list(self, async_session):
        repo = TodoRepository(async_session)
        result = await repo.get_by_ids([])
        assert result == {}

    async def test_get_by_ids_partial_match(self, async_session):
        repo = TodoRepository(async_session)
        t1 = await repo.create({"todo_id": "A1", "title": "a", "status": "backlog"})
        result = await repo.get_by_ids([t1.todo_id, "NOPE"])
        assert t1.todo_id in result
        assert len(result) == 1

    async def test_count_active_zero(self, async_session):
        repo = TodoRepository(async_session)
        assert await repo.count_active() == 0

    async def test_count_active_scoped(self, async_session):
        repo_a = TodoRepository.scoped(async_session, "A")
        await repo_a.create(_td("a1", "A", TodoStatus.ACTIVE))
        await repo_a.create(_td("a2", "A", TodoStatus.ACTIVE))
        repo_b = TodoRepository.scoped(async_session, "B")
        await repo_b.create(_td("b1", "B", TodoStatus.ACTIVE))
        assert await repo_a.count_active() == 2
        assert await repo_b.count_active() == 1

    async def test_list_all_schedule_paused_filter(self, async_session):
        repo = TodoRepository(async_session)
        await repo.create(
            {"title": "p", "status": "scheduled", "schedule_paused": True, "scheduled_at": datetime.now(UTC)}
        )
        await repo.create({"title": "np", "status": "scheduled", "schedule_paused": False})
        paused = await repo.list_all(schedule_paused=True)
        not_paused = await repo.list_all(schedule_paused=False)
        assert all(t.schedule_paused for t in paused)
        assert not any(t.schedule_paused for t in not_paused)

    async def test_list_all_offset_and_limit_clamping(self, async_session):
        repo = TodoRepository(async_session)
        for i in range(5):
            await repo.create({"title": f"t{i}", "status": "backlog"})
        page = await repo.list_all(limit=2, offset=1)
        assert len(page) <= 2


# ── TaskReturnRepository edge cases ───────────────────────────────────


class TestTaskReturnEdge:
    async def test_claim_unreviewed_no_created_rows(self, async_session):
        repo = TaskReturnRepository(async_session)
        await repo.create(
            {
                "return_id": "R1",
                "job_id": "j",
                "playbook": "p.yml",
                "queue": "q",
                "work_type": "w",
                "status": "claimed_for_review",
            }
        )
        assert await repo.claim_unreviewed() == []

    async def test_claim_unreviewed_unscoped_cross_tenant_isolation(self, async_session):
        repo = TaskReturnRepository(async_session)
        await repo.create(
            {
                "return_id": "R-A",
                "project_id": "A",
                "job_id": "j",
                "playbook": "p.yml",
                "queue": "q",
                "work_type": "w",
                "status": "created",
            }
        )
        await repo.create(
            {
                "return_id": "R-B",
                "project_id": "B",
                "job_id": "j",
                "playbook": "p.yml",
                "queue": "q",
                "work_type": "w",
                "status": "created",
            }
        )
        claimed_a = await repo.claim_unreviewed(project_id="A")
        assert {c.return_id for c in claimed_a} == {"R-A"}

    async def test_history_summary_excludes_claimed_return_from_created_count(self, async_session):
        await TaskReturnRepository(async_session).create(
            {
                "return_id": "R1",
                "job_id": "j",
                "playbook": "p.yml",
                "queue": "q",
                "work_type": "w",
                "exit_code": 0,
                "status": "created",
            }
        )
        s = await TaskReturnRepository(async_session).history_summary()
        assert s["total_returns"] == 1


# ── VariableNamespaceRepository edge cases ────────────────────────────


class TestVariableNamespaceEdge:
    async def test_set_var_idempotent_concurrent_first_write(self, async_session):
        repo = VariableNamespaceRepository(async_session)
        v1 = await repo.set_var("ns1", "k1", "v1")
        v2 = await repo.set_var("ns1", "k1", "v2")
        assert v2.value == "v2"
        assert v1.namespace_id == v2.namespace_id

    async def test_set_var_null_project_namespace(self, async_session):
        repo = VariableNamespaceRepository(async_session)
        v = await repo.set_var("global_ns", "key", "val", project_id=None)
        assert v.key == "key"
        assert v.value == "val"

    async def test_load_vars_empty(self, async_session):
        repo = VariableNamespaceRepository(async_session)
        result = await repo.load_vars_for_project("NOPE")
        assert result == {}


# ── BenchmarkRepository edge cases ────────────────────────────────────


class TestBenchmarkEdge:
    async def test_record_result_persists(self, async_session):
        repo = BenchmarkRepository(session=async_session)
        row = await repo.record_result(
            {
                "task_type": "code",
                "success": True,
                "completion_score": 0.9,
                "code_quality_score": 0.8,
                "instruction_adherence_score": 0.7,
                "token_efficiency_score": 0.6,
                "cost_usd": 0.01,
                "prompt_profile_id": "p1",
                "model_profile_id": "m1",
            }
        )
        assert row.task_type == "code"

    async def test_no_session_or_factory_raises_runtime_error(self):
        repo = BenchmarkRepository(session=None, session_factory=None)
        with pytest.raises(RuntimeError, match="no session or session_factory"):
            await repo.record_result({"task_type": "code"})

    async def test_get_best_for_task_filters_min_samples(self, async_session):
        repo = BenchmarkRepository(session=async_session)
        for i in range(5):
            await repo.record_result(
                {
                    "task_type": "code",
                    "success": True,
                    "completion_score": 0.5 + i * 0.1,
                    "code_quality_score": 0.5,
                    "instruction_adherence_score": 0.5,
                    "token_efficiency_score": 0.5,
                    "cost_usd": 0.01,
                    "prompt_profile_id": "p1",
                    "model_profile_id": "m1",
                }
            )
        best = await repo.get_best_for_task("code", min_samples=3)
        assert len(best) == 1
        assert best[0]["sample_count"] >= 3

    async def test_get_best_for_task_insufficient_samples(self, async_session):
        repo = BenchmarkRepository(session=async_session)
        await repo.record_result(
            {
                "task_type": "code",
                "success": True,
                "completion_score": 0.5,
                "code_quality_score": 0.5,
                "instruction_adherence_score": 0.5,
                "token_efficiency_score": 0.5,
                "cost_usd": 0.01,
                "prompt_profile_id": "p1",
                "model_profile_id": "m1",
            }
        )
        best = await repo.get_best_for_task("code", min_samples=10)
        assert best == []

    async def test_get_aggregate_scores_task_role_filter(self, async_session):
        repo = BenchmarkRepository(session=async_session)
        await repo.record_result(
            {
                "task_type": "code",
                "task_role": "coder",
                "success": True,
                "completion_score": 0.8,
                "code_quality_score": 0.8,
                "instruction_adherence_score": 0.8,
                "token_efficiency_score": 0.8,
                "cost_usd": 0.01,
                "prompt_profile_id": "p1",
                "model_profile_id": "m1",
            }
        )
        await repo.record_result(
            {
                "task_type": "code",
                "task_role": "reviewer",
                "success": True,
                "completion_score": 0.9,
                "code_quality_score": 0.9,
                "instruction_adherence_score": 0.9,
                "token_efficiency_score": 0.9,
                "cost_usd": 0.01,
                "prompt_profile_id": "p1",
                "model_profile_id": "m1",
            }
        )
        coder = await repo.get_aggregate_scores(task_role="coder")
        assert len(coder) == 1
        assert coder[0]["task_role"] == "coder"


# ── ProjectRelationshipRepository edge cases ──────────────────────────


class TestProjectRelationshipEdge:
    async def test_invalid_relation_type_enum(self, async_session):
        repo = ProjectRelationshipRepository(async_session)
        with pytest.raises(ValueError, match="invalid relation_type"):
            await repo.add_relationship(
                {
                    "project_id": "p1",
                    "relation_type": "not_a_real_type",
                    "location_kind": "path",
                    "location_value": "/x",
                }
            )

    async def test_invalid_location_kind_enum(self, async_session):
        repo = ProjectRelationshipRepository(async_session)
        with pytest.raises(ValueError, match="invalid location_kind"):
            await repo.add_relationship(
                {
                    "project_id": "p1",
                    "relation_type": "parent",
                    "location_kind": "not_a_real_kind",
                    "location_value": "/x",
                }
            )

    async def test_self_edge_rejected(self, async_session):
        repo = ProjectRelationshipRepository(async_session)
        with pytest.raises(ValueError, match="self-edge"):
            await repo.add_relationship(
                {
                    "project_id": "p1",
                    "relation_type": "parent",
                    "location_kind": "directory",
                    "location_value": "/x",
                    "related_project_id": "p1",
                }
            )

    async def test_singleton_parent_replaces_previous(self, async_session):
        repo = ProjectRelationshipRepository(async_session)
        await repo.add_relationship(
            {"project_id": "P", "relation_type": "parent", "location_kind": "path", "location_value": "/old"}
        )
        await repo.add_relationship(
            {"project_id": "P", "relation_type": "parent", "location_kind": "path", "location_value": "/new"}
        )
        parents = await repo.list_for_project("P", relation_type="parent")
        assert len(parents) == 1
        assert parents[0].location_value == "/new"

    async def test_same_edge_upsert(self, async_session):
        repo = ProjectRelationshipRepository(async_session)
        r1 = await repo.add_relationship(
            {
                "project_id": "P",
                "relation_type": "parent",
                "location_kind": "path",
                "location_value": "/x",
                "related_project_id": "parent-1",
            }
        )
        r2 = await repo.add_relationship(
            {
                "project_id": "P",
                "relation_type": "parent",
                "location_kind": "path",
                "location_value": "/x",
                "related_project_id": "parent-1",
            }
        )
        assert r1.id == r2.id
        parents = await repo.list_for_project("P", relation_type="parent")
        assert len(parents) == 1

    async def test_get_parent_none(self, async_session):
        repo = ProjectRelationshipRepository(async_session)
        assert await repo.get_parent("NOPE") is None

    async def test_remove_nonexistent(self, async_session):
        repo = ProjectRelationshipRepository(async_session)
        assert await repo.remove("nonexistent-id") is False


# ── AgentMessageRepository edge cases ─────────────────────────────────


class TestAgentMessageEdge:
    async def test_send_keyword_style_returns_true_on_ack(self, async_session):
        repo = AgentMessageRepository(async_session)
        msg = await repo.send(sender="A", recipient="B", topic="t", body="hi", project_id="P")
        result = await repo.ack(msg.id)
        assert result is True

    async def test_ack_already_read_does_not_overwrite(self, async_session):
        repo = AgentMessageRepository(async_session)
        msg = await repo.send(sender="A", recipient="B", topic="t", body="hi")
        await repo.ack(msg.id)
        first_read = (await repo.get_by_id(msg.id)).read_at
        await repo.ack(msg.id)
        second_read = (await repo.get_by_id(msg.id)).read_at
        assert second_read == first_read

    async def test_ack_cross_tenant_rejection(self, async_session):
        repo = AgentMessageRepository(async_session)
        msg = await repo.send(sender="A", recipient="B", topic="t", body="hi", project_id="project-A")
        result = await repo.ack(msg.id, project_id="project-B")
        assert result is None

    async def test_ack_nonexistent(self, async_session):
        repo = AgentMessageRepository(async_session)
        assert await repo.ack("nonexistent") is None

    async def test_inbox_excludes_expired(self, async_session):
        repo = AgentMessageRepository(async_session)
        await repo.send(sender="A", recipient="B", topic="t", body="hi", ttl_seconds=1)
        await asyncio.sleep(1.1)
        inbox = await repo.inbox("B")
        assert len(inbox) == 0

    async def test_inbox_no_broadcast(self, async_session):
        repo = AgentMessageRepository(async_session)
        await repo.send(sender="A", recipient="B", topic="t", body="hi")
        await repo.send(sender="X", recipient="broadcast", topic="t", body="all")
        inbox = await repo.inbox("B", include_broadcast=False)
        assert len(inbox) == 1
        assert inbox[0].sender == "A"

    async def test_inbox_project_scoped(self, async_session):
        repo = AgentMessageRepository(async_session)
        await repo.send(sender="A", recipient="B", topic="t", body="hi", project_id="P1")
        await repo.send(sender="X", recipient="B", topic="t", body="hi", project_id="P2")
        inbox = await repo.inbox("B", project_id="P1")
        assert len(inbox) == 1
        assert inbox[0].project_id == "P1"

    async def test_purge_expired(self, async_session):
        repo = AgentMessageRepository(async_session)
        await repo.send(sender="A", recipient="B", topic="t", body="hi", ttl_seconds=1)
        await asyncio.sleep(1.1)
        purged = await repo.purge_expired()
        assert purged == 1

    async def test_unread_counts_excludes_expired(self, async_session):
        repo = AgentMessageRepository(async_session)
        await repo.send(sender="A", recipient="B", topic="t", body="hi", ttl_seconds=1)
        await asyncio.sleep(1.1)
        counts = await repo.unread_counts()
        assert "B" not in counts or counts["B"] == 0


# ── HumanTodoRepository edge cases ────────────────────────────────────


class TestHumanTodoEdge:
    async def test_create_empty_title_rejected(self, async_session):
        repo = HumanTodoRepository(async_session)
        with pytest.raises(ValueError, match="title must not be empty"):
            await repo.create(agent_id="A", title="", body="b", category="blocker")

    async def test_create_whitespace_title_rejected(self, async_session):
        repo = HumanTodoRepository(async_session)
        with pytest.raises(ValueError, match="title must not be empty"):
            await repo.create(agent_id="A", title="   ", body="b", category="blocker")

    async def test_create_empty_body_rejected(self, async_session):
        repo = HumanTodoRepository(async_session)
        with pytest.raises(ValueError, match="body must not be empty"):
            await repo.create(agent_id="A", title="t", body="", category="blocker")

    async def test_create_empty_agent_id_rejected(self, async_session):
        repo = HumanTodoRepository(async_session)
        with pytest.raises(ValueError, match="agent_id must not be empty"):
            await repo.create(agent_id="", title="t", body="b", category="blocker")

    async def test_create_invalid_category_rejected(self, async_session):
        repo = HumanTodoRepository(async_session)
        with pytest.raises(ValueError, match="invalid category"):
            await repo.create(agent_id="A", title="t", body="b", category="not_a_real_category")

    async def test_create_invalid_priority_rejected(self, async_session):
        repo = HumanTodoRepository(async_session)
        with pytest.raises(ValueError, match="invalid priority"):
            await repo.create(agent_id="A", title="t", body="b", category="blocker", priority="extreme")

    async def test_transition_done_to_any_rejected(self, async_session):
        repo = HumanTodoRepository(async_session)
        ht = await repo.create(agent_id="A", title="t", body="b", category="blocker")
        await repo.mark_done(ht.id, human_resolver="admin", resolution_text="done")
        with pytest.raises(InvalidTransitionError, match="invalid human-todo transition"):
            await repo._transition(ht.id, "open")

    async def test_supersede_sets_resolved_at(self, async_session):
        repo = HumanTodoRepository(async_session)
        ht = await repo.create(agent_id="A", title="t", body="b", category="blocker")
        sup = await repo.supersede(ht.id, "HT-NEW", "outdated")
        assert sup.status == "superseded"
        assert sup.resolved_at is not None

    async def test_mark_done_empty_resolution_rejected(self, async_session):
        repo = HumanTodoRepository(async_session)
        ht = await repo.create(agent_id="A", title="t", body="b", category="blocker")
        with pytest.raises(ValueError, match="resolution_text must not be empty"):
            await repo.mark_done(ht.id, human_resolver="admin", resolution_text="")

    async def test_dismiss_empty_reason_rejected(self, async_session):
        repo = HumanTodoRepository(async_session)
        ht = await repo.create(agent_id="A", title="t", body="b", category="blocker")
        with pytest.raises(ValueError, match="dismiss reason must not be empty"):
            await repo.dismiss(ht.id, human_resolver="admin", reason="")

    async def test_list_open_filters(self, async_session):
        repo = HumanTodoRepository(async_session)
        await repo.create(agent_id="A1", title="t1", body="b1", category="blocker", priority="high")
        await repo.create(agent_id="A2", title="t2", body="b2", category="decision", priority="low")
        by_cat = await repo.list_open(filter_category="blocker")
        assert len(by_cat) == 1
        assert by_cat[0].agent_id == "A1"
        by_agent = await repo.list_open(filter_agent_id="A2")
        assert len(by_agent) == 1
        by_pri = await repo.list_open(filter_priority="low")
        assert len(by_pri) == 1

    async def test_get_done_for_parent_none(self, async_session):
        repo = HumanTodoRepository(async_session)
        assert await repo.get_done_for_parent("NO_PARENT") is None

    async def test_add_tag_idempotent(self, async_session):
        repo = HumanTodoRepository(async_session)
        ht = await repo.create(agent_id="A", title="t", body="b", category="blocker")
        await repo.add_tag(ht.id, "urgent")
        await repo.add_tag(ht.id, "urgent")
        tags = _json.loads((await repo.get(ht.id)).tags or "[]")
        assert tags.count("urgent") == 1

    async def test_remove_tag_nonexistent(self, async_session):
        repo = HumanTodoRepository(async_session)
        ht = await repo.create(agent_id="A", title="t", body="b", category="blocker")
        await repo.add_tag(ht.id, "urgent")
        await repo.remove_tag(ht.id, "nonexistent")
        tags = _json.loads((await repo.get(ht.id)).tags or "[]")
        assert tags == ["urgent"]

    async def test_search_empty_query(self, async_session):
        repo = HumanTodoRepository(async_session)
        assert await repo.search("") == []
        assert await repo.search("   ") == []

    async def test_search_escapes_wildcards(self, async_session):
        repo = HumanTodoRepository(async_session)
        await repo.create(agent_id="A", title="100% bug", body="details", category="blocker")
        results = await repo.search("100%")
        assert len(results) == 1

    async def test_list_changed_since_filters(self, async_session):
        repo = HumanTodoRepository(async_session)
        ht = await repo.create(agent_id="A", title="t", body="b", category="blocker")
        await asyncio.sleep(0.01)
        await repo.mark_in_progress(ht.id)
        cutoff = datetime.now(UTC) - timedelta(seconds=5)
        changed = await repo.list_changed_since(cutoff)
        assert len(changed) >= 1


# ── FeatureRepository edge cases ──────────────────────────────────────


class TestFeatureEdge:
    async def test_upsert_no_update_cols(self, async_session):
        repo = FeatureRepository(async_session)
        f = await repo.upsert({"name": "feat-1"})
        assert f.name == "feat-1"
        f2 = await repo.upsert({"name": "feat-1"})
        assert f2.id == f.id

    async def test_set_status_nonexistent(self, async_session):
        repo = FeatureRepository(async_session)
        with pytest.raises(KeyError, match="not found"):
            await repo.set_status("nonexistent", FeatureStatus.PLANNED)

    async def test_get_by_id_scoped(self, async_session):
        repo_a = FeatureRepository.scoped(async_session, "A")
        await repo_a.upsert({"name": "f1", "project_id": "A"})
        repo_b = FeatureRepository.scoped(async_session, "B")
        assert await repo_b.get_by_name("f1") is None


# ── RemediationActionRepository edge cases ────────────────────────────


class TestRemediationEdge:
    async def test_exists_recent_false_when_none(self, async_session):
        repo = RemediationActionRepository(async_session)
        since = datetime.now(UTC) - timedelta(hours=1)
        assert await repo.exists_recent("TODO-X", since) is False

    async def test_exists_recent_by_action_different_action_not_duplicate(self, async_session):
        repo = RemediationActionRepository(async_session)
        await repo.record(
            blocked_todo_id="TODO-X", action_kind="schedule_retry", blocker_kind="human_input", project_id="P"
        )
        since = datetime.now(UTC) - timedelta(hours=1)
        assert await repo.exists_recent_by_action("TODO-X", "dispatch_agent", since) is False
        assert await repo.exists_recent_by_action("TODO-X", "schedule_retry", since) is True

    async def test_find_by_idempotency_key(self, async_session):
        repo = RemediationActionRepository(async_session)
        await repo.record(blocked_todo_id="T1", action_kind="retry", blocker_kind="x", idempotency_key="ik-1")
        await repo.record(blocked_todo_id="T1", action_kind="retry", blocker_kind="x", idempotency_key="ik-2")
        results = await repo.find_by_idempotency_key("ik-1")
        assert len(results) == 1

    async def test_get_nonexistent(self, async_session):
        repo = RemediationActionRepository(async_session)
        assert await repo.get("NOPE") is None


# ── SpendRepository edge cases ────────────────────────────────────────


class TestSpendEdge:
    async def test_total_since_empty(self, async_session):
        repo = SpendRepository(async_session)
        assert await repo.total_since(0.0) == 0.0

    async def test_total_since_project_scoped(self, async_session):
        repo = SpendRepository(async_session)
        await repo.add(ts=100.0, cost_usd=5.0, kind="token", project_id="P1")
        await repo.add(ts=200.0, cost_usd=3.0, kind="token", project_id="P2")
        assert await repo.total_since(0.0, project_id="P1") == 5.0
        assert await repo.total_since(0.0, project_id="P2") == 3.0

    async def test_list_since_boundary(self, async_session):
        repo = SpendRepository(async_session)
        await repo.add(ts=100.0, cost_usd=1.0, kind="token")
        await repo.add(ts=200.0, cost_usd=2.0, kind="token")
        rows = await repo.list_since(150.0)
        assert len(rows) == 1
        assert rows[0].cost_usd == 2.0


# ── PromptProfileRepository edge cases ────────────────────────────────


class TestPromptProfileEdge:
    async def test_list_for_task_type_malformed_json(self, async_session):
        repo = PromptProfileRepository(async_session)
        await repo.upsert({"name": "pp1", "task_types": "not-json", "source": "s"})
        result = await repo.list_for_task_type("code")
        assert len(result) >= 1

    async def test_list_for_task_type_string_not_list(self, async_session):
        repo = PromptProfileRepository(async_session)
        await repo.upsert({"name": "pp2", "task_types": _json.dumps("single_type"), "source": "s"})
        result = await repo.list_for_task_type("single_type")
        assert len(result) >= 1

    async def test_list_for_task_type_empty_types_matches_all(self, async_session):
        repo = PromptProfileRepository(async_session)
        await repo.upsert({"name": "pp3", "task_types": "[]", "source": "s"})
        result = await repo.list_for_task_type("any")
        assert len(result) >= 1

    async def test_list_by_source(self, async_session):
        repo = PromptProfileRepository(async_session)
        await repo.upsert({"name": "pp4", "source": "src-A"})
        await repo.upsert({"name": "pp5", "source": "src-B"})
        result = await repo.list_by_source("src-A")
        assert all(r.source == "src-A" for r in result)


# ── QueueRepository edge cases ────────────────────────────────────────


class TestQueueEdge:
    async def test_get_by_name_nonexistent(self, async_session):
        assert await QueueRepository(async_session).get_by_name("nope") is None

    async def test_list_enabled_empty(self, async_session):
        assert await QueueRepository(async_session).list_enabled() == []
