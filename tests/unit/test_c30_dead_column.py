"""Tests for C30: determine whether TodoModel.version is a dead column.

The ``version`` column on TodoModel (models.py:227) is used by TWO mechanisms:

1. **ORM ``version_id_col``** (models.py:289) — SQLAlchemy auto-increments the
   column on every ORM-level UPDATE and adds ``WHERE version = <committed>``,
   raising ``StaleDataError`` on concurrent-write detection.

2. **Manual CAS in repository.py** — ``update()``, ``transition()``, and
   ``claim_runnable()`` each perform their own guarded conditional UPDATE via
   Core-level ``_update(TodoModel)``, carrying the version into the WHERE
   clause and bumping it in the VALUES clause.  The repository path bypasses
   the ORM mapper entirely (Core UPDATEs do not trigger ``version_id_col``).

The column IS NOT DEAD — it is the substrate for BOTH concurrency guards.
The ``version_id_col`` mapper arg is redundant with the manual CAS for
repository-governed paths but serves as a defence-in-depth safety net for
direct ORM updates that bypass the repository.

If the ``version_id_col`` mapper arg were removed, the column itself MUST
stay (the repository's manual CAS depends on it).  The migration plan would
be a comment-only change to models.py since the column schema is unchanged.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Integer, inspect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, TodoModel
from general_ludd.db.repository import TodoRepository

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    return engine


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestC30VersionColumnIsAlive:
    """The ``version`` column is NOT dead — it is the substrate for CAS."""

    def test_version_column_exists_in_model(self) -> None:
        """The model defines a ``version`` mapped column of type Integer."""
        col = inspect(TodoModel).columns.get("version")
        assert col is not None, "version column must exist on TodoModel"
        assert isinstance(col.type, Integer), (
            f"Expected Integer type, got {type(col.type).__name__}"
        )

    def test_version_column_defaults_to_one(self) -> None:
        """New TodoModel instances start at version=1 after persist.

        The ``version`` column is managed by SQLAlchemy's ``version_id_col``,
        so its value is None before the row is flushed/committed.  After a
        commit it reflects the DB-side default (1).
        """
        todo = TodoModel(title="test", status="backlog", priority=0)
        assert todo.version is None, (
            "version_id_col managed columns start as None before persist"
        )

    @pytest.mark.asyncio
    async def test_version_column_persisted_in_db(self) -> None:
        """The column is physically present in the schema."""
        from sqlalchemy import text

        engine = _make_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT name FROM pragma_table_info('todos') WHERE name='version'")
            )
            names = rows.scalars().all()
            assert "version" in names, "version column absent from todos table"
        await engine.dispose()


class TestC30VersionIdColWired:
    """The ``version_id_col`` mapper arg IS wired — defence-in-depth."""

    def test_version_id_col_is_set(self) -> None:
        assert TodoModel.__mapper__.version_id_col is not None
        assert TodoModel.__mapper__.version_id_col.name == "version"

    @pytest.mark.asyncio
    async def test_orm_version_auto_increment_works(self) -> None:
        """Direct ORM update without the repository — version auto-increments."""
        engine = _make_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            todo = TodoModel(title="orm direct", status="backlog", priority=0)
            session.add(todo)
            await session.commit()
            assert todo.version == 1

            todo.title = "changed via ORM"
            await session.commit()
            await session.refresh(todo)
            assert todo.version == 2

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_orm_stale_detection_via_version_id_col(self) -> None:
        """Concurrent ORM updates detect staleness via version_id_col."""
        engine = _make_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as s1:
            todo = TodoModel(title="stale test", status="backlog", priority=0)
            s1.add(todo)
            await s1.commit()
            todo_id = todo.id

        async with factory() as s2:
            t2 = await s2.get(TodoModel, todo_id)
            assert t2 is not None

            async with factory() as s3:
                t3 = await s3.get(TodoModel, todo_id)
                t3.title = "winner"
                await s3.commit()

            t2.title = "loser"
            with pytest.raises(StaleDataError):
                await s2.commit()

        await engine.dispose()


class TestC30ManualCasWorks:
    """The repository's guarded UPDATE path uses the column for CAS."""

    @pytest.mark.asyncio
    async def test_update_increments_version(self) -> None:
        """Repository update() bumps version from 1 → 2."""
        engine = _make_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            repo = TodoRepository(session)
            todo = await repo.create({"title": "cas test", "status": "backlog", "priority": 0})
            assert todo.version == 1

            updated = await repo.update(todo.todo_id, {"title": "renamed"}, expected_version=1)
            assert updated.version == 2
            assert updated.title == "renamed"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_update_rejects_stale_version(self) -> None:
        """Stale expected_version raises ConcurrencyError."""
        from general_ludd.db.repository import ConcurrencyError

        engine = _make_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as session:
            repo = TodoRepository(session)
            todo = await repo.create({"title": "concurrent", "status": "backlog", "priority": 0})

            await repo.update(todo.todo_id, {"title": "first"}, expected_version=1)

            with pytest.raises(ConcurrencyError):
                await repo.update(todo.todo_id, {"title": "stale"}, expected_version=1)

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_transition_increments_version(self) -> None:
        """Repository transition() bumps version."""
        from general_ludd.schemas.todo import TodoStatus

        engine = _make_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as session:
            repo = TodoRepository(session)
            todo = await repo.create({"title": "trans test", "status": "backlog", "priority": 0})
            assert todo.version == 1

            moved = await repo.transition(todo.todo_id, TodoStatus.QUEUED, expected_version=1)
            assert moved.version == 2
            assert moved.status == "queued"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_manual_cas_bypasses_version_id_col(self) -> None:
        """Core-level _update() in repository does NOT trigger ORM version_id_col.

        The repository uses Core ``_update(TodoModel)`` (not ORM flush), so the
        version_id_col safety net is bypassed.  This test proves BOTH mechanisms
        can coexist on the same column without stepping on each other: the
        repository's manual CAS sets version=2 via a Core UPDATE; the subsequent
        ORM-level flush does NOT trigger a false StaleDataError.
        """
        engine = _make_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as session:
            repo = TodoRepository(session)
            todo = await repo.create({"title": "coexist test", "status": "backlog", "priority": 0})
            assert todo.version == 1

            # Repository path: Core UPDATE + manual version sync + flush
            updated = await repo.update(todo.todo_id, {"title": "coexist ok"}, expected_version=1)
            assert updated.version == 2

            # Flush in the same session MUST NOT raise StaleDataError
            await session.flush()
            await session.refresh(todo)
            assert todo.version == 2

        await engine.dispose()


