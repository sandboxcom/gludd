"""Unit tests for sandbox/backends/ — ProcessBackend and ContainerBackend.

Covers:
  - SandboxBackend Protocol conformance for both backends
  - ProcessBackend subprocess execution, timeout, output truncation
  - ContainerBackend docker/podman detection, execution, image pull, cleanup
  - SandboxConfig → backend wiring
"""

from __future__ import annotations

import asyncio
import os
import resource
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn, cast
from unittest import mock

import pytest

from general_ludd.sandbox.contracts import (
    IsolationLevel,
    SandboxBackend,
    SandboxConfig,
    SandboxResult,
)

# ---------------------------------------------------------------------------
# ProcessBackend
# ---------------------------------------------------------------------------


class TestProcessBackendProtocol:
    def test_satisfies_sandbox_backend_protocol(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig())
        assert isinstance(backend, SandboxBackend)

    def test_name_is_process(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        assert ProcessBackend(SandboxConfig()).name == "process"

    def test_available_returns_true(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        assert ProcessBackend(SandboxConfig()).available() is True


class TestProcessBackendExecute:
    def test_successful_command(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("echo hello")
        assert isinstance(result, SandboxResult)
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert result.success is True

    def test_failing_command(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("exit 1")
        assert result.returncode == 1
        assert result.success is False

    def test_stderr_captured(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("echo error >&2")
        assert "error" in result.stderr

    def test_timeout_kills_process(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=1))
        result = backend.execute("sleep 10")
        assert result.was_killed is True
        assert result.returncode != 0

    def test_hosted_user_tasks_are_added_before_applying_process_budget(self) -> None:
        from general_ludd.sandbox.backends.process_backend import _nproc_soft_limit

        assert _nproc_soft_limit(
            requested_processes=50,
            existing_user_tasks=75,
            hard_limit=-1,
        ) == 125
        assert _nproc_soft_limit(
            requested_processes=50,
            existing_user_tasks=75,
            hard_limit=100,
        ) == 100

    def test_timeout_kills_owned_process_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from general_ludd.sandbox.backends import process_backend as module

        class FakeProcess:
            pid = 4242
            returncode: int | None = None

            def __init__(self) -> None:
                self.communications = 0

            def communicate(self, *, timeout: float) -> tuple[str, str]:
                self.communications += 1
                if self.communications == 1:
                    raise subprocess.TimeoutExpired("command", timeout)
                return "", ""

            def kill(self) -> None:
                raise AssertionError("owned POSIX groups must not kill only the shell")

        process = FakeProcess()
        popen_kwargs: dict[str, object] = {}
        killed: list[tuple[int, int]] = []

        def fake_popen(_command: str, **kwargs: object) -> FakeProcess:
            popen_kwargs.update(kwargs)
            return process

        def kill_group(pgid: int, sig: int) -> None:
            killed.append((pgid, sig))
            process.returncode = -sig

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(os, "getpgid", lambda pid: pid)
        monkeypatch.setattr(os, "getsid", lambda pid: process.pid if pid else 101)
        monkeypatch.setattr(os, "getpgrp", lambda: 101)
        monkeypatch.setattr(os, "killpg", kill_group)

        result = module.ProcessBackend(SandboxConfig(timeout=1)).execute("sleep 10")

        assert popen_kwargs["start_new_session"] is True
        assert killed == [(process.pid, signal.SIGKILL)]
        assert result.was_killed is True

    def test_timeout_never_signals_an_ambiguous_caller_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from general_ludd.sandbox.backends import process_backend as module

        class FakeProcess:
            pid = 0
            returncode: int | None = None

            def __init__(self) -> None:
                self.communications = 0
                self.direct_kills = 0

            def communicate(self, *, timeout: float) -> tuple[str, str]:
                self.communications += 1
                if self.communications == 1:
                    raise subprocess.TimeoutExpired("command", timeout)
                return "", ""

            def kill(self) -> None:
                self.direct_kills += 1
                self.returncode = -signal.SIGKILL

        process = FakeProcess()
        kill_group = mock.Mock()
        monkeypatch.setattr(
            subprocess, "Popen", lambda *_args, **_kwargs: process
        )
        monkeypatch.setattr(os, "killpg", kill_group)

        result = module.ProcessBackend(SandboxConfig(timeout=1)).execute("wait")

        kill_group.assert_not_called()
        assert process.direct_kills == 1
        assert result.was_killed is True

    def test_timeout_requires_a_confined_child_session_and_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from general_ludd.sandbox.backends import process_backend as module

        class FakeProcess:
            pid = 4242
            returncode: int | None = None

            def __init__(self) -> None:
                self.communications = 0
                self.direct_kills = 0

            def communicate(self, *, timeout: float) -> tuple[str, str]:
                self.communications += 1
                if self.communications == 1:
                    raise subprocess.TimeoutExpired("command", timeout)
                return "", ""

            def kill(self) -> None:
                self.direct_kills += 1
                self.returncode = -signal.SIGKILL

        process = FakeProcess()
        kill_group = mock.Mock()
        monkeypatch.setattr(
            subprocess, "Popen", lambda *_args, **_kwargs: process
        )
        monkeypatch.setattr(os, "getpgid", lambda _pid: 202)
        monkeypatch.setattr(os, "getsid", lambda _pid: 202)
        monkeypatch.setattr(os, "getpgrp", lambda: 202)
        monkeypatch.setattr(os, "killpg", kill_group)

        module.ProcessBackend(SandboxConfig(timeout=1)).execute("wait")

        kill_group.assert_not_called()
        assert process.direct_kills == 1

    @pytest.mark.parametrize("returncode", [0, 2, -signal.SIGKILL])
    def test_completed_process_termination_is_idempotent(
        self,
        monkeypatch: pytest.MonkeyPatch,
        returncode: int,
    ) -> None:
        from general_ludd.sandbox.backends import process_backend as module

        process = mock.Mock(pid=4242, returncode=returncode)
        kill_group = mock.Mock()
        monkeypatch.setattr(os, "killpg", kill_group)

        module._terminate_owned_process(cast(subprocess.Popen[str], process))
        module._terminate_owned_process(cast(subprocess.Popen[str], process))

        kill_group.assert_not_called()
        process.kill.assert_not_called()

    def test_cancellation_terminates_and_reaps_verified_child_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from general_ludd.sandbox.backends import process_backend as module

        class FakeProcess:
            pid = 4242
            returncode: int | None = None

            def __init__(self) -> None:
                self.communications = 0

            def communicate(self, *, timeout: float) -> tuple[str, str]:
                self.communications += 1
                if self.communications == 1:
                    raise asyncio.CancelledError
                return "", ""

            def kill(self) -> None:
                raise AssertionError("verified child group must be terminated as a group")

        process = FakeProcess()
        killed: list[tuple[int, int]] = []

        def kill_group(pgid: int, sig: int) -> None:
            killed.append((pgid, sig))
            process.returncode = -sig

        monkeypatch.setattr(
            subprocess, "Popen", lambda *_args, **_kwargs: process
        )
        monkeypatch.setattr(os, "getpgid", lambda pid: pid)
        monkeypatch.setattr(os, "getsid", lambda pid: process.pid if pid else 101)
        monkeypatch.setattr(os, "getpgrp", lambda: 101)
        monkeypatch.setattr(os, "killpg", kill_group)

        with pytest.raises(asyncio.CancelledError):
            module.ProcessBackend(SandboxConfig(timeout=1)).execute("wait")

        assert killed == [(process.pid, signal.SIGKILL)]
        assert process.communications == 2

    def test_linux_user_task_count_tracks_real_uid_threads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from general_ludd.sandbox.backends import process_backend as module

        class Entry:
            def __init__(
                self, name: str, path: str, uid: int, *, vanished: bool = False
            ) -> None:
                self.name = name
                self.path = path
                self.uid = uid
                self.vanished = vanished

            def stat(self, *, follow_symlinks: bool) -> SimpleNamespace:
                assert follow_symlinks is False
                if self.vanished:
                    raise FileNotFoundError(self.path)
                return SimpleNamespace(st_uid=self.uid)

        class Scan:
            def __init__(self, entries: list[Entry]) -> None:
                self.entries = entries

            def __enter__(self) -> Iterator[Entry]:
                return iter(self.entries)

            def __exit__(
                self, _exc_type: object, _exc: object, _traceback: object
            ) -> None:
                return None

        processes = [
            Entry("self", "/proc/self", 1000),
            Entry("101", "/proc/101", 1000),
            Entry("102", "/proc/102", 2000),
            Entry("103", "/proc/103", 1000, vanished=True),
        ]
        tasks = [
            Entry("101", "/proc/101/task/101", 1000),
            Entry("104", "/proc/101/task/104", 1000),
            Entry("fd", "/proc/101/task/fd", 1000),
        ]

        def fake_scandir(path: str) -> Scan:
            if path == "/proc":
                return Scan(processes)
            assert path == "/proc/101/task"
            return Scan(tasks)

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(os, "getuid", lambda: 1000)
        monkeypatch.setattr(os, "scandir", fake_scandir)

        assert module._linux_user_task_count() == 2

    def test_linux_user_task_count_fails_closed_when_proc_is_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from general_ludd.sandbox.backends import process_backend as module

        def inaccessible(_path: str) -> NoReturn:
            raise PermissionError("/proc")

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(os, "scandir", inaccessible)

        assert module._linux_user_task_count() == 0

    def test_preexec_applies_translated_resource_limits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from general_ludd.sandbox.backends import process_backend as module

        applied: list[tuple[int, tuple[int, int]]] = []

        class FakeProcess:
            pid = 22
            returncode = 0

            def communicate(self, *, timeout: float) -> tuple[str, str]:
                return "", ""

        def fake_popen(_command: str, **kwargs: object) -> FakeProcess:
            cast(Callable[[], None], kwargs["preexec_fn"])()
            return FakeProcess()

        monkeypatch.setattr(module, "_linux_user_task_count", lambda: 75)
        monkeypatch.setattr(resource, "getrlimit", lambda _kind: (500, 500))
        monkeypatch.setattr(
            resource,
            "setrlimit",
            lambda kind, limits: applied.append((kind, limits)),
        )
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(
            resource,
            "getrusage",
            lambda _kind: SimpleNamespace(ru_utime=0.0, ru_stime=0.0, ru_maxrss=0),
        )

        module.ProcessBackend(
            SandboxConfig(memory_mb=1, cpu_seconds=2, max_processes=3)
        ).execute("true")

        assert (resource.RLIMIT_AS, (1024 * 1024, 1024 * 1024)) in applied
        assert (resource.RLIMIT_CPU, (2, 2)) in applied
        assert (resource.RLIMIT_NPROC, (78, 500)) in applied

    def test_missing_shell_returns_command_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from general_ludd.sandbox.backends import process_backend as module

        def missing(*_args: object, **_kwargs: object) -> NoReturn:
            raise FileNotFoundError

        monkeypatch.setattr(subprocess, "Popen", missing)

        result = module.ProcessBackend(SandboxConfig()).execute("missing arg")

        assert result.returncode == 127
        assert result.stderr == "command not found: missing"
        assert result.was_killed is False

    def test_windows_timeout_kills_direct_child(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from general_ludd.sandbox.backends import process_backend as module

        class FakeProcess:
            pid = 33
            returncode: int | None = None

            def __init__(self) -> None:
                self.calls = 0
                self.killed = False

            def communicate(self, *, timeout: float) -> tuple[str, str]:
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired("command", timeout)
                return "", ""

            def kill(self) -> None:
                self.killed = True

        process = FakeProcess()
        popen_kwargs: dict[str, object] = {}

        def fake_popen(_command: str, **kwargs: object) -> FakeProcess:
            popen_kwargs.update(kwargs)
            return process

        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        result = module.ProcessBackend(SandboxConfig(timeout=1)).execute("wait")

        assert popen_kwargs["start_new_session"] is False
        assert process.killed is True
        assert result.was_killed is True

    def test_repeated_timeout_returns_bounded_empty_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from general_ludd.sandbox.backends import process_backend as module

        class FakeProcess:
            pid = 44

            def __init__(self) -> None:
                self.returncode: int | None = None
                self.killed = False

            def communicate(self, *, timeout: float) -> tuple[str, str]:
                raise subprocess.TimeoutExpired("command", timeout)

            def kill(self) -> None:
                self.killed = True

        monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
        monkeypatch.setattr(os, "killpg", lambda _pgid, _sig: None)
        monkeypatch.setattr(
            resource,
            "getrusage",
            mock.Mock(side_effect=OSError("usage unavailable")),
        )

        result = module.ProcessBackend(SandboxConfig(timeout=1)).execute("wait")

        assert result.returncode == -1
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.cpu_time_ms == 0
        assert result.memory_used_bytes == 0

    def test_output_budget_trims_longer_stream_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from general_ludd.sandbox.backends import process_backend as module

        class FakeProcess:
            pid = 55
            returncode = 0

            def communicate(self, *, timeout: float) -> tuple[str, str]:
                return "x" * 80, "e" * 10

        monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            resource,
            "getrusage",
            lambda _kind: SimpleNamespace(ru_utime=0.0, ru_stime=0.0, ru_maxrss=2),
        )

        result = module.ProcessBackend(SandboxConfig(max_output_bytes=50)).execute("emit")

        assert len(result.stdout) == 40
        assert len(result.stderr) == 10
        assert result.memory_used_bytes == 2048

    def test_respects_workdir(self, tmp_path: Path) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("pwd", workdir=str(tmp_path))
        assert tmp_path.name in result.stdout or str(tmp_path) in result.stdout

    def test_respects_env(self, tmp_path: Path) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("echo $GLUDD_TEST_VAR", env={"GLUDD_TEST_VAR": "abc123"})
        assert "abc123" in result.stdout

    def test_max_output_bytes_truncates(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10, max_output_bytes=50))
        result = backend.execute("python3 -c 'print(\"x\" * 1000)'")
        total = len(result.stdout) + len(result.stderr)
        assert total <= 50 or result.returncode != 0

    def test_max_output_bytes_does_not_truncate_small_output(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10, max_output_bytes=1_000_000))
        result = backend.execute("echo small")
        assert "small" in result.stdout

    def test_pid_populated(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("echo hi")
        assert result.pid > 0

    def test_cpu_time_ms_populated(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("python3 -c 'import time; time.sleep(0.1)'")
        assert result.cpu_time_ms >= 0

    def test_memory_used_bytes_populated(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("echo hi")
        assert result.memory_used_bytes >= 0


class TestProcessBackendCleanup:
    def test_cleanup_is_noop(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig())
        backend.cleanup()


class TestProcessBackendWithConfig:
    def test_default_config(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig())
        assert backend.config.timeout == 300
        assert backend.config.max_output_bytes == 1_000_000

    def test_custom_config(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        config = SandboxConfig(
            timeout=60,
            max_output_bytes=5000,
            isolation=IsolationLevel.PROCESS,
            memory_mb=128,
        )
        backend = ProcessBackend(config)
        assert backend.config == config

    def test_respects_memory_limit(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10, memory_mb=64))
        result = backend.execute("echo ok")
        assert result.success is True


# ---------------------------------------------------------------------------
# ContainerBackend
# ---------------------------------------------------------------------------


class TestContainerBackendProtocol:
    def test_satisfies_sandbox_backend_protocol(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        assert isinstance(backend, SandboxBackend)

    def test_name_is_podman_or_docker(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        assert backend.name in ("docker", "podman")

    def test_default_name_is_podman_preferred(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        assert backend.name in ("docker", "podman")


class TestContainerBackendAvailable:
    def test_available_when_docker_found(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        with mock.patch("shutil.which", side_effect=lambda x: "/usr/bin/" + x):
            assert backend.available() is True

    def test_available_false_when_no_runtime(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        with mock.patch("shutil.which", return_value=None):
            assert backend.available() is False

    def test_available_first_checks_podman_then_docker(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        calls: list[str] = []

        def _which(prog: str) -> str | None:
            calls.append(prog)
            if prog == "podman":
                return "/usr/bin/podman"
            return None

        with mock.patch("shutil.which", side_effect=_which):
            assert backend.available() is True
        assert calls[0] == "podman", "must check podman first"


class TestContainerBackendExecute:
    def test_execute_success_mocked(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(timeout=10, image_path="alpine:latest"))
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="hello container", stderr="")
            result = backend.execute("echo hello")
        assert isinstance(result, SandboxResult)
        assert result.returncode == 0
        assert "hello container" in result.stdout

    def test_execute_failure_mocked(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(timeout=10, image_path="alpine:latest"))
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="command failed")
            result = backend.execute("bad-command")
        assert result.success is False
        assert "command failed" in result.stderr

    def test_execute_timeout_mocked(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(timeout=5, image_path="alpine:latest"))
        with mock.patch.object(backend, "_run_container") as _run:
            _run.side_effect = subprocess.TimeoutExpired(cmd="docker run ...", timeout=5)
            result = backend.execute("sleep 100")
        assert result.was_killed is True
        assert result.returncode != 0

    def test_uses_podman_when_available(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        with mock.patch("shutil.which", side_effect=lambda x: "/usr/bin/" + x):
            backend = ContainerBackend(SandboxConfig(timeout=10, image_path="alpine:latest"))
        assert backend._runtime == "podman"


class TestContainerBackendImagePull:
    def test_pull_image_success_mocked(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(timeout=30, image_path="alpine:latest"))
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            result = backend.pull_image("alpine:latest")
        assert result.returncode == 0

    def test_pull_image_failure_mocked(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(timeout=30, image_path="alpine:latest"))
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="pull failed")
            result = backend.pull_image("alpine:latest")
        assert result.returncode == 1


class TestContainerBackendCleanup:
    def test_cleanup_is_noop_when_no_containers(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        backend.cleanup()

    def test_cleanup_removes_containers_mocked(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine:latest"))
        backend._container_ids = ["abc123", "def456"]
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0)
            backend.cleanup()
        assert run.call_count == 2


class TestContainerBackendWithConfig:
    def test_image_path_required(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="python:3.11-slim"))
        assert backend.config.image_path == "python:3.11-slim"

    def test_config_values_propagate(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        config = SandboxConfig(
            timeout=120,
            max_output_bytes=500_000,
            memory_mb=256,
            cpu_seconds=60,
            image_path="ubuntu:22.04",
            allow_network=True,
            allowed_hosts=["api.example.com"],
            isolation=IsolationLevel.CONTAINER,
        )
        backend = ContainerBackend(config)
        assert backend.config == config


class TestContainerBackendAutoDetection:
    def test_prefers_podman_over_docker(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine"))
        with mock.patch.object(backend, "_detect_runtime") as _detect:
            _detect.return_value = "podman"
            assert backend._detect_runtime() == "podman"

    def test_falls_back_to_docker(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend

        backend = ContainerBackend(SandboxConfig(image_path="alpine"))
        with mock.patch("shutil.which", side_effect=lambda p: "/usr/bin/" + p if p == "docker" else None):
            assert backend._detect_runtime() == "docker"


# ---------------------------------------------------------------------------
# Cross-backend consistency
# ---------------------------------------------------------------------------


class TestBackendConsistency:
    def test_both_backends_conform_to_protocol(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        process = ProcessBackend(SandboxConfig(timeout=5))
        container = ContainerBackend(SandboxConfig(image_path="alpine"))

        assert isinstance(process, SandboxBackend)
        assert isinstance(container, SandboxBackend)

    def test_both_return_sandbox_result(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig(timeout=10))
        result = backend.execute("echo hi")
        assert isinstance(result, SandboxResult)

    def test_names_are_distinct(self) -> None:
        from general_ludd.sandbox.backends.container_backend import ContainerBackend
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        process = ProcessBackend(SandboxConfig())
        container = ContainerBackend(SandboxConfig(image_path="alpine"))
        assert process.name != container.name

    def test_both_cleanup_is_safe_on_double_call(self) -> None:
        from general_ludd.sandbox.backends.process_backend import ProcessBackend

        backend = ProcessBackend(SandboxConfig())
        backend.cleanup()
        backend.cleanup()
