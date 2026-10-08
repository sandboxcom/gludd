from __future__ import annotations

import importlib.util
import io
import json
import signal
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "check_integration_health.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_integration_health_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    original_sigterm = signal.getsignal(signal.SIGTERM)
    original_sigint = signal.getsignal(signal.SIGINT)
    try:
        spec.loader.exec_module(module)
    finally:
        signal.signal(signal.SIGTERM, original_sigterm)
        signal.signal(signal.SIGINT, original_sigint)
    return module


health = _load_module()

XDIST_OUTPUT = """bringing up nodes...
......                                                                   [100%]
=========================== short test summary info ============================
FAILED tests/integration/test_alpha.py::test_first - AssertionError: first
FAILED tests/integration/test_alpha.py::TestGroup::test_second - RuntimeError: second
FAILED tests/integration/test_beta.py::test_third[param-a] - ValueError: third
FAILED tests/integration/test_beta.py::test_third[param-b] - ValueError: fourth
FAILED tests/integration/test_gamma.py::test_fifth - assert False
FAILED tests/integration/test_delta.py::test_sixth - TimeoutError: sixth
========================= 6 failed, 6 passed in 1.25s ==========================
"""


def test_gate_owned_integration_child_carries_legacy_watchdog_marker(
    tmp_path: Path,
) -> None:
    """The wrapper's pytest child must be excluded by older task watchdogs."""
    command = health._build_pytest_command(
        [tmp_path / "test_example.py"],
        workers="1",
        watchdog_owned_gate=True,
        temp_root=tmp_path,
    )

    assert "watchdog-owned-gate" in " ".join(command)


def test_parse_failures_reports_every_xdist_short_summary_nodeid() -> None:
    failures = health._parse_failures(XDIST_OUTPUT)

    assert [failure["test"] for failure in failures] == [
        "tests/integration/test_alpha.py::test_first",
        "tests/integration/test_alpha.py::TestGroup::test_second",
        "tests/integration/test_beta.py::test_third[param-a]",
        "tests/integration/test_beta.py::test_third[param-b]",
        "tests/integration/test_gamma.py::test_fifth",
        "tests/integration/test_delta.py::test_sixth",
    ]
    assert {failure["file"] for failure in failures} == {
        "tests/integration/test_alpha.py",
        "tests/integration/test_beta.py",
        "tests/integration/test_gamma.py",
        "tests/integration/test_delta.py",
    }
    assert failures[0]["reason"] == "AssertionError: first"


def test_parse_failures_deduplicates_repeated_xdist_nodeids() -> None:
    output = """[gw0] [ 50%] FAILED tests/integration/test_alpha.py::test_first
=========================== short test summary info ============================
FAILED tests/integration/test_alpha.py::test_first - AssertionError: first
"""

    assert health._parse_failures(output) == [
        {
            "raw": (
                "FAILED tests/integration/test_alpha.py::test_first "
                "- AssertionError: first"
            ),
            "test": "tests/integration/test_alpha.py::test_first",
            "file": "tests/integration/test_alpha.py",
            "line": "",
            "reason": "AssertionError: first",
        }
    ]


