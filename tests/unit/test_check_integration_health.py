from __future__ import annotations

import importlib.util
import io
import json
import signal
from pathlib import Path
from types import ModuleType

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


def test_main_streams_and_reports_exact_xdist_failures(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    class FakeProcess:
        stdout = io.StringIO(XDIST_OUTPUT)

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
