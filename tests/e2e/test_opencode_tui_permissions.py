"""Live TUI E2E coverage for project-level OpenCode permissions.

This test uses a real pseudo-terminal and one persistent OpenCode TUI session.
It submits multiple prompts that exercise read, grep, and allowed bash access.
The test is intentionally live: a boot-only server smoke cannot detect rule
ordering that denies every legitimate workspace path.
"""

from __future__ import annotations

import errno
import fcntl
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import termios
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPENCODE = shutil.which("opencode")

_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")


def _plain(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").replace("\r", "\n")
    return _CSI_RE.sub("", _OSC_RE.sub("", text))


def _compact(text: str) -> str:
    """Strip TUI redraw glyphs while retaining meaningful answer characters."""
    return re.sub(r"[^A-Za-z0-9._-]+", "", text)


class _Tui:
    def __init__(self) -> None:
        self.master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(
            slave_fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", 48, 180, 0, 0),
        )
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
        # The test targets OpenCode's permission engine. Project plugins have
        # their own suites and intentionally skip delegated/subagent contexts.
        env["OPENCODE_SUBAGENT"] = "1"
        self.proc = subprocess.Popen(
            [
                str(OPENCODE),
                "--print-logs",
                "--log-level",
                "INFO",
            ],
            cwd=ROOT,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave_fd)
        os.set_blocking(self.master_fd, False)
        self.raw = bytearray()

    def _drain(self, timeout: float = 0.2) -> None:
        ready, _, _ = select.select([self.master_fd], [], [], timeout)
        if not ready:
            return
        try:
            chunk = os.read(self.master_fd, 65_536)
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise
            return
        if chunk:
            self.raw.extend(chunk)

    def wait_for(self, expected: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._drain()
            rendered = _plain(bytes(self.raw))
            if expected in rendered:
                return rendered
            if self.proc.poll() is not None:
                break
        rendered = _plain(bytes(self.raw))
        pytest.fail(
            f"OpenCode TUI did not produce {expected!r} within {timeout}s; "
            f"rc={self.proc.poll()}\n--- TUI tail ---\n{rendered[-6000:]}"
        )

    def prompt(self, text: str, expected: str) -> str:
        before = len(self.raw)
        prior_exits = _plain(bytes(self.raw)).count('message="exiting loop"')
        os.write(self.master_fd, text.encode("utf-8") + b"\r")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            self._drain()
            rendered = _plain(bytes(self.raw))
            if rendered.count('message="exiting loop"') > prior_exits:
                break
            if self.proc.poll() is not None:
                pytest.fail(
                    f"OpenCode TUI exited during prompt; rc={self.proc.poll()}\n"
                    f"--- TUI tail ---\n{rendered[-6000:]}"
                )
        else:
            pytest.fail(
                "OpenCode TUI did not finish the prompt within 120s\n"
                f"--- TUI tail ---\n{rendered[-6000:]}"
            )
        segment = _plain(bytes(self.raw[before:]))
        assert _compact(expected) in _compact(segment), (
            f"Expected answer {expected!r} was not rendered for the prompt\n"
            f"--- prompt segment ---\n{segment[-6000:]}"
        )
        time.sleep(1)
        self._drain()
        return segment

    def close(self) -> None:
        if self.proc.poll() is None:
            os.write(self.master_fd, b"\x03")
            time.sleep(0.5)
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(timeout=5)
        os.close(self.master_fd)


@pytest.mark.skipif(OPENCODE is None, reason="opencode binary not on PATH")
@pytest.mark.timeout(420)
@pytest.mark.xdist_group("opencode-tui-permissions")
def test_tui_handles_multiple_permissioned_tool_prompts() -> None:
    """A persistent TUI can read, grep, and run an allowed Make target."""
    tui = _Tui()
    try:
        tui.wait_for("OpenCode", timeout=30)
        read_segment = tui.prompt(
            "Use the read tool to inspect pyproject.toml, then reply with only "
            "the value of project.name.",
            "general-ludd-agent",
        )
        assert "permission=read" in read_segment
        assert "action.action=allow" in read_segment
        assert f"file={ROOT / 'pyproject.toml'}" in read_segment

        grep_segment = tui.prompt(
            "Use the grep tool to locate the exact `authors =` declaration in "
            "pyproject.toml, then use the read tool on that matching line and "
            "reply with only the author name, not its line number.",
            "General Ludd Team",
        )
        assert "permission=grep" in grep_segment
        assert "action.action=allow" in grep_segment

        bash_segment = tui.prompt(
            "Use the bash tool to run make version, then reply with only the "
            "version value printed by that command.",
            "0.1.0-beta.3",
        )
        assert "permission=bash" in bash_segment
        assert "action.action=allow" in bash_segment

        denied_segment = tui.prompt(
            "Use the bash tool to run pwd exactly once. If OpenCode denies the "
            "command, explain that briefly.",
            "action.action=deny",
        )
        assert "permission=bash" in denied_segment
        assert "action.action=deny" in denied_segment
    finally:
        tui.close()
