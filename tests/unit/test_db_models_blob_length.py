"""D-07 regression tests: CHECK(length(col) <= N) on unbounded Text blobs.

docs/audit/NEW_FINDINGS_2026-06-16.md db/models.py P2 flagged several Text
columns (JSON-in-Text blobs on ``task_decisions`` and ``audit_events``) with
no upper bound — an unbounded write is a DoS vector. ``db/models.py`` now
declares ``CheckConstraint(f"length({{col}}) <= {{MAX_JSON_BLOB_LEN}}", ...)``
on those columns, and migration
``026_add_blob_length_check_constraints`` reproduces the same constraints via
SQLite ``batch_alter_table`` recreate.

Three layers are proven here, each closing a different way the guard could be
silently absent:

* :class:`TestAuditEventDetailsLengthCheck` — the ORM/create_all path via
  :class:`~general_ludd.db.repository.AuditEventRepository`.
* :class:`TestTaskDecisionBlobLengthCheck` — the ORM/create_all path via a
  direct ``session.add`` + ``flush`` (bypassing ``event_loop/loop.py``'s
  broad ``except Exception`` around its own ``TaskDecisionModel`` insert,
  which would otherwise swallow the ``IntegrityError`` as a logged warning).
* :class:`TestMigrationEnforcesCheckConstraints` — the CRITICAL third layer:
  builds a database via ``alembic upgrade head`` (not ``create_all``) and
  proves the *migration* enforces the same constraint. SQLite's
  ``batch_alter_table(recreate="always")`` rebuilds the whole table from a
  fresh ``CREATE TABLE``; if a future edit to the migration got the new-table
  DDL wrong (or reflection silently dropped a constraint), the ORM-level
  tests above would still pass — only running against a migrated DB reveals
  that regression.
"""

from __future__ import annotations

import os
import pathlib

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import (
    MAX_JSON_BLOB_LEN,
    Base,
    ProjectModel,
    TaskDecisionModel,
    TaskReturnModel,
)
from general_ludd.db.repository import AuditEventRepository

_OVER_LEN = MAX_JSON_BLOB_LEN + 1

_TASK_DECISION_BLOB_COLUMNS = (
    "todo_updates",
    "child_todos",
    "validation_requests",
    "git_requests",
    "audit_notes",
    "policy_flags",
)


def _make_async_engine():
    return create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


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
    factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestAuditEventDetailsLengthCheck:
    """AuditEventModel.details via AuditEventRepository.create (ORM/create_all path)."""

    @pytest.mark.asyncio
    async def test_over_length_details_raises_integrity_error(
        self, async_session: AsyncSession
    ) -> None:
        project = ProjectModel(name="blob-len-audit-over")
        async_session.add(project)
        await async_session.flush()

        repo = AuditEventRepository(async_session)
        with pytest.raises(IntegrityError, match="ck_audit_events_details_len"):
            await repo.create(
                event_type="test_event",
                entity_type="todo",
                entity_id="TODO-DEADBEEF",
                project_id=project.project_id,
                details="x" * _OVER_LEN,
            )
        await async_session.rollback()

    @pytest.mark.asyncio
    async def test_boundary_length_details_succeeds(
        self, async_session: AsyncSession
    ) -> None:
        project = ProjectModel(name="blob-len-audit-boundary")
        async_session.add(project)
        await async_session.flush()

        repo = AuditEventRepository(async_session)
        row = await repo.create(
            event_type="test_event",
            entity_type="todo",
            entity_id="TODO-DEADBEEF",
            project_id=project.project_id,
            details="x" * MAX_JSON_BLOB_LEN,
        )
        assert row.id is not None
        assert len(row.details) == MAX_JSON_BLOB_LEN


