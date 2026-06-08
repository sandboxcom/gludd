from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from general_ludd.dependency.manager import (
    DependencyManager,
)


def _make_process(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> AsyncMock:
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(
        return_value=(stdout.encode(), stderr.encode())
    )
    return proc


class TestDependencyManagerInit:
    def test_default_project_root(self):
        mgr = DependencyManager()
        assert mgr.project_root == "."

    def test_custom_project_root(self):
        mgr = DependencyManager(project_root="/tmp/project")
        assert mgr.project_root == "/tmp/project"


class TestUpdateWithUvFailure:
    @pytest.mark.asyncio
    async def test_update_with_uv_nonzero_returncode(self):
        proc = _make_process(stderr="uv error", returncode=1)
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            result = await mgr.update_package("bad-pkg", ">=1.0.0")

        assert result.changed is False
        assert result.tool_used == "uv"
        assert result.package_name == "bad-pkg"


class TestUpdateWithPipFailure:
    @pytest.mark.asyncio
    async def test_update_with_pip_nonzero_returncode(self):
        proc = _make_process(stderr="pip error", returncode=1)
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value=None),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            result = await mgr.update_package("bad-pkg", ">=1.0.0")

        assert result.changed is False
        assert result.tool_used == "pip"
        assert result.package_name == "bad-pkg"


class TestUpdatePackageNoConstraint:
    @pytest.mark.asyncio
    async def test_uv_no_version_constraint(self):
        proc = _make_process(stdout="Installed foo-2.0.0", returncode=0)
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            result = await mgr.update_package("foo")

        assert result.changed is True
        assert result.tool_used == "uv"
        args = mock_exec.call_args[0]
        assert "foo" in args

    @pytest.mark.asyncio
    async def test_pip_no_version_constraint(self):
        proc = _make_process(
            stdout="Successfully installed foo-2.0.0", returncode=0
        )
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value=None),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            result = await mgr.update_package("foo")

        assert result.changed is True
        assert result.tool_used == "pip"
        args = mock_exec.call_args[0]
        assert "foo" in " ".join(str(a) for a in args)


class TestSyncWithPipFailure:
    @pytest.mark.asyncio
    async def test_sync_pip_nonzero_returncode(self):
        proc = _make_process(stderr="pip sync error", returncode=1)
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value=None),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            result = await mgr.sync_environment()

        assert result.success is False
        assert result.tool_used == "pip"


class TestCheckOutdatedJsonPath:
    @pytest.mark.asyncio
    async def test_check_outdated_uv_json_output(self):
        import json

        json_data = [
            {"name": "alpha", "version": "1.0.0", "latest_version": "2.0.0"},
            {"name": "beta", "version": "0.5.0", "latest_version": "1.0.0"},
        ]
        proc_json = _make_process(
            stdout=json.dumps(json_data), returncode=0
        )
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", return_value=proc_json),
        ):
            outdated = await mgr.check_for_updates()

        assert len(outdated) == 2
        assert outdated[0].name == "alpha"
        assert outdated[0].current_version == "1.0.0"
        assert outdated[0].latest_version == "2.0.0"
        assert outdated[1].name == "beta"

    @pytest.mark.asyncio
    async def test_check_outdated_pip_json_output(self):
        import json

        json_data = [
            {"name": "gamma", "version": "3.0.0", "latest_version": "4.0.0"},
        ]
        proc_json = _make_process(
            stdout=json.dumps(json_data), returncode=0
        )
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value=None),
            patch("asyncio.create_subprocess_exec", return_value=proc_json),
        ):
            outdated = await mgr.check_for_updates()

        assert len(outdated) == 1
        assert outdated[0].name == "gamma"

    @pytest.mark.asyncio
    async def test_check_outdated_pip_text_fallback(self):
        text_output = "delta 1.0.0 2.0.0\n"
        proc_json = _make_process(stdout="", returncode=0)
        proc_text = _make_process(stdout=text_output, returncode=0)
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value=None),
            patch("asyncio.create_subprocess_exec", side_effect=[proc_json, proc_text]),
        ):
            outdated = await mgr.check_for_updates()

        assert len(outdated) == 1
        assert outdated[0].name == "delta"

    @pytest.mark.asyncio
    async def test_check_outdated_uv_text_fallback(self):
        text_output = "epsilon 0.1.0 0.2.0\n"
        proc_json = _make_process(stdout="", returncode=0)
        proc_text = _make_process(stdout=text_output, returncode=0)
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", side_effect=[proc_json, proc_text]),
        ):
            outdated = await mgr.check_for_updates()

        assert len(outdated) == 1
        assert outdated[0].name == "epsilon"

    @pytest.mark.asyncio
    async def test_check_outdated_pip_no_packages(self):
        proc = _make_process(stdout="", returncode=0)
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value=None),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            outdated = await mgr.check_for_updates()

        assert outdated == []


class TestParseOutdatedText:
    def test_skips_header_lines(self):
        mgr = DependencyManager()
        text = "Package Version Latest\n-----   ------- ------\nfoo 1.0.0 2.0.0\n"
        result = mgr._parse_outdated_text(text)
        assert len(result) == 1
        assert result[0].name == "foo"

    def test_skips_short_lines(self):
        mgr = DependencyManager()
        text = "foo\nbar 1.0.0\nbaz 1.0.0 2.0.0\n"
        result = mgr._parse_outdated_text(text)
        assert len(result) == 1
        assert result[0].name == "baz"

    def test_empty_text(self):
        mgr = DependencyManager()
        result = mgr._parse_outdated_text("")
        assert result == []

    def test_multiple_valid_lines(self):
        mgr = DependencyManager()
        text = "alpha 1.0.0 2.0.0\nbeta 3.0.0 4.0.0\ngamma 0.1.0 1.0.0\n"
        result = mgr._parse_outdated_text(text)
        assert len(result) == 3
        assert result[0].name == "alpha"
        assert result[1].name == "beta"
        assert result[2].name == "gamma"


class TestParseOutdatedJson:
    def test_valid_json(self):
        import json

        mgr = DependencyManager()
        data = [
            {"name": "foo", "version": "1.0.0", "latest_version": "2.0.0"},
        ]
        result = mgr._parse_outdated_json(json.dumps(data))
        assert len(result) == 1
        assert result[0].name == "foo"
        assert result[0].current_version == "1.0.0"
        assert result[0].latest_version == "2.0.0"

    def test_invalid_json(self):
        mgr = DependencyManager()
        result = mgr._parse_outdated_json("not json at all")
        assert result == []

    def test_empty_json_array(self):
        mgr = DependencyManager()
        result = mgr._parse_outdated_json("[]")
        assert result == []


class TestRunUsesProjectRoot:
    @pytest.mark.asyncio
    async def test_run_passes_project_root_as_cwd(self):
        proc = _make_process(stdout="", returncode=0)
        mgr = DependencyManager(project_root="/custom/root")

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            await mgr.update_package("pkg")

        assert mock_exec.call_args[1]["cwd"] == "/custom/root"


class TestSyncWithPipSuccess:
    @pytest.mark.asyncio
    async def test_sync_pip_success_returns_zero_packages(self):
        proc = _make_process(stdout="Successfully installed", returncode=0)
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value=None),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            result = await mgr.sync_environment()

        assert result.success is True
        assert result.packages_synced == 0
        assert result.tool_used == "pip"
        args = mock_exec.call_args[0]
        cmd = " ".join(str(a) for a in args)
        assert "requirements.txt" in cmd
