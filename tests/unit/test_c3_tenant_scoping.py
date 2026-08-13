"""C.3 — DB tenant scoping: TDD tests that FAIL to prove the bug.

These tests verify the four gaps documented in:
- TASKS.md C.3 (re-opened 2026-07-14, FALSE COMPLETION)
- docs/design/STUB_CLOSURE_SPEC.md S27 (tenant contextvar is WRITE-ONLY)

The bug shape: db/tenant.py defines get_tenant() but nothing in src/
ever calls it. No do_orm_execute / with_loader_criteria listener exists
to inject tenant filters into ORM queries. Repositories default to
unscoped, and the accounting route calls list_all() unfiltered.

These tests MUST FAIL in the current tree. When the fix is implemented
(do_orm_execute listener + repository scoping by default), they will PASS.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from general_ludd.db.models import TodoModel
from general_ludd.db.repository import TodoRepository
from general_ludd.db.tenant import get_tenant, reset_tenant, set_tenant

SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"

# ---------------------------------------------------------------------------
# Test 1: No do_orm_execute / with_loader_criteria listener exists
# ---------------------------------------------------------------------------


class TestNoDoOrmExecuteListener:
    """Prove that no SQLAlchemy tenant-filter listener is registered.

    The tenant contextvar is set but never consumed by the ORM. There is
    no do_orm_execute or with_loader_criteria listener anywhere in the
    production db module. This test FAILS now because the source lacks
    these patterns — it will PASS when the fix adds a listener.
    """

    def test_session_py_contains_do_orm_execute_or_with_loader_criteria(self):
        session_py = SRC_DIR / "general_ludd" / "db" / "session.py"
        content = session_py.read_text()
        has_listener = (
            "do_orm_execute" in content or "with_loader_criteria" in content
        )
        assert has_listener, (
            "session.py has NO do_orm_execute or with_loader_criteria listener — "
            "the tenant contextvar is WRITE-ONLY. Cross-tenant reads are unfiltered."
        )

    def test_session_py_listeners_are_not_only_sqlite_pragmas(self):
        session_py = SRC_DIR / "general_ludd" / "db" / "session.py"
        content = session_py.read_text()

        count_pragma = content.count("@event.listens_for")
        has_non_pragma = (
            "do_orm_execute" in content
            or "with_loader_criteria" in content
            or count_pragma > 2
        )
        assert has_non_pragma, (
            f"session.py has {count_pragma} @event.listens_for decorators, "
            "all sqlite-PRAGMA-only. No tenant filter listener exists."
        )


# ---------------------------------------------------------------------------
# Test 2: get_tenant() has ZERO production call sites
# ---------------------------------------------------------------------------


class _GetTenantCallSiteFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.call_sites: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        func_node = node.func
        if (isinstance(func_node, ast.Name) and func_node.id == "get_tenant") or (
            isinstance(func_node, ast.Attribute)
            and func_node.attr == "get_tenant"
        ):
            self.call_sites.append(f"get_tenant() at line {func_node.lineno}")
        self.generic_visit(node)


class TestGetTenantZeroCallSites:
    """Prove that get_tenant() is never called in production code.

    The only call sites are in db/tenant.py (definition), db/__init__.py
    (re-export), and test files. Nothing in the production call path
    reads the contextvar.

    This test FAILS now because no production caller exists. When the fix
    adds a do_orm_execute listener that reads the contextvar, it PASSES.
    """

    _EXCLUDE: frozenset[str] = frozenset({"db/tenant.py", "db/__init__.py"})

    def test_get_tenant_has_production_call_sites(self):
        all_call_sites: list[str] = []
        excluded_call_sites: list[str] = []

        for py_file in SRC_DIR.rglob("*.py"):
            rel = py_file.relative_to(SRC_DIR).as_posix()
            source = py_file.read_text()
            # The full suite runs this late in an already large xdist worker.
            # Avoid materialising an AST for every production module: only
            # files containing the symbol can possibly contain a matching
            # call.  All source files are still inspected, while peak memory
            # stays bounded to the small candidate set.
            if "get_tenant" not in source:
                continue
            try:
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue
            finder = _GetTenantCallSiteFinder()
            finder.visit(tree)
            for site in finder.call_sites:
                site_ref = f"{rel}: {site}"
                if any(rel.startswith(ex) for ex in self._EXCLUDE):
                    excluded_call_sites.append(site_ref)
                else:
                    all_call_sites.append(site_ref)

        assert len(all_call_sites) > 0, (
            "get_tenant() has ZERO production call sites outside "
            f"{self._EXCLUDE}. Found {len(excluded_call_sites)} call sites "
            f"in excluded modules only: {excluded_call_sites}. "
            "The tenant contextvar is WRITE-ONLY — never consumed by any query path."
        )


# ---------------------------------------------------------------------------
# Test 3: Accounting route calls list_all() unscoped
# ---------------------------------------------------------------------------


class TestAccountingListAllCrossTenantLeak:
    """Prove that the accounting route calls list_all() without project_id=
    argument, leaking cross-tenant data."""

    def test_list_all_called_without_project_id_argument(self):
        accounting_py = SRC_DIR / "general_ludd" / "routers" / "accounting.py"
        content = accounting_py.read_text()
        tree = ast.parse(content, filename=str(accounting_py))

        class _ListAllCallFinder(ast.NodeVisitor):
            def __init__(self) -> None:
                self.calls: list[str] = []

            def visit_Call(self, node: ast.Call) -> None:
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "list_all"
                ):
                    has_project_id = any(
                        kw.arg == "project_id" for kw in node.keywords
                    )
                    self.calls.append(
                        f"line {node.lineno}: list_all() "
                        f"{'WITH' if has_project_id else 'WITHOUT'} project_id="
                    )
                self.generic_visit(node)

        finder = _ListAllCallFinder()
        finder.visit(tree)

        unscoped = [c for c in finder.calls if "WITHOUT project_id" in c]
        assert len(unscoped) == 0, (
            f"accounting.py has {len(unscoped)} unscoped list_all() call(s): "
            f"{unscoped}. These calls return ALL tenants' data — "
            f"cross-tenant leak is open."
        )


# ---------------------------------------------------------------------------
# Test 4: Unscoped repository returns cross-tenant data
# ---------------------------------------------------------------------------

_TODOS_DDL = text("""
    CREATE TABLE IF NOT EXISTS projects (
        project_id VARCHAR(32) PRIMARY KEY,
        name VARCHAR(128) NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        workspace_path VARCHAR(512) NOT NULL DEFAULT '',
        config TEXT NOT NULL DEFAULT '{}',
        active BOOLEAN NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
""")

_TODOS_TABLE = text("""
    CREATE TABLE IF NOT EXISTS todos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        todo_id VARCHAR(32) UNIQUE NOT NULL,
        project_id VARCHAR(32) REFERENCES projects(project_id) ON DELETE SET NULL,
        title VARCHAR(512) NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        status VARCHAR(32) NOT NULL DEFAULT 'backlog',
        priority INTEGER NOT NULL DEFAULT 0,
        queue VARCHAR(64) NOT NULL DEFAULT 'core',
        tags TEXT NOT NULL DEFAULT '[]',
        risk_level VARCHAR(16) NOT NULL DEFAULT 'low',
        work_type VARCHAR(32) NOT NULL DEFAULT 'unknown',
        resource_profile VARCHAR(32) NOT NULL DEFAULT 'low_resource',
        estimated_cost_usd FLOAT,
        actual_cost_accrued FLOAT NOT NULL DEFAULT 0.0,
        parent_todo_id VARCHAR(32),
        child_todo_ids TEXT NOT NULL DEFAULT '[]',
        acceptance_criteria TEXT,
        definition_of_done TEXT,
        test_commands TEXT NOT NULL DEFAULT '[]',
        molecule_scenarios TEXT NOT NULL DEFAULT '[]',
        molecule_evidence_refs TEXT NOT NULL DEFAULT '[]',
        coverage_requirements VARCHAR(256),
        dependencies TEXT NOT NULL DEFAULT '[]',
        created_by VARCHAR(64) NOT NULL DEFAULT 'agent',
        assigned_agent VARCHAR(128),
        model_profile VARCHAR(64),
        prompt_profile VARCHAR(64),
        worktree VARCHAR(512),
        branch_name VARCHAR(256),
        artifacts TEXT NOT NULL DEFAULT '[]',
        evidence_refs TEXT NOT NULL DEFAULT '[]',
        plan_artifact TEXT,
        confidence FLOAT,
        manual_hold_reason TEXT,
        approval_policy VARCHAR(32) NOT NULL DEFAULT 'none',
        version INTEGER NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at DATETIME,
        scheduled_at DATETIME,
        cron VARCHAR(256),
        schedule_timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
        next_run_at DATETIME,
        last_run_at DATETIME,
        run_count INTEGER NOT NULL DEFAULT 0,
        max_runs INTEGER,
        schedule_paused BOOLEAN NOT NULL DEFAULT 0
    )
