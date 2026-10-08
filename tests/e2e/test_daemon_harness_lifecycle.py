"""Regression tests for the real-daemon E2E process harness."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_process_cleanup_closes_completed_process_pipes() -> None:
    """Cleanup owns both PIPE streams even when the daemon already exited."""
    from tests.e2e._daemon_harness import stop_daemon_process

    process = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    process.wait(timeout=5)

    stop_daemon_process(process)
    stop_daemon_process(process)

    assert process.stdout is not None
    assert process.stderr is not None
    assert process.stdout.closed
    assert process.stderr.closed


def test_daemon_environment_is_namespaced(tmp_path: Path) -> None:
    """Each daemon gets isolated project and state namespaces."""
    from tests.e2e._daemon_harness import daemon_subprocess_env

    env = daemon_subprocess_env(tmp_path, port=41237)

    assert env["GLUDD_PROJECT_NAMESPACE"].endswith("-41237")
    assert env["GLUDD_STATE_DIR"] == str(tmp_path / "state")
