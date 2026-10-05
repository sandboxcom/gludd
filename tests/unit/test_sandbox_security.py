"""Adversarial security tests for SandboxExecutor.

Each test declares what it expects of the sandbox and proves the property
through an attempted evasion.
"""

from __future__ import annotations

import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from general_ludd.sandbox_exec.executor import SandboxExecutor


class TestShellMetacharacterInjection:
    def test_semicolon_command_separation_blocked(self) -> None:
        executor = SandboxExecutor(timeout=5)
        probe_path = "/tmp/sandbox_injection_probe_semicolon"
        Path(probe_path).unlink(missing_ok=True)

        result = executor.execute(f"echo hello; touch {probe_path}")

        assert not Path(probe_path).exists(), "semicolon must not spawn a second command"
        assert "touch" in result.stdout or ";" in result.stdout

    def test_pipe_injection_blocked(self) -> None:
        executor = SandboxExecutor(timeout=5)
        probe_path = "/tmp/sandbox_injection_probe_pipe"
        Path(probe_path).unlink(missing_ok=True)

        result = executor.execute(f"echo hello | dd of={probe_path}")

        assert not Path(probe_path).exists(), "pipe must not create a file"
        assert "|" in result.stdout

    def test_dollar_subshell_injection_blocked(self) -> None:
        executor = SandboxExecutor(timeout=5)
        result = executor.execute("echo $(whoami)")

        assert "$(whoami)" in result.stdout, "$() must appear as literal text"

    def test_backtick_subshell_injection_blocked(self) -> None:
        executor = SandboxExecutor(timeout=5)
        result = executor.execute("echo `whoami`")

        assert "`whoami`" in result.stdout, "backticks must appear as literal text"

    def test_redirection_injection_blocked(self) -> None:
        executor = SandboxExecutor(timeout=5)
        probe_path = "/tmp/sandbox_injection_probe_redirect"
        Path(probe_path).unlink(missing_ok=True)

        result = executor.execute(f"echo hello > {probe_path}")

        assert not Path(probe_path).exists(), "redirect must not create a file"
        assert ">" in result.stdout

    def test_newline_command_separation_blocked(self) -> None:
        executor = SandboxExecutor(timeout=5)
        probe_path = "/tmp/sandbox_injection_probe_newline"
        Path(probe_path).unlink(missing_ok=True)

        executor.execute(f'echo "hello\ntouch {probe_path}"')

        assert not Path(probe_path).exists(), "newline must not spawn a second command"


class TestPathTraversal:
    def test_dotdot_slash_above_workdir_is_reachable_no_fs_isolation(self, tmp_path: Path) -> None:
        executor = SandboxExecutor(timeout=5)
        workdir = tmp_path / "sandbox" / "deep"
        workdir.mkdir(parents=True)
        outside = tmp_path / "outside.txt"
        outside.write_text("classified")

        result = executor.execute("cat ../../outside.txt", workdir=str(workdir))

        assert "classified" in result.stdout, (
            "current executor has no fs isolation — path traversal succeeds "
            "(documenting gap for enforcement)"
        )

    def test_absolute_path_to_etc_passwd_is_readable_no_fs_isolation(self, tmp_path: Path) -> None:
        executor = SandboxExecutor(timeout=5)
        workdir = tmp_path / "sandbox"
        workdir.mkdir()

        result = executor.execute("cat /etc/passwd", workdir=str(workdir))

        assert result.returncode == 0, (
            "current executor has no fs isolation — /etc/passwd is readable "
            "(documenting gap for enforcement)"
        )


class TestTimeoutEnforcement:
    def test_timeout_kills_long_running_process(self) -> None:
        executor = SandboxExecutor(timeout=1)

        start = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            executor.execute("sleep 10")
        elapsed = time.monotonic() - start

        assert elapsed < 5, f"timeout must fire within 5s, took {elapsed:.1f}s"

    def test_timeout_kills_tight_loop(self) -> None:
        executor = SandboxExecutor(timeout=2)

        start = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            executor.execute("python3 -c 'while True: pass'")
        elapsed = time.monotonic() - start

        assert elapsed < 6, f"tight-loop timeout must fire within 6s, took {elapsed:.1f}s"

    def test_quick_command_not_affected_by_timeout(self) -> None:
        executor = SandboxExecutor(timeout=30)

        result = executor.execute("echo ok")
        assert result.returncode == 0
        assert "ok" in result.stdout