class TestTaskDecisionBlobLengthCheck:
    """TaskDecisionModel's 6 blob columns via direct session.add + flush.

    Direct persistence (rather than going through
    ``event_loop.loop._phase_...`` review-response handling) is required
    because that call site wraps its own insert in a broad
    ``except Exception`` that only logs a warning — it would silently
    swallow the ``IntegrityError`` this test needs to observe.
    """

    @pytest_asyncio.fixture
    async def task_return(self, async_session: AsyncSession) -> TaskReturnModel:
        ret = TaskReturnModel(
            return_id="blob-len-return",
            job_id="job-blob-len",
            playbook="test.yml",
            queue="test-queue",
            status="completed",
        )
        async_session.add(ret)
        await async_session.flush()
        return ret

    @pytest.mark.asyncio
    @pytest.mark.parametrize("column", _TASK_DECISION_BLOB_COLUMNS)
    async def test_over_length_column_raises_integrity_error(
        self,
        async_session: AsyncSession,
        task_return: TaskReturnModel,
        column: str,
    ) -> None:
        kwargs = {
            "return_id": task_return.return_id,
            "project_id": None,
            "decision": "accept",
            "confidence": 0.5,
            column: "x" * _OVER_LEN,
        }
        dm = TaskDecisionModel(**kwargs)
        async_session.add(dm)
        expected_name = f"ck_task_decisions_{column}_len"
        with pytest.raises(IntegrityError, match=expected_name):
            await async_session.flush()
        await async_session.rollback()

    @pytest.mark.asyncio
    async def test_boundary_length_all_columns_succeeds(
        self, async_session: AsyncSession, task_return: TaskReturnModel
    ) -> None:
        kwargs = {c: "x" * MAX_JSON_BLOB_LEN for c in _TASK_DECISION_BLOB_COLUMNS}
        dm = TaskDecisionModel(
            return_id=task_return.return_id,
            project_id=None,
            decision="accept",
            confidence=0.5,
            **kwargs,
        )
        async_session.add(dm)
        await async_session.flush()
        assert dm.id is not None
        for c in _TASK_DECISION_BLOB_COLUMNS:
            assert len(getattr(dm, c)) == MAX_JSON_BLOB_LEN


def _run_migrations(db_path: str) -> None:
    """Run ``alembic upgrade head`` against an SQLite DB at *db_path*.

    Mirrors ``tests/unit/test_alembic_create_all_parity.py::_run_migrations``:
    ``DATABASE_URL`` is popped for the duration so alembic uses the URL set
    on the Config object (the test DB file), not any ambient production URL.
    """
    from alembic import command
    from alembic.config import Config as AlembicConfig

    root = pathlib.Path(__file__).resolve().parents[2]
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    saved = os.environ.pop("DATABASE_URL", None)
    try:
        command.upgrade(cfg, "head")
    finally:
        if saved is not None:
            os.environ["DATABASE_URL"] = saved


class TestMigrationEnforcesCheckConstraints:
    """CRITICAL: prove the MIGRATION (not just the ORM) enforces the CHECKs.

    SQLite's ``batch_alter_table(recreate="always")`` rebuilds the whole
    table from scratch; it is possible for that rebuild to silently drop a
    constraint (e.g. if column reflection during the batch recreate is off)
    while ``Base.metadata.create_all`` — which the tests above exercise —
    still declares it correctly. Only a DB built purely from
    ``alembic upgrade head`` can reveal that class of drift.
    """

    @pytest.mark.asyncio
    async def test_migrated_db_enforces_audit_events_details_check(
        self, tmp_path: pathlib.Path
    ) -> None:
        db_path = str(tmp_path / "migrated_blob_len_audit.db")
        _run_migrations(db_path)

        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                project = ProjectModel(name="migrated-blob-len-audit")
                session.add(project)
                await session.flush()

                repo = AuditEventRepository(session)
                with pytest.raises(IntegrityError, match="ck_audit_events_details_len"):
                    await repo.create(
                        event_type="test_event",
                        entity_type="todo",
                        entity_id="TODO-DEADBEEF",
                        project_id=project.project_id,
                        details="x" * _OVER_LEN,
                    )
                await session.rollback()
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_migrated_db_enforces_task_decisions_todo_updates_check(
        self, tmp_path: pathlib.Path
    ) -> None:
        db_path = str(tmp_path / "migrated_blob_len_task_decisions.db")
        _run_migrations(db_path)

        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                ret = TaskReturnModel(
                    return_id="migrated-blob-len-return",
                    job_id="job-migrated-blob-len",
                    playbook="test.yml",
                    queue="test-queue",
                    status="completed",
                )
                session.add(ret)
                await session.flush()

                dm = TaskDecisionModel(
                    return_id=ret.return_id,
                    project_id=None,
                    decision="accept",
                    confidence=0.5,
                    todo_updates="x" * _OVER_LEN,
                )
                session.add(dm)
                with pytest.raises(
                    IntegrityError, match="ck_task_decisions_todo_updates_len"
                ):
                    await session.flush()
                await session.rollback()
        finally:
            await engine.dispose()
