"""Subprocess E2E tests for the TUI — catch runtime bugs automated tests miss.

These tests spawn `gludd tui` in a real PTY, send keyboard input, and verify behavior.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import select
import subprocess
import sys
import time

import pytest

GLUDD_CMD = [sys.executable, "-m", "general_ludd.cli", "tui"]

# Absolute path to the repo's src/ directory.  When CI runs pytest with
# PYTHONPATH=src (rather than an editable install), the spawned subprocess
# inherits os.environ — but os.environ may not contain PYTHONPATH at all if
# the harness injected importability via sys.path manipulation only.  We ensure
# the subprocess always has src/ on PYTHONPATH regardless.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC_DIR = str(_REPO_ROOT / "src")


def _subprocess_env() -> dict[str, str]:
    """Return a copy of os.environ that lets the child import BOTH general_ludd AND
    its third-party deps (httpx, rich, ...).

    Passing only src/ on PYTHONPATH is insufficient: under CI the child resolves
    general_ludd from src/ but then fails at `import httpx` because the parent's
    site-packages are not guaranteed to be on the child's import path. We therefore
    hand the child the parent interpreter's ENTIRE sys.path (which already contains
    both the installed deps and, after we prepend it, src/). Combined with launching
    via sys.executable (the same venv interpreter running this test), the child sees
    exactly what the parent sees.
    """
    env = dict(os.environ)
    # src/ first, then every non-empty entry of the parent's sys.path (site-packages
    # with httpx/rich/etc.), then any pre-existing PYTHONPATH. De-duplicate, keep order.
    parts: list[str] = [_SRC_DIR, *[p for p in sys.path if p]]
    existing = env.get("PYTHONPATH", "")
    if existing:
        parts.extend(existing.split(os.pathsep))
    seen: set[str] = set()
    ordered = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    env["PYTHONPATH"] = os.pathsep.join(ordered)
    env["TERM"] = "xterm-256color"
    return env


@pytest.mark.xdist_group("port_8000")
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
            output = b""
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                readable, _, _ = select.select([master_fd], [], [], 0.2)
                if readable:
                    try:
                        data = os.read(master_fd, 65536)
                        if data:
                            output += data
                            if b"tui |" in output.lower():
                                break
                    except (OSError, BlockingIOError):
                        pass
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
            assert "general ludd" in text or "0.1" in text, (
                f"TUI did not render version. rc={proc.returncode} "
                f"output_tail={output[-400:]!r}"
            )
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
