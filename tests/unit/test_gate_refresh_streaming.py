"""Regression coverage for observable ``make gate-refresh`` pytest runs."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
STREAM_RUNNER = ROOT / "scripts" / "stream_command.py"


def _gate_refresh_body() -> str:
    text = MAKEFILE.read_text(encoding="utf-8")
    return text.split("_gate-refresh-body:", 1)[1].split("\n\n", 1)[0]


def test_gate_refresh_streams_verbose_nodeids_to_a_durable_log() -> None:
    body = _gate_refresh_body()

    assert "scripts/stream_command.py" in body
    assert "--log .gate-logs/gate-refresh-test.log" in body
    assert "pytest tests/unit/ -vv --no-header" in body
    assert "> /tmp/gludd-gate-refresh-test.log" not in body
    assert 'echo "PASS 0" >> .gate-status' in body
    assert 'echo "FAIL non-zero-exit" >> .gate-status' in body


def test_stream_command_forwards_a_nodeid_before_the_child_exits(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "release-child"
    log_path = tmp_path / "pytest.log"
    child = (
        "import pathlib,sys,time; "
        "print('tests/unit/test_demo.py::test_live PASSED', flush=True); "
        "marker=pathlib.Path(sys.argv[1]); "
        "exec(\"while not marker.exists():\\n time.sleep(0.01)\"); "
        "print('session complete', flush=True)"
    )
    with subprocess.Popen(
        [
            sys.executable,
            str(STREAM_RUNNER),
            "--log",
            str(log_path),
            "--",
            sys.executable,
            "-c",
            child,
            str(marker),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        assert process.stdout is not None
        stdout = process.stdout
        observed = threading.Event()
        first_line: list[bytes] = []

        def read_first_line() -> None:
            first_line.append(stdout.readline())
            observed.set()

        reader = threading.Thread(target=read_first_line, daemon=True)
        reader.start()
        streamed_while_running = observed.wait(timeout=2)
        marker.touch()
        returncode = process.wait(timeout=5)
        reader.join(timeout=1)
        remaining_stdout = stdout.read()
        stderr = process.stderr.read() if process.stderr is not None else b""

    assert streamed_while_running, "first node ID was buffered until pytest exited"
    assert first_line == [b"tests/unit/test_demo.py::test_live PASSED\n"]
    assert remaining_stdout == b"session complete\n"
    assert stderr == b""
    assert returncode == 0
    assert log_path.read_bytes() == first_line[0] + remaining_stdout


def test_stream_command_mirrors_output_and_preserves_failure_status(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "nested" / "pytest.log"
    command = [
        sys.executable,
        str(STREAM_RUNNER),
        "--log",
        str(log_path),
        "--",
        sys.executable,
        "-c",
        "import sys; print('tests/unit/test_demo.py::test_case FAILED'); sys.exit(7)",
    ]

    completed = subprocess.run(command, capture_output=True, check=False)

    expected = b"tests/unit/test_demo.py::test_case FAILED\n"
    assert completed.returncode == 7
    assert completed.stdout == expected
    assert completed.stderr == b""
    assert log_path.read_bytes() == expected
