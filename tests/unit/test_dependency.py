"""Unit tests for dependency update pipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agentic_harness.dependency.manager import (
    DependencyManager,
    OutdatedPackage,
    SyncResult,
    UpdateResult,
)

PLAYBOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "playbooks"


def _make_process(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> AsyncMock:
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(
        return_value=(stdout.encode(), stderr.encode())
    )
    return proc


class TestUpdateResultDataclass:
    def test_fields(self):
        r = UpdateResult(
            package_name="foo",
            old_version="1.0.0",
            new_version="2.0.0",
            changed=True,
            tool_used="uv",
        )
        assert r.package_name == "foo"
        assert r.old_version == "1.0.0"
        assert r.new_version == "2.0.0"
        assert r.changed is True
        assert r.tool_used == "uv"

    def test_no_change(self):
        r = UpdateResult(
            package_name="bar",
            old_version="1.0.0",
            new_version="1.0.0",
            changed=False,
            tool_used="pip",
        )
        assert r.changed is False


class TestOutdatedPackageDataclass:
    def test_fields(self):
        p = OutdatedPackage(
            name="baz",
            current_version="1.0.0",
            latest_version="2.0.0",
        )
        assert p.name == "baz"
        assert p.current_version == "1.0.0"
        assert p.latest_version == "2.0.0"


class TestSyncResultDataclass:
    def test_fields(self):
        r = SyncResult(success=True, packages_synced=5, tool_used="uv")
        assert r.success is True
        assert r.packages_synced == 5
        assert r.tool_used == "uv"


class TestDependencyManagerUpdateWithUv:
    @pytest.mark.asyncio
    async def test_update_package_uses_uv(self):
        proc = _make_process(stdout="", returncode=0)
        mgr = DependencyManager()

        with (
            patch("agentic_harness.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            result = await mgr.update_package("requests", ">=2.32.0")

        assert result.changed is True
        assert result.tool_used == "uv"
        assert result.package_name == "requests"
        args = mock_exec.call_args[0]
        assert "uv" in args[0]

    @pytest.mark.asyncio
    async def test_update_package_no_change_when_already_current(self):
        proc = _make_process(
            stdout="Resolved 0 packages",
            returncode=0,
        )
        mgr = DependencyManager()

        with (
            patch("agentic_harness.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            result = await mgr.update_package("requests", ">=2.32.0")

        assert result.changed is False
        assert result.tool_used == "uv"


class TestDependencyManagerUpdateWithPipFallback:
    @pytest.mark.asyncio
    async def test_update_package_falls_back_to_pip(self):
        proc = _make_process(
            stdout="Successfully installed foo-2.0.0",
            returncode=0,
        )
        mgr = DependencyManager()

        with (
            patch("agentic_harness.dependency.manager.shutil.which", return_value=None),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            result = await mgr.update_package("foo", ">=1.0.0")

        assert result.changed is True
        assert result.tool_used == "pip"
        args = mock_exec.call_args[0]
        assert "pip" in " ".join(str(a) for a in args)

    @pytest.mark.asyncio
    async def test_update_package_pip_no_change(self):
        proc = _make_process(
            stdout="Requirement already satisfied: foo==1.0.0",
            returncode=0,
        )
        mgr = DependencyManager()

        with (
            patch("agentic_harness.dependency.manager.shutil.which", return_value=None),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            result = await mgr.update_package("foo", "==1.0.0")

        assert result.changed is False
        assert result.tool_used == "pip"


class TestDependencyManagerSyncEnvironment:
    @pytest.mark.asyncio
    async def test_sync_with_uv(self):
        proc = _make_process(stdout="Resolved 42 packages", returncode=0)
        mgr = DependencyManager()

        with (
            patch("agentic_harness.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            result = await mgr.sync_environment()

        assert result.success is True
        assert result.tool_used == "uv"

    @pytest.mark.asyncio
    async def test_sync_with_pip_fallback(self):
        proc = _make_process(
            stdout="Successfully installed 10 packages",
            returncode=0,
        )
        mgr = DependencyManager()

        with (
            patch("agentic_harness.dependency.manager.shutil.which", return_value=None),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            result = await mgr.sync_environment()

        assert result.success is True
        assert result.tool_used == "pip"
        args = mock_exec.call_args[0]
        assert "pip" in " ".join(str(a) for a in args)

    @pytest.mark.asyncio
    async def test_sync_failure(self):
        proc = _make_process(stderr="error", returncode=1)
        mgr = DependencyManager()

        with (
            patch("agentic_harness.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            result = await mgr.sync_environment()

        assert result.success is False


class TestDependencyManagerCheckForUpdates:
    @pytest.mark.asyncio
    async def test_parses_outdated_packages(self):
        output = "foo 1.0.0 2.0.0\nbar 3.0.0 4.0.0\n"
        proc_json = _make_process(stdout="", returncode=0)
        proc_text = _make_process(stdout=output, returncode=0)
        mgr = DependencyManager()

        with (
            patch("agentic_harness.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", side_effect=[proc_json, proc_text]),
        ):
            outdated = await mgr.check_for_updates()

        assert len(outdated) == 2
        assert outdated[0].name == "foo"
        assert outdated[0].current_version == "1.0.0"
        assert outdated[0].latest_version == "2.0.0"
        assert outdated[1].name == "bar"

    @pytest.mark.asyncio
    async def test_no_outdated_packages(self):
        proc = _make_process(stdout="", returncode=0)
        mgr = DependencyManager()

        with (
            patch("agentic_harness.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            outdated = await mgr.check_for_updates()

        assert outdated == []


class TestDependencyManagerGenerateRequirements:
    @pytest.mark.asyncio
    async def test_generate_requirements_with_uv(self):
        proc = _make_process(stdout="", returncode=0)
        mgr = DependencyManager()

        with (
            patch("agentic_harness.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            await mgr.generate_requirements()

        args = mock_exec.call_args[0]
        cmd_str = " ".join(str(a) for a in args)
        assert "uv" in cmd_str
        assert "pip" in cmd_str

    @pytest.mark.asyncio
    async def test_generate_requirements_with_pip_fallback(self):
        proc = _make_process(stdout="", returncode=0)
        mgr = DependencyManager()

        with (
            patch("agentic_harness.dependency.manager.shutil.which", return_value=None),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            await mgr.generate_requirements()

        args = mock_exec.call_args[0]
        cmd_str = " ".join(str(a) for a in args)
        assert "pip" in cmd_str
        assert "freeze" in cmd_str


class TestDependencyUpdatePlaybookExists:
    def test_playbook_file_exists(self):
        path = PLAYBOOKS_DIR / "dependency_update.yml"
        assert path.exists(), f"dependency_update.yml not found in {PLAYBOOKS_DIR}"

    def test_playbook_is_valid_yaml(self):
        import yaml

        path = PLAYBOOKS_DIR / "dependency_update.yml"
        content = path.read_text()
        data = yaml.safe_load(content)
        assert isinstance(data, list)
        assert len(data) > 0
        assert data[0]["name"]

    def test_playbook_has_expected_vars(self):
        import yaml

        path = PLAYBOOKS_DIR / "dependency_update.yml"
        content = path.read_text()
        data = yaml.safe_load(content)
        play = data[0]
        vars_block = play.get("vars", {})
        assert "job_id" in vars_block
        assert "package_name" in vars_block