def test_find_integration_test_files_is_sorted_and_skips_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tests_dir = tmp_path / "integration"
    nested = tests_dir / "nested"
    cached = tests_dir / "__pycache__"
    nested.mkdir(parents=True)
    cached.mkdir()
    second = nested / "test_second.py"
    first = tests_dir / "test_first.py"
    second.write_text("", encoding="utf-8")
    first.write_text("", encoding="utf-8")
    (cached / "test_ignored.py").write_text("", encoding="utf-8")
    (tests_dir / "helper.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(health, "TESTS_DIR", tests_dir)

    assert health._find_integration_test_files() == sorted([first, second])


def test_parse_failures_supports_collection_and_file_error_fallbacks() -> None:
    collection = health._parse_failures(
        "ERROR collecting tests/integration/test_collect.py\n"
    )
    file_error = health._parse_failures("ERROR tests/integration/test_import.py\n")

    assert collection[0]["test"] == "tests/integration/test_collect.py"
    assert collection[0]["file"] == "tests/integration/test_collect.py"
    assert file_error[0]["test"] == "tests/integration/test_import.py"
    assert file_error[0]["reason"] == ""


def test_signal_handler_persists_interrupted_failure_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_file = tmp_path / "interrupted.json"
    monkeypatch.setattr(health, "OUTPUT_FILE", output_file)
    health._accumulated_lines[:] = [XDIST_OUTPUT]
    monkeypatch.setattr(health, "_test_file_count", 4)
    monkeypatch.setattr(health, "_start_time", health.time.time())

    with pytest.raises(SystemExit, match="1"):
        health._signal_handler(signal.SIGTERM, None)

    report = json.loads(output_file.read_text(encoding="utf-8"))
    assert report["status"] == "interrupted"
    assert report["total_files"] == 4
    assert report["total_failures"] == 6


def test_main_handles_empty_plan_and_spawn_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(health, "_find_integration_test_files", lambda: [])
    assert health.main() == 0
    assert "No integration test files found" in capsys.readouterr().out

    monkeypatch.setattr(
        health,
        "_find_integration_test_files",
        lambda: [tmp_path / "test_example.py"],
    )

    def fail_spawn(*_args: object, **_kwargs: object) -> None:
        raise OSError("spawn denied")

    monkeypatch.setattr(health.subprocess, "Popen", fail_spawn)
    assert health.main() == 2
    assert "failed to start pytest: spawn denied" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("returncode", "expected_main_rc", "expected_failures"),
    [(0, 0, 0), (5, 1, 1)],
)
def test_main_classifies_success_and_unparseable_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    returncode: int,
    expected_main_rc: int,
    expected_failures: int,
) -> None:
    class FakeProcess:
        stdout = io.StringIO("unparseable child output\n" if returncode else "")

        @staticmethod
        def poll() -> int:
            return returncode

        @staticmethod
        def wait(timeout: int) -> int:
            assert timeout == health.TIMEOUT_SEC
            return returncode

    output_file = tmp_path / f"terminal-{returncode}.json"
    monkeypatch.setattr(
        health,
        "_find_integration_test_files",
        lambda: [tmp_path / "test_example.py"],
    )
    monkeypatch.setattr(health.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(health, "OUTPUT_FILE", output_file)
    health._accumulated_lines.clear()

    assert health.main() == expected_main_rc
    report = json.loads(output_file.read_text(encoding="utf-8"))
    assert report["status"] == "complete"
    assert report["total_failures"] == expected_failures
    assert report["failed_files"] == 0


def test_main_kills_and_reports_timed_out_pytest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeProcess:
        stdout = io.StringIO("")

        def __init__(self) -> None:
            self.returncode: int | None = None
            self.wait_calls = 0

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.returncode = -signal.SIGKILL

        def wait(self, timeout: int) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                assert timeout == health.TIMEOUT_SEC
                raise subprocess.TimeoutExpired("pytest", timeout)
            assert timeout == 5
            assert self.returncode is not None
            return self.returncode

    process = FakeProcess()
    output_file = tmp_path / "timeout.json"
    monkeypatch.setattr(
        health,
        "_find_integration_test_files",
        lambda: [tmp_path / "test_example.py"],
    )
    monkeypatch.setattr(health.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(health, "OUTPUT_FILE", output_file)
    health._accumulated_lines.clear()

    assert health.main() == 2
    report = json.loads(output_file.read_text(encoding="utf-8"))
    assert report["status"] == "timeout"
    assert report["returncode"] == -signal.SIGKILL
    assert process.wait_calls == 2


def test_main_streams_and_reports_exact_xdist_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeProcess:
        stdout = io.StringIO(XDIST_OUTPUT)
        returncode = 1

        @classmethod
        def poll(cls) -> int:
            return cls.returncode

        @staticmethod
        def wait(timeout: int) -> int:
            assert timeout == health.TIMEOUT_SEC
            return 1

    output_file = tmp_path / "integration-health.json"
    monkeypatch.setattr(
        health,
        "_find_integration_test_files",
        lambda: [Path(f"tests/integration/test_{index}.py") for index in range(4)],
    )
    monkeypatch.setattr(health.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(health, "OUTPUT_FILE", output_file)
    health._accumulated_lines.clear()

    returncode = health.main()

    captured = capsys.readouterr()
    report = json.loads(output_file.read_text())
    assert returncode == 1
    assert "FAILED tests/integration/test_delta.py::test_sixth" in captured.out
    assert "FAIL: 4 failed files, 6 total failures" in captured.out
    assert "--- Progress: ~5 results, 5 failures" in captured.out
    assert report["returncode"] == 1
    assert report["total_failures"] == 6
    assert report["failed_files"] == 4
    assert [failure["test"] for failure in report["failures"]] == [
        "tests/integration/test_alpha.py::test_first",
        "tests/integration/test_alpha.py::TestGroup::test_second",
        "tests/integration/test_beta.py::test_third[param-a]",
        "tests/integration/test_beta.py::test_third[param-b]",
        "tests/integration/test_gamma.py::test_fifth",
        "tests/integration/test_delta.py::test_sixth",
    ]


def test_main_persists_timeout_with_partial_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeProcess:
        stdout = io.StringIO(XDIST_OUTPUT)
        killed = False

        @classmethod
        def poll(cls) -> int:
            return -signal.SIGKILL if cls.killed else 1

        @classmethod
        def kill(cls) -> None:
            cls.killed = True

        @classmethod
        def wait(cls, timeout: int) -> int:
            if not cls.killed:
                raise health.subprocess.TimeoutExpired("pytest", timeout)
            return -signal.SIGKILL

    output_file = tmp_path / "timeout.json"
    monkeypatch.setattr(
        health,
        "_find_integration_test_files",
        lambda: [tmp_path / "test_example.py"],
    )
    monkeypatch.setattr(health.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(health, "OUTPUT_FILE", output_file)
    health._accumulated_lines.clear()

    assert health.main() == 2

    report = json.loads(output_file.read_text(encoding="utf-8"))
    assert report["status"] == "timeout"
    assert report["total_failures"] == 6
    assert "TIMEOUT: integration tests exceeded" in capsys.readouterr().out


def test_main_escalates_lingering_successful_wrapper_and_reports_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeProcess:
        stdout = io.StringIO("")
        wait_calls = 0
        terminated = False
        killed = False

        @classmethod
        def poll(cls) -> None:
            return None

        @classmethod
        def terminate(cls) -> None:
            cls.terminated = True

        @classmethod
        def kill(cls) -> None:
            cls.killed = True

        @classmethod
        def wait(cls, timeout: int) -> int:
            cls.wait_calls += 1
            if cls.wait_calls == 1:
                return 0
            if cls.wait_calls == 2:
                raise health.subprocess.TimeoutExpired("pytest", timeout)
            return -signal.SIGKILL

    output_file = tmp_path / "success.json"
    monkeypatch.setattr(
        health,
        "_find_integration_test_files",
        lambda: [tmp_path / "test_example.py"],
    )
    monkeypatch.setattr(health.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(health, "OUTPUT_FILE", output_file)
    health._accumulated_lines.clear()

    assert health.main() == 0
    assert FakeProcess.terminated is True
    assert FakeProcess.killed is True
    assert "PASS: 0 failures" in capsys.readouterr().out
