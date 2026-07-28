"""E2E tests: database repository, models, and planning subsystems.

Covers:
  1. TodoRepository — create, get_by_id, list, update, delete, filter by project, pagination
  2. AgentMessageRepository — send, get, inbox, ack, purge, unread_counts
  3. ModelRouter — resolve role, fallback chains, budget-aware routing
  4. ModelGateway — dispatch, fallback, retry, error classification
  5. Planning — artifact creation/retrieval, repo map generation
  6. Database session lifecycle — connection pooling, transaction boundaries, rollback on error
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import (
    AgentMessageRepository,
    ConcurrencyError,
    InvalidTransitionError,
    TodoRepository,
)
from general_ludd.models.failover import ModelFailoverChain
from general_ludd.models.router import ModelRouter
from general_ludd.planning.artifact import PlanArtifact
from general_ludd.planning.repo_map import CodeSymbol, RepoMap, RepoMapBuilder
from general_ludd.schemas.todo import TodoStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BROADCAST_RECIPIENT = "broadcast"


@pytest.fixture()
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            yield session
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    finally:
        await engine.dispose()


@pytest.fixture()
async def db_file_session():
    """File-backed SQLite session for RepoMap tests that need real filesystem."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db", prefix="gludd_e2e_")
    os.close(db_fd)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            yield session
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    finally:
        await engine.dispose()
        with contextlib.suppress(OSError):
            os.unlink(db_path)


