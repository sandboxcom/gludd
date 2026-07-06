"""RED-TEAM: database-layer concurrency / scoping / cascade correctness.

These tests target CONFIRMED bugs in src/general_ludd/db/{repository,models}.py:

1. claim_runnable / claim_unreviewed must treat SQLITE_BUSY ('database is
   locked') from the guarded UPDATE as a LOST RACE (skip), not propagate it as
   an OperationalError — so "loser skips" holds under contention.
2. set_var / PromptProfileRepository.upsert / FeatureRepository.upsert must use
   INSERT ... ON CONFLICT DO UPDATE so a concurrent first-write converges
   instead of raising IntegrityError (and silently losing data).
3. update / transition / set_status / deactivate / ack must guard the UPDATE on
   the expected version / current state so a concurrent writer cannot silently
   clobber (lost update) — the loser gets a ConcurrencyError or a no-op.
4. update / transition / count_active must honour project_id scoping.
5. FK cascade: deleting a parent (project / namespace) must CASCADE or SET NULL
   per the ondelete declarations rather than leaving orphans / FK violations.

SQLite has NO row-level locks, so every cross-session race needs a FILE-backed
DB: an in-memory DB gives each connection a PRIVATE database, which cannot
exercise a real cross-connection race. We use tmp_path file engines with
PRAGMA foreign_keys=ON.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from general_ludd.db.models import (
    AgentMessageModel,
    Base,
    FeatureStatus,
    ProjectModel,
    TodoModel,
    VariableNamespaceModel,
    VariableValueModel,
)
from general_ludd.db.repository import (
    AgentMessageRepository,
    ConcurrencyError,
    FeatureRepository,
    ProjectRepository,
    PromptProfileRepository,
    TaskReturnRepository,
    TodoRepository,
    VariableNamespaceRepository,
)
from general_ludd.schemas.todo import TodoStatus


# ---------------------------------------------------------------------------
# File-backed engine: a real DB on disk so two connections / sessions race on
# the SAME database (the only way to exercise SQLite cross-session contention).
# PRAGMA foreign_keys=ON so the ondelete cascade/set-null rules actually fire.
# ---------------------------------------------------------------------------
def _make_file_engine(db_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=2000")
        cur.close()

    return engine


@pytest_asyncio.fixture
async def file_factory(tmp_path):
    db_path = tmp_path / "redteam.db"
    engine = _make_file_engine(db_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _ensure_project(factory, project_id: str) -> None:
    """Create the parent project row (FK target) if absent.

    With PRAGMA foreign_keys=ON a todo.project_id must reference an existing
    project, so scoping tests have to seed the parent first.
    """
    async with factory() as s:
        existing = await ProjectRepository(s).get_by_id(project_id)
        if existing is None:
            s.add(ProjectModel(project_id=project_id, name=project_id))
            await s.commit()


async def _add_queued_todo(
    factory, todo_id: str, project_id: str | None = None
) -> None:
    if project_id is not None:
        await _ensure_project(factory, project_id)
    async with factory() as s:
        s.add(
            TodoModel(
                todo_id=todo_id,
                project_id=project_id,
                title=f"todo {todo_id}",
                status=TodoStatus.QUEUED.value,
                queue="core",
                version=1,
            )
        )
        await s.commit()


# ===========================================================================
# FIX 1 — claim contention: SQLITE_BUSY on the guarded UPDATE == lost race.
# ===========================================================================
class TestClaimContention:
    async def test_claim_runnable_treats_locked_as_lost_race(self, file_factory):
        """A 'database is locked' OperationalError from the guarded UPDATE must
        be swallowed as a lost race (row skipped), never propagated."""
        await _add_queued_todo(file_factory, "T-LOCK")

        async with file_factory() as session:
            repo = TodoRepository(session)

            real_execute = session.execute
            calls = {"n": 0}

            async def flaky_execute(stmt, *a, **k):
                # Fail the FIRST guarded UPDATE (an `update()` construct) with a
                # simulated SQLITE_BUSY; SELECTs and later statements pass through.
                from sqlalchemy.sql.dml import Update

                if isinstance(stmt, Update) and calls["n"] == 0:
                    calls["n"] += 1
                    raise OperationalError(
                        "UPDATE todos ...", {}, Exception("database is locked")
                    )
                return await real_execute(stmt, *a, **k)

            session.execute = flaky_execute  # type: ignore[method-assign]  # pytest monkeypatch
            # Must not raise: the locked guard is a lost race -> the row is skipped.
            claimed = await repo.claim_runnable(limit=10)
            assert calls["n"] == 1  # we did inject one lock error
            assert all(c.todo_id != "T-LOCK" for c in claimed)

    async def test_claim_runnable_propagates_non_lock_operational_error(
        self, file_factory
    ):
        """A non-lock OperationalError is a real fault and must NOT be swallowed."""
        await _add_queued_todo(file_factory, "T-ERR")

        async with file_factory() as session:
            repo = TodoRepository(session)
            real_execute = session.execute

            async def boom_execute(stmt, *a, **k):
                from sqlalchemy.sql.dml import Update

                if isinstance(stmt, Update):
                    raise OperationalError(
                        "UPDATE todos ...", {}, Exception("no such column: bogus")
                    )
                return await real_execute(stmt, *a, **k)

            session.execute = boom_execute  # type: ignore[method-assign]  # pytest monkeypatch
            with pytest.raises(OperationalError):
                await repo.claim_runnable(limit=10)

    async def test_claim_runnable_concurrent_no_double_claim(self, file_factory):
        """Two concurrent claimers over the same queued rows: every row is
        claimed by exactly one caller (no double dispatch)."""
        for i in range(8):
            await _add_queued_todo(file_factory, f"T{i}")

        async def claim():
            async with file_factory() as s:
                repo = TodoRepository(s)
                got = await repo.claim_runnable(limit=8)
                await s.commit()
                return [t.todo_id for t in got]

        a, b = await asyncio.gather(claim(), claim())
        assert set(a).isdisjoint(set(b))  # no row claimed twice
        assert len(set(a) | set(b)) == 8  # all rows claimed exactly once

    async def test_claim_unreviewed_treats_locked_as_lost_race(self, file_factory):
        async with file_factory() as s:
            tr_repo = TaskReturnRepository(s)
            await tr_repo.create(
                {"return_id": "R-LK", "job_id": "J", "playbook": "p", "queue": "core"}
            )
            await s.commit()

        async with file_factory() as session:
            repo = TaskReturnRepository(session)
            real_execute = session.execute
            calls = {"n": 0}

            async def flaky_execute(stmt, *a, **k):
                from sqlalchemy.sql.dml import Update

                if isinstance(stmt, Update) and calls["n"] == 0:
                    calls["n"] += 1
                    raise OperationalError(
                        "UPDATE task_returns ...", {}, Exception("database is locked")
                    )
                return await real_execute(stmt, *a, **k)

            session.execute = flaky_execute  # type: ignore[method-assign]  # pytest monkeypatch
            claimed = await repo.claim_unreviewed(limit=10)
            assert calls["n"] == 1
            assert all(c.return_id != "R-LK" for c in claimed)


# ===========================================================================
# FIX 2 — upsert TOCTOU: concurrent first-write must converge, not raise.
# ===========================================================================
class TestUpsertTOCTOU:
    async def test_set_var_concurrent_first_write_no_integrity_error(
        self, file_factory
    ):
        """Two sessions setting the same (namespace, key) for the first time
        must both succeed (last writer wins), not raise IntegrityError.

        A real project_id is used so the (namespace, project_id) unique
        constraint actually applies — under SQLite, NULL values are distinct in
        a unique index, so the namespace dedup only bites for a non-NULL scope.
        """
        async with file_factory() as s:
            s.add(ProjectModel(project_id="proj-var", name="V"))
            await s.commit()

        async def setter(val: str):
            async with file_factory() as s:
                repo = VariableNamespaceRepository(s)
                row = await repo.set_var("ns", "K", val, project_id="proj-var")
                await s.commit()
                return row.value

        await asyncio.gather(setter("v1"), setter("v2"))

        async with file_factory() as s:
            stmt = (
                select(VariableValueModel)
                .join(VariableNamespaceModel)
                .where(VariableValueModel.key == "K")
            )
            rows = (await s.execute(stmt)).scalars().all()
            # Exactly one row for the unique (namespace_id, key) — no dup, no loss.
            assert len(rows) == 1
            assert rows[0].value in {"v1", "v2"}

    async def test_set_var_updates_existing_value(self, file_factory):
        async with file_factory() as s:
            repo = VariableNamespaceRepository(s)
            await repo.set_var("ns", "K", "first")
            await s.commit()
        async with file_factory() as s:
            repo = VariableNamespaceRepository(s)
            row = await repo.set_var("ns", "K", "second")
            await s.commit()
            assert row.value == "second"

    async def test_prompt_profile_upsert_concurrent_converges(self, file_factory):
        async def upsert(text_val: str):
            async with file_factory() as s:
                repo = PromptProfileRepository(s)
                row = await repo.upsert(
                    {"name": "P", "source": "test", "prompt_text": text_val}
                )
                await s.commit()
                return row.prompt_text

        await asyncio.gather(upsert("a"), upsert("b"))
        async with file_factory() as s:
            repo = PromptProfileRepository(s)
            rows = await repo.list_all()
            assert len([r for r in rows if r.name == "P"]) == 1

    async def test_feature_upsert_concurrent_converges(self, file_factory):
        async def upsert(desc: str):
            async with file_factory() as s:
                repo = FeatureRepository(s)
                row = await repo.upsert(
                    {
                        "name": "F",
                        "description": desc,
                        "status": FeatureStatus.REQUESTED,
                        "evidence": [],
                        "acceptance_criteria": [],
                    }
                )
                await s.commit()
                return row.description

        await asyncio.gather(upsert("d1"), upsert("d2"))
        async with file_factory() as s:
            repo = FeatureRepository(s)
            rows = await repo.list_all()
            assert len([r for r in rows if r.name == "F"]) == 1


# ===========================================================================
# FIX 3 — lost update: guarded conditional UPDATE on version / state.
# ===========================================================================
class TestLostUpdate:
    async def test_update_concurrent_loser_gets_concurrency_error(self, file_factory):
        """Two sessions read version 1 and both update -> exactly one wins; the
        other must raise ConcurrencyError (its UPDATE affects zero rows)."""
        await _add_queued_todo(file_factory, "T-UPD")

        s1 = file_factory()
        s2 = file_factory()
        async with s1, s2:
            r1, r2 = TodoRepository(s1), TodoRepository(s2)
            t1 = await r1.get_by_id("T-UPD")
            t2 = await r2.get_by_id("T-UPD")
            assert t1.version == t2.version == 1

            await r1.update("T-UPD", {"title": "by-1"}, expected_version=1)
            await s1.commit()

            # s2 still believes version==1; its guarded UPDATE must affect 0 rows.
            with pytest.raises(ConcurrencyError):
                await r2.update("T-UPD", {"title": "by-2"}, expected_version=1)

        async with file_factory() as s:
            t = await TodoRepository(s).get_by_id("T-UPD")
            assert t.title == "by-1"  # winner's write survived
            assert t.version == 2  # bumped exactly once

    async def test_transition_concurrent_loser_gets_concurrency_error(
        self, file_factory
    ):
        await _add_queued_todo(file_factory, "T-TR")

        s1 = file_factory()
        s2 = file_factory()
        async with s1, s2:
            r1, r2 = TodoRepository(s1), TodoRepository(s2)
            await r1.get_by_id("T-TR")
            await r2.get_by_id("T-TR")

            await r1.transition("T-TR", TodoStatus.ACTIVE, expected_version=1)
            await s1.commit()

            with pytest.raises(ConcurrencyError):
                await r2.transition("T-TR", TodoStatus.ACTIVE, expected_version=1)

        async with file_factory() as s:
            t = await TodoRepository(s).get_by_id("T-TR")
            assert t.status == TodoStatus.ACTIVE.value
            assert t.version == 2

    async def test_ack_concurrent_only_first_writes_read_at(self, file_factory):
        """Two acks on the same unread message: only the first stamps read_at;
        the second is a no-op (guarded on read_at IS NULL) — no clobber."""
        async with file_factory() as s:
            s.add(AgentMessageModel(id="MSG-1", sender="a", recipient="b"))
            await s.commit()

        async with file_factory() as s1:
            r1 = AgentMessageRepository(s1)
            row = await r1.ack("MSG-1")
            await s1.commit()
            first_read_at = row.read_at
            assert first_read_at is not None

        async with file_factory() as s2:
            r2 = AgentMessageRepository(s2)
            row2 = await r2.ack("MSG-1")
            await s2.commit()
            # Second ack must NOT overwrite the original timestamp.
            assert row2.read_at == first_read_at

    async def test_deactivate_is_guarded_and_idempotent(self, file_factory):
        async with file_factory() as s:
            s.add(ProjectModel(project_id="proj-x", name="X", active=True))
            await s.commit()

        async with file_factory() as s:
            repo = ProjectRepository(s)
            await repo.deactivate("proj-x")
            await s.commit()
        # Second deactivate is a guarded no-op (active already False), not an error.
        async with file_factory() as s:
            repo = ProjectRepository(s)
            await repo.deactivate("proj-x")
            await s.commit()
            proj = await repo.get_by_id("proj-x")
            assert proj is not None
            assert proj.active is False

    async def test_set_status_missing_feature_raises_keyerror(self, file_factory):
        async with file_factory() as s:
            repo = FeatureRepository(s)
            with pytest.raises(KeyError):
                await repo.set_status("FEAT-NOPE", FeatureStatus.VERIFIED)


# ===========================================================================
# FIX 4 — cross-project scoping on update / transition / count_active.
# ===========================================================================
class TestProjectScoping:
    async def test_update_wrong_project_id_is_rejected(self, file_factory):
        """A todo owned by project A must not be updatable under project B's
        scope — the guarded UPDATE's project_id WHERE makes it a no-op."""
        await _add_queued_todo(file_factory, "T-A", project_id="proj-A")

        async with file_factory() as s:
            repo = TodoRepository(s)
            # Wrong project scope: get_by_id(project_id='proj-B') finds nothing.
            with pytest.raises(ConcurrencyError):
                await repo.update(
                    "T-A", {"title": "hijack"}, expected_version=1, project_id="proj-B"
                )
            # Correct scope works.
            t = await repo.update(
                "T-A", {"title": "ok"}, expected_version=1, project_id="proj-A"
            )
            assert t.title == "ok"

    async def test_transition_wrong_project_id_is_rejected(self, file_factory):
        await _add_queued_todo(file_factory, "T-B", project_id="proj-A")
        async with file_factory() as s:
            repo = TodoRepository(s)
            with pytest.raises(ConcurrencyError):
                await repo.transition(
                    "T-B", TodoStatus.ACTIVE, expected_version=1, project_id="proj-B"
                )

    async def test_count_active_is_project_scoped(self, file_factory):
        # Two active todos in A, one in B.
        await _ensure_project(file_factory, "proj-A")
        await _ensure_project(file_factory, "proj-B")
        async with file_factory() as s:
            for tid, proj in [("A1", "proj-A"), ("A2", "proj-A"), ("B1", "proj-B")]:
                s.add(
                    TodoModel(
                        todo_id=tid,
                        project_id=proj,
                        title=tid,
                        status=TodoStatus.ACTIVE.value,
                    )
                )
            await s.commit()
        async with file_factory() as s:
            repo = TodoRepository(s)
            assert await repo.count_active(project_id="proj-A") == 2
            assert await repo.count_active(project_id="proj-B") == 1
            assert await repo.count_active() == 3  # all projects


