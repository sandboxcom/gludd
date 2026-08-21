"""Tests for sandbox/enforcer.py — path confinement, network isolation, resource limits.

Covers the SandboxEnforcer with behavioral tests for:
  - Path confinement (within-jail, escape, symlink, parent traversal, null byte)
  - Network isolation (default deny, allowlist, platform no-ops)
  - Resource limits (memory, CPU, process count, file size)
  - Fail-closed (verify_ready gate, execute gate, confine_path gate)
  - Fail-open (warn-and-proceed)
  - Max output enforcement
  - Auto-jail directory creation
  - Configuration defaults
"""

from __future__ import annotations

import gc
import os
import socket
import subprocess
import tempfile
import weakref
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.sandbox.enforcer import (
    MaxOutputExceededError,
    PathEscapeError,
    SandboxConfig,
    SandboxEnforcer,
    SandboxNotAvailableError,
)
from general_ludd.sandbox.process_executor import ProcessExecutor


class TestPathConfinement:
    """Path confinement — all file access restricted to jail_dir."""

    def test_confine_path_within_jail_subdirectory(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        sub = jail / "deep" / "nested"
        sub.mkdir(parents=True)
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        result = enforcer.confine_path(str(sub / "data.json"))
        assert jail.name in result

    def test_confine_path_absolute_outside_jail_raises(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError, match="escapes sandbox jail"):
            enforcer.confine_path("/etc/passwd")

    def test_confine_path_dot_dot_traversal_blocked(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path(str(jail / ".." / ".." / "tmp"))

    def test_confine_path_symlink_to_outside_blocked(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        symlink = jail / "escape"
        symlink.symlink_to(outside_dir)
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path(str(symlink))

    def test_confine_path_symlink_to_inside_allowed(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        target = jail / "real.txt"
        target.write_text("data")
        symlink = jail / "shortcut.txt"
        symlink.symlink_to(target)
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        result = enforcer.confine_path(str(symlink))
        assert "real.txt" in result

    def test_confine_path_empty_string_on_verified(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path("")

    def test_confine_path_relative_basename_resolved_against_jail(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        result = enforcer.confine_path("somefile.txt")
        assert jail.name in result
        assert result.endswith("somefile.txt")

    def test_confine_path_unverified_raises_fail_closed(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path)))
        with pytest.raises(SandboxNotAvailableError, match="not verified"):
            enforcer.confine_path(str(tmp_path / "x"))

    def test_confine_path_unverified_passes_fail_open(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), fail_open=True,
        ))
        result = enforcer.confine_path(str(tmp_path / "x"))
        assert result == str(tmp_path / "x")

    def test_auto_jail_is_removed_on_close_but_external_jail_is_preserved(
        self,
        tmp_path: Path,
    ) -> None:
        owned = SandboxEnforcer(SandboxConfig())
        owned.verify_ready()
        owned_path = Path(owned.jail_dir)
        assert owned_path.is_dir()
        owned.close()
        owned.close()
        assert not owned_path.exists()

        external_path = tmp_path / "external-jail"
        external_path.mkdir()
        external = SandboxEnforcer(SandboxConfig(jail_dir=str(external_path)))
        external.verify_ready()
        external.close()
        assert external_path.is_dir()

    def test_auto_jail_owner_release_is_warning_free(self) -> None:
        enforcer = SandboxEnforcer(SandboxConfig())
        enforcer.verify_ready()
        jail = Path(enforcer.jail_dir)
        owner_ref = weakref.ref(enforcer)

        del enforcer
        gc.collect()

        assert owner_ref() is None
        assert not jail.exists()

    def test_failed_auto_jail_verification_rolls_back_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        enforcer = SandboxEnforcer(SandboxConfig())
        created: list[Path] = []
        real_temporary_directory = tempfile.TemporaryDirectory

        def temporary_directory(
            *args: Any, **kwargs: Any
        ) -> tempfile.TemporaryDirectory[str]:
            owner = real_temporary_directory(*args, **kwargs)
            created.append(Path(owner.name))
            return owner

        monkeypatch.setattr("general_ludd.sandbox.enforcer.tempfile.TemporaryDirectory", temporary_directory)
        monkeypatch.setattr("general_ludd.sandbox.enforcer.os.access", lambda *_args: False)

        with pytest.raises(SandboxNotAvailableError, match="not writable"):
            enforcer.verify_ready()

        assert created and not created[0].exists()


class TestNetworkIsolation:
    """Network restrictions — outbound connections blocked by default."""

    def test_default_config_denies_network(self) -> None:
        config = SandboxConfig()
        assert config.allow_network is False
        assert config.allowed_hosts == []

    def test_allow_network_with_hostlist(self) -> None:
        config = SandboxConfig(allow_network=True, allowed_hosts=["10.0.0.1", "10.0.0.2"])
        assert config.allow_network is True
        assert "10.0.0.1" in config.allowed_hosts
        assert "10.0.0.2" in config.allowed_hosts

    def test_isolate_network_noop_on_windows(self) -> None:
        with patch("os.name", "nt"):
            SandboxEnforcer._isolate_network()

    def test_isolate_network_posix_no_network_env(self) -> None:
        with (
            patch("os.name", "posix"),
            patch.dict(os.environ, {"GLUDD_SANDBOX_NO_NETWORK": "1"}),
        ):
            SandboxEnforcer._isolate_network()

    def test_isolate_network_posix_env_variants_true(self) -> None:
        for val in ("1", "true", "yes"):
            with (
                patch("os.name", "posix"),
                patch.dict(os.environ, {"GLUDD_SANDBOX_NO_NETWORK": val}),
            ):
                SandboxEnforcer._isolate_network()

    def test_isolate_network_does_not_leak_process_socket_timeout(self) -> None:
        """Isolation must not mutate sockets subsequently created by the caller."""
        original_timeout = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(None)
            with (
                patch("os.name", "posix"),
                patch.dict(os.environ, {"GLUDD_SANDBOX_NO_NETWORK": "1"}),
            ):
                SandboxEnforcer._isolate_network()
            assert socket.getdefaulttimeout() is None
        finally:
            socket.setdefaulttimeout(original_timeout)

    def test_isolate_network_posix_env_not_set(self) -> None:
        with (
            patch("os.name", "posix"),
            patch.dict(os.environ, {}, clear=True),
        ):
            SandboxEnforcer._isolate_network()


class TestResourceLimits:
    """Resource limits — memory, CPU, process count, file size."""

    def test_default_limits_from_config(self, tmp_path: Path) -> None:
        config = SandboxConfig(
            jail_dir=str(tmp_path),
            memory_mb=256, cpu_seconds=120, max_processes=30,
        )
        enforcer = SandboxEnforcer(config)
        enforcer.verify_ready()
        assert enforcer._limits.memory_mb == 256
        assert enforcer._limits.cpu_seconds == 120
        assert enforcer._limits.max_processes == 30

    def test_config_defaults_match_process_limits(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig())
        assert enforcer._limits.memory_mb == 512
        assert enforcer._limits.cpu_seconds == 300
        assert enforcer._limits.max_file_size is None
        assert enforcer._limits.max_open_files is None
        assert enforcer._limits.max_processes == 50

    def test_sandbox_config_defaults_preserved(self) -> None:
        config = SandboxConfig()
        assert config.memory_mb == 512
        assert config.cpu_seconds == 300
        assert config.max_output_bytes == 1_000_000
        assert config.max_processes == 50
        assert config.timeout == 300

    def test_limit_values_propagate_to_process_executor(self, tmp_path: Path) -> None:
        config = SandboxConfig(
            jail_dir=str(tmp_path), memory_mb=512, cpu_seconds=180,
        )
        enforcer = SandboxEnforcer(config)
        enforcer.verify_ready()
        limits = enforcer._limits
        assert limits.memory_mb == 512
        assert limits.cpu_seconds == 180

    def test_apply_sandbox_preexec_calls_both_limits_and_isolation(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path)))
        enforcer.verify_ready()
        with (
            patch.object(ProcessExecutor, "_apply_limits") as mock_limits,
            patch.object(SandboxEnforcer, "_isolate_network") as mock_net,
        ):
            enforcer._apply_sandbox_preexec(enforcer._limits)
            mock_limits.assert_called_once_with(enforcer._limits)
            mock_net.assert_called_once()

    def test_executor_timeout_from_config(self, tmp_path: Path) -> None:
        config = SandboxConfig(jail_dir=str(tmp_path), timeout=45)
        enforcer = SandboxEnforcer(config)
        assert enforcer._executor.timeout == 45