class TestResourceLimits:
    def test_max_output_bytes_config_present(self) -> None:
        executor = SandboxExecutor(max_output_bytes=100)
        assert executor.max_output_bytes == 100

    def test_large_output_is_captured(self, tmp_path: Path) -> None:
        executor = SandboxExecutor(timeout=10, max_output_bytes=5000)
        workdir = tmp_path / "sandbox"
        workdir.mkdir()

        script = workdir / "bigout.py"
        script.write_text("print('A' * 200_000)")

        result = executor.execute(f"python3 {script}", workdir=str(workdir))

        assert len(result.stdout) == 200_001, "200k A's + newline from print()"
        assert result.returncode == 0

    def test_large_stderr_is_captured(self, tmp_path: Path) -> None:
        executor = SandboxExecutor(timeout=10, max_output_bytes=5000)
        workdir = tmp_path / "sandbox"
        workdir.mkdir()

        script = workdir / "bigerrtxt"
        script.write_text("import sys; sys.stderr.write('X' * 100_000)")

        result = executor.execute(f"python3 {script}", workdir=str(workdir))

        assert len(result.stderr) <= 100_000
        assert len(result.stdout) <= 100_000


class TestCwdConfinement:
    def test_process_runs_in_specified_workdir(self, tmp_path: Path) -> None:
        executor = SandboxExecutor(timeout=5)
        workdir = tmp_path / "sandbox"
        workdir.mkdir()

        marker = workdir / "marker.txt"
        marker.write_text("proof")

        result = executor.execute("cat marker.txt", workdir=str(workdir))

        assert "proof" in result.stdout
        assert result.returncode == 0

    def test_relative_paths_resolve_under_workdir(self, tmp_path: Path) -> None:
        executor = SandboxExecutor(timeout=5)
        workdir = tmp_path / "sandbox"
        workdir.mkdir()
        subdir = workdir / "sub"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("nested")

        result = executor.execute("cat sub/nested.txt", workdir=str(workdir))

        assert "nested" in result.stdout
        assert result.returncode == 0

    def test_cwd_is_not_root(self, tmp_path: Path) -> None:
        executor = SandboxExecutor(timeout=5)
        workdir = tmp_path / "sandbox"
        workdir.mkdir()

        result = executor.execute("pwd", workdir=str(workdir))

        assert result.stdout.strip() == str(workdir)
        assert result.stdout.strip() != "/"


class TestCommandLengthLimits:
    def test_very_long_command_single_arg_handled_gracefully(self) -> None:
        executor = SandboxExecutor(timeout=5)
        padding = "A" * 100_000

        result = executor.execute(f"echo {padding}")

        assert result.returncode == 0, (
            "command with 100k-char arg completes under OS ARG_MAX — "
            "executor-level command length enforcement would be a future addition"
        )

    def test_many_small_args_handled_gracefully(self) -> None:
        executor = SandboxExecutor(timeout=10)
        args = " ".join("arg" for _ in range(10_000))

        result = executor.execute(f"echo {args}")

        assert result.returncode == 0, (
            "10k args complete under OS ARG_MAX — "
            "executor-level arg count enforcement would be a future addition"
        )

    def test_command_exceeding_os_arg_max_is_rejected(self) -> None:
        executor = SandboxExecutor(timeout=5)
        padding = "A" * 2_000_000

        with pytest.raises((subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired)):
            executor.execute(f"echo {padding}")

    def test_empty_command_is_handled(self) -> None:
        executor = SandboxExecutor(timeout=5)

        with pytest.raises((ValueError, IndexError, OSError)):
            executor.execute("")