# ===========================================================================
# FIX 5 — FK cascade / set-null on parent delete (PRAGMA foreign_keys=ON).
# ===========================================================================
class TestFKCascade:
    async def test_delete_project_sets_null_on_scoping_fks(self, file_factory):
        """Deleting a project must SET NULL on nullable scoping FKs (todos),
        not raise an FK violation or orphan a dangling reference."""
        async with file_factory() as s:
            s.add(ProjectModel(project_id="proj-del", name="D"))
            await s.flush()
            s.add(
                TodoModel(
                    todo_id="T-DEL", project_id="proj-del", title="t",
                    status=TodoStatus.QUEUED.value,
                )
            )
            await s.commit()

        async with file_factory() as s:
            # Raw DELETE so the DB-level ondelete rule (not ORM cascade) is tested.
            await s.execute(
                text("DELETE FROM projects WHERE project_id = 'proj-del'")
            )
            await s.commit()

        async with file_factory() as s:
            todo = await TodoRepository(s).get_by_id("T-DEL")
            assert todo is not None  # row survives
            assert todo.project_id is None  # FK was SET NULL

    async def test_delete_namespace_cascades_values(self, file_factory):
        """Deleting a namespace must CASCADE-delete its variable_values."""
        async with file_factory() as s:
            ns = VariableNamespaceModel(namespace="cascade-ns", project_id=None)
            s.add(ns)
            await s.flush()
            s.add(VariableValueModel(namespace_id=ns.id, key="K", value="v"))
            await s.commit()
            ns_id = ns.id

        async with file_factory() as s:
            await s.execute(
                text("DELETE FROM variable_namespaces WHERE id = :i"), {"i": ns_id}
            )
            await s.commit()

        async with file_factory() as s:
            rows = (
                await s.execute(
                    select(VariableValueModel).where(
                        VariableValueModel.namespace_id == ns_id
                    )
                )
            ).scalars().all()
            assert rows == []  # children cascade-deleted, no orphans

    async def test_create_all_still_builds_schema(self, file_factory):
        """The ondelete additions must not break Base.metadata.create_all —
        a smoke check that the schema still materializes and is queryable."""
        async with file_factory() as s:
            # A trivial insert+select round-trips => schema intact.
            s.add(ProjectModel(project_id="proj-smoke", name="S"))
            await s.commit()
            got = await ProjectRepository(s).get_by_id("proj-smoke")
            assert got is not None