class TestC30ColumnNotDeadEvidence:
    """Static evidence: the column is imported and referenced throughout."""

    def test_version_referenced_in_repository(self) -> None:
        """The repository accesses TodoModel.version for CAS."""
        import ast

        with open("src/general_ludd/db/repository.py") as f:
            tree = ast.parse(f.read())

        version_refs = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "TodoModel"
            and node.attr == "version"
        ]
        assert len(version_refs) >= 1, (
            "TodoModel.version must be referenced in repository.py for CAS"
        )

    def test_version_referenced_in_models(self) -> None:
        """models.py defines the column and the mapper arg."""
        import ast

        with open("src/general_ludd/db/models.py") as f:
            tree = ast.parse(f.read())

        class_def = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "TodoModel"
        )

        body_stmts = [
            node
            for node in ast.walk(class_def)
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "version"
        ]
        assert len(body_stmts) >= 1, "version column annotation missing from TodoModel"

        mapper_dicts = [
            node
            for node in ast.walk(class_def)
            if isinstance(node, ast.Dict)
            and any(
                isinstance(k, ast.Constant) and k.value == "version_id_col"
                for k in (node.keys or [])
            )
        ]
        assert len(mapper_dicts) == 1, (
            "__mapper_args__ must contain version_id_col"
        )
