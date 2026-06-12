"""Tests for end-to-end data flow: DB wired into daemon, all pipelines functional."""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import (
    AuditEventModel,
    Base,
    TaskDecisionModel,
    TodoModel,
)
from general_ludd.db.repository import AuditEventRepository, TaskReturnRepository
from general_ludd.db.session import (
    get_default_db_path,
    get_default_db_url,
    init_engine_from_config,
    is_sqlite_url,
    run_wal_pragmas,
)


class TestSqliteDefault:
    def test_default_url_is_sqlite(self):
        url = get_default_db_url()
        assert "sqlite" in url
        assert "aiosqlite" in url

    def test_sqlite_detected(self):
        assert is_sqlite_url("sqlite+aiosqlite:///foo.db")

    def test_postgres_not_sqlite(self):
        assert not is_sqlite_url("postgresql+psycopg://localhost/gludd")

    def test_default_db_path_has_general_ludd(self):
        path = get_default_db_path()
        assert "general-ludd" in str(path)

    def test_empty_config_returns_sqlite_engine(self):
        engine = init_engine_from_config({})
        assert "sqlite" in str(engine.url)
        engine.sync_engine.dispose()

    def test_postgres_config_is_refused(self):
        # W3.5 (M8/H18): SQLite-only — a Postgres URL is refused, not silently
        # accepted into a half-broken (SQLite-only-schema) engine.
        with pytest.raises(ValueError, match=r"SQLite only"):
            init_engine_from_config({"url": "postgresql+psycopg://localhost/gludd"})

    @pytest.mark.asyncio
    async def test_wal_mode_set(self, tmp_path):
        db = tmp_path / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        run_wal_pragmas(engine)
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            assert result.scalar() == "wal"
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_busy_timeout_set(self, tmp_path):
        db = tmp_path / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        run_wal_pragmas(engine)
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA busy_timeout"))
            assert result.scalar() == 5000
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_foreign_keys_on(self, tmp_path):
        db = tmp_path / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        run_wal_pragmas(engine)
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA foreign_keys"))
            assert result.scalar() == 1
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_auto_creates_tables(self, tmp_path):
        db = tmp_path / "auto.db"
        engine = init_engine_from_config({"url": f"sqlite+aiosqlite:///{db}"})
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        assert db.exists()
        async with engine.begin() as conn:
            tables = await conn.run_sync(
                lambda c: c.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            )
            names = [row[0] for row in tables]
            assert "todos" in names
            assert "projects" in names
            assert "task_returns" in names
        await engine.dispose()


class TestTaskReturnPersistence:
    @pytest_asyncio.fixture
    async def session(self, tmp_path):
        db = tmp_path / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        run_wal_pragmas(engine)

        @event.listens_for(engine.sync_engine, "connect")
        def _fk(conn, _):
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            yield s
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_task_return_repo_creates_row(self, session: AsyncSession):
        repo = TaskReturnRepository(session)
        tr = await repo.create(
            data={
                "return_id": "RET-001",
                "todo_id": "TODO-001",
                "job_id": "EXEC-001",
                "playbook": "noop.yml",
                "queue": "core",
                "exit_code": 0,
                "result_summary": "Success",
            }
        )
        assert tr.id is not None
        assert tr.return_id == "RET-001"
        assert tr.exit_code == 0

    @pytest.mark.asyncio
    async def test_task_return_repo_claim_unreviewed(self, session: AsyncSession):
        repo = TaskReturnRepository(session)
        await repo.create(
            data={
                "return_id": "RET-002",
                "todo_id": "TODO-002",
                "job_id": "EXEC-002",
                "playbook": "noop.yml",
                "queue": "core",
                "exit_code": 0,
                "result_summary": "Done",
            }
        )
        claimed = await repo.claim_unreviewed()
        assert len(claimed) == 1
        assert claimed[0].return_id == "RET-002"


class TestDecisionPersistence:
    @pytest_asyncio.fixture
    async def session(self, tmp_path):
        db = tmp_path / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        run_wal_pragmas(engine)

        @event.listens_for(engine.sync_engine, "connect")
        def _fk(conn, _):
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            yield s
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_decision_persisted_to_db(self, session: AsyncSession):
        decision = TaskDecisionModel(
            return_id="RET-001",
            matched_todo_id="TODO-001",
            decision="complete",
            confidence=0.95,
            evidence_refs='["test_evidence"]',
            audit_notes='["All tests pass"]',
        )
        session.add(decision)
        await session.flush()
        assert decision.id is not None
        assert decision.decision == "complete"

    @pytest.mark.asyncio
    async def test_decision_with_project_id(self, session: AsyncSession):
        from general_ludd.db.models import ProjectModel

        project = ProjectModel(
            project_id="proj-abc",
            name="Test Project",
        )
        session.add(project)
        await session.flush()
        decision = TaskDecisionModel(
            return_id="RET-003",
            project_id="proj-abc",
            matched_todo_id="TODO-003",
            decision="needs_more_work",
            confidence=0.7,
        )
        session.add(decision)
        await session.flush()
        assert decision.project_id == "proj-abc"