def _make_todo_data(**overrides):
    data = {
        "title": "test todo",
        "description": "test desc",
        "status": TodoStatus.BACKLOG.value,
        "priority": 5,
        "queue": "core",
        "tags": '["e2e"]',
        "risk_level": "low",
        "work_type": "code",
        "resource_profile": "ai_heavy",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# 1. TodoRepository
# ---------------------------------------------------------------------------


class TestTodoRepositoryCRUD:
    async def test_create_and_get_by_id(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(title="E2E Create Test")
        created = await repo.create(data)

        assert created.todo_id
        assert created.title == "E2E Create Test"
        assert created.version == 1

        loaded = await repo.get_by_id(created.todo_id)
        assert loaded is not None
        assert loaded.todo_id == created.todo_id
        assert loaded.title == "E2E Create Test"

    async def test_create_with_custom_todo_id(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(todo_id="CUSTOM-001", title="Custom ID Todo")
        created = await repo.create(data)
        assert created.todo_id == "CUSTOM-001"

        loaded = await repo.get_by_id("CUSTOM-001")
        assert loaded is not None
        assert loaded.title == "Custom ID Todo"

    async def test_create_rejects_immutable_fields(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(version=5)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create(data)

    async def test_create_rejects_immutable_id(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data()
        data["id"] = 999
        with pytest.raises(ValueError, match="immutable"):
            await repo.create(data)

    async def test_create_clamps_priority(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        created = await repo.create(_make_todo_data(title="Clamp High", priority=9999))
        assert created.priority == 1000

        created2 = await repo.create(_make_todo_data(title="Clamp Low", priority=-50))
        assert created2.priority == 0

    async def test_create_priority_label(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        created = await repo.create(_make_todo_data(title="Label Test", priority="high"))
        assert created.priority == 2

    async def test_create_rejects_oversized_strings(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        big = "x" * 100_000
        with pytest.raises(ValueError, match="exceeds"):
            await repo.create(_make_todo_data(title=big))

    async def test_get_by_ids_batch(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(todo_id="BATCH-1", title="One"))
        await repo.create(_make_todo_data(todo_id="BATCH-2", title="Two"))
        await repo.create(_make_todo_data(todo_id="BATCH-3", title="Three"))

        result = await repo.get_by_ids(["BATCH-1", "BATCH-3", "NONEXISTENT"])
        assert len(result) == 2
        assert "BATCH-1" in result
        assert "BATCH-3" in result
        assert result["BATCH-1"].title == "One"

    async def test_list_all_basic(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(todo_id="LIST-1", title="A"))
        await repo.create(_make_todo_data(todo_id="LIST-2", title="B"))

        all_todos = await repo.list_all()
        assert len(all_todos) >= 2

    async def test_list_all_filter_by_status(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(todo_id="S1", title="Backlog", status=TodoStatus.BACKLOG.value))
        await repo.create(_make_todo_data(todo_id="S2", title="Queued", status=TodoStatus.QUEUED.value))

        backlog = await repo.list_all(status=TodoStatus.BACKLOG.value)
        assert len(backlog) >= 1
        assert all(t.status == TodoStatus.BACKLOG.value for t in backlog)

        queued = await repo.list_all(status=TodoStatus.QUEUED.value)
        assert all(t.status == TodoStatus.QUEUED.value for t in queued)

    async def test_list_all_filter_by_queue(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(todo_id="Q1", queue="core"))
        await repo.create(_make_todo_data(todo_id="Q2", queue="gate"))

        core_todos = await repo.list_all(queue="core")
        assert all(t.queue == "core" for t in core_todos)

    async def test_list_all_offset_limit(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        for i in range(10):
            await repo.create(_make_todo_data(title=f"Page {i}"))

        page1 = await repo.list_all(limit=3, offset=0)
        page2 = await repo.list_all(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        page1_ids = {t.id for t in page1}
        page2_ids = {t.id for t in page2}
        assert page1_ids.isdisjoint(page2_ids)

    async def test_list_by_status(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(todo_id="BS1", status=TodoStatus.BACKLOG.value))
        await repo.create(_make_todo_data(todo_id="BS2", status=TodoStatus.BACKLOG.value))
        await repo.create(_make_todo_data(todo_id="BS3", status=TodoStatus.QUEUED.value))

        backlog = await repo.list_by_status(TodoStatus.BACKLOG)
        assert len(backlog) >= 2
        assert all(t.status == TodoStatus.BACKLOG.value for t in backlog)

    async def test_list_by_work_type(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(todo_id="WT1", work_type="review"))
        await repo.create(_make_todo_data(todo_id="WT2", work_type="code"))
        await repo.create(_make_todo_data(todo_id="WT3", work_type="review"))

        reviews = await repo.list_by_work_type("review")
        assert len(reviews) >= 2
        assert all(t.work_type == "review" for t in reviews)

    async def test_filter_by_project(self, db_session: AsyncSession):
        repo_a = TodoRepository(db_session, project_id="proj-a")
        repo_b = TodoRepository(db_session, project_id="proj-b")
        unscoped = TodoRepository(db_session)

        await repo_a.create(_make_todo_data(todo_id="PA-1", project_id="proj-a", title="A Todo"))
        await repo_b.create(_make_todo_data(todo_id="PB-1", project_id="proj-b", title="B Todo"))

        a_results = await repo_a.list_all()
        assert all(t.project_id == "proj-a" for t in a_results)

        b_results = await repo_b.list_all()
        assert all(t.project_id == "proj-b" for t in b_results)

        unscoped_results = await unscoped.list_all()
        assert len(unscoped_results) >= 2

    async def test_update_simple(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        created = await repo.create(_make_todo_data(title="Before Update"))
        assert created.version == 1

        updated = await repo.update(created.todo_id, {"title": "After Update"}, expected_version=1)
        assert updated.title == "After Update"
        assert updated.version == 2

        loaded = await repo.get_by_id(created.todo_id)
        assert loaded.title == "After Update"

    async def test_update_version_mismatch(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        created = await repo.create(_make_todo_data(title="Version Test"))

        with pytest.raises(ConcurrencyError):
            await repo.update(created.todo_id, {"title": "Stale"}, expected_version=999)

    async def test_update_rejects_immutable_update_fields(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        created = await repo.create(_make_todo_data(title="Immutable Test"))

        with pytest.raises(ValueError, match="immutable"):
            await repo.update(created.todo_id, {"todo_id": "NEW-ID"}, expected_version=1)

        with pytest.raises(ValueError, match="immutable"):
            await repo.update(created.todo_id, {"project_id": "other-proj"}, expected_version=1)

    async def test_update_nonexistent(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        with pytest.raises(InvalidTransitionError, match="not found"):
            await repo.update("NONEXISTENT", {"title": "X"}, expected_version=1)

    async def test_transition_valid(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        created = await repo.create(_make_todo_data(status=TodoStatus.BACKLOG.value))

        result = await repo.transition(created.todo_id, TodoStatus.QUEUED, expected_version=1)
        assert result.status == TodoStatus.QUEUED.value
        assert result.version == 2

    async def test_transition_invalid(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        created = await repo.create(_make_todo_data(status=TodoStatus.COMPLETE.value))

        with pytest.raises(InvalidTransitionError, match="Invalid transition"):
            await repo.transition(created.todo_id, TodoStatus.QUEUED, expected_version=1)

    async def test_count_active(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(status=TodoStatus.ACTIVE.value, title="Active 1"))
        await repo.create(_make_todo_data(status=TodoStatus.ACTIVE.value, title="Active 2"))
        await repo.create(_make_todo_data(status=TodoStatus.QUEUED.value, title="Queued"))

        count = await repo.count_active()
        assert count == 2

    async def test_status_summary(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(status=TodoStatus.BACKLOG.value, queue="core", work_type="code"))
        await repo.create(_make_todo_data(status=TodoStatus.QUEUED.value, queue="core", work_type="review"))

        summary = await repo.status_summary()
        assert "total" in summary
        assert "by_status" in summary
        assert "by_queue" in summary
        assert "by_work_type" in summary
        assert "oldest_age_seconds" in summary
        assert "backlog_size" in summary

    async def test_claim_runnable(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(todo_id="CL-1", status=TodoStatus.QUEUED.value, title="Claim 1"))
        await repo.create(_make_todo_data(todo_id="CL-2", status=TodoStatus.QUEUED.value, title="Claim 2"))
        await repo.create(_make_todo_data(todo_id="CL-3", status=TodoStatus.BACKLOG.value, title="Not Runnable"))

        claimed = await repo.claim_runnable(limit=10)
        assert len(claimed) == 2
        for t in claimed:
            assert t.status == TodoStatus.ACTIVE.value

        claimed_again = await repo.claim_runnable(limit=10)
        assert len(claimed_again) == 0

    async def test_list_due_scheduled(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        past = datetime.now(UTC) - timedelta(hours=1)
        future = datetime.now(UTC) + timedelta(hours=1)

        await repo.create(
            _make_todo_data(
                todo_id="DUE-1",
                status=TodoStatus.SCHEDULED.value,
                scheduled_at=past,
                schedule_paused=False,
                title="Due",
            )
        )
        await repo.create(
            _make_todo_data(
                todo_id="DUE-2",
                status=TodoStatus.SCHEDULED.value,
                scheduled_at=future,
                schedule_paused=False,
                title="Not Yet",
            )
        )

        due = await repo.list_due_scheduled(now=datetime.now(UTC))
        assert len(due) >= 1
        assert any(t.todo_id == "DUE-1" for t in due)


# ---------------------------------------------------------------------------
# 2. AgentMessageRepository
# ---------------------------------------------------------------------------


class TestAgentMessageRepository:
    async def test_send_and_get(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        msg = await repo.send(
            {
                "sender": "orchestrator",
                "recipient": "worker-1",
                "topic": "test",
                "body": "hello world",
            }
        )
        assert msg.id.startswith("MSG-")

        loaded = await repo.get_by_id(msg.id)
        assert loaded is not None
        assert loaded.body == "hello world"
        assert loaded.recipient == "worker-1"

    async def test_inbox_filters_by_recipient(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        await repo.send({"sender": "s", "recipient": "agent-a", "topic": "t", "body": "for a"})
        await repo.send({"sender": "s", "recipient": "agent-b", "topic": "t", "body": "for b"})

        inbox_a = await repo.inbox("agent-a")
        assert all(m.recipient == "agent-a" for m in inbox_a)

    async def test_inbox_includes_broadcast(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        await repo.send({"sender": "s", "recipient": BROADCAST_RECIPIENT, "topic": "t", "body": "all"})
        await repo.send({"sender": "s", "recipient": "agent-a", "topic": "t", "body": "a"})

        inbox = await repo.inbox("agent-a", include_broadcast=True)
        recipients = {m.recipient for m in inbox}
        assert BROADCAST_RECIPIENT in recipients
        assert "agent-a" in recipients

    async def test_inbox_excludes_broadcast(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        await repo.send({"sender": "s", "recipient": BROADCAST_RECIPIENT, "topic": "t", "body": "all"})
        await repo.send({"sender": "s", "recipient": "agent-a", "topic": "t", "body": "a"})

        inbox = await repo.inbox("agent-a", include_broadcast=False)
        recipients = {m.recipient for m in inbox}
        assert BROADCAST_RECIPIENT not in recipients

    async def test_ack_marks_read(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        msg = await repo.send({"sender": "s", "recipient": "agent-a", "topic": "t", "body": "ack me"})

        acked = await repo.ack(msg.id)
        assert acked is not None
        assert acked.read_at is not None

    async def test_ack_idempotent(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        msg = await repo.send({"sender": "s", "recipient": "agent-a", "topic": "t", "body": "x"})

        acked1 = await repo.ack(msg.id)
        acked2 = await repo.ack(msg.id)
        assert acked1.read_at is not None
        assert acked2 is not None

    async def test_ack_cross_project_denied(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        msg = await repo.send(
            {"sender": "s", "recipient": "agent-a", "topic": "t", "body": "x", "project_id": "proj-x"}
        )

        result = await repo.ack(msg.id, project_id="proj-y")
        assert result is None

    async def test_purge_expired(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        await repo.send(
            {
                "sender": "s",
                "recipient": "agent-a",
                "topic": "t",
                "body": "old",
                "ttl_seconds": 0,
            }
        )
        await repo.send(
            {
                "sender": "s",
                "recipient": "agent-a",
                "topic": "t",
                "body": "fresh",
                "ttl_seconds": 86400,
            }
        )

        import asyncio

        await asyncio.sleep(1)
        purged = await repo.purge_expired()
        assert purged >= 1

    async def test_unread_counts(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        await repo.send({"sender": "s", "recipient": "agent-a", "topic": "t", "body": "x"})
        await repo.send({"sender": "s", "recipient": "agent-a", "topic": "t", "body": "y"})
        await repo.send({"sender": "s", "recipient": "agent-b", "topic": "t", "body": "z"})

        counts = await repo.unread_counts()
        assert counts.get("agent-a", 0) == 2
        assert counts.get("agent-b", 0) == 1

    async def test_inbox_filters_project(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        await repo.send({"sender": "s", "recipient": "agent-a", "topic": "t", "body": "proj-x", "project_id": "proj-x"})
        await repo.send({"sender": "s", "recipient": "agent-a", "topic": "t", "body": "proj-y", "project_id": "proj-y"})

        inbox_x = await repo.inbox("agent-a", project_id="proj-x")
        assert len(inbox_x) >= 1
        for m in inbox_x:
            assert m.project_id in (None, "proj-x")


# ---------------------------------------------------------------------------
# 3. ModelRouter
# ---------------------------------------------------------------------------


class TestModelRouter:
    def test_resolve_role_exact_match(self):
        router = ModelRouter(role_mapping={"coder": "profile-gpt4"})
        assert router.resolve_role("coder") == "profile-gpt4"

    def test_resolve_role_falls_to_default(self):
        router = ModelRouter(
            role_mapping={"coder": "profile-gpt4"},
            default_profile_id="profile-default",
        )
        assert router.resolve_role("unknown") == "profile-default"

    def test_resolve_role_returns_none_no_default(self):
        router = ModelRouter(role_mapping={"coder": "profile-gpt4"})
        assert router.resolve_role("unknown") is None

    def test_resolve_role_strict_raises(self):
        router = ModelRouter(role_mapping={"coder": "profile-gpt4"})
        with pytest.raises(ValueError, match="Unrecognised role"):
            router.resolve_role("unknown", strict=True)

    def test_resolve_role_strict_allows_weak(self):
        router = ModelRouter(role_mapping={}, weak_model_profile_id="profile-weak")
        assert router.resolve_role("weak", strict=True) == "profile-weak"

    def test_resolve_weak_role(self):
        router = ModelRouter(role_mapping={"coder": "profile-gpt4"}, weak_model_profile_id="profile-weak")
        assert router.resolve_role("weak") == "profile-weak"

    def test_add_role(self):
        router = ModelRouter()
        router.add_role("reviewer", "profile-review")
        assert router.resolve_role("reviewer") == "profile-review"

    def test_list_roles(self):
        router = ModelRouter(role_mapping={"a": "p1", "b": "p2"})
        roles = router.list_roles()
        assert "a" in roles
        assert "b" in roles

    def test_list_profiles_by_role(self):
        router = ModelRouter(role_mapping={"a": "p1", "b": "p1", "c": "p2"})
        result = router.list_profiles_by_role("p1")
        assert sorted(result) == ["a", "b"]

    def test_set_role_routing(self):
        router = ModelRouter()
        router.set_role_routing("planner", "profile-plan")
        assert router.resolve_role("planner") == "profile-plan"

    def test_quality_and_latency_mappings(self):
        router = ModelRouter()
        router.add_quality_mapping("high", "profile-quality")
        router.add_latency_mapping("low", "profile-fast")

        assert router.resolve_by_quality("high") == "profile-quality"
        assert router.resolve_by_latency("low") == "profile-fast"
        assert router.resolve_by_quality("unknown") is None

    def test_pattern_mapping(self):
        router = ModelRouter(role_mapping={"generator": "profile-gen"})
        router.add_pattern_mapping("code-gen", "generator")
        assert router.resolve_pattern("code-gen") == "profile-gen"
        assert router.resolve_pattern("nonexistent") is None
        assert "code-gen" in router.list_patterns()

    def test_build_from_profiles(self):
        from general_ludd.models.gateway import ModelProfile

        profiles = [
            ModelProfile(
                model_profile_id="p1",
                role_names=["coder", "planner"],
                quality_class="high",
                latency_class="low",
            ),
            ModelProfile(
                model_profile_id="p2",
                role_names=["reviewer"],
                quality_class="medium",
            ),
        ]
        router = ModelRouter.build_from_profiles(profiles)
        assert router.resolve_role("coder") == "p1"
        assert router.resolve_role("planner") == "p1"
        assert router.resolve_role("reviewer") == "p2"
        assert router.resolve_by_quality("high") == "p1"
        assert router.resolve_by_quality("medium") == "p2"
        assert router.resolve_by_latency("low") == "p1"


# ---------------------------------------------------------------------------
# 4. ModelGateway — budget, failover, error classification
# ---------------------------------------------------------------------------


class TestModelGatewayBudget:
    def test_estimate_cost_basic(self):
        from general_ludd.models.gateway import ModelGateway, ModelProfile

        profile = ModelProfile(
            model_profile_id="test",
            model_name="test-model",
            cost_per_input_token=0.0001,
            cost_per_output_token=0.0002,
            max_output_tokens=1000,
        )
        msgs = [{"role": "user", "content": "Hello, this is a test message with some content"}]
        cost = ModelGateway.estimate_cost(
            profile,
            msgs,
        )
        assert cost > 0

    def test_estimate_cost_empty_messages(self):
        from general_ludd.models.gateway import ModelGateway, ModelProfile

        profile = ModelProfile(
            model_profile_id="test",
            model_name="test-model",
            cost_per_input_token=0.0001,
            cost_per_output_token=0.0002,
            max_output_tokens=1000,
        )
        cost = ModelGateway.estimate_cost(profile, [])
        assert cost == 0.0

    def test_check_budget_sufficient(self):
        from general_ludd.models.gateway import ModelGateway, ModelProfile

        profile = ModelProfile(
            model_profile_id="test",
            model_name="test-model",
            run_budget_usd=200.0,
            api_metered=True,
        )
        gw = ModelGateway(profiles=[profile])
        assert gw.check_budget("test", 1.0, 100.0)
        assert not gw.check_budget("test", 250.0, 100.0)

    def test_check_budget_nonexistent_profile(self):
        from general_ludd.models.gateway import ModelGateway

        gw = ModelGateway()
        assert not gw.check_budget("nonexistent", 1.0, 100.0)

    def test_get_profile(self):
        from general_ludd.models.gateway import ModelGateway, ModelProfile

        profile = ModelProfile(model_profile_id="test", model_name="test-model")
        gw = ModelGateway(profiles=[profile])
        assert gw.get_profile("test") is not None
        assert gw.get_profile("nonexistent") is None

    def test_is_available(self):
        from general_ludd.models.gateway import ModelGateway, ModelProfile

        profile = ModelProfile(model_profile_id="test", model_name="test-model", enabled=True, api_metered=False)
        gw = ModelGateway(profiles=[profile])
        assert gw.is_available("test")
        assert not gw.is_available("off")

    def test_list_profiles(self):
        from general_ludd.models.gateway import ModelGateway, ModelProfile

        profiles = [
            ModelProfile(model_profile_id="a", model_name="a-model"),
            ModelProfile(model_profile_id="b", model_name="b-model"),
        ]
        gw = ModelGateway(profiles=profiles)
        result = gw.list_profiles()
        assert len(result) == 2
        ids = {p.model_profile_id for p in result}
        assert ids == {"a", "b"}


class TestModelFailoverChain:
    def test_chain_order(self):
        chain = ModelFailoverChain(
            primary_profile="primary",
            fallback_profiles=["f1", "f2"],
            max_retries=2,
        )
        assert chain.get_chain() == ["primary", "f1", "f2"]

    def test_record_failover_event(self):
        chain = ModelFailoverChain(
            primary_profile="primary",
            fallback_profiles=["f1"],
        )
        chain.record_failover("primary", "f1", "timeout", exception_type="ConnectError")
        events = chain.get_failover_events()
        assert len(events) == 1
        assert events[0]["from"] == "primary"
        assert events[0]["to"] == "f1"
        assert events[0]["error"] == "timeout"

    def test_should_retry_retryable_status(self):
        chain = ModelFailoverChain(
            primary_profile="primary",
            fallback_profiles=["f1"],
            max_retries=3,
        )

        class Fake503(Exception):
            status_code = 503

        assert chain.should_retry(Fake503()) is True

    def test_should_retry_retryable_keyword(self):
        chain = ModelFailoverChain(
            primary_profile="primary",
            fallback_profiles=["f1"],
            max_retries=2,
        )
        assert chain.should_retry(ValueError("connection timeout occurred")) is True

    def test_should_not_retry_non_retryable(self):
        chain = ModelFailoverChain(
            primary_profile="primary",
            max_retries=3,
        )
        assert chain.should_retry(ValueError("invalid argument")) is False

    def test_primary_and_fallbacks(self):
        chain = ModelFailoverChain(primary_profile="primary", fallback_profiles=["f1", "f2"])
        assert chain.get_chain() == ["primary", "f1", "f2"]
        events = chain.get_failover_events()
        assert events == []

    def test_get_failover_events_empty(self):
        chain = ModelFailoverChain(primary_profile="p")
        assert chain.get_failover_events() == []

    def test_max_retries_setting(self):
        chain = ModelFailoverChain(primary_profile="p", max_retries=5)
        assert chain._max_retries == 5

    def test_backoff_setting(self):
        chain = ModelFailoverChain(primary_profile="p", backoff_seconds=4.0)
        assert chain._backoff == 4.0


class TestModelGatewayErrors:
    def test_coerce_token_count_bool(self):
        from general_ludd.models.gateway import _coerce_token_count

        assert _coerce_token_count(True) == 0
        assert _coerce_token_count(False) == 0

    def test_coerce_token_count_valid(self):
        from general_ludd.models.gateway import _coerce_token_count

        assert _coerce_token_count(100) == 100
        assert _coerce_token_count(50.7) == 50

    def test_coerce_token_count_non_finite(self):
        from general_ludd.models.gateway import _coerce_token_count

        assert _coerce_token_count(float("nan")) == 0
        assert _coerce_token_count(float("inf")) == 0

    def test_coerce_token_count_non_numeric(self):
        from general_ludd.models.gateway import _coerce_token_count

        assert _coerce_token_count("abc") == 0

    def test_ssrf_rejection_error(self):
        from general_ludd.models.gateway import SSRFRejectionError

        err = SSRFRejectionError("blocked")
        assert isinstance(err, ValueError)
        assert isinstance(err, SSRFRejectionError)
        assert str(err) == "blocked"

    def test_model_paused_error(self):
        from general_ludd.models.gateway import ModelPausedError

        err = ModelPausedError("paused")
        assert not isinstance(err, ValueError)
        assert isinstance(err, ModelPausedError)

    def test_circuit_breaker_open_error(self):
        from general_ludd.models.gateway import CircuitBreakerOpenError

        err = CircuitBreakerOpenError("all open")
        assert not isinstance(err, ValueError)
        assert isinstance(err, CircuitBreakerOpenError)

    def test_budget_exceeded_error(self):
        from general_ludd.models.gateway import BudgetExceededError

        err = BudgetExceededError("over budget")
        assert isinstance(err, ValueError)
        assert isinstance(err, BudgetExceededError)


# ---------------------------------------------------------------------------
# 5. Planning — PlanArtifact
# ---------------------------------------------------------------------------


class TestPlanArtifact:
    def test_create_minimal(self):
        pa = PlanArtifact(todo_id="TODO-001")
        assert pa.todo_id == "TODO-001"
        assert pa.title == ""
        assert pa.created_at is not None

    def test_create_full(self):
        pa = PlanArtifact(
            todo_id="TODO-002",
            title="Refactor X",
            description="Refactor the X module",
            target_files=["src/x.py", "tests/test_x.py"],
            contracts=["XProtocol"],
            dependencies=["Y module"],
            notes="Important",
            content="Plan content here",
        )
        assert pa.title == "Refactor X"
        assert len(pa.target_files) == 2
        assert pa.contracts[0] == "XProtocol"
        assert pa.dependencies[0] == "Y module"

    def test_empty_todo_id_raises(self):
        with pytest.raises(ValueError):
            PlanArtifact(todo_id="")

    def test_whitespace_todo_id_raises(self):
        with pytest.raises(ValueError):
            PlanArtifact(todo_id="   ")

    def test_to_markdown(self):
        pa = PlanArtifact(
            todo_id="TODO-003",
            title="Add Feature",
            description="A new feature",
            target_files=["src/feature.py"],
            contracts=["FeatureContract"],
            dependencies=["BaseModule"],
            notes="Handle edge cases",
            content="Implementation details",
        )
        md = pa.to_markdown()
        assert "## Plan: Add Feature" in md
        assert "**Todo ID:** TODO-003" in md
        assert "**Description:** A new feature" in md
        assert "src/feature.py" in md
        assert "FeatureContract" in md
        assert "BaseModule" in md
        assert "Handle edge cases" in md
        assert "Implementation details" in md

    def test_to_markdown_no_title(self):
        pa = PlanArtifact(todo_id="TODO-004")
        md = pa.to_markdown()
        assert "## Plan: TODO-004" in md

    def test_to_dict_roundtrip(self):
        pa = PlanArtifact(
            todo_id="TODO-005",
            title="Roundtrip Test",
            target_files=["f1.py"],
            contracts=["C1"],
        )
        data = pa.to_dict()
        restored = PlanArtifact.from_dict(data)
        assert restored.todo_id == "TODO-005"
        assert restored.title == "Roundtrip Test"
        assert restored.target_files == ["f1.py"]
        assert restored.contracts == ["C1"]

    def test_from_todo(self):
        class FakeTodo:
            todo_id = "TODO-FAKE"
            tags: ClassVar[list[str]] = ["urgent", "backend"]
            test_commands: ClassVar[list[str]] = ["pytest -v", "make lint"]
            title = "Fake Title"
            description = "Fake Description"

        pa = PlanArtifact.from_todo(FakeTodo())
        assert pa.todo_id == "TODO-FAKE"
        assert pa.title == "Fake Title"
        assert pa.description == "Fake Description"
        assert pa.notes and "urgent" in pa.notes
        assert pa.notes and "pytest" in pa.notes


# ---------------------------------------------------------------------------
# 6. Planning — RepoMap
# ---------------------------------------------------------------------------


class TestRepoMap:
    def test_code_symbol_creation(self):
        sym = CodeSymbol(
            name="my_function",
            kind="function",
            file_path="src/module.py",
            line_start=10,
            line_end=25,
        )
        assert sym.name == "my_function"
        assert sym.kind == "function"
        assert sym.parent is None

    def test_code_symbol_with_parent(self):
        sym = CodeSymbol(
            name="my_method",
            kind="method",
            file_path="src/class.py",
            line_start=15,
            line_end=20,
            parent="MyClass",
        )
        assert sym.parent == "MyClass"

    def test_code_symbol_empty_name_raises(self):
        with pytest.raises(ValueError):
            CodeSymbol(name="", kind="function", file_path="x.py", line_start=0, line_end=1)

    def test_code_symbol_negative_line_start_raises(self):
        with pytest.raises(ValueError):
            CodeSymbol(name="f", kind="function", file_path="x.py", line_start=-1, line_end=1)

    def test_code_symbol_line_end_before_start_raises(self):
        with pytest.raises(ValueError):
            CodeSymbol(name="f", kind="function", file_path="x.py", line_start=10, line_end=5)

    def test_repo_map_add_and_get_symbols(self):
        rm = RepoMap(file_count=2, total_lines=50)
        sym1 = CodeSymbol(name="func_a", kind="function", file_path="src/a.py", line_start=1, line_end=5)
        sym2 = CodeSymbol(name="func_b", kind="function", file_path="src/b.py", line_start=3, line_end=8)
        rm.add_symbol(sym1)
        rm.add_symbol(sym2)

        a_syms = rm.get_symbols_for_file("src/a.py")
        assert len(a_syms) == 1
        assert a_syms[0].name == "func_a"

        b_syms = rm.get_symbols_for_file("src/b.py")
        assert len(b_syms) == 1
        assert b_syms[0].name == "func_b"

    def test_repo_map_get_top_symbols(self):
        rm = RepoMap()
        for i in range(10):
            rm.add_symbol(
                CodeSymbol(
                    name=f"sym_{i}",
                    kind="function",
                    file_path="src/mod.py",
                    line_start=i,
                    line_end=i + 2,
                )
            )
        rm.add_symbol(
            CodeSymbol(
                name="MyClass",
                kind="class",
                file_path="src/mod.py",
                line_start=100,
                line_end=120,
            )
        )
        top = rm.get_top_symbols(n=5)
        assert len(top) <= 5
        assert top[0].kind == "class"

    def test_repo_map_to_compact_string(self):
        rm = RepoMap(file_count=1, total_lines=10)
        rm.add_symbol(CodeSymbol(name="foo", kind="function", file_path="src/x.py", line_start=1, line_end=3))
        compact = rm.to_compact_string()
        assert "src/x.py" in compact
        assert "foo" in compact

    def test_repo_map_to_compact_string_empty(self):
        rm = RepoMap()
        assert rm.to_compact_string() == ""

    def test_repo_map_to_dict_roundtrip(self):
        rm = RepoMap(file_count=3, total_lines=100)
        rm.add_symbol(CodeSymbol(name="cls", kind="class", file_path="a.py", line_start=0, line_end=10))
        data = rm.to_dict()
        restored = RepoMap.from_dict(data)
        assert restored.file_count == 3
        assert restored.total_lines == 100
        assert len(restored.symbols) == 1

    def test_repo_map_builder_parse_file(self):
        builder = RepoMapBuilder()
        content = """
class MyClass:
    def method_one(self):
        pass

    def method_two(self):
        pass

def top_level_function():
    pass

import os
from pathlib import Path
""".strip()
        symbols = builder.parse_file("test_module.py", content)
        class_syms = [s for s in symbols if s.kind == "class"]
        method_syms = [s for s in symbols if s.kind == "method"]
        func_syms = [s for s in symbols if s.kind == "function"]
        import_syms = [s for s in symbols if s.kind == "import"]

        assert len(class_syms) == 1
        assert class_syms[0].name == "MyClass"
        assert len(method_syms) >= 2
        assert len(func_syms) >= 1
        assert len(import_syms) >= 2

    def test_repo_map_builder_from_directory(self):
        builder = RepoMapBuilder()
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_dir = os.path.join(tmpdir, "mypkg")
            os.makedirs(pkg_dir)
            Path(os.path.join(pkg_dir, "__init__.py")).write_text("# pkg init\n")
            Path(os.path.join(pkg_dir, "core.py")).write_text(
                "def calculate(x):\n    return x * 2\n\nclass Processor:\n    def run(self):\n        pass\n"
            )
            repo_map = builder.build_from_directory(tmpdir)
            assert repo_map.file_count >= 1
            assert repo_map.total_lines > 0
            core_syms = repo_map.get_symbols_for_file("mypkg/core.py")
            names = {s.name for s in core_syms}
            assert "calculate" in names or "Processor" in names

    def test_rank_symbols_prioritizes_classes(self):
        builder = RepoMapBuilder()
        symbols = [
            CodeSymbol(name="helper", kind="function", file_path="a.py", line_start=10, line_end=12),
            CodeSymbol(name="MainClass", kind="class", file_path="a.py", line_start=1, line_end=9),
            CodeSymbol(name="var_x", kind="variable", file_path="a.py", line_start=13, line_end=13),
        ]
        ranked = builder._rank_symbols(symbols, 2)
        assert len(ranked) == 2
        assert ranked[0].name == "MainClass"


# ---------------------------------------------------------------------------
# 7. Database Session Lifecycle
# ---------------------------------------------------------------------------


class TestDatabaseSessionLifecycle:
    async def test_engine_creation_and_disposal(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                assert result.scalar() == 1
        finally:
            await engine.dispose()

    async def test_transaction_commit(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        created = await repo.create(_make_todo_data(title="Commit Test"))
        await db_session.commit()

        loaded = await repo.get_by_id(created.todo_id)
        assert loaded is not None

    async def test_transaction_rollback_on_error(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        created = await repo.create(_make_todo_data(title="Rollback Test"))
        await db_session.commit()

        try:
            async with db_session.begin():
                await repo.update(created.todo_id, {"title": "Bad"}, expected_version=999)
        except ConcurrencyError:
            pass

        await db_session.refresh(created)
        assert created.title == "Rollback Test"

    async def test_session_commit_preserves_identity(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        t1 = await repo.create(_make_todo_data(todo_id="IDENT-1", title="Identity"))
        await db_session.commit()

        t2 = await repo.get_by_id("IDENT-1")
        assert t2 is not None
        assert t2.id == t1.id
        assert t2.todo_id == "IDENT-1"

    async def test_concurrent_update_optimistic_lock(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        created = await repo.create(_make_todo_data(title="OptLock"))
        await db_session.commit()

        assert created.version == 1
        await repo.update(created.todo_id, {"title": "First Update"}, expected_version=1)

        with pytest.raises(ConcurrencyError):
            await repo.update(created.todo_id, {"title": "Race"}, expected_version=1)

        loaded = await repo.get_by_id(created.todo_id)
        assert loaded.title == "First Update"
        assert loaded.version == 2

    async def test_connection_pool_reuse(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            async with engine.connect():
                pass
            async with engine.connect():
                pass
        finally:
            await engine.dispose()

    async def test_file_backed_session(self, db_file_session: AsyncSession):
        repo = TodoRepository(db_file_session)
        created = await repo.create(_make_todo_data(title="File-backed"))
        await db_file_session.commit()

        assert created.todo_id
        assert created.title == "File-backed"


# ---------------------------------------------------------------------------
# Re-run protocol: `make collect-check` post-collect
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