class TestFailClosed:
    """Fail-closed: execution blocked until verify_ready() succeeds."""

    def test_execute_raises_before_verify(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path)))
        with pytest.raises(SandboxNotAvailableError, match="not verified"):
            enforcer.execute("echo hi")

    def test_execute_succeeds_after_verify(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), memory_mb=0, cpu_seconds=0, max_processes=0,
        ))
        enforcer.verify_ready()
        with patch.object(subprocess, "Popen") as mock_popen, patch.object(
            enforcer._executor.__class__, "_apply_limits",
        ):
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("ok\n", "")
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            result = enforcer.execute("echo ok")
            assert result.returncode == 0
            assert "ok" in result.stdout

    def test_confine_path_raises_before_verify(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path)))
        with pytest.raises(SandboxNotAvailableError, match="not verified"):
            enforcer.confine_path(str(tmp_path / "x"))

    def test_execute_with_workdir_in_jail(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        sub_dir = jail / "sub"
        sub_dir.mkdir(parents=True)
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with patch.object(subprocess, "Popen") as mock_popen, patch.object(
            enforcer._executor.__class__, "_apply_limits",
        ):
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (str(sub_dir), "")
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            result = enforcer.execute("pwd", workdir=str(sub_dir))
            assert result.returncode == 0

    def test_execute_with_workdir_outside_jail_raises(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError, match="escapes sandbox jail"):
            enforcer.execute("pwd", workdir=str(outside))

    def test_execute_with_none_workdir_is_allowed(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), memory_mb=0, cpu_seconds=0, max_processes=0,
        ))
        enforcer.verify_ready()
        with patch.object(subprocess, "Popen") as mock_popen, patch.object(
            enforcer._executor.__class__, "_apply_limits",
        ):
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("none\n", "")
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            result = enforcer.execute("echo none")
            assert "none" in result.stdout

    def test_is_ready_false_before_verify(self) -> None:
        enforcer = SandboxEnforcer(SandboxConfig())
        assert enforcer.is_ready is False

    def test_is_ready_true_after_verify(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path)))
        enforcer.verify_ready()
        assert enforcer.is_ready is True

    def test_double_verify_is_idempotent(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path)))
        enforcer.verify_ready()
        enforcer.verify_ready()
        assert enforcer.is_ready


