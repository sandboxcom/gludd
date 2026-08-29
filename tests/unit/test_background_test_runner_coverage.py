"""Branch coverage for background-runner failures and CLI dispatch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

import general_ludd.runner.background_test_runner as background


class _CliRunner:
    """Deterministic runner double used to exercise the module CLI."""

    launch_result: ClassVar[dict[str, object]] = {"phase": "running"}

    def launch(self, testfile: str, wait: bool = False) -> dict[str, object]:
        del testfile, wait
        return self.launch_result

    def status(self, testfile: str) -> dict[str, object]:
        return {"testfile": testfile, "phase": "running"}

    def poll_all(self) -> list[dict[str, object]]:
        return [{"phase": "running"}]

    def kill(self, testfile: str, force: bool = False) -> dict[str, object]:
        del testfile, force
        return {"status": "terminated"}

    def results(self, testfile: str) -> dict[str, object]:
        del testfile
        return {"complete": True}


def test_sanitizer_rejects_non_allowlisted_unicode(tmp_path: Path) -> None:
    """Characters outside the explicit allowlist fail before process acquisition."""
    runner = background.BackgroundTestRunner(tmp_path)
    with pytest.raises(ValueError, match="disallowed characters"):
        runner._sanitize_testfile("tests/unit/test_ü.py")


def test_launch_replaces_stale_pid_and_delegates_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale PID never blocks a fresh launch and wait mode uses the owner path."""
    runner = background.BackgroundTestRunner(tmp_path)
    testfile = "tests/unit/test_example.py"
    runner._pid_path(testfile).write_text("123")
    monkeypatch.setattr(runner, "_pid_alive", lambda pid: False)

    process = type("Process", (), {"pid": 456})()
    monkeypatch.setattr(
        "general_ludd.runner.background_test_runner.MakeRunner.spawn",
        lambda self, target, extra_args, log_file: (process, log_file),
    )
    monkeypatch.setattr(
        runner,
        "_wait",
        lambda name, timeout: {"testfile": name, "phase": "completed"},
    )

    result = runner.launch(testfile, timeout_min=1, wait=True)
    assert result["phase"] == "completed"


def test_status_and_log_read_failures_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Corrupt status and unreadable logs return a stable partial status."""
    runner = background.BackgroundTestRunner(tmp_path)
    testfile = "tests/unit/test_example.py"
    runner._status_path(testfile).write_text("{invalid")
    log = tmp_path / f"test-{runner._sanitize(testfile)}-latest.log"
    log.write_text("1 passed")

    original_read_text = Path.read_text

    def fail_log_read(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == log:
            raise OSError("unreadable")
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", fail_log_read)
    status = runner.status(testfile)
    assert status["phase"] == "unknown"
    assert status["last_lines"] == []


def test_poll_all_skips_unresolvable_pid_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PID entry without a recoverable test identity is ignored."""
    runner = background.BackgroundTestRunner(tmp_path)
    (tmp_path / ".test-unknown.pid").write_text("123")
    monkeypatch.setattr(runner, "_resolve_testfile", lambda path: None)
    assert runner.poll_all() == []


def test_non_force_timeout_cleans_pid_without_unowned_sigkill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-force termination preserves the bounded TERM-only contract."""
    runner = background.BackgroundTestRunner(tmp_path)
    testfile = "tests/unit/test_example.py"
    runner._pid_path(testfile).write_text("321")
    monkeypatch.setattr(runner, "_pid_alive", lambda pid: True)
    monkeypatch.setattr("general_ludd.runner.background_test_runner.os.kill", lambda pid, sig: None)
    monkeypatch.setattr("general_ludd.runner.background_test_runner.time.sleep", lambda seconds: None)

    result = runner.kill(testfile, force=False)
    assert result["status"] == "killed_after_timeout"
    assert not runner._pid_path(testfile).exists()


def test_pid_and_log_helpers_cover_corruption_and_os_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed PID files and inaccessible log artifacts fail closed."""
    runner = background.BackgroundTestRunner(tmp_path)
    testfile = "tests/unit/test_example.py"
    runner._pid_path(testfile).write_text("not-a-pid")
    assert runner._read_pid(testfile) is None

    def missing_process(pid: int, flags: int) -> tuple[int, int]:
        del pid, flags
        raise ProcessLookupError

    monkeypatch.setattr("general_ludd.runner.background_test_runner.os.waitpid", missing_process)
    assert runner._pid_alive(999999) is False

    missing = tmp_path / "missing.log"
    assert runner._detect_terminal_marker(missing) is None
    assert runner._parse_exit_code(missing) is None
    assert runner._detect_terminal_marker("=== FAILED ===\n1 failed") == "FAIL"
    assert runner._detect_terminal_marker("Error") == "FAIL"
    assert runner._parse_exit_code("PYTEST_EXIT=7") == 7


def test_resolve_testfile_falls_back_after_corrupt_status(tmp_path: Path) -> None:
    """Corrupt status metadata falls back to the conservative filename mapping."""
    runner = background.BackgroundTestRunner(tmp_path)
    pid_file = tmp_path / ".test-tests_unit_example_py.pid"
    pid_file.write_text("123")
    status_file = tmp_path / ".test-tests_unit_example_py.status.json"
    status_file.write_text("{invalid")
    assert runner._resolve_testfile(pid_file) == "tests/unit/example/py"


@pytest.mark.parametrize(
    "argv",
    [
        ["background_test_runner"],
        ["background_test_runner", "launch"],
        ["background_test_runner", "status"],
        ["background_test_runner", "kill"],
        ["background_test_runner", "results"],
        ["background_test_runner", "unknown"],
    ],
)
def test_cli_missing_arguments_and_unknown_commands_fail_closed(
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every incomplete or unknown command returns an explicit failure."""
    monkeypatch.setattr("general_ludd.runner.background_test_runner.sys.argv", argv)
    monkeypatch.setattr(background, "BackgroundTestRunner", _CliRunner)
    with pytest.raises(SystemExit, match="1"):
        background.cli()


@pytest.mark.parametrize(
    "argv",
    [
        ["background_test_runner", "launch", "tests/unit/test_x.py"],
        ["background_test_runner", "launch", "tests/unit/test_x.py", "--wait"],
        ["background_test_runner", "status", "tests/unit/test_x.py"],
        ["background_test_runner", "poll-all"],
        ["background_test_runner", "kill", "tests/unit/test_x.py", "--force"],
        ["background_test_runner", "results", "tests/unit/test_x.py"],
    ],
)
def test_cli_dispatches_each_supported_command(
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every public CLI command emits machine-readable JSON."""
    monkeypatch.setattr("general_ludd.runner.background_test_runner.sys.argv", argv)
    monkeypatch.setattr(background, "BackgroundTestRunner", _CliRunner)
    background.cli()
    assert json.loads(capsys.readouterr().out)


def test_cli_launch_timeout_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out wait is never reported as a successful CLI launch."""
    class TimeoutRunner(_CliRunner):
        launch_result: ClassVar[dict[str, object]] = {"phase": "timeout"}

    monkeypatch.setattr(
        "general_ludd.runner.background_test_runner.sys.argv",
        ["background_test_runner", "launch", "tests/unit/test_x.py", "--wait"],
    )
    monkeypatch.setattr(background, "BackgroundTestRunner", TimeoutRunner)
    with pytest.raises(SystemExit, match="1"):
        background.cli()
