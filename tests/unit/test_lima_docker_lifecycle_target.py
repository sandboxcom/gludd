"""Hermetic behavior tests for the namespaced Lima shutdown target."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _run_stop(tmp_path: Path, state: str) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_file = tmp_path / "state"
    state_file.write_text(state)
    calls_file = tmp_path / "calls"

    _write_executable(
        fake_bin / "limactl",
        """#!/bin/sh
set -eu
if [ "$1" = "list" ]; then
    if [ "$(cat "$LIMA_FAKE_STATE")" = "Absent" ]; then
        exit 0
    fi
    printf '%s|%s\n' "$2" "$(cat "$LIMA_FAKE_STATE")"
elif [ "$1" = "--tty=false" ] && [ "$2" = "stop" ]; then
    printf '%s\n' "$*" >> "$LIMA_FAKE_CALLS"
    printf 'Stopped' > "$LIMA_FAKE_STATE"
else
    exit 64
fi
""",
    )
    _write_executable(
        fake_bin / "gtimeout",
        """#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$LIMA_FAKE_CALLS"
while [ "$#" -gt 0 ] && [ "$1" != "limactl" ]; do
    shift
done
exec "$@"
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "LIMA_FAKE_CALLS": str(calls_file),
            "LIMA_FAKE_STATE": str(state_file),
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
        }
    )
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "lima-docker-stop",
            "LIMA_INSTANCE=gludd-test",
            "LIMA_DOCKER_STOP_KILL_AFTER_SECS=3",
            "LIMA_DOCKER_STOP_TIMEOUT_SECS=9",
            "LIMA_DOCKER_VALIDATE_ONLY=0",
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, state_file, calls_file


def test_stop_is_idempotent_without_invoking_shutdown(tmp_path: Path) -> None:
    result, state_file, calls_file = _run_stop(tmp_path, "Stopped")

    assert result.returncode == 0, result.stderr
    assert "LIMA_DOCKER_STOP_ALREADY_STOPPED instance=gludd-test" in result.stdout
    assert state_file.read_text() == "Stopped"
    assert not calls_file.exists()


def test_running_instance_stops_through_bounded_graceful_command(tmp_path: Path) -> None:
    result, state_file, calls_file = _run_stop(tmp_path, "Running")

    assert result.returncode == 0, result.stderr
    assert "LIMA_DOCKER_STOP_BEGIN instance=gludd-test timeout_secs=9" in result.stdout
    assert "LIMA_DOCKER_STOP_READY instance=gludd-test status=Stopped" in result.stdout
    assert state_file.read_text() == "Stopped"
    calls = calls_file.read_text()
    assert "--foreground --signal=TERM --kill-after=3s 9s" in calls
    assert "limactl --tty=false stop gludd-test" in calls
    assert "--force" not in calls
    assert "delete" not in calls


def test_missing_instance_fails_without_invoking_shutdown(tmp_path: Path) -> None:
    result, _, calls_file = _run_stop(tmp_path, "Absent")

    assert result.returncode != 0
    assert "Refusing to stop missing Lima instance: gludd-test" in result.stdout
    assert not calls_file.exists()