class TestFailOpen:
    """Fail-open mode: warn-and-proceed when sandbox cannot be applied."""

    def test_execute_unverified_proceeds_fail_open(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), fail_open=True, memory_mb=0, cpu_seconds=0,
            max_processes=0,
        ))
        with patch.object(subprocess, "Popen") as mock_popen, patch.object(
            enforcer._executor.__class__, "_apply_limits",
        ):
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("hello\n", "")
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            result = enforcer.execute("echo hello")
            assert result.returncode == 0
            assert "hello" in result.stdout

    def test_verify_ready_nonexistent_dir_warns_fail_open(self, tmp_path: Path) -> None:
        nonexistent = str(tmp_path / "gone")
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=nonexistent, fail_open=True,
        ))
        enforcer.verify_ready()
        assert enforcer.is_ready

    def test_verify_ready_refuses_explicit_missing_jail_dir_fail_closed(self, tmp_path: Path) -> None:
        jail = tmp_path / "created-by-verify"
        assert not jail.exists()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        with pytest.raises(SandboxNotAvailableError, match="does not exist"):
            enforcer.verify_ready()
        assert not enforcer.is_ready

    def test_confine_path_unverified_passthrough_fail_open(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), fail_open=True,
        ))
        result = enforcer.confine_path(str(tmp_path / "x"))
        assert result == str(tmp_path / "x")

    def test_output_exceeded_warns_fail_open(self, tmp_path: Path) -> None:
        long_str = "x" * 2000
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), max_output_bytes=100, fail_open=True,
        ))
        enforcer.verify_ready()
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (long_str, "")
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            result = enforcer.execute("echo x")
            assert result.returncode == 0


