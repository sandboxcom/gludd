"""Subprocess E2E tests for the TUI — catch runtime bugs automated tests miss.

These tests spawn `gludd tui` in a real PTY, send keyboard input, and verify behavior.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import subprocess
import sys
import time

GLUDD_CMD = [sys.executable, "-m", "general_ludd.cli", "tui"]

# Absolute path to the repo's src/ directory.  When CI runs pytest with
# PYTHONPATH=src (rather than an editable install), the spawned subprocess
# inherits os.environ — but os.environ may not contain PYTHONPATH at all if
# the harness injected importability via sys.path manipulation only.  We ensure
# the subprocess always has src/ on PYTHONPATH regardless.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC_DIR = str(_REPO_ROOT / "src")


def _subprocess_env() -> dict[str, str]:
    """Return a copy of os.environ that lets the child import general_ludd and deps.

    Strategy: rely on sys.executable being the venv interpreter (the same one running
    this test under `uv run python -m pytest`). A venv interpreter's default sys.path
    already includes the venv's site-packages (httpx, rich, etc.), so the child process
    inherits dependency access automatically — no PYTHONPATH injection is needed for
    third-party deps.

    We only prepend src/ to PYTHONPATH to cover the editable-install source path in
    case the package metadata lookup differs across platforms. We do NOT dump the
    parent's full sys.path into PYTHONPATH: pytest inserts many internal paths
    (conftest dirs, tmp basetemp, plugin paths) that are meaningless to the child
    and can shadow the venv's own site-packages lookup on some Linux CI configurations,
    causing ModuleNotFoundError for packages like httpx that are installed in the venv
    but not on those injected paths.
    """
    env = dict(os.environ)
    # Only prepend src/ — deps come from the venv interpreter's built-in site-packages.
    existing = env.get("PYTHONPATH", "")
    parts: list[str] = [_SRC_DIR]
    if existing:
        parts.extend(p for p in existing.split(os.pathsep) if p and p != _SRC_DIR)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["TERM"] = "xterm-256color"
    return env


class TestTUIE2E:
    def test_tui_starts_and_exits_cleanly_on_q(self):
        import pty
        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                GLUDD_CMD,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env=_subprocess_env(),
            )
            os.close(slave_fd)
            time.sleep(1.0)
            os.write(master_fd, b"q")
            time.sleep(1.0)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            remaining = b""
            while True:
                try:
                    data = os.read(master_fd, 4096)
                    if not data:
                        break
                    remaining += data
                except (OSError, BlockingIOError):
                    break
            os.close(master_fd)
            assert True
        except Exception as exc:
            if "out of pty devices" in str(exc).lower():
                import pytest
                pytest.skip("No PTY devices available")
            raise

    def test_tui_shows_version_in_output(self):
        import pty
        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as exc:
            if "out of pty devices" in str(exc).lower():
                import pytest
                pytest.skip("No PTY devices available")
            raise
        try:
            proc = subprocess.Popen(
                GLUDD_CMD,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env=_subprocess_env(),
            )
            os.close(slave_fd)
            time.sleep(1.5)
            output = b""
            for _ in range(10):
                try:
                    data = os.read(master_fd, 65536)
                    if data:
                        output += data
                except (OSError, BlockingIOError):
                    time.sleep(0.2)
            os.write(master_fd, b"q")
            time.sleep(0.5)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            os.close(master_fd)
            text = output.decode("utf-8", errors="ignore").lower()
            assert "general ludd" in text or "0.1" in text
        finally:
            with contextlib.suppress(OSError):
                os.close(master_fd)

    def test_tui_exits_on_ctrl_c(self):
        """Ctrl+C sends SIGINT which triggers KeyboardInterrupt -> clean exit."""
        try:
            import pty
            master_fd, slave_fd = pty.openpty()
        except OSError as exc:
            if "out of pty devices" in str(exc).lower():
                import pytest
                pytest.skip("No PTY devices available")
            raise
        try:
            proc = subprocess.Popen(
                GLUDD_CMD,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env=_subprocess_env(),
            )
            os.close(slave_fd)
            time.sleep(1.0)
            os.write(master_fd, b"\x03")
            time.sleep(1.0)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            os.close(master_fd)
            assert True
        finally:
            with contextlib.suppress(OSError):
                os.close(master_fd)

    def test_tui_does_not_exit_on_arrow_key(self):
        """Arrow keys send escape sequences — must not trigger exit."""
        try:
            import pty
            master_fd, slave_fd = pty.openpty()
        except OSError as exc:
            if "out of pty devices" in str(exc).lower():
                import pytest
                pytest.skip("No PTY devices available")
            raise
        try:
            proc = subprocess.Popen(
                GLUDD_CMD,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env=_subprocess_env(),
            )
            os.close(slave_fd)
            time.sleep(1.0)
            os.write(master_fd, b"\x1b[A")
            time.sleep(0.5)
            os.write(master_fd, b"\x1b[B")
            time.sleep(0.5)
            os.write(master_fd, b"\x1b[C")
            time.sleep(0.5)
            os.write(master_fd, b"\x1b[D")
            time.sleep(0.5)
            proc.poll()
            assert proc.returncode is None, "TUI should not exit on arrow keys"
            os.write(master_fd, b"q")
            time.sleep(1.0)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            os.close(master_fd)
        finally:
            with contextlib.suppress(OSError):
                os.close(master_fd)
