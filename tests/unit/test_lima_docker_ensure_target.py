"""Hermetic behavior tests for namespaced Lima Docker provisioning."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _run_ensure(
    tmp_path: Path,
    state: str,
    *,
    instance: str = "gludd-test",
    target: str = "lima-docker-ensure",
    docker_build_status: int = 0,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_file = tmp_path / "state"
    state_file.write_text(state)
    calls_file = tmp_path / "calls"
    socket_path = tmp_path / "docker.sock"

    _write_executable(
        fake_bin / "limactl",
        """#!/bin/sh
set -eu
printf 'limactl %s\n' "$*" >> "$LIMA_FAKE_CALLS"
if [ "$1" = "list" ]; then
    if [ "$(cat "$LIMA_FAKE_STATE")" = "Absent" ]; then
        exit 0
    fi
    case "$*" in
        *'{{.Name}}|{{.Status}}'*)
            printf '%s|%s\n' "$2" "$(cat "$LIMA_FAKE_STATE")"
            ;;
        *'{{.Dir}}/sock/docker.sock'*)
            printf '%s\n' "$LIMA_FAKE_SOCKET"
            ;;
        *) exit 64 ;;
    esac
elif [ "$1" = "start" ]; then
    printf 'Running' > "$LIMA_FAKE_STATE"
else
    exit 64
fi
""",
    )
    _write_executable(
        fake_bin / "docker",
        """#!/bin/sh
set -eu
printf 'docker %s host=%s config=%s buildkit=%s\n' \
  "$*" "$DOCKER_HOST" "$DOCKER_CONFIG" "${DOCKER_BUILDKIT:-}" >> "$LIMA_FAKE_CALLS"
if [ "$1" = "info" ]; then
    printf 'server=fake containers=0 images=0\n'
    exit 0
fi
if [ "$1" = "run" ]; then
    printf '3.12.14|uv 0.12.19\n'
    exit 0
fi
exit 64
""",
    )
    _write_executable(
        fake_bin / "docker-buildx",
        """#!/bin/sh
set -eu
printf 'docker-buildx %s host=%s config=%s\n' "$*" "$DOCKER_HOST" "$DOCKER_CONFIG" >> "$LIMA_FAKE_CALLS"
if [ "$1" = "version" ]; then
    printf 'github.com/docker/buildx v0.37.1\n'
    exit 0
fi
if [ "$1" = "build" ]; then
    exit "$LIMA_FAKE_DOCKER_BUILD_STATUS"
fi
exit 64
""",
    )

    env = os.environ.copy()
    uv = shutil.which("uv")
    assert uv is not None
    env.update(
        {
            "LIMA_FAKE_CALLS": str(calls_file),
            "LIMA_FAKE_DOCKER_BUILD_STATUS": str(docker_build_status),
            "LIMA_FAKE_SOCKET": str(socket_path),
            "LIMA_FAKE_STATE": str(state_file),
            "PATH": os.pathsep.join(
                (str(fake_bin), str(Path(uv).parent), "/usr/bin", "/bin")
            ),
        }
    )
    command = [
            "make",
            "--no-print-directory",
            target,
            f"LIMA_INSTANCE={instance}",
            f"LIMA_DOCKER_CONFIG={tmp_path / 'docker-config'}",
            "LIMA_DOCKER_TEMPLATE=template:docker",
            "LIMA_DOCKER_START_TIMEOUT_SECS=180",
            "LIMA_DOCKER_VALIDATE_ONLY=0",
    ]
    if target == "build-linux-binary-image":
        command.extend(
            [
                "LINUX_BINARY_IMAGE=gludd-linux-binary-build:test",
                "LINUX_BINARY_DOCKERFILE=config/containers/linux-binary.Dockerfile",
                "LINUX_BINARY_IMAGE_BUILD_VALIDATE_ONLY=0",
                "PYINSTALLER_PYTHON_VERSION_LINUX=3.12.14",
                "PYINSTALLER_UV_VERSION_LINUX=0.12.19",
                f"DOCKER_BUILDX_BIN={fake_bin / 'docker-buildx'}",
                "DOCKER_BUILDX_AUTO_INSTALL=0",
            ]
        )
    result = subprocess.run(
        command,
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result, calls_file


def test_missing_instance_is_created_from_explicit_docker_template(tmp_path: Path) -> None:
    result, calls_file = _run_ensure(tmp_path, "Absent")

    assert result.returncode == 0, result.stderr
    assert "LIMA_DOCKER_ENSURE_READY instance=gludd-test" in result.stdout
    calls = calls_file.read_text()
    assert "start --name gludd-test --timeout 180s --progress template:docker" in calls
    assert "docker info" in calls


def test_stopped_instance_is_started_without_recreating_it(tmp_path: Path) -> None:
    result, calls_file = _run_ensure(tmp_path, "Stopped")

    assert result.returncode == 0, result.stderr
    calls = calls_file.read_text()
    assert "start --timeout 180s --progress gludd-test" in calls
    assert "start --name" not in calls


def test_running_instance_is_reused_and_engine_readiness_is_proved(tmp_path: Path) -> None:
    result, calls_file = _run_ensure(tmp_path, "Running")

    assert result.returncode == 0, result.stderr
    calls = calls_file.read_text()
    assert "limactl start" not in calls
    assert "docker info" in calls


def test_non_namespaced_instance_is_rejected_before_any_tool_call(tmp_path: Path) -> None:
    result, calls_file = _run_ensure(tmp_path, "Absent", instance="default")

    assert result.returncode != 0
    assert "Refusing non-Gludd Lima instance: default" in result.stdout
    assert not calls_file.exists()


def test_linux_build_automatically_ensures_lima_engine() -> None:
    makefile = (_ROOT / "Makefile").read_text()

    assert "build-linux-executable: worktree-guard" in makefile
    assert "$(MAKE) --no-print-directory build-linux-binary-image" in makefile


def test_builder_failure_stops_before_identity_run(tmp_path: Path) -> None:
    result, calls_file = _run_ensure(
        tmp_path,
        "Running",
        target="build-linux-binary-image",
        docker_build_status=23,
    )

    assert result.returncode != 0
    calls = calls_file.read_text()
    assert "docker-buildx build --load --progress=plain" in calls
    assert "docker build" not in calls
    assert "docker run" not in calls