""")


@pytest.fixture
async def raw_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.execute(_TODOS_DDL)
        await conn.execute(_TODOS_TABLE)
    yield engine
    await engine.dispose()


@pytest.fixture
async def raw_session_factory(raw_engine):
    return async_sessionmaker(raw_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def seeded_todos(raw_session_factory):
    async with raw_session_factory() as session:
        await session.execute(
            text("INSERT INTO projects (project_id, name) VALUES (:pid, :name)"),
            [{"pid": "acme-corp", "name": "Acme"}, {"pid": "globex-inc", "name": "Globex"}],
        )
        await session.execute(
            text(
                "INSERT INTO todos (todo_id, title, status, project_id) "
                "VALUES (:tid, :title, 'queued', :pid)"
            ),
            [
                {"tid": "A1", "title": "Acme 1", "pid": "acme-corp"},
                {"tid": "A2", "title": "Acme 2", "pid": "acme-corp"},
                {"tid": "G1", "title": "Globex 1", "pid": "globex-inc"},
            ],
        )
        await session.commit()


class TestRepositoryUnscopedDefault:
    """Prove that a Repository created WITHOUT project_id returns cross-tenant data.

    TodoRepository(session) defaults to unscoped (project_id=None). The
    caller must pass project_id= on every method call. The event loop and
    accounting route both call the unscoped constructor.
    """

    @pytest.mark.asyncio
    async def test_unscoped_list_all_returns_single_tenant_only(
        self, raw_session_factory, seeded_todos
    ):
        tok = set_tenant("acme-corp")
        try:
            async with raw_session_factory() as session:
                repo = TodoRepository(session)
                todos = await repo.list_all()

                project_ids = {t.project_id for t in todos}
                assert len(project_ids) <= 1, (
                    f"Unscoped list_all() returned {len(project_ids)} tenants: "
                    f"{project_ids}. Cross-tenant leak is OPEN — an unscoped "
                    f"repository must not return data across tenant boundaries."
                )
                assert project_ids == {"acme-corp"}, (
                    f"Contextvar-scoped repo returned {project_ids} instead of "
                    f"just acme-corp. do_orm_execute listener is not filtering."
                )
        finally:
            reset_tenant(tok)

    @pytest.mark.asyncio
    async def test_scoped_repo_filters_correctly(
        self, raw_session_factory, seeded_todos
    ):
        async with raw_session_factory() as session:
            repo = TodoRepository(session, project_id="acme-corp")
            todos = await repo.list_all()

            project_ids = {t.project_id for t in todos}
            assert project_ids == {"acme-corp"}, (
                f"Scoped repo returned {project_ids} instead of just acme-corp. "
                "The scoped constructor is broken."
            )


# ---------------------------------------------------------------------------
# Test 5: ThreadPoolExecutor sessions lack tenant filter
# ---------------------------------------------------------------------------


class TestThreadPoolSpawnLosesTenantContext:
    """Prove that a session opened with a tenant contextvar set automatically
    enforces tenant filtering via the do_orm_execute listener.

    The bug: loop.py:737 sets the contextvar, but nothing reads it in the ORM
    path — sessions carry no tenant filter.  The fix (do_orm_execute listener
    + contextvar propagation via asyncio.to_thread) closes this gap.
    """

    @pytest.mark.asyncio
    async def test_threadpool_session_auto_filters_by_tenant(
        self, raw_session_factory, seeded_todos
    ):
        tok = set_tenant("acme-corp")
        try:
            async with raw_session_factory() as sess:
                stmt = select(func.count()).select_from(TodoModel)
                result = await sess.execute(stmt)
                count = result.scalar_one()
            assert count == 2, (
                f"Contextvar-scoped query returned {count} rows instead of 2. "
                "The tenant contextvar is set but never consumed — "
                "the ORM has no do_orm_execute listener."
            )
        finally:
            reset_tenant(tok)

    @pytest.mark.asyncio
    async def test_contextvar_propagates_via_to_thread(self):
        def _check():
            return get_tenant()

        tok = set_tenant("globex-inc")
        try:
            # asyncio.to_thread copies the current Context itself. Wrapping the
            # callable in a second Context.run is redundant and proved unstable
            # in a long-lived Python 3.14 xdist worker.
            got = await asyncio.to_thread(_check)
            assert got == "globex-inc", (
                f"Expected globex-inc via to_thread, got {got}. "
                "Contextvar propagation to ThreadPoolExecutor is broken."
            )
        finally:
            reset_tenant(tok)
