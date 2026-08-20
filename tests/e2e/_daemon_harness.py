"""Shared lifecycle helpers for real-daemon E2E subprocesses."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
from pathlib import Path


def daemon_subprocess_env(root: Path, *, port: int) -> dict[str, str]:
    """Return an inherited environment with test-owned runtime namespaces."""
    env = os.environ.copy()
    env["GLUDD_PROJECT_NAMESPACE"] = f"gludd-e2e-daemon-{os.getpid()}-{port}"
    env["GLUDD_STATE_DIR"] = str(root / "state")
    return env


def start_daemon_process(
    *,
    config_dir: str | Path,
    cwd: Path,
    port: int,
    tick_interval: float = 0.5,
) -> subprocess.Popen[str]:
    """Start one namespaced daemon process group with captured diagnostics."""
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "general_ludd.cli",
            "daemon",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--config-dir",
            str(config_dir),
            "--tick-interval",
            str(tick_interval),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(cwd),
        env=daemon_subprocess_env(cwd, port=port),
        start_new_session=True,
    )


def stop_daemon_process(
    process: subprocess.Popen[str],
    *,
    terminate_timeout: float = 10.0,
    kill_timeout: float = 5.0,
) -> tuple[str, str]:
    """Stop one owned process group, reap it, and close both captured pipes."""
    streams = (process.stdout, process.stderr)
    if process.poll() is not None and all(stream is None or stream.closed for stream in streams):
        return "", ""

    try:
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=terminate_timeout)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=kill_timeout)
        return stdout or "", stderr or ""
    finally:
        for stream in streams:
            if stream is not None and not stream.closed:
                stream.close()
