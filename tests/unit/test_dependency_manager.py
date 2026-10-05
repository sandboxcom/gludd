from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from general_ludd.dependency.manager import (
    DependencyManager,
    InvalidPackageSpecError,
    OutdatedPackage,
    SyncResult,
    UpdateResult,
    _has_uv,
    _validate_package_spec,
)


class TestInvalidPackageSpecError:
    def test_is_value_error_subclass(self) -> None:
        err = InvalidPackageSpecError("bad spec")
        assert isinstance(err, ValueError)

    def test_message_preserved(self) -> None:
        err = InvalidPackageSpecError("bad spec: foo")
        assert str(err) == "bad spec: foo"


class TestValidatePackageSpec:
    def test_simple_name_valid(self) -> None:
        result = _validate_package_spec("requests")
        assert result == "requests"

    def test_name_with_version_valid(self) -> None:
        result = _validate_package_spec("requests>=2.0.0")
        assert result == "requests>=2.0.0"

    def test_name_with_extras_valid(self) -> None:
        result = _validate_package_spec("package[extra1,extra2]")
        assert result == "package[extra1,extra2]"

    def test_name_with_extras_and_version_valid(self) -> None:
        result = _validate_package_spec("package[extra]>=1.0,<2.0")
        assert result == "package[extra]>=1.0,<2.0"

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(InvalidPackageSpecError, match="empty or padded"):
            _validate_package_spec("")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(InvalidPackageSpecError, match="empty or padded"):
            _validate_package_spec("   ")

    def test_rejects_padded_spec(self) -> None:
        with pytest.raises(InvalidPackageSpecError, match="empty or padded"):
            _validate_package_spec("  requests  ")

    def test_rejects_leading_dash_flag(self) -> None:
        with pytest.raises(InvalidPackageSpecError, match="must not start with '-'"):
            _validate_package_spec("--index-url=http://evil.com")

    def test_rejects_editable_install(self) -> None:
        with pytest.raises(InvalidPackageSpecError, match="must not start with '-'"):
            _validate_package_spec("-e .")

    def test_rejects_url_spec(self) -> None:
        with pytest.raises(InvalidPackageSpecError, match="unsafe or malformed"):
            _validate_package_spec("https://example.com/pkg.tar.gz")

    def test_rejects_path_spec(self) -> None:
        with pytest.raises(InvalidPackageSpecError, match="unsafe or malformed"):
            _validate_package_spec("./local/pkg")

    def test_rejects_spaces_in_name(self) -> None:
        with pytest.raises(InvalidPackageSpecError, match="unsafe or malformed"):
            _validate_package_spec("my package")

    def test_rejects_non_string_input(self) -> None:
        with pytest.raises(InvalidPackageSpecError, match="must be a string"):
            _validate_package_spec(cast(str, 42))

    def test_rejects_shell_command(self) -> None:
        with pytest.raises(InvalidPackageSpecError, match="unsafe or malformed"):
            _validate_package_spec("requests; rm -rf /")

    def test_name_with_underscores_valid(self) -> None:
        result = _validate_package_spec("my_package==1.0.0")
        assert result == "my_package==1.0.0"

    def test_name_with_dots_valid(self) -> None:
        result = _validate_package_spec("my.package~=1.0")
        assert result == "my.package~=1.0"


class TestUpdateResult:
    def test_defaults(self) -> None:
        r = UpdateResult(package_name="x", old_version="", new_version="", changed=False, tool_used="uv")
        assert r.package_name == "x"
        assert r.changed is False

    def test_changed_true(self) -> None:
        r = UpdateResult(package_name="y", old_version="1.0", new_version="2.0", changed=True, tool_used="pip")
        assert r.changed is True
        assert r.old_version == "1.0"
        assert r.new_version == "2.0"
        assert r.tool_used == "pip"


class TestSyncResult:
    def test_success(self) -> None:
        r = SyncResult(success=True, packages_synced=5, tool_used="uv")
        assert r.success is True
        assert r.packages_synced == 5

    def test_failure(self) -> None:
        r = SyncResult(success=False, packages_synced=0, tool_used="pip")
        assert r.success is False
        assert r.tool_used == "pip"


class TestOutdatedPackage:
    def test_fields(self) -> None:
        pkg = OutdatedPackage(name="alpha", current_version="1.0.0", latest_version="2.0.0")
        assert pkg.name == "alpha"
        assert pkg.current_version == "1.0.0"
        assert pkg.latest_version == "2.0.0"


class TestHasUv:
    def test_has_uv_true(self) -> None:
        with patch("general_ludd.dependency.manager.shutil.which", return_value="/usr/bin/uv"):
            assert _has_uv() is True

    def test_has_uv_false(self) -> None:
        with patch("general_ludd.dependency.manager.shutil.which", return_value=None):
            assert _has_uv() is False


class TestDependencyManagerInit:
    def test_default_project_root(self) -> None:
        mgr = DependencyManager()
        assert mgr.project_root == "."

    def test_custom_project_root(self) -> None:
        mgr = DependencyManager(project_root="/abs/path")
        assert mgr.project_root == "/abs/path"


class TestSyncWithUvSuccess:
    @pytest.mark.asyncio
    async def test_sync_uv_success(self) -> None:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            result = await mgr.sync_environment()

        assert result.success is True
        assert result.tool_used == "uv"


class TestGenerateRequirements:
    @pytest.mark.asyncio
    async def test_generate_with_uv_fallback(self) -> None:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value=None),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            await mgr.generate_requirements()

        args = mock_exec.call_args[0]
        assert args[0] == "pip"
        assert "freeze" in args

    @pytest.mark.asyncio
    async def test_generate_with_uv(self) -> None:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        mgr = DependencyManager()

        with (
            patch("general_ludd.dependency.manager.shutil.which", return_value="/usr/bin/uv"),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            await mgr.generate_requirements()

        args = mock_exec.call_args[0]
        assert args[0] == "uv"


class TestDependencyManagerRun:
    @pytest.mark.asyncio
    async def test_run_returns_returncode_stdout_stderr(self) -> None:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"out", b"err"))
        mgr = DependencyManager()

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            rc, stdout, stderr = await mgr._run("echo", "hello")

        assert rc == 0
        assert "out" in stdout
        assert "err" in stderr

    @pytest.mark.asyncio
    async def test_run_confines_uv_environment_to_managed_project(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A nested uv project must not mutate its caller's toolchain."""
        caller_environment = tmp_path / "caller-toolchain"
        managed_project = tmp_path / "managed-project"
        managed_project.mkdir()
        monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(caller_environment))
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        mgr = DependencyManager(project_root=str(managed_project))

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await mgr._run("uv", "sync")

        child_environment = mock_exec.call_args.kwargs["env"]
        assert child_environment["UV_PROJECT_ENVIRONMENT"] == str(
            managed_project / ".venv"
        )
        assert child_environment["UV_PROJECT_ENVIRONMENT"] != str(
            caller_environment
        )