class TestAuditEventPersistence:
    @pytest_asyncio.fixture
    async def session(self, tmp_path):
        db = tmp_path / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        run_wal_pragmas(engine)

        @event.listens_for(engine.sync_engine, "connect")
        def _fk(conn, _):
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            yield s
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_audit_event_written_on_todo_create(self, session: AsyncSession):
        todo = TodoModel(title="Test todo")
        session.add(todo)
        await session.flush()
        audit = AuditEventModel(
            event_type="todo_created",
            entity_type="todo",
            entity_id=todo.todo_id,
            details=json.dumps({"title": "Test todo"}),
        )
        session.add(audit)
        await session.flush()
        assert audit.id is not None
        assert audit.event_type == "todo_created"

    @pytest.mark.asyncio
    async def test_audit_event_written_on_status_change(self, session: AsyncSession):
        todo = TodoModel(title="Status change test")
        session.add(todo)
        await session.flush()
        audit = AuditEventModel(
            event_type="todo_status_changed",
            entity_type="todo",
            entity_id=todo.todo_id,
            details=json.dumps({"old": "backlog", "new": "queued"}),
        )
        session.add(audit)
        await session.flush()
        assert audit.id is not None


class TestQueueSeeding:
    @pytest_asyncio.fixture
    async def session(self, tmp_path):
        db = tmp_path / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        run_wal_pragmas(engine)

        @event.listens_for(engine.sync_engine, "connect")
        def _fk(conn, _):
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            yield s
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_seed_creates_12_queues(self, session: AsyncSession):
        from general_ludd.db.session import seed_initial_queues

        count = await seed_initial_queues(session)
        await session.flush()
        assert count == 12
        result = await session.execute(text("SELECT COUNT(*) FROM queues"))
        assert result.scalar() == 12

    @pytest.mark.asyncio
    async def test_seed_idempotent(self, session: AsyncSession):
        from general_ludd.db.session import seed_initial_queues

        await seed_initial_queues(session)
        await session.flush()
        count2 = await seed_initial_queues(session)
        await session.flush()
        assert count2 == 0
        result = await session.execute(text("SELECT COUNT(*) FROM queues"))
        assert result.scalar() == 12

    @pytest.mark.asyncio
    async def test_core_queue_exists(self, session: AsyncSession):
        from general_ludd.db.session import seed_initial_queues

        await seed_initial_queues(session)
        await session.flush()
        result = await session.execute(
            text("SELECT queue_name FROM queues WHERE queue_name='core'")
        )
        assert result.scalar() == "core"


class TestAuditEventRepository:
    @pytest_asyncio.fixture
    async def session(self, tmp_path):
        db = tmp_path / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        run_wal_pragmas(engine)

        @event.listens_for(engine.sync_engine, "connect")
        def _fk(conn, _):
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            yield s
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_audit_repo_creates_event(self, session: AsyncSession):
        repo = AuditEventRepository(session)
        audit = await repo.create(
            event_type="todo_created",
            entity_type="todo",
            entity_id="TODO-001",
            details=json.dumps({"title": "Test"}),
        )
        assert audit.id is not None
        assert audit.event_type == "todo_created"
        assert audit.entity_id == "TODO-001"

    @pytest.mark.asyncio
    async def test_audit_repo_lists_by_entity(self, session: AsyncSession):
        repo = AuditEventRepository(session)
        await repo.create(
            event_type="todo_created",
            entity_type="todo",
            entity_id="TODO-002",
        )
        await repo.create(
            event_type="todo_status_changed",
            entity_type="todo",
            entity_id="TODO-002",
        )
        events = await repo.list_by_entity("todo", "TODO-002")
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_audit_repo_lists_by_project(self, session: AsyncSession):
        from general_ludd.db.models import ProjectModel

        project = ProjectModel(project_id="proj-x", name="Audit Test")
        session.add(project)
        await session.flush()

        repo = AuditEventRepository(session)
        await repo.create(
            event_type="task_return_created",
            entity_type="task_return",
            entity_id="RET-001",
            project_id="proj-x",
        )
        events = await repo.list_by_project("proj-x")
        assert len(events) == 1
        assert events[0].project_id == "proj-x"