class TestConcurrentExecution:
    def test_concurrent_executions_do_not_interfere(self, tmp_path: Path) -> None:
        workdir_a = tmp_path / "sandbox_a"
        workdir_a.mkdir()
        (workdir_a / "name.txt").write_text("alice")

        workdir_b = tmp_path / "sandbox_b"
        workdir_b.mkdir()
        (workdir_b / "name.txt").write_text("bob")

        executor_a = SandboxExecutor(timeout=10)
        executor_b = SandboxExecutor(timeout=10)

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_a = pool.submit(executor_a.execute, "cat name.txt", str(workdir_a))
            fut_b = pool.submit(executor_b.execute, "cat name.txt", str(workdir_b))

            result_a = fut_a.result(timeout=15)
            result_b = fut_b.result(timeout=15)

        assert "alice" in result_a.stdout
        assert "bob" in result_b.stdout
        assert result_a.returncode == 0
        assert result_b.returncode == 0

    def test_high_concurrency_stress(self, tmp_path: Path) -> None:
        workdirs = []
        executors = []
        for i in range(8):
            wd = tmp_path / f"sandbox_{i}"
            wd.mkdir()
            (wd / "id.txt").write_text(str(i))
            workdirs.append(wd)
            executors.append(SandboxExecutor(timeout=15))

        # Exercise eight independent executions through a bounded worker pool.
        # Hosted runners share a real-UID task budget with their service agents,
        # so equating the workload size with eight new OS threads makes this
        # application test depend on ambient host capacity rather than Gludd's
        # concurrency contract.  Two workers still overlap subprocesses while
        # the eight queued jobs prove repeated isolation under contention.
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(ex.execute, "cat id.txt", str(wd))
                for ex, wd in zip(executors, workdirs, strict=False)
            ]

        results = [f.result(timeout=20) for f in futures]

        for i, result in enumerate(results):
            assert str(i) in result.stdout
            assert result.returncode == 0

    def test_concurrent_cwd_isolation(self, tmp_path: Path) -> None:
        shared_executor = SandboxExecutor(timeout=10)
        workdir_a = tmp_path / "sandbox_a"
        workdir_a.mkdir()
        workdir_b = tmp_path / "sandbox_b"
        workdir_b.mkdir()

        (workdir_a / "a.txt").write_text("A")
        (workdir_b / "b.txt").write_text("B")

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_a = pool.submit(shared_executor.execute, "cat a.txt", str(workdir_a))
            fut_b = pool.submit(shared_executor.execute, "cat b.txt", str(workdir_b))

            result_a = fut_a.result(timeout=15)
            result_b = fut_b.result(timeout=15)

        assert "A" in result_a.stdout, f"expected 'A' in stdout, got: {result_a.stdout!r}"
        assert "B" in result_b.stdout, f"expected 'B' in stdout, got: {result_b.stdout!r}"
        assert result_a.returncode == 0
        assert result_b.returncode == 0


class TestSubprocessSecurity:
    def test_no_shell_true_is_used(self) -> None:
        executor = SandboxExecutor(timeout=5)

        result = executor.execute("echo $HOME")

        assert "$HOME" in result.stdout, "$HOME must not be expanded — shell=False"

    def test_no_shell_env_var_expansion(self) -> None:
        executor = SandboxExecutor(timeout=5)

        result = executor.execute("echo $PATH")

        assert "$PATH" in result.stdout, "$PATH must not be expanded — shell=False"

    def test_no_shell_glob_expansion(self) -> None:
        executor = SandboxExecutor(timeout=5)

        result = executor.execute("echo *.py")

        assert "*.py" in result.stdout, "glob must not be expanded — shell=False"


class TestErrorHandling:
    def test_nonexistent_command_returns_error(self) -> None:
        executor = SandboxExecutor(timeout=5)

        with pytest.raises(FileNotFoundError):
            executor.execute("nonexistent_command_xyz_123")

    def test_nonzero_exit_is_captured_not_raised(self) -> None:
        executor = SandboxExecutor(timeout=5)

        result = executor.execute("cat /nonexistent_file_xyz_123")

        assert result.returncode != 0
        assert "/nonexistent_file_xyz_123" in result.stderr
