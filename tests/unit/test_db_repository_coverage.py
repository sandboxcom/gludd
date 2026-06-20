"""Coverage-focused unit tests for db.repository.

Targets the lower-coverage branches not exercised by test_db_models.py:
  * TodoRepository.create() mass-assignment guard (immutable fields,
    oversized text) and the version=1 belt-and-suspenders.
  * scoped() tenant isolation across get/update/list/transition.
  * status_summary / work_summary / history_summary aggregation.
  * Optimistic-version / not-found guards on update() and transition().
  * The repositories with no existing coverage: AuditEvent, VariableNamespace,
    Project, AgentMessage, Feature, Spend, RoleRun, Benchmark, PromptProfile.

Uses the same in-memory SQLite async fixtures as test_db_models.py so it runs
without a PostgreSQL instance.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import (
    AuditEventType,
    Base,
    FeatureStatus,
    ProjectModel,
)
from general_ludd.db.repository import (
    AgentMessageRepository,
    AuditEventRepository,
    BenchmarkRepository,
    FeatureRepository,
    InvalidTransitionError,
    ProjectRepository,
    PromptProfileRepository,
    QueueRepository,
    RoleRunRepository,
    SpendRepository,
    TaskReturnRepository,
    TodoRepository,
    VariableNamespaceRepository,
)
from general_ludd.schemas.todo import TodoStatus


def _make_async_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest_asyncio.fixture
async def async_engine():
    engine = _make_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def session_factory(async_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


async def _make_project(session: AsyncSession, project_id: str) -> ProjectModel:
    p = ProjectModel(project_id=project_id, name=f"Project {project_id}")
    session.add(p)
    await session.flush()
    return p


# ---------------------------------------------------------------------------
# TodoRepository.create() mass-assignment guard (D-28)
# ---------------------------------------------------------------------------


class TestTodoCreateGuard:
    @pytest.mark.parametrize("field", ["id", "version", "created_at", "updated_at"])
    async def test_create_rejects_immutable_field(self, async_session: AsyncSession, field: str):
        repo = TodoRepository(async_session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create({"title": "x", field: 99})

    async def test_create_rejects_multiple_immutable_fields(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        with pytest.raises(ValueError, match="immutable") as exc:
            await repo.create({"title": "x", "id": 1, "version": 5})
        # sorted() in the message — both names present.
        assert "id" in str(exc.value)
        assert "version" in str(exc.value)

    async def test_create_rejects_oversized_text(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        big = "a" * (65_536 + 1)
        with pytest.raises(ValueError, match="exceeds"):
            await repo.create({"title": "ok", "description": big})

    async def test_create_accepts_text_at_limit(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        at_limit = "a" * 65_536
        todo = await repo.create({"title": "ok", "description": at_limit})
        assert len(todo.description.encode()) == 65_536

    async def test_create_forces_version_one(self, async_session: AsyncSession):
        # version is immutable (rejected), but the belt-and-suspenders forces 1
        # even on a fresh create that omits it.
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "fresh"})
        assert todo.version == 1

    async def test_create_allows_todo_id(self, async_session: AsyncSession):
        # todo_id is deliberately NOT immutable (business identifier).
        repo = TodoRepository(async_session)
        todo = await repo.create({"todo_id": "TODO-CUSTOM1", "title": "named"})
        assert todo.todo_id == "TODO-CUSTOM1"

    async def test_create_oversized_only_checks_strings(self, async_session: AsyncSession):
        # Non-string oversized values are not subject to the byte check.
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "ok", "priority": 999_999})
        assert todo.priority == 999_999


# ---------------------------------------------------------------------------
# scoped() tenant isolation
# ---------------------------------------------------------------------------


class TestTodoScopedIsolation:
    async def test_scoped_get_filters_by_project(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-a")
        await _make_project(async_session, "proj-b")
        repo_a = TodoRepository.scoped(async_session, "proj-a")
        repo_b = TodoRepository.scoped(async_session, "proj-b")
        todo = await repo_a.create({"title": "owned by a", "project_id": "proj-a"})
        # repo_a sees it; repo_b (other tenant) does not.
        assert await repo_a.get_by_id(todo.todo_id) is not None
        assert await repo_b.get_by_id(todo.todo_id) is None

    async def test_scoped_explicit_override_beats_scope(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-a")
        await _make_project(async_session, "proj-b")
        repo_a = TodoRepository.scoped(async_session, "proj-a")
        todo = await repo_a.create({"title": "b-owned", "project_id": "proj-b"})
        # Default scope (proj-a) misses it; explicit proj-b override finds it.
        assert await repo_a.get_by_id(todo.todo_id) is None
        assert await repo_a.get_by_id(todo.todo_id, project_id="proj-b") is not None

    async def test_scoped_list_by_status_isolated(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-a")
        await _make_project(async_session, "proj-b")
        repo_a = TodoRepository.scoped(async_session, "proj-a")
        repo_b = TodoRepository.scoped(async_session, "proj-b")
        await repo_a.create({"title": "a1", "project_id": "proj-a"})
        await repo_b.create({"title": "b1", "project_id": "proj-b"})
        a_backlog = await repo_a.list_by_status(TodoStatus.BACKLOG)
        assert {t.title for t in a_backlog} == {"a1"}

    async def test_scoped_list_all_filters(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-a")
        await _make_project(async_session, "proj-b")
        repo_a = TodoRepository.scoped(async_session, "proj-a")
        repo_b = TodoRepository.scoped(async_session, "proj-b")
        await repo_a.create({"title": "a1", "project_id": "proj-a", "queue": "core"})
        await repo_b.create({"title": "b1", "project_id": "proj-b", "queue": "core"})
        rows = await repo_a.list_all(queue="core")
        assert {t.title for t in rows} == {"a1"}

    async def test_scoped_list_by_work_type(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-a")
        repo_a = TodoRepository.scoped(async_session, "proj-a")
        await repo_a.create({"title": "a1", "project_id": "proj-a", "work_type": "code"})
        rows = await repo_a.list_by_work_type("code")
        assert len(rows) == 1
        assert rows[0].work_type == "code"

    async def test_scoped_count_active(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-a")
        repo_a = TodoRepository.scoped(async_session, "proj-a")
        t = await repo_a.create({"title": "a1", "project_id": "proj-a"})
        await repo_a.transition(t.todo_id, TodoStatus.QUEUED, expected_version=1)
        t2 = await repo_a.get_by_id(t.todo_id)
        await repo_a.transition(t.todo_id, TodoStatus.ACTIVE, expected_version=t2.version)
        assert await repo_a.count_active() == 1

    async def test_scoped_claim_runnable_isolated(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-a")
        await _make_project(async_session, "proj-b")
        repo_a = TodoRepository.scoped(async_session, "proj-a")
        repo_b = TodoRepository.scoped(async_session, "proj-b")
        ta = await repo_a.create({"title": "a-q", "project_id": "proj-a"})
        await repo_a.transition(ta.todo_id, TodoStatus.QUEUED, expected_version=1)
        tb = await repo_b.create({"title": "b-q", "project_id": "proj-b"})
        await repo_b.transition(tb.todo_id, TodoStatus.QUEUED, expected_version=1)
        claimed = await repo_a.claim_runnable(limit=10)
        assert {c.title for c in claimed} == {"a-q"}


# ---------------------------------------------------------------------------
# Optimistic-version + not-found guards
# ---------------------------------------------------------------------------


class TestTodoVersionGuards:
    async def test_update_not_found_raises_invalid_transition(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        with pytest.raises(InvalidTransitionError, match="not found"):
            await repo.update("TODO-MISSING", {"title": "x"}, expected_version=1)

    async def test_transition_not_found_raises(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        with pytest.raises(InvalidTransitionError, match="not found"):
            await repo.transition("TODO-MISSING", TodoStatus.QUEUED, expected_version=1)

    async def test_update_increments_version_and_syncs(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        created = await repo.create({"title": "v"})
        updated = await repo.update(created.todo_id, {"title": "v2"}, expected_version=1)
        assert updated.version == 2
        assert updated.title == "v2"
        # Second update must use the new version.
        again = await repo.update(created.todo_id, {"title": "v3"}, expected_version=2)
        assert again.version == 3

    async def test_transition_full_lifecycle(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        t = await repo.create({"title": "lifecycle"})
        t = await repo.transition(t.todo_id, TodoStatus.QUEUED, expected_version=1)
        t = await repo.transition(t.todo_id, TodoStatus.ACTIVE, expected_version=t.version)
        t = await repo.transition(t.todo_id, TodoStatus.COMPLETE, expected_version=t.version)
        assert t.status == TodoStatus.COMPLETE.value

    async def test_transition_complete_is_terminal(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        t = await repo.create({"title": "terminal"})
        t = await repo.transition(t.todo_id, TodoStatus.QUEUED, expected_version=1)
        t = await repo.transition(t.todo_id, TodoStatus.ACTIVE, expected_version=t.version)
        t = await repo.transition(t.todo_id, TodoStatus.COMPLETE, expected_version=t.version)
        with pytest.raises(InvalidTransitionError):
            await repo.transition(t.todo_id, TodoStatus.QUEUED, expected_version=t.version)


# ---------------------------------------------------------------------------
# status_summary aggregation
# ---------------------------------------------------------------------------


class TestStatusSummary:
    async def test_empty_summary(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        summary = await repo.status_summary()
        assert summary["total"] == 0
        assert summary["by_status"] == {}
        assert summary["oldest_age_seconds"] is None
        assert summary["backlog_size"] == 0

    async def test_summary_counts_and_backlog(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        await repo.create({"title": "b1", "queue": "core", "work_type": "code"})
        await repo.create({"title": "b2", "queue": "core", "work_type": "docs"})
        q = await repo.create({"title": "q1", "queue": "extra"})
        await repo.transition(q.todo_id, TodoStatus.QUEUED, expected_version=1)
        summary = await repo.status_summary()
        assert summary["total"] == 3
        assert summary["by_status"][TodoStatus.BACKLOG.value] == 2
        assert summary["by_status"][TodoStatus.QUEUED.value] == 1
        assert summary["by_queue"]["core"] == 2
        assert summary["by_queue"]["extra"] == 1
        assert summary["by_work_type"]["code"] == 1
        # backlog_size = BACKLOG + QUEUED
        assert summary["backlog_size"] == 3
        assert summary["oldest_age_seconds"] is not None
        assert summary["oldest_age_seconds"] >= 0

    async def test_summary_scoped(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-a")
        await _make_project(async_session, "proj-b")
        repo_a = TodoRepository.scoped(async_session, "proj-a")
        repo_b = TodoRepository.scoped(async_session, "proj-b")
        await repo_a.create({"title": "a1", "project_id": "proj-a"})
        await repo_b.create({"title": "b1", "project_id": "proj-b"})
        await repo_b.create({"title": "b2", "project_id": "proj-b"})
        assert (await repo_a.status_summary())["total"] == 1
        assert (await repo_b.status_summary())["total"] == 2


# ---------------------------------------------------------------------------
# TaskReturnRepository: work_summary / history_summary / scoping
# ---------------------------------------------------------------------------


class TestTaskReturnSummaries:
    async def _seed(self, repo: TaskReturnRepository, n: int, queue: str = "core"):
        for i in range(n):
            await repo.create(
                {
                    "return_id": f"R-{queue}-{i}",
                    "job_id": f"J-{i}",
                    "playbook": "noop.yml",
                    "queue": queue,
                    "work_type": "code",
                    "exit_code": 0 if i % 2 == 0 else 1,
                }
            )

    async def test_work_summary_empty(self, async_session: AsyncSession):
        repo = TaskReturnRepository(async_session)
        summary = await repo.work_summary()
        assert summary == {
            "total": 0,
            "by_status": {},
            "by_queue": {},
            "by_work_type": {},
        }

    async def test_work_summary_counts(self, async_session: AsyncSession):
        repo = TaskReturnRepository(async_session)
        await self._seed(repo, 3)
        summary = await repo.work_summary()
        assert summary["total"] == 3
        assert summary["by_status"]["created"] == 3
        assert summary["by_queue"]["core"] == 3
        assert summary["by_work_type"]["code"] == 3

    async def test_history_summary_rates(self, async_session: AsyncSession):
        repo = TaskReturnRepository(async_session)
        await self._seed(repo, 4)  # exit codes 0,1,0,1 -> 2 success, 2 failure
        hist = await repo.history_summary(recent_limit=2)
        assert hist["total_returns"] == 4
        assert hist["success_count"] == 2
        assert hist["failure_count"] == 2
        assert hist["success_rate"] == 0.5
        assert len(hist["recent"]) == 2
        assert "return_id" in hist["recent"][0]

    async def test_history_summary_empty_rate_zero(self, async_session: AsyncSession):
        repo = TaskReturnRepository(async_session)
        hist = await repo.history_summary()
        assert hist["total_returns"] == 0
        assert hist["success_rate"] == 0.0
        assert hist["recent"] == []

    async def test_get_by_id_not_found(self, async_session: AsyncSession):
        repo = TaskReturnRepository(async_session)
        assert await repo.get_by_id("R-NONE") is None

    async def test_claim_unreviewed_only_once(self, async_session: AsyncSession):
        repo = TaskReturnRepository(async_session)
        await self._seed(repo, 2)
        first = await repo.claim_unreviewed(limit=10)
        assert len(first) == 2
        assert all(r.status == "claimed_for_review" for r in first)
        # Already claimed -> not returned a second time.
        second = await repo.claim_unreviewed(limit=10)
        assert second == []


# ---------------------------------------------------------------------------
# AuditEventRepository
# ---------------------------------------------------------------------------


class TestAuditEventRepository:
    async def test_create_and_list_by_entity(self, async_session: AsyncSession):
        repo = AuditEventRepository(async_session)
        await repo.create(
            event_type="todo_created", entity_type="todo", entity_id="TODO-1", details="{}"
        )
        rows = await repo.list_by_entity("todo", "TODO-1")
        assert len(rows) == 1
        assert rows[0].entity_id == "TODO-1"

    async def test_record_typed_serializes_details(self, async_session: AsyncSession):
        repo = AuditEventRepository(async_session)
        row = await repo.record_typed(
            AuditEventType.TODO_STATUS_CHANGED,
            entity_type="todo",
            entity_id="TODO-2",
            details={"from": "backlog", "to": "queued"},
        )
        assert row.event_type == AuditEventType.TODO_STATUS_CHANGED.value
        assert '"from"' in row.details

    async def test_record_typed_no_details(self, async_session: AsyncSession):
        repo = AuditEventRepository(async_session)
        row = await repo.record_typed(
            AuditEventType.TODO_DELETED, entity_type="todo", entity_id="TODO-3"
        )
        assert row.details is None

    async def test_list_by_project(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-x")
        repo = AuditEventRepository(async_session)
        await repo.create(
            event_type="todo_created",
            entity_type="todo",
            entity_id="TODO-4",
            project_id="proj-x",
        )
        rows = await repo.list_by_project("proj-x")
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# VariableNamespaceRepository (upsert + TOCTOU-safe set_var)
# ---------------------------------------------------------------------------


class TestVariableNamespaceRepository:
    async def test_create_namespace_and_set_var(self, async_session: AsyncSession):
        repo = VariableNamespaceRepository(async_session)
        await repo.create_namespace("env")
        val = await repo.set_var("env", "KEY", "value1")
        assert val.key == "KEY"
        assert val.value == "value1"

    async def test_set_var_creates_namespace_implicitly(self, async_session: AsyncSession):
        repo = VariableNamespaceRepository(async_session)
        val = await repo.set_var("new_ns", "K", "v")
        assert val.value == "v"

    async def test_set_var_upsert_overwrites(self, async_session: AsyncSession):
        repo = VariableNamespaceRepository(async_session)
        await repo.set_var("ns", "K", "first")
        again = await repo.set_var("ns", "K", "second")
        assert again.value == "second"

    async def test_load_vars_for_project_prefers_project_over_global(
        self, async_session: AsyncSession
    ):
        await _make_project(async_session, "proj-v")
        repo = VariableNamespaceRepository(async_session)
        # global namespace + value
        await repo.set_var("cfg", "TOKEN", "global-val", project_id=None)
        # project-scoped namespace + value with same key
        await repo.set_var("cfg", "TOKEN", "project-val", project_id="proj-v")
        loaded = await repo.load_vars_for_project("proj-v")
        # ordering puts global last; project value wins in the dict overwrite.
        assert loaded["TOKEN"] == "project-val"

    async def test_load_vars_global_only(self, async_session: AsyncSession):
        repo = VariableNamespaceRepository(async_session)
        await repo.set_var("g", "ONLY", "x", project_id=None)
        loaded = await repo.load_vars_for_project(None)
        assert loaded["ONLY"] == "x"


# ---------------------------------------------------------------------------
# ProjectRepository
# ---------------------------------------------------------------------------


class TestProjectRepository:
    async def test_create_get_list_active(self, async_session: AsyncSession):
        repo = ProjectRepository(async_session)
        await repo.create({"project_id": "p1", "name": "One"})
        await repo.create({"project_id": "p2", "name": "Two"})
        assert (await repo.get_by_id("p1")).name == "One"
        active = await repo.list_active()
        assert {p.project_id for p in active} == {"p1", "p2"}

    async def test_get_by_id_not_found(self, async_session: AsyncSession):
        repo = ProjectRepository(async_session)
        assert await repo.get_by_id("nope") is None

    async def test_deactivate_removes_from_active(self, async_session: AsyncSession):
        repo = ProjectRepository(async_session)
        await repo.create({"project_id": "p1", "name": "One"})
        await repo.deactivate("p1")
        assert await repo.list_active() == []
        proj = await repo.get_by_id("p1")
        assert proj.active is False

    async def test_deactivate_missing_is_noop(self, async_session: AsyncSession):
        repo = ProjectRepository(async_session)
        # Should not raise even though nothing matches.
        await repo.deactivate("ghost")


# ---------------------------------------------------------------------------
# AgentMessageRepository (inbox / ack / purge / unread_counts / ttl)
# ---------------------------------------------------------------------------


class TestAgentMessageRepository:
    async def test_send_and_inbox(self, async_session: AsyncSession):
        repo = AgentMessageRepository(async_session)
        await repo.send({"sender": "a", "recipient": "b", "body": "hi"})
        inbox = await repo.inbox("b")
        assert len(inbox) == 1
        assert inbox[0].body == "hi"

    async def test_inbox_includes_broadcast(self, async_session: AsyncSession):
        repo = AgentMessageRepository(async_session)
        await repo.send({"sender": "a", "recipient": "broadcast", "body": "all"})
        assert len(await repo.inbox("anyone")) == 1
        assert len(await repo.inbox("anyone", include_broadcast=False)) == 0

    async def test_ack_marks_read_and_excludes_from_unread_inbox(
        self, async_session: AsyncSession
    ):
        repo = AgentMessageRepository(async_session)
        msg = await repo.send({"sender": "a", "recipient": "b", "body": "x"})
        acked = await repo.ack(msg.id)
        assert acked.read_at is not None
        assert await repo.inbox("b", unread_only=True) == []

    async def test_ack_missing_returns_none(self, async_session: AsyncSession):
        repo = AgentMessageRepository(async_session)
        assert await repo.ack("MSG-DOESNOTEXIST") is None

    async def test_get_by_id(self, async_session: AsyncSession):
        repo = AgentMessageRepository(async_session)
        msg = await repo.send({"sender": "a", "recipient": "b", "body": "g"})
        assert (await repo.get_by_id(msg.id)).body == "g"
        assert await repo.get_by_id("MSG-NONE") is None

    async def test_expired_message_excluded_and_purged(self, async_session: AsyncSession):
        repo = AgentMessageRepository(async_session)
        # ttl_seconds=0 with created_at in the past -> always expired.
        await repo.send({"sender": "a", "recipient": "b", "body": "old", "ttl_seconds": 0})
        # ttl 0 means any elapsed time is > ttl; inbox filters it out.
        assert await repo.inbox("b") == []
        purged = await repo.purge_expired()
        assert purged == 1

    async def test_unread_counts(self, async_session: AsyncSession):
        repo = AgentMessageRepository(async_session)
        await repo.send({"sender": "a", "recipient": "b", "body": "1"})
        await repo.send({"sender": "a", "recipient": "b", "body": "2"})
        await repo.send({"sender": "a", "recipient": "c", "body": "3"})
        counts = await repo.unread_counts()
        assert counts["b"] == 2
        assert counts["c"] == 1

    async def test_inbox_project_scope(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-m")
        repo = AgentMessageRepository(async_session)
        await repo.send(
            {"sender": "a", "recipient": "b", "body": "scoped", "project_id": "proj-m"}
        )
        await repo.send({"sender": "a", "recipient": "b", "body": "global"})
        # project filter returns both project-matched and null-project rows.
        rows = await repo.inbox("b", project_id="proj-m")
        assert {r.body for r in rows} == {"scoped", "global"}


# ---------------------------------------------------------------------------
# FeatureRepository (upsert JSON serialization + set_status)
# ---------------------------------------------------------------------------


class TestFeatureRepository:
    async def test_upsert_serializes_json_fields(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        row = await repo.upsert(
            {
                "name": "auth",
                "acceptance_criteria": ["jwt works", "logout works"],
                "evidence": ["test:tests/test_auth.py::test_jwt"],
            }
        )
        assert row.name == "auth"
        # Lists are JSON-serialized into Text columns.
        assert row.acceptance_criteria.startswith("[")
        assert "jwt works" in row.acceptance_criteria

    async def test_upsert_updates_existing(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        await repo.upsert({"name": "f", "description": "v1"})
        row = await repo.upsert({"name": "f", "description": "v2"})
        assert row.description == "v2"
        assert len(await repo.list_all()) == 1

    async def test_upsert_string_passthrough(self, async_session: AsyncSession):
        # When a JSON field is already a string it is stored verbatim.
        repo = FeatureRepository(async_session)
        row = await repo.upsert({"name": "g", "evidence": '["already-json"]'})
        assert row.evidence == '["already-json"]'

    async def test_set_status_updates_and_records_detail(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        feat = await repo.upsert({"name": "h"})
        updated = await repo.set_status(
            feat.id, FeatureStatus.VERIFIED, detail={"all": "pass"}
        )
        assert updated.status == FeatureStatus.VERIFIED.value
        assert '"all"' in updated.last_verify_detail

    async def test_set_status_missing_raises_keyerror(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        with pytest.raises(KeyError):
            await repo.set_status("FEAT-NONE", FeatureStatus.VERIFIED)

    async def test_list_by_status_and_category(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        await repo.upsert({"name": "a", "category": "security", "status": FeatureStatus.REQUESTED})
        await repo.upsert({"name": "b", "category": "ux"})
        sec = await repo.list_by_category("security")
        assert {f.name for f in sec} == {"a"}
        req = await repo.list_by_status(FeatureStatus.REQUESTED)
        assert "a" in {f.name for f in req}

    async def test_get_by_name_and_id(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        feat = await repo.upsert({"name": "named"})
        assert (await repo.get_by_name("named")).id == feat.id
        assert (await repo.get_by_id(feat.id)).name == "named"
        assert await repo.get_by_name("missing") is None


# ---------------------------------------------------------------------------
# SpendRepository
# ---------------------------------------------------------------------------


class TestSpendRepository:
    async def test_add_and_list_since(self, async_session: AsyncSession):
        repo = SpendRepository(async_session)
        await repo.add(ts=100.0, cost_usd=1.0, kind="token")
        await repo.add(ts=200.0, cost_usd=2.0, kind="token")
        rows = await repo.list_since(150.0)
        assert len(rows) == 1
        assert rows[0].ts == 200.0

    async def test_total_since(self, async_session: AsyncSession):
        repo = SpendRepository(async_session)
        await repo.add(ts=100.0, cost_usd=1.5, kind="token")
        await repo.add(ts=200.0, cost_usd=2.5, kind="infra")
        assert await repo.total_since(0.0) == 4.0

    async def test_total_since_empty_is_zero(self, async_session: AsyncSession):
        repo = SpendRepository(async_session)
        assert await repo.total_since(0.0) == 0.0

    async def test_project_scoped_filter(self, async_session: AsyncSession):
        await _make_project(async_session, "proj-s")
        repo = SpendRepository(async_session)
        await repo.add(ts=1.0, cost_usd=5.0, kind="token", project_id="proj-s")
        await repo.add(ts=2.0, cost_usd=7.0, kind="token", project_id=None)
        assert await repo.total_since(0.0, project_id="proj-s") == 5.0
        rows = await repo.list_since(0.0, project_id="proj-s")
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# RoleRunRepository
# ---------------------------------------------------------------------------


class TestRoleRunRepository:
    async def test_record_and_count_by_role(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        await repo.record("proj-r", "planner")
        await repo.record("proj-r", "planner")
        await repo.record("proj-r", "reviewer")
        counts = await repo.count_by_role("proj-r")
        assert counts == {"planner": 2, "reviewer": 1}

    async def test_count_all_projects(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        await repo.record("p1", "x")
        await repo.record("p2", "x")
        assert (await repo.count_by_role())["x"] == 2

    async def test_list_all_scoped(self, async_session: AsyncSession):
        repo = RoleRunRepository(async_session)
        await repo.record("p1", "a")
        await repo.record("p2", "b")
        assert len(await repo.list_all("p1")) == 1
        assert len(await repo.list_all()) == 2


# ---------------------------------------------------------------------------
# PromptProfileRepository
# ---------------------------------------------------------------------------


class TestPromptProfileRepository:
    async def test_upsert_and_get(self, async_session: AsyncSession):
        repo = PromptProfileRepository(async_session)
        row = await repo.upsert(
            {
                "name": "concise",
                "source": "curated",
                "prompt_text": "Be concise.",
                "task_types": '["code"]',
            }
        )
        assert row.name == "concise"
        assert (await repo.get_by_name("concise")).id == row.id
        assert (await repo.get_by_id(row.id)).name == "concise"

    async def test_upsert_updates_existing(self, async_session: AsyncSession):
        repo = PromptProfileRepository(async_session)
        await repo.upsert({"name": "p", "source": "s", "prompt_text": "v1"})
        again = await repo.upsert({"name": "p", "source": "s", "prompt_text": "v2"})
        assert again.prompt_text == "v2"
        assert len(await repo.list_all()) == 1

    async def test_list_by_source(self, async_session: AsyncSession):
        repo = PromptProfileRepository(async_session)
        await repo.upsert({"name": "a", "source": "curated", "prompt_text": "x"})
        await repo.upsert({"name": "b", "source": "scraped", "prompt_text": "y"})
        assert {p.name for p in await repo.list_by_source("curated")} == {"a"}

    async def test_list_for_task_type_matches_and_wildcard(self, async_session: AsyncSession):
        repo = PromptProfileRepository(async_session)
        await repo.upsert(
            {"name": "code", "source": "s", "prompt_text": "x", "task_types": '["code"]'}
        )
        await repo.upsert(
            {"name": "any", "source": "s", "prompt_text": "y", "task_types": "[]"}
        )
        names = {p.name for p in await repo.list_for_task_type("code")}
        # 'code' matches the substring, 'any' matches the empty-list wildcard.
        assert names == {"code", "any"}

    async def test_get_by_name_missing(self, async_session: AsyncSession):
        repo = PromptProfileRepository(async_session)
        assert await repo.get_by_name("none") is None


# ---------------------------------------------------------------------------
# QueueRepository.create + list_all (uncovered create path)
# ---------------------------------------------------------------------------


class TestQueueRepositoryCreate:
    async def test_create_and_list_all(self, async_session: AsyncSession):
        repo = QueueRepository(async_session)
        await repo.create({"queue_name": "q1"})
        await repo.create({"queue_name": "q2", "queue_enabled": False})
        all_q = await repo.list_all()
        assert {q.queue_name for q in all_q} == {"q1", "q2"}
        enabled = await repo.list_enabled()
        assert {q.queue_name for q in enabled} == {"q1"}


# ---------------------------------------------------------------------------
# BenchmarkRepository (both session & session_factory modes + aggregation)
# ---------------------------------------------------------------------------


class TestBenchmarkRepository:
    def _result(self, **over):
        base = {
            "model_profile_id": "m1",
            "task_type": "code",
            "completion_score": 1.0,
            "code_quality_score": 1.0,
            "instruction_adherence_score": 1.0,
            "token_efficiency_score": 1.0,
            "cost_usd": 0.1,
            "success": True,
        }
        base.update(over)
        return base

    async def test_record_and_list_recent_session_mode(self, async_session: AsyncSession):
        repo = BenchmarkRepository(session=async_session)
        await repo.record_result(self._result())
        recent = await repo.list_recent()
        assert len(recent) == 1
        assert recent[0].model_profile_id == "m1"

    async def test_record_factory_mode(self, session_factory):
        repo = BenchmarkRepository(session_factory=session_factory)
        row = await repo.record_result(self._result())
        assert row.model_profile_id == "m1"
        recent = await repo.list_recent()
        assert len(recent) == 1

    async def test_no_session_raises(self):
        repo = BenchmarkRepository()
        with pytest.raises(RuntimeError, match="no session"):
            await repo.list_recent()

    async def test_aggregate_and_best_for_task(self, async_session: AsyncSession):
        repo = BenchmarkRepository(session=async_session)
        for _ in range(3):
            await repo.record_result(self._result(model_profile_id="m1"))
        for _ in range(3):
            await repo.record_result(
                self._result(model_profile_id="m2", completion_score=0.1)
            )
        agg = await repo.get_aggregate_scores(task_type="code")
        assert len(agg) == 2
        best = await repo.get_best_for_task("code", min_samples=3)
        # m1 (higher completion) should rank first by composite score.
        assert best[0]["model_profile_id"] == "m1"

    async def test_best_for_task_min_samples_filters(self, async_session: AsyncSession):
        repo = BenchmarkRepository(session=async_session)
        await repo.record_result(self._result())  # only 1 sample
        assert await repo.get_best_for_task("code", min_samples=3) == []

    async def test_get_model_scores(self, async_session: AsyncSession):
        repo = BenchmarkRepository(session=async_session)
        await repo.record_result(self._result(model_profile_id="m9"))
        scores = await repo.get_model_scores("m9")
        assert len(scores) == 1
        assert scores[0].model_profile_id == "m9"
