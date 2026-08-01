"""Contract tests for the namespaced Podman machine startup target."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_podman_project_up_validate_only_is_side_effect_free() -> None:
    result = subprocess.run(
        [
            "make",
            "podman-project-up",
            "PODMAN_MACHINE=gludd",
            "PODMAN_START_TIMEOUT_SECS=1",
            "PODMAN_VALIDATE_ONLY=1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "PODMAN_PROJECT_UP_VALID machine=gludd" in result.stdout


def test_podman_project_up_bounds_a_hung_machine_start(tmp_path: Path) -> None:
    fake_podman = tmp_path / "podman"
    fake_podman.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "machine" ] && { [ "$2" = "start" ] || [ "$2" = "stop" ]; }; '
        "then /bin/sleep 10; fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_podman.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"

    started = time.monotonic()
    result = subprocess.run(
        [
            "make",
            "podman-project-up",
            "PODMAN_MACHINE=gludd-test",
            "PODMAN_START_TIMEOUT_SECS=1",
            "PODMAN_VALIDATE_ONLY=0",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    elapsed = time.monotonic() - started

    assert result.returncode != 0
    assert "PODMAN_PROJECT_UP_TIMEOUT machine=gludd-test" in result.stdout
    assert "PODMAN_PROJECT_UP_RECOVER machine=gludd-test" in result.stdout
    assert elapsed < 5


def test_podman_project_recreate_validate_only_is_namespaced() -> None:
    result = subprocess.run(
        [
            "make",
            "podman-project-recreate",
            "PODMAN_MACHINE=gludd-e2e",
            "PODMAN_MEMORY_MB=4096",
            "PODMAN_CPUS=4",
            "PODMAN_DISK_GB=20",
            "PODMAN_START_TIMEOUT_SECS=30",
            "PODMAN_VALIDATE_ONLY=1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "PODMAN_PROJECT_RECREATE_VALID machine=gludd-e2e" in result.stdout


def test_podman_project_delete_validate_only_is_namespaced() -> None:
    result = subprocess.run(
        [
            "make",
            "podman-project-delete",
            "PODMAN_MACHINE=gludd-e2e",
            "PODMAN_DELETE_TIMEOUT_SECS=30",
            "PODMAN_VALIDATE_ONLY=1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "PODMAN_PROJECT_DELETE_VALID machine=gludd-e2e" in result.stdout


def test_podman_project_delete_rejects_non_project_machine() -> None:
    result = subprocess.run(
        [
            "make",
            "podman-project-delete",
            "PODMAN_MACHINE=podman-machine-default",
            "PODMAN_DELETE_TIMEOUT_SECS=30",
            "PODMAN_VALIDATE_ONLY=1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "Refusing non-project Podman machine" in result.stdout
