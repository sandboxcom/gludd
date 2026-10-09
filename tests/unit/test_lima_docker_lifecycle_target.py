"""Hermetic behavior tests for the namespaced Lima shutdown target."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _run_stop(
    tmp_path: Path,
    state: str,
    *,
    stop_mode: str = "complete",
    timeout_secs: int = 9,
    kill_after_secs: int = 3,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
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
    if [ "$LIMA_FAKE_STOP_MODE" = "complete" ]; then
        printf 'Stopped' > "$LIMA_FAKE_STATE"
    else
        trap 'printf "TERM\n" >> "$LIMA_FAKE_CALLS"' TERM
        # Stay in this owned process so POSIX trap delivery is deterministic;
        # a child `sleep` can defer the shell trap until after the KILL bound.
        while :; do :; done
    fi
else
    exit 64
fi
""",
    )

    env = os.environ.copy()
    uv = shutil.which("uv")
    assert uv is not None
    env.update(
        {
            "LIMA_FAKE_CALLS": str(calls_file),
            "LIMA_FAKE_STATE": str(state_file),
            "LIMA_FAKE_STOP_MODE": stop_mode,
            "PATH": os.pathsep.join(
                (str(fake_bin), str(Path(uv).parent), "/usr/bin", "/bin")
            ),
        }
    )
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "lima-docker-stop",
            "LIMA_INSTANCE=gludd-test",
            f"LIMA_DOCKER_STOP_KILL_AFTER_SECS={kill_after_secs}",
            f"LIMA_DOCKER_STOP_TIMEOUT_SECS={timeout_secs}",
            "LIMA_DOCKER_VALIDATE_ONLY=0",
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    return result, state_file, calls_file


def _run_delete(
    tmp_path: Path,
    state: str,
    *,
    confirm: str = "gludd-test",
    validate_only: int = 0,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_file = tmp_path / "state"
    state_file.write_text(state)
    calls_file = tmp_path / "calls"
    instance_dir = tmp_path / "gludd-test"
    instance_dir.mkdir()
    (instance_dir / "disk.raw").write_bytes(b"reproducible")

    _write_executable(
        fake_bin / "limactl",
        """#!/bin/sh
set -eu
if [ "$1" = "list" ]; then
    if [ "$(cat "$LIMA_FAKE_STATE")" = "Absent" ]; then
        exit 0
    fi
    printf '%s|%s|%s\n' "$2" "$(cat "$LIMA_FAKE_STATE")" "$LIMA_FAKE_DIR"
elif [ "$1" = "delete" ]; then
    printf '%s\n' "$*" >> "$LIMA_FAKE_CALLS"
    printf 'Absent' > "$LIMA_FAKE_STATE"
else
    exit 64
fi
""",
    )

    env = os.environ.copy()
    uv = shutil.which("uv")
    assert uv is not None
    env.update(
        {
            "LIMA_FAKE_CALLS": str(calls_file),
            "LIMA_FAKE_DIR": str(instance_dir),
            "LIMA_FAKE_STATE": str(state_file),
            "PATH": os.pathsep.join(
                (str(fake_bin), str(Path(uv).parent), "/usr/bin", "/bin")
            ),
        }
    )
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "lima-docker-delete",
            "LIMA_INSTANCE=gludd-test",
            f"LIMA_DOCKER_DELETE_CONFIRM={confirm}",
            "LIMA_DOCKER_DELETE_KILL_AFTER_SECS=3",
            "LIMA_DOCKER_DELETE_TIMEOUT_SECS=9",
            f"LIMA_DOCKER_DELETE_VALIDATE_ONLY={validate_only}",
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
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
    assert "--tty=false stop gludd-test" in calls
    assert "--force" not in calls
    assert "delete" not in calls


def test_hung_shutdown_receives_bounded_term_then_kill(tmp_path: Path) -> None:
    result, state_file, calls_file = _run_stop(
        tmp_path,
        "Running",
        stop_mode="hang",
        timeout_secs=1,
        kill_after_secs=1,
    )

    assert result.returncode != 0
    assert "LIMA_DOCKER_STOP_TIMEOUT instance=gludd-test timeout_secs=1 signal=TERM" in result.stdout
    assert "LIMA_DOCKER_STOP_KILL instance=gludd-test kill_after_secs=1 signal=KILL" in result.stdout
    assert "Lima Docker shutdown failed or exceeded its bound: rc=124" in result.stdout
    assert state_file.read_text() == "Running"
    calls = calls_file.read_text()
    assert "--tty=false stop gludd-test" in calls
    assert "TERM" in calls


def test_missing_instance_fails_without_invoking_shutdown(tmp_path: Path) -> None:
    result, _, calls_file = _run_stop(tmp_path, "Absent")

    assert result.returncode != 0
    assert "Refusing to stop missing Lima instance: gludd-test" in result.stdout
    assert not calls_file.exists()


def test_delete_requires_exact_confirmation_for_stopped_instance(tmp_path: Path) -> None:
    result, state_file, calls_file = _run_delete(
        tmp_path,
        "Stopped",
        confirm="wrong-instance",
    )

    assert result.returncode != 0
    assert "LIMA_DOCKER_DELETE_CONFIRM must exactly match LIMA_INSTANCE" in result.stdout
    assert state_file.read_text() == "Stopped"
    assert not calls_file.exists()


def test_delete_refuses_running_instance_without_mutation(tmp_path: Path) -> None:
    result, state_file, calls_file = _run_delete(tmp_path, "Running")

    assert result.returncode != 0
    assert "Refusing to delete running Lima instance: gludd-test" in result.stdout
    assert state_file.read_text() == "Running"
    assert not calls_file.exists()


def test_delete_removes_only_confirmed_stopped_instance(tmp_path: Path) -> None:
    result, state_file, calls_file = _run_delete(tmp_path, "Stopped")

    assert result.returncode == 0, result.stderr
    assert "LIMA_DOCKER_DELETE_BEGIN instance=gludd-test" in result.stdout
    assert "LIMA_DOCKER_DELETE_READY instance=gludd-test status=Absent" in result.stdout
    assert state_file.read_text() == "Absent"
    assert calls_file.read_text().strip() == "delete gludd-test"


def test_delete_validate_only_never_invokes_limactl(tmp_path: Path) -> None:
    result, state_file, calls_file = _run_delete(
        tmp_path,
        "Stopped",
        validate_only=1,
    )

    assert result.returncode == 0, result.stderr
    assert "LIMA_DOCKER_DELETE_VALID instance=gludd-test" in result.stdout
    assert state_file.read_text() == "Stopped"
    assert not calls_file.exists()