class TestMaxOutput:
    """Max output bytes enforcement — prevents runaway output."""

    def test_small_output_passes(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), max_output_bytes=10_000, memory_mb=0,
            cpu_seconds=0, max_processes=0,
        ))
        enforcer.verify_ready()
        with patch.object(subprocess, "Popen") as mock_popen, patch.object(
            enforcer._executor.__class__, "_apply_limits",
        ):
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("small", "")
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            result = enforcer.execute("echo small")
            assert result.returncode == 0

    def test_exceeded_output_raises_fail_closed(self, tmp_path: Path) -> None:
        long_str = "a" * 2000
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), max_output_bytes=50,
        ))
        enforcer.verify_ready()
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (long_str, "b" * 100)
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            with pytest.raises(MaxOutputExceededError, match="exceeds max"):
                enforcer.execute("printf long")

    def test_stderr_counts_toward_output_limit(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), max_output_bytes=50,
        ))
        enforcer.verify_ready()
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("", "x" * 60)
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            with pytest.raises(MaxOutputExceededError):
                enforcer.execute("cmd")

    def test_combined_stdout_stderr_checked(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), max_output_bytes=50,
        ))
        enforcer.verify_ready()
        with patch.object(subprocess, "Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("a" * 30, "b" * 30)
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            with pytest.raises(MaxOutputExceededError):
                enforcer.execute("cmd")


class TestAutoJail:
    """Auto-creation of jail directory when none specified."""

    def test_empty_jail_dir_triggers_tempdir(self) -> None:
        enforcer = SandboxEnforcer(SandboxConfig())
        enforcer.verify_ready()
        assert enforcer.jail_dir
        assert "gludd-sandbox-" in enforcer.jail_dir
        assert os.path.isdir(enforcer.jail_dir)
        enforcer.close()
        assert not enforcer.jail_dir

    def test_explicit_jail_dir_not_overwritten(self, tmp_path: Path) -> None:
        jail = tmp_path / "my-jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        assert enforcer.jail_dir == str(jail)

    def test_explicit_missing_jail_dir_not_auto_created(self, tmp_path: Path) -> None:
        jail = tmp_path / "new-jail"
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        assert not jail.exists()
        with pytest.raises(SandboxNotAvailableError, match="does not exist"):
            enforcer.verify_ready()
        assert not jail.exists()

    def test_jail_dir_mkdtemp_and_is_ready(self) -> None:
        enforcer = SandboxEnforcer(SandboxConfig())
        enforcer.verify_ready()
        assert enforcer.is_ready
        assert enforcer.jail_dir
        assert os.path.isdir(enforcer.jail_dir)
        enforcer.close()
        assert not enforcer.is_ready


class TestErrorTypes:
    """Custom error type hierarchy."""

    def test_sandbox_not_available_is_runtime_error(self) -> None:
        err = SandboxNotAvailableError("msg")
        assert isinstance(err, RuntimeError)

    def test_path_escape_is_value_error(self) -> None:
        err = PathEscapeError("msg")
        assert isinstance(err, ValueError)

    def test_max_output_exceeded_is_runtime_error(self) -> None:
        err = MaxOutputExceededError("msg")
        assert isinstance(err, RuntimeError)

    def test_error_messages_preserved(self) -> None:
        assert str(SandboxNotAvailableError("test")) == "test"
        assert str(PathEscapeError("path issue")) == "path issue"
        assert str(MaxOutputExceededError("too big")) == "too big"


class TestConfigDefaults:
    """SandboxConfig defaults and immutability of fields."""

    def test_all_fields_have_sensible_defaults(self) -> None:
        c = SandboxConfig()
        assert c.jail_dir == ""
        assert c.allow_network is False
        assert c.allowed_hosts == []
        assert c.memory_mb == 512
        assert c.cpu_seconds == 300
        assert c.max_output_bytes == 1_000_000
        assert c.max_processes == 50
        assert c.timeout == 300
        assert c.fail_open is False

    def test_partial_config_overrides(self) -> None:
        c = SandboxConfig(memory_mb=1024, timeout=60)
        assert c.memory_mb == 1024
        assert c.timeout == 60
        assert c.cpu_seconds == 300
        assert c.max_output_bytes == 1_000_000


class TestExecuteResult:
    """End-to-end execution returns expected ProcessResult shapes."""

    def test_successful_command_has_returncode_zero(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), memory_mb=0, cpu_seconds=0, max_processes=0,
        ))
        enforcer.verify_ready()
        with patch.object(subprocess, "Popen") as mock_popen, patch.object(
            enforcer._executor.__class__, "_apply_limits",
        ):
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("ok\n", "")
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            result = enforcer.execute("echo ok")
            assert result.returncode == 0
            assert "ok" in result.stdout
            assert not result.was_killed

    def test_failing_command_has_nonzero_returncode(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), memory_mb=0, cpu_seconds=0, max_processes=0,
        ))
        enforcer.verify_ready()
        with patch.object(subprocess, "Popen") as mock_popen, patch.object(
            enforcer._executor.__class__, "_apply_limits",
        ):
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("", "error")
            mock_proc.returncode = 1
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            result = enforcer.execute("false")
            assert result.returncode != 0

    def test_stdout_and_stderr_captured(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), memory_mb=0, cpu_seconds=0, max_processes=0,
        ))
        enforcer.verify_ready()
        with patch.object(subprocess, "Popen") as mock_popen, patch.object(
            enforcer._executor.__class__, "_apply_limits",
        ):
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("stdout\n", "stderr\n")
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            result = enforcer.execute("cmd")
            assert "stdout" in result.stdout
            assert "stderr" in result.stderr
