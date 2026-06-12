"""W3.11 (H13): project workspaces are materialized from repo_url and persisted.

A dispatched job needs an actual checkout to edit. These tests prove:
- GitAutomation.clone() materializes a repo_url into a target directory (idempotent).
- materialize_project_workspace() clones repo_url into the project's workspace repo dir.
- Projects round-trip through ProjectRepository (repo_url survives) so a restart
  (a fresh ProjectManager built from the DB) still lists the project.

All git operations use a LOCAL file:// fixture repo created in tmp_path — no network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base
from general_ludd.db.repository import ProjectRepository
from general_ludd.git_automation.repo import GitAutomation
from general_ludd.projects.manager import (
    materialize_project_workspace,
    persist_project,
    rebuild_manager_from_db,
)


def _make_fixture_repo(path: Path) -> str:
    """Create a real local git repo with one commit; return its file:// URL."""
    path.mkdir(parents=True, exist_ok=True)
    env = {"GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, env=None)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("# fixture\n")
    (path / "main.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, env=env)
    return f"file://{path}"


class TestGitClone:
    def test_clone_materializes_repo(self, tmp_path):
        url = _make_fixture_repo(tmp_path / "origin")
        target = tmp_path / "checkout"
        git = GitAutomation()
        result = git.clone(url, str(target))
        assert result.success is True
        assert (target / ".git").is_dir()
        assert (target / "README.md").exists()
        assert (target / "main.py").exists()

    def test_clone_is_idempotent(self, tmp_path):
        url = _make_fixture_repo(tmp_path / "origin")
        target = tmp_path / "checkout"
        git = GitAutomation()
        first = git.clone(url, str(target))
        assert first.success is True
        # Second clone into an existing checkout must NOT fail and must not re-clone.
        second = git.clone(url, str(target))
        assert second.success is True
        assert (target / ".git").is_dir()

    def test_clone_bad_url_fails_closed(self, tmp_path):
        git = GitAutomation()
        result = git.clone("file:///nonexistent/repo", str(tmp_path / "out"))
        assert result.success is False


class TestMaterializeWorkspace:
    def test_materialize_clones_repo_url_into_workspace_repo_dir(self, tmp_path):
        url = _make_fixture_repo(tmp_path / "origin")
        workspace_path = tmp_path / "ws"
        repo_dir = materialize_project_workspace(
            repo_url=url, workspace_path=str(workspace_path)
        )
        assert repo_dir is not None
        repo_path = Path(repo_dir)
        assert (repo_path / ".git").is_dir()
        assert (repo_path / "main.py").exists()

    def test_materialize_no_repo_url_is_noop(self, tmp_path):
        result = materialize_project_workspace(
            repo_url="", workspace_path=str(tmp_path / "ws")
        )
        assert result is None


def _make_async_engine():
    return create_async_engine("sqlite+aiosqlite://", echo=False)


@pytest_asyncio.fixture
async def session_factory():
    engine = _make_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


class TestPersistenceRoundTrip:
    @pytest.mark.asyncio
    async def test_project_with_repo_url_survives_restart(self, session_factory, tmp_path):
        url = _make_fixture_repo(tmp_path / "origin")
        # Persist a project (as add-project would).
        async with session_factory() as session:
            repo = ProjectRepository(session)
            await persist_project(
                repo,
                project_id="proj-abc123",
                name="alpha",
                weight=30.0,
                description="d",
                repo_url=url,
                workspace_path=str(tmp_path / "ws"),
                dispatch_mode="active",
            )
            await session.commit()

        # Restart: build a brand-new ProjectManager from the DB only.
        async with session_factory() as session:
            repo = ProjectRepository(session)
            mgr = await rebuild_manager_from_db(repo)

        listed = mgr.list_active()
        assert len(listed) == 1
        p = listed[0]
        assert p.project_id == "proj-abc123"
        assert p.name == "alpha"
        assert p.repo_url == url
        assert p.weight == 30.0
        assert p.dispatch_mode == "active"
