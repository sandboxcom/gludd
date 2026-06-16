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
        # allow_local: this fixture uses a trusted local file:// repo (no network).
        # base_dir=tmp_path so the in-tmp workspace_path is inside the jail root.
        repo_dir = materialize_project_workspace(
            repo_url=url,
            workspace_path=str(workspace_path),
            base_dir=str(tmp_path),
            allow_local=True,
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


class TestMaterializeRejectsUnsafeCloneUrls:
    """#56 (RCE/SSRF): caller-supplied repo_url is validated before git clone.

    Each dangerous URL form must be REFUSED with ValueError (the router turns
    that into HTTP 422) and must never reach GitAutomation.clone.
    """

    @pytest.mark.parametrize(
        "bad_url",
        [
            "file:///etc/passwd",
            "file://localhost/etc/shadow",
            "ext::sh -c 'touch /tmp/pwned'",
            "git::https://evil.example/repo",
            "fd::17/foo",
            "ssh://git@github.com/-oProxyCommand=cmd/repo.git",
            "git@github.com:-oProxyCommand=touch${IFS}pwned/repo.git",
            "https://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1/repo.git",
            "https://localhost/repo.git",
            "https://10.0.0.5/internal.git",
            "git://192.168.1.10/internal.git",
        ],
    )
    def test_unsafe_clone_url_raises_422_inputs(self, tmp_path, bad_url):
        with pytest.raises(ValueError):
            materialize_project_workspace(
                repo_url=bad_url,
                workspace_path="proj-x",
                base_dir=str(tmp_path),
            )

    def test_normal_https_public_clone_is_accepted(self, tmp_path):
        """A normal https clone to a public host is materialized (not rejected).

        We serve it from a LOCAL bare fixture repo so there is no network, but
        present it as an https URL to a public host by validating the guard
        directly and then cloning the trusted local mirror.
        """
        from general_ludd.security.auth import is_safe_clone_url

        # The guard must ACCEPT a normal public https clone URL.
        assert is_safe_clone_url("https://github.com/octocat/Hello-World.git")
        assert is_safe_clone_url("git://git.example.org/public/repo.git")

        # And an in-root workspace_path + trusted local repo materializes cleanly.
        url = _make_fixture_repo(tmp_path / "origin")
        repo_dir = materialize_project_workspace(
            repo_url=url,
            workspace_path="proj-ok",
            base_dir=str(tmp_path),
            allow_local=True,
        )
        assert repo_dir is not None
        assert (Path(repo_dir) / ".git").is_dir()


class TestMaterializeConfinesWorkspacePath:
    """#56 (traversal): a ../-escaping workspace_path is rejected with ValueError."""

    @pytest.mark.parametrize(
        "escape",
        [
            "../../../etc/evil",
            "../outside",
            "/etc/cron.d/evil",
            "sub/../../../../tmp/escape",
        ],
    )
    def test_escaping_workspace_path_raises(self, tmp_path, escape):
        # A safe local fixture URL (allow_local) so ONLY the path guard can trip.
        url = _make_fixture_repo(tmp_path / "origin")
        with pytest.raises(ValueError):
            materialize_project_workspace(
                repo_url=url,
                workspace_path=escape,
                base_dir=str(tmp_path / "root"),
                allow_local=True,
            )

    def test_in_root_relative_workspace_path_is_accepted(self, tmp_path):
        url = _make_fixture_repo(tmp_path / "origin")
        repo_dir = materialize_project_workspace(
            repo_url=url,
            workspace_path="nested/proj-ok",
            base_dir=str(tmp_path / "root"),
            allow_local=True,
        )
        assert repo_dir is not None
        assert (Path(repo_dir) / ".git").is_dir()


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
