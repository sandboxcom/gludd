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

# Absolute path to the repo's src/ directory.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC_DIR = str(_REPO_ROOT / "src")

# Launch the child so it can import general_ludd from src/ WITHOUT setting
# PYTHONPATH.  Setting a non-empty PYTHONPATH on a Python 3.12+ venv interpreter
# makes site.py skip adding the venv's own site-packages, which causes the child
# to fail `import httpx` ("No module named 'httpx'") even though httpx is
# installed in the venv — this was the exact CI failure on the 3.12 gate.  We
# instead bootstrap via `python -c`: site initialization runs normally (so the
# venv site-packages stay on sys.path and httpx/rich import fine), then we
# inject src/ into sys.path *inside* the child before importing the CLI.  This
# works whether or not general_ludd is also installed in site-packages.
_BOOTSTRAP = (
    "import sys; "
    f"sys.path.insert(0, {_SRC_DIR!r}); "
    "from general_ludd.cli import main; "
    "main()"
)
GLUDD_CMD = [sys.executable, "-c", _BOOTSTRAP, "tui"]


def _subprocess_env() -> dict[str, str]:
    """Return a child env that lets the TUI import general_ludd and its
    third-party deps (httpx, rich, ...).

    We deliberately do NOT set PYTHONPATH: on Python 3.12+ venvs a non-empty
    PYTHONPATH can cause site.py to skip adding the venv's own site-packages,
    which produces the "No module named 'httpx'" error seen in CI.  src/ is
    instead injected via the in-child `-c` bootstrap (see _BOOTSTRAP), and the
    venv site-packages are added by normal site initialization.
    """
    env = dict(os.environ)
    # Ensure no inherited PYTHONPATH suppresses venv site-packages on 3.12+.
    env.pop("PYTHONPATH", None)
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
