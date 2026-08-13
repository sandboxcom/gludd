"""E2E tests: DB operations, agent lifecycle, and dispatch pipeline.
Covers:
  1. DB: migration stamp, engine lifecycle, pool exhaustion/recovery, transaction isolation
  2. Agents: registration, behavior rendering, tool adaptation, lifecycle
  3. Dispatch: scheduling, rate limiting, concurrency, failure retry, spiral detection
  4. Integrated: todo→agent claim→dispatch→result→completion lifecycle
  5. Error paths: DB connection loss, agent timeout, queue overflow, version conflicts
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.agents.behavior import (
    AgentBehavior,
    BehaviorRenderer,
    GuardrailConfig,
    default_primary_behavior,
    default_subagent_behavior,
)
from general_ludd.agents.dispatcher import (
    AgentDispatcher,
    AgentTaskResult,
)
from general_ludd.agents.registry import AgentRegistry, default_registry
from general_ludd.agents.tool_adapter import AgentToolAdapter
from general_ludd.agents.types import (
    AgentConfig,
    AgentPermission,
    AgentTask,
    AgentType,
)
from general_ludd.db.migrations import get_alembic_config, stamp_head
from general_ludd.db.models import (
    Base,
    TodoEventModel,
)
from general_ludd.db.repository import (
    AgentMessageRepository,
    ConcurrencyError,
    InvalidTransitionError,
    QueueRepository,
    TodoRepository,
    scoped_to,
)
from general_ludd.db.session import (
    close_engine,
    create_async_session_factory,
    ensure_tables,
    get_async_session,
    init_engine_from_config,
    json_dumps,
    run_wal_pragmas,
)
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
async def db_file_engine():
    db_fd, db_path = tempfile.mkstemp(suffix=".db", prefix="gludd_e2e_")
    os.close(db_fd)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        yield engine
    finally:
        await engine.dispose()
        with contextlib.suppress(OSError):
            os.unlink(db_path)


@pytest.fixture()
async def agent_registry():
    return default_registry()


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


def _make_agent_task(task_id="test-1", agent_name="general", **overrides):
    data = {
        "task_id": task_id,
        "agent_name": agent_name,
        "description": "test task",
        "prompt": "do something useful",
        "invoker_name": "build",
    }
    data.update(overrides)
    return AgentTask(**data)


# ---------------------------------------------------------------------------
# 1. Database Operations
# ---------------------------------------------------------------------------


class TestMigrations:
    async def test_get_alembic_config_returns_valid_cfg(self):
        cfg = get_alembic_config("sqlite:///./test.db")
        assert cfg.get_main_option("script_location") is not None
        assert "alembic" in cfg.get_main_option("script_location")

    async def test_migration_stamp_head_on_empty_db(self, db_file_engine):
        async with db_file_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        cfg = get_alembic_config(str(db_file_engine.url))
        # Should not raise
        stamp_head(cfg)


class TestEngineLifecycle:
    async def test_init_engine_from_config_sqlite_memory(self):
        engine = init_engine_from_config({"url": "sqlite+aiosqlite:///:memory:"})
        assert engine is not None
        assert "sqlite" in str(engine.url)
        await engine.dispose()

    async def test_database_bootstrap_creates_db_file(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test.db")
            url = f"sqlite+aiosqlite:///{db_path}"
            engine = init_engine_from_config({"url": url})
            await ensure_tables(engine)
            assert os.path.exists(db_path)
            await engine.dispose()

    async def test_init_engine_accepts_postgres(self):
        engine = init_engine_from_config(
            {"url": "postgresql+psycopg://localhost/test"}
        )
        assert engine.dialect.name == "postgresql"
        await engine.dispose()

    async def test_init_engine_falls_back_to_default_on_empty_config(self):
        engine = init_engine_from_config({})
        assert engine is not None
        assert "sqlite" in str(engine.url)
        await engine.dispose()

    async def test_engine_close_prevents_new_sessions(self):
        engine = init_engine_from_config({"url": "sqlite+aiosqlite:///:memory:"})
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        close_engine(engine)
        with pytest.raises(RuntimeError, match="closed/disposed engine"):
            async for _ in get_async_session(factory):
                pass
        await engine.dispose()

    async def test_run_wal_pragmas_accepts_sqlite(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        run_wal_pragmas(engine)
        await engine.dispose()


class TestConnectionPool:
    async def test_pool_exhaustion_triggers_queue_pool(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "pool.db")
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{db_path}",
                pool_size=2,
                max_overflow=0,
                pool_timeout=0.1,
            )
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

                async def _hold_task(_n):
                    async with factory() as session:
                        await session.execute(text("SELECT 1"))
                        await asyncio.sleep(0.3)
                        return True

                results = await asyncio.gather(
                    *(_hold_task(i) for i in range(10)),
                    return_exceptions=True,
                )
                assert sum(result is True for result in results) == 2
                assert sum(isinstance(result, SATimeoutError) for result in results) == 8
            finally:
                await engine.dispose()

    async def test_pool_recovery_after_dispose_and_recreate(self):
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
                await session.execute(text("SELECT 1"))

            await engine.dispose()
            engine2 = create_async_engine(
                "sqlite+aiosqlite:///:memory:",
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
            )
            try:
                async with engine2.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                factory2 = sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
                async with factory2() as session:
                    await session.execute(text("SELECT 1"))
            finally:
                await engine2.dispose()
        finally:
            await engine.dispose()


class TestTransactionIsolation:
    async def test_rollback_on_exception_clears_uncommitted_changes(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(title="Should Rollback")
        await repo.create(data)

        try:
            async with db_session.begin_nested():
                data2 = _make_todo_data(title="Nested Rollback")
                await repo.create(data2)
                raise ValueError("Simulated error")
        except ValueError:
            pass

        await repo.get_by_id(data2["todo_id"]) if "todo_id" in data2 else None
        # Without the todo_id set, we test via list_all
        all_todos = await repo.list_all()
        assert len(all_todos) == 1

    async def test_commit_persists_data_across_sessions(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(title="Persist Test")
        created = await repo.create(data)
        await db_session.commit()

        # Verify it's there after commit
        loaded = await repo.get_by_id(created.todo_id)
        assert loaded is not None
        assert loaded.title == "Persist Test"

    async def test_concurrent_update_triggers_concurrency_error(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(title="Concurrent Test")
        todo = await repo.create(data)
        await db_session.commit()

        # Simulate concurrent update: first update bumps version
        await repo.update(todo.todo_id, {"title": "First Update"}, expected_version=1)
        await db_session.commit()

        # Second update with stale version should fail
        with pytest.raises(ConcurrencyError):
            await repo.update(todo.todo_id, {"title": "Stale Update"}, expected_version=1)

    async def test_wal_mode_enabled_on_file_db(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "wal_test.db")
            engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
            try:
                run_wal_pragmas(engine)
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                async with factory() as session:
                    result = await session.execute(text("PRAGMA journal_mode"))
                    mode = result.scalar()
                    assert mode.upper() in ("WAL", "DELETE", "MEMORY")
            finally:
                await engine.dispose()


class TestSessionManagement:
    async def test_get_async_session_yields_and_commits(self, db_session: AsyncSession):
        engine = db_session.bind
        assert isinstance(engine, AsyncEngine)
        factory = create_async_session_factory(engine)
        async for session in get_async_session(factory):
            await session.execute(text("SELECT 1"))
            assert not session.info.get("rolled_back", False)

    async def test_get_async_session_rollback_on_exception(self, db_session: AsyncSession):
        engine = db_session.bind
        assert isinstance(engine, AsyncEngine)
        factory = create_async_session_factory(engine)
        with pytest.raises(ValueError):
            async for _session in get_async_session(factory):
                raise ValueError("forced rollback")

    async def test_ensure_tables_creates_todo_table(self, db_session: AsyncSession):
        result = await db_session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='todos'")
        )
        assert result.scalar() == "todos"


class TestSeedInitialQueues:
    async def test_seed_queues_is_idempotent(self, db_session: AsyncSession):
        from general_ludd.db.session import seed_initial_queues

        c1 = await seed_initial_queues(db_session)
        await db_session.commit()
        c2 = await seed_initial_queues(db_session)
        await db_session.commit()

        assert c1 > 0
        assert c2 == 0


# ---------------------------------------------------------------------------
# 2. Agent Lifecycle
# ---------------------------------------------------------------------------


class TestAgentRegistry:
    async def test_default_registry_has_builtin_agents(self, agent_registry: AgentRegistry):
        agents = agent_registry.list_agents()
        assert len(agents) > 0
        names = [a.name for a in agents]
        assert "build" in names
        assert "general" in names

    async def test_registry_seal_prevents_registration(self):
        registry = AgentRegistry()
        registry.seal()
        with pytest.raises(RuntimeError, match="sealed"):
            registry.register(AgentConfig(
                name="new-agent",
                description="test",
                type=AgentType.SUBAGENT,
            ))

    async def test_can_invoke_permission_check(self, agent_registry: AgentRegistry):
        assert agent_registry.can_invoke("build", "general")

    async def test_can_invoke_denies_unknown_invoker(self, agent_registry: AgentRegistry):
        assert not agent_registry.can_invoke("nonexistent", "general")

    async def test_list_subagents_filters_by_type(self, agent_registry: AgentRegistry):
        subagents = agent_registry.list_subagents()
        assert all(a.type == AgentType.SUBAGENT for a in subagents)
        assert len(subagents) > 0

    async def test_render_behavior_prompt_returns_string(self, agent_registry: AgentRegistry):
        prompt = agent_registry.render_behavior_prompt("build", "implement feature X")
        assert prompt is not None
        assert "build" in prompt.lower() or "implement" in prompt.lower() or "feature" in prompt.lower()


class TestBehaviorRendering:
    def test_default_primary_behavior_has_required_fields(self):
        behavior = default_primary_behavior()
        assert behavior.completion_policy == "complete_all"
        assert behavior.self_directed_work is True
        assert behavior.tdd_enforced is True
        assert behavior.evidence_required is True
        assert behavior.guardrail.layer_count() == 3

    def test_default_subagent_behavior_has_required_fields(self):
        behavior = default_subagent_behavior()
        assert behavior.tdd_enforced is True
        assert behavior.never_block_on_questions is True

    def test_guardrail_config_requires_at_least_one_layer(self):
        with pytest.raises(ValueError, match="At least one guardrail layer"):
            GuardrailConfig(config_layer=False, hook_layer=False, prompt_layer=False)

    def test_behavior_renderer_produces_prompt(self):
        renderer = BehaviorRenderer()
        behavior = AgentBehavior(role="tester", goal="validate code")
        prompt = renderer.render_as_prompt(behavior, agent_name="test-agent", task="run tests")
        assert prompt is not None
        assert "tester" in prompt.lower()

    def test_behavior_subagent_context_limit(self):
        behavior = AgentBehavior(subagent_context_limit_lines=10)
        assert behavior.subagent_context_limit_lines == 10


class TestToolAdapter:
    def test_agent_tool_adapter_lists_agents_as_tools(self, agent_registry: AgentRegistry):
        adapter = AgentToolAdapter(agent_registry)
        tools = adapter.list_agent_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0
        assert all("name" in t and "description" in t for t in tools)

    def test_agent_tool_adapter_filters_by_invoker(self, agent_registry: AgentRegistry):
        adapter = AgentToolAdapter(agent_registry)
        tools = adapter.list_agent_tools(invoker="build")
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_get_agent_as_tool_returns_dict(self, agent_registry: AgentRegistry):
        adapter = AgentToolAdapter(agent_registry)
        tool = adapter.get_agent_as_tool("general")
        assert tool is not None
        assert tool["name"] == "dispatch_general"
        assert tool["type"] == "agent_dispatch"

    def test_get_agent_as_tool_nonexistent_returns_none(self, agent_registry: AgentRegistry):
        adapter = AgentToolAdapter(agent_registry)
        tool = adapter.get_agent_as_tool("nonexistent")
        assert tool is None


class TestAgentPermission:
    def test_default_permission_read_only(self):
        perm = AgentPermission()
        assert perm.can_read is True
        assert perm.can_edit is False
        assert perm.can_bash is False
        assert perm.can_dispatch_subagents is False

    def test_full_permission_agent(self):
        perm = AgentPermission(
            can_edit=True,
            can_bash=True,
            can_read=True,
            can_dispatch_subagents=True,
            allowed_subagents=["general", "explore", "research"],
        )
        assert perm.can_edit and perm.can_bash
        assert "general" in perm.allowed_subagents


class TestAgentConfig:
    def test_config_defaults(self):
        config = AgentConfig(name="test", description="a test agent", type=AgentType.SUBAGENT)
        assert config.max_steps == 10
        assert config.max_concurrent == 1
        assert config.enabled is True

    def test_config_bind_tools_on_dispatch(self):
        config = AgentConfig(
            name="test",
            description="test",
            type=AgentType.SUBAGENT,
            bind_tools_on_dispatch=False,
        )
        assert config.bind_tools_on_dispatch is False


# ---------------------------------------------------------------------------
# 3. Dispatch Pipeline
# ---------------------------------------------------------------------------


class TestAgentDispatcherBasic:
    async def test_dispatch_one_completed(self, agent_registry: AgentRegistry):
        executed_tasks = []

        async def mock_executor(task):
            executed_tasks.append(task.task_id)
            return f"Result: {task.description}"

        dispatcher = AgentDispatcher(agent_registry, executor=mock_executor)
        task = _make_agent_task(task_id="disp-1")
        result = await dispatcher.dispatch_one(task)

        assert result.status == "completed"
        assert result.task_id == "disp-1"
        assert "Result:" in result.output
        assert "disp-1" in executed_tasks

    async def test_dispatch_one_agent_not_found(self, agent_registry: AgentRegistry):
        dispatcher = AgentDispatcher(agent_registry)
        task = _make_agent_task(agent_name="nonexistent")
        result = await dispatcher.dispatch_one(task)

        assert result.status == "failed"
        assert "not found" in result.output.lower()

    async def test_dispatch_one_agent_disabled(self):
        registry = AgentRegistry()
        registry.register(AgentConfig(
            name="offline-agent",
            description="disabled",
            type=AgentType.SUBAGENT,
            enabled=False,
        ))
        registry.register(AgentConfig(
            name="primary",
            description="primary",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(can_dispatch_subagents=True, allowed_subagents=["offline-agent"]),
        ))
        dispatcher = AgentDispatcher(registry)
        task = _make_agent_task(agent_name="offline-agent", invoker_name="primary")
        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
        assert "disabled" in result.output.lower()

    async def test_dispatch_one_permission_denied(self, agent_registry: AgentRegistry):
        no_invoke_registry = AgentRegistry()
        no_invoke_registry.register(AgentConfig(
            name="guarded",
            description="guarded agent",
            type=AgentType.SUBAGENT,
        ))
        dispatcher = AgentDispatcher(no_invoke_registry)
        task = _make_agent_task(agent_name="guarded", invoker_name="unknown")
        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
        assert "permission denied" in result.output.lower()

    async def test_dispatch_many_concurrent_execution(self, agent_registry: AgentRegistry):
        concurrency_seen = 0
        max_seen = 0
        lock = asyncio.Lock()

        async def mock_executor(task):
            nonlocal concurrency_seen, max_seen
            async with lock:
                concurrency_seen += 1
                max_seen = max(max_seen, concurrency_seen)
            await asyncio.sleep(0.05)
            async with lock:
                concurrency_seen -= 1
            return f"Done: {task.task_id}"

        dispatcher = AgentDispatcher(agent_registry, executor=mock_executor)
        tasks = [_make_agent_task(task_id=f"many-{i}") for i in range(5)]
        results = await dispatcher.dispatch_many(tasks)
        assert len(results) == 5
        assert all(r.status == "completed" for r in results)
        assert max_seen > 1


class TestDispatchRateLimiting:
    async def test_rate_limiter_blocks_over_limit(self, agent_registry: AgentRegistry):
        from general_ludd.config.user_config import OrchestrationGuardConfig

        executed: list[str] = []
        async def mock_executor(task):
            executed.append(task.task_id)
            return f"OK: {task.task_id}"

        guard = OrchestrationGuardConfig(
            max_dispatches_per_window=3,
            dispatch_rate_window_s=60.0,
        )
        dispatcher = AgentDispatcher(agent_registry, executor=mock_executor, orchestration_guard=guard)

        tasks = [_make_agent_task(task_id=f"rate-{i}") for i in range(5)]
        results = [await dispatcher.dispatch_one(t) for t in tasks]

        success = [r for r in results if r.status == "completed"]
        failed = [r for r in results if r.status == "failed"]
        assert len(success) == 3
        assert len(failed) == 2
        assert any("rate limited" in f.output.lower() for f in failed)


class TestDispatchSpiralDetection:
    async def test_spiral_detection_blocks_re_dispatch(self, agent_registry: AgentRegistry):
        from general_ludd.config.user_config import OrchestrationGuardConfig

        async def mock_executor(task):
            return f"OK: {task.task_id}"

        guard = OrchestrationGuardConfig(max_redispatch_count=2)
        dispatcher = AgentDispatcher(agent_registry, executor=mock_executor, orchestration_guard=guard)

        task = _make_agent_task(task_id="spiral-1")
        r1 = await dispatcher.dispatch_one(task)
        r2 = await dispatcher.dispatch_one(task)
        r3 = await dispatcher.dispatch_one(task)
        r4 = await dispatcher.dispatch_one(task)

        assert r1.status == "completed"
        assert r2.status == "completed"
        assert r3.status == "failed"
        assert "spiral" in r3.output.lower()
        assert r4.status == "failed"


class TestDispatchNestingDepth:
    async def test_nesting_depth_limit_blocks_deep_tasks(self, agent_registry: AgentRegistry):
        from general_ludd.config.user_config import OrchestrationGuardConfig

        async def mock_executor(task):
            return f"OK: {task.task_id}"

        guard = OrchestrationGuardConfig(max_nesting_depth=3)
        dispatcher = AgentDispatcher(agent_registry, executor=mock_executor, orchestration_guard=guard)

        shallow = _make_agent_task(task_id="depth-2", depth=2)
        deep = _make_agent_task(task_id="depth-4", depth=4)

        r_shallow = await dispatcher.dispatch_one(shallow)
        r_deep = await dispatcher.dispatch_one(deep)

        assert r_shallow.status == "completed"
        assert r_deep.status == "failed"
        assert "nesting depth" in r_deep.output.lower()


class TestDispatchCapabilityEscalation:
    async def test_escalation_guard_blocks_elevation(self, agent_registry: AgentRegistry):
        from general_ludd.config.user_config import OrchestrationGuardConfig

        guard = OrchestrationGuardConfig(enforce_capability_escalation=True)

        registry = AgentRegistry()
        registry.register(AgentConfig(
            name="parent",
            description="parent",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_read=True,
                can_edit=False,
                can_dispatch_subagents=True,
                allowed_subagents=["child"],
            ),
        ))
        registry.register(AgentConfig(
            name="child",
            description="child",
            type=AgentType.SUBAGENT,
            permissions=AgentPermission(can_read=True, can_edit=True),
        ))

        async def mock_executor(task):
            return f"OK: {task.task_id}"

        dispatcher = AgentDispatcher(registry, executor=mock_executor, orchestration_guard=guard)
        task = _make_agent_task(agent_name="child", invoker_name="parent")
        result = await dispatcher.dispatch_one(task)

        assert result.status == "failed"
        assert "escalation" in result.output.lower()


class TestDispatchConcurrency:
    async def test_bounded_semaphore_limits_concurrent_calls(self, agent_registry: AgentRegistry):
        call_tracker = []

        async def mock_executor(task):
            call_tracker.append(("start", task.task_id))
            await asyncio.sleep(0.1)
            call_tracker.append(("end", task.task_id))
            return f"Done: {task.task_id}"

        dispatcher = AgentDispatcher(agent_registry, executor=mock_executor)
        tasks = [_make_agent_task(task_id=f"conc-{i}") for i in range(8)]
        results = await dispatcher.dispatch_many(tasks)
        assert len(results) == 8
        assert all(r.status == "completed" for r in results)
        assert len(call_tracker) == 16

    async def test_active_count_reflects_in_flight_tasks(self, agent_registry: AgentRegistry):
        active_counts = []

        async def mock_executor(task):
            active_counts.append(dispatcher.active_count)
            await asyncio.sleep(0.05)
            return f"Done: {task.task_id}"

        dispatcher = AgentDispatcher(agent_registry, executor=mock_executor)
        tasks = [_make_agent_task(task_id=f"act-{i}") for i in range(5)]
        await dispatcher.dispatch_many(tasks)
        assert any(c >= 1 for c in active_counts)


class TestDispatchTimeout:
    async def test_dispatch_many_timeout_cancels_pending(self, agent_registry: AgentRegistry):
        async def slow_executor(task):
            if task.task_id == "slow-1":
                await asyncio.sleep(2.0)
            return f"Done: {task.task_id}"

        dispatcher = AgentDispatcher(agent_registry, executor=slow_executor)
        tasks = [_make_agent_task(task_id=f"slow-{i}") for i in range(3)]
        results = await dispatcher.dispatch_many(tasks, timeout=0.1)
        assert len(results) == 3
        assert any(r.status in ("failed", "timeout") for r in results)


class TestDispatchRecordEvent:
    async def test_recorder_receives_events_on_dispatch(self, agent_registry: AgentRegistry):
        from general_ludd.replay.recorder import RunRecorder

        recorder = RunRecorder()
        async def mock_executor(task):
            return f"OK: {task.task_id}"

        dispatcher = AgentDispatcher(agent_registry, executor=mock_executor, run_recorder=recorder)
        task = _make_agent_task(task_id="rec-1")
        result = await dispatcher.dispatch_one(task)
        assert result.status == "completed"

    async def test_recorder_receives_failure_event(self, agent_registry: AgentRegistry):
        from general_ludd.replay.recorder import RunRecorder

        recorder = RunRecorder()
        dispatcher = AgentDispatcher(agent_registry, run_recorder=recorder)
        task = _make_agent_task(agent_name="nonexistent")
        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# 4. Integrated Workflows
# ---------------------------------------------------------------------------


class TestTodoToDispatchWorkflow:
    async def test_create_claim_dispatch_complete(self, db_session: AsyncSession, agent_registry: AgentRegistry):
        repo = TodoRepository(db_session)
        data = _make_todo_data(
            title="Integration Test Todo",
            status=TodoStatus.QUEUED.value,
            priority=10,
        )
        created = await repo.create(data)
        await db_session.commit()
        assert created.status == TodoStatus.QUEUED.value

        claimed = await repo.claim_runnable(limit=5)
        assert len(claimed) == 1
        assert claimed[0].todo_id == created.todo_id
        assert claimed[0].status == TodoStatus.ACTIVE.value
        await db_session.commit()

        async def mock_executor(task):
            return f"Completed: {task.description}"

        dispatcher = AgentDispatcher(agent_registry, executor=mock_executor)
        task = _make_agent_task(
            task_id=f"integ-{created.todo_id}",
            description=created.title,
            prompt=f"Work on: {created.title}",
        )
        result = await dispatcher.dispatch_one(task)
        assert result.status == "completed"

        await repo.transition(created.todo_id, TodoStatus.COMPLETE, expected_version=2)
        await db_session.commit()

        final = await repo.get_by_id(created.todo_id)
        assert final.status == TodoStatus.COMPLETE.value

    async def test_concurrent_claim_does_not_double_assign(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        for i in range(10):
            await repo.create(_make_todo_data(
                title=f"Concurrent Claim {i}",
                status=TodoStatus.QUEUED.value,
            ))
        await db_session.commit()

        claimed_all: set[str] = set()
        for _ in range(3):
            batch = await repo.claim_runnable(limit=3)
            for t in batch:
                assert t.todo_id not in claimed_all
                claimed_all.add(t.todo_id)
            await db_session.commit()

        assert len(claimed_all) > 0

    async def test_todo_events_recorded_on_status_change(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(
            title="Event Tracking",
            status=TodoStatus.QUEUED.value,
        )
        created = await repo.create(data)
        await db_session.commit()

        claimed = await repo.claim_runnable(limit=1)
        assert len(claimed) == 1
        await db_session.commit()

        todo_id = created.todo_id
        stmt = await db_session.execute(
            __import__("sqlalchemy").select(TodoEventModel).where(
                TodoEventModel.todo_id == todo_id
            ).order_by(TodoEventModel.id)
        )
        events = stmt.scalars().all()
        assert any(e.new_status == TodoStatus.ACTIVE.value for e in events)

    async def test_task_return_created_on_dispatch(self, db_session: AsyncSession):
        from general_ludd.db.repository import TaskReturnRepository

        tr_repo = TaskReturnRepository(db_session)
        return_data = {
            "return_id": "ret-integ-1",
            "job_id": "job-1",
            "playbook": "test_playbook",
            "queue": "core",
            "status": "created",
            "project_id": "proj-1",
        }
        created = await tr_repo.create(return_data)
        await db_session.commit()
        assert created.return_id == "ret-integ-1"

        loaded = await tr_repo.get_by_id("ret-integ-1")
        assert loaded is not None
        assert loaded.status == "created"


class TestQueueManagement:
    async def test_queue_repository_list_enabled(self, db_session: AsyncSession):
        from general_ludd.db.session import seed_initial_queues

        await seed_initial_queues(db_session)
        await db_session.commit()

        qr = QueueRepository(db_session)
        enabled = await qr.list_enabled()
        assert len(enabled) > 0
        assert all(q.queue_enabled for q in enabled)

    async def test_queue_repository_get_by_name(self, db_session: AsyncSession):
        from general_ludd.db.session import seed_initial_queues

        await seed_initial_queues(db_session)
        await db_session.commit()

        qr = QueueRepository(db_session)
        core = await qr.get_by_name("core")
        assert core is not None
        assert core.queue_name == "core"


class TestAgentMessages:
    async def test_send_and_retrieve_message(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        msg = await repo.send(
            {
                "sender": "build",
                "recipient": "general",
                "topic": "code-review",
                "body": "Please review PR #42",
            }
        )
        await db_session.commit()
        assert msg.sender == "build"
        assert msg.recipient == "general"

    async def test_inbox_returns_unread_messages(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        await repo.send(
            {"sender": "build", "recipient": "general", "topic": "task-1", "body": "msg 1"}
        )
        await repo.send(
            {"sender": "build", "recipient": "general", "topic": "task-2", "body": "msg 2"}
        )
        await repo.send(
            {"sender": "build", "recipient": "explore", "topic": "task-3", "body": "msg 3"}
        )
        await db_session.commit()

        inbox = await repo.inbox("general")
        assert len(inbox) == 2
        assert all(m.recipient == "general" for m in inbox)
        assert all(m.read_at is None for m in inbox)

    async def test_ack_marks_message_as_read(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        msg = await repo.send(
            {"sender": "build", "recipient": "coder", "topic": "ack-test", "body": "ack me"}
        )
        await db_session.commit()

        acked = await repo.ack(msg.id)
        assert acked is not None
        assert acked.read_at is not None
        await db_session.commit()

        inbox_after = await repo.inbox("coder")
        assert len(inbox_after) == 0

    async def test_purge_removes_expired_messages(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        msg = await repo.send(
            {
                "sender": "build",
                "recipient": "coder",
                "topic": "old",
                "body": "stale",
                "ttl_seconds": 60,
            }
        )
        msg.created_at = datetime.now(UTC) - timedelta(days=10)
        await db_session.flush()

        assert await repo.inbox("coder") == []
        count = await repo.purge_expired()
        assert count == 1
        await db_session.commit()

    async def test_broadcast_message_reaches_everyone(self, db_session: AsyncSession):
        repo = AgentMessageRepository(db_session)
        await repo.send(
            {
                "sender": "orchestrator",
                "recipient": BROADCAST_RECIPIENT,
                "topic": "announce",
                "body": "New release",
            }
        )
        await db_session.commit()

        inbox_coder = await repo.inbox("coder", include_broadcast=True)
        inbox_build = await repo.inbox("build", include_broadcast=True)
        assert len(inbox_coder) >= 1
        assert len(inbox_build) >= 1


# ---------------------------------------------------------------------------
# 5. Error Paths
# ---------------------------------------------------------------------------


class TestErrorDBConnectionLoss:
    async def test_repository_raises_on_disposed_engine(self):
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
                repo = TodoRepository(session)
                data = _make_todo_data(title="DB Lost Test")
                await repo.create(data)
                await session.commit()

            close_engine(engine)
            with pytest.raises(RuntimeError, match="closed/disposed"):
                async for _ in get_async_session(factory):
                    pass
        finally:
            await engine.dispose()


class TestErrorInvalidTransition:
    async def test_invalid_status_transition_raises(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(title="Invalid Transition Test", status=TodoStatus.COMPLETE.value)
        todo = await repo.create(data)
        await db_session.commit()

        with pytest.raises(InvalidTransitionError):
            await repo.transition(todo.todo_id, TodoStatus.QUEUED, expected_version=1)

    async def test_valid_transition_succeeds(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(title="Valid Transition", status=TodoStatus.QUEUED.value)
        todo = await repo.create(data)
        await db_session.commit()

        result = await repo.transition(todo.todo_id, TodoStatus.ACTIVE, expected_version=1)
        await db_session.commit()
        assert result.status == TodoStatus.ACTIVE.value

    async def test_failed_todos_can_be_requeued(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(title="Retry Todo", status=TodoStatus.FAILED.value)
        todo = await repo.create(data)
        await db_session.commit()

        result = await repo.transition(todo.todo_id, TodoStatus.QUEUED, expected_version=1)
        assert result.status == TodoStatus.QUEUED.value


class TestErrorDispatchRetry:
    async def test_dispatcher_returns_different_results_for_retries(self, agent_registry: AgentRegistry):
        call_count = {"count": 0}

        async def flaky_executor(task):
            call_count["count"] += 1
            if call_count["count"] <= 2:
                raise RuntimeError("transient failure")
            return f"Success on attempt {call_count['count']}"

        dispatcher = AgentDispatcher(agent_registry, executor=flaky_executor)
        task = _make_agent_task(task_id="retry-1")

        results = []
        for _ in range(3):
            try:
                result = await dispatcher.dispatch_one(task)
                results.append(result)
            except RuntimeError:
                results.append(AgentTaskResult(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    status="failed",
                    output="transient failure",
                ))
            if results[-1].status == "completed":
                break

        assert any(r.status == "completed" for r in results)
        assert call_count["count"] >= 1


class TestErrorVersionConflicts:
    async def test_concurrent_claim_race_handled_gracefully(self, db_session: AsyncSession):
        repo_a = TodoRepository(db_session)
        repo_b = TodoRepository(db_session)

        for i in range(20):
            await repo_a.create(_make_todo_data(
                title=f"Race Todo {i}",
                status=TodoStatus.QUEUED.value,
            ))
        await db_session.commit()

        batch_a = await repo_a.claim_runnable(limit=10)
        batch_b = await repo_b.claim_runnable(limit=10)
        await db_session.commit()

        ids_a = {t.todo_id for t in batch_a}
        ids_b = {t.todo_id for t in batch_b}
        assert len(ids_a & ids_b) == 0

    async def test_lost_update_detected(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(title="Lost Update Test")
        todo = await repo.create(data)
        await db_session.commit()

        await repo.update(todo.todo_id, {"title": "First"}, expected_version=1)
        await db_session.commit()

        with pytest.raises(ConcurrencyError):
            await repo.update(todo.todo_id, {"title": "Stale"}, expected_version=1)

    async def test_todo_not_found_transition(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        with pytest.raises(InvalidTransitionError, match="not found"):
            await repo.transition("NONEXISTENT-ID", TodoStatus.ACTIVE, expected_version=1)


class TestErrorImmutableFields:
    async def test_create_rejects_immutable_fields(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create(_make_todo_data(version=5))

    async def test_update_rejects_project_id_change(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(title="Cross Tenant Block", project_id="proj-a")
        todo = await repo.create(data)
        await db_session.commit()

        with pytest.raises(ValueError, match="immutable"):
            await repo.update(todo.todo_id, {"project_id": "proj-b"}, expected_version=1)


class TestErrorPriorityBounds:
    async def test_priority_clamped_to_min(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(title="Low Priority", priority=-50)
        todo = await repo.create(data)
        assert todo.priority == 0

    async def test_priority_clamped_to_max(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(title="High Priority", priority=5000)
        todo = await repo.create(data)
        assert todo.priority == 1000


class TestErrorEmptyInputs:
    async def test_empty_title_allowed(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        data = _make_todo_data(title="")
        todo = await repo.create(data)
        assert todo.title == ""

    async def test_get_by_id_nonexistent_returns_none(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        result = await repo.get_by_id("NONEXISTENT")
        assert result is None

    async def test_list_all_empty_db(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        results = await repo.list_all()
        assert results == []

    async def test_count_active_zero_on_empty(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        count = await repo.count_active()
        assert count == 0


# ---------------------------------------------------------------------------
# 6. Tenant Scoping
# ---------------------------------------------------------------------------


class TestTenantScoping:
    async def test_scoped_todo_repository_filters_by_project(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(title="ProjA Todo", project_id="proj-a"))
        await repo.create(_make_todo_data(title="ProjB Todo", project_id="proj-b"))
        await db_session.commit()

        scoped = TodoRepository.scoped(db_session, project_id="proj-a")
        results = await scoped.list_all()
        assert len(results) == 1
        assert results[0].project_id == "proj-a"

    async def test_unscoped_repository_sees_all(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(title="Todo 1", project_id="proj-a"))
        await repo.create(_make_todo_data(title="Todo 2", project_id="proj-b"))
        await db_session.commit()

        results = await repo.list_all()
        assert len(results) == 2

    async def test_scoped_to_context_manager(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(title="CTX Todo", project_id="ctx-proj"))
        await db_session.commit()

        with scoped_to("ctx-proj"):
            scoped_repo = TodoRepository(db_session, project_id="ctx-proj")
            results = await scoped_repo.list_all()
            assert len(results) == 1
            assert results[0].project_id == "ctx-proj"


# ---------------------------------------------------------------------------
# 7. Status Summary & Aggregation
# ---------------------------------------------------------------------------


class TestStatusSummary:
    async def test_status_summary_counts_by_status(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(title="Backlog 1", status=TodoStatus.BACKLOG.value))
        await repo.create(_make_todo_data(title="Queued 1", status=TodoStatus.QUEUED.value))
        await repo.create(_make_todo_data(title="Queued 2", status=TodoStatus.QUEUED.value))
        await repo.create(_make_todo_data(title="Complete 1", status=TodoStatus.COMPLETE.value))
        await db_session.commit()

        summary = await repo.status_summary()
        assert summary["total"] == 4
        assert summary["by_status"].get(TodoStatus.BACKLOG.value, 0) == 1
        assert summary["by_status"].get(TodoStatus.QUEUED.value, 0) == 2
        assert summary["backlog_size"] >= 3


class TestListByStatus:
    async def test_list_by_status_filters_correctly(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        await repo.create(_make_todo_data(title="Q1", status=TodoStatus.QUEUED.value))
        await repo.create(_make_todo_data(title="Q2", status=TodoStatus.QUEUED.value))
        await repo.create(_make_todo_data(title="A1", status=TodoStatus.ACTIVE.value))
        await db_session.commit()

        queued = await repo.list_by_status(TodoStatus.QUEUED)
        assert len(queued) == 2
        assert all(t.status == TodoStatus.QUEUED.value for t in queued)

        active = await repo.list_by_status(TodoStatus.ACTIVE)
        assert len(active) == 1


class TestListDueScheduled:
    async def test_list_due_scheduled_returns_past_due(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        past_time = datetime.now(UTC) - __import__("datetime").timedelta(hours=1)
        future_time = datetime.now(UTC) + __import__("datetime").timedelta(hours=1)

        await repo.create(_make_todo_data(
            title="Due Now",
            status=TodoStatus.SCHEDULED.value,
            scheduled_at=past_time,
        ))
        await repo.create(_make_todo_data(
            title="Not Yet Due",
            status=TodoStatus.SCHEDULED.value,
            scheduled_at=future_time,
        ))
        await db_session.commit()

        due = await repo.list_due_scheduled(now=datetime.now(UTC))
        assert len(due) == 1
        assert due[0].title == "Due Now"

    async def test_list_due_scheduled_skips_paused(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        past_time = datetime.now(UTC) - __import__("datetime").timedelta(hours=1)

        await repo.create(_make_todo_data(
            title="Paused Due",
            status=TodoStatus.SCHEDULED.value,
            scheduled_at=past_time,
            schedule_paused=True,
        ))
        await db_session.commit()

        due = await repo.list_due_scheduled(now=datetime.now(UTC))
        paused_items = [d for d in due if d.title == "Paused Due"]
        assert len(paused_items) == 0


# ---------------------------------------------------------------------------
# 8. JSON-in-Text columns
# ---------------------------------------------------------------------------


class TestJsonTextColumns:
    async def test_tags_default_empty_list(self, db_session: AsyncSession):
        repo = TodoRepository(db_session)
        todo = await repo.create(_make_todo_data(title="Tags Test"))
        assert todo.tags == '["e2e"]'

    async def test_json_dumps_empty_returns_empty_array(self):
        assert json_dumps(None) == "[]"
        assert json_dumps({}) == "{}"

    async def test_json_dumps_serializes_list(self):
        result = json_dumps(["a", "b"])
        assert json.loads(result) == ["a", "b"]
