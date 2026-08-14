"""Actual OpenCode binary boot checks using the supported serve path.

OpenCode 1.17.9 has an upstream crash in ``opencode run``. ``opencode serve``
uses the same plugin loader as the TUI and provides a stable real-binary probe.
"""

import os
import re
import socket
import subprocess
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OPENCODE_BIN = "opencode"
pytestmark = pytest.mark.xdist_group("opencode-live")

PLUGIN_LOAD_FAILED_RE = re.compile(r'failed to load plugin.*error="([^"]+)"')
PLUGIN_HOOK_FAILED_RE = re.compile(r'plugin \w+ hook failed.*error="([^"]+)"')
EVENT_LISTENER_FAILED_RE = re.compile(r'Event listener failed.*cause="([^"]+)"')
UNEXPECTED_SERVER_ERROR_RE = re.compile(r"ref=(err_\w+)")
CRASH_SIGNATURES = [
    "undefined is not an object",
    "Cannot read properties of undefined",
    "is not a function",
    "TypeError:",
    "ReferenceError:",
]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _boot_output() -> str:
    env = os.environ.copy()
    env["OPENCODE_SERVER_PASSWORD"] = "test-only"
    command = [
        OPENCODE_BIN,
        "serve",
        "--print-logs",
        "--log-level",
        "ERROR",
        "--hostname",
        "127.0.0.1",
        "--port",
        str(_free_port()),
    ]
    proc = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: list[str] = []
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            line = proc.stdout.readline() if proc.stdout else ""
            if line:
                lines.append(line)
                if "listening" in line.lower() or "server" in line.lower():
                    break
            else:
                time.sleep(0.1)
    finally:
        proc.terminate()
        try:
            remaining, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            remaining, _ = proc.communicate(timeout=5)
    return "".join(lines) + (remaining or "")


@pytest.fixture(scope="module")
def boot_output() -> str:
    return _boot_output()


class TestOpencodeBinaryBoot:
    def test_no_plugin_load_failures(self, boot_output: str) -> None:
        assert not PLUGIN_LOAD_FAILED_RE.findall(boot_output)

    def test_no_plugin_hook_failures(self, boot_output: str) -> None:
        assert not PLUGIN_HOOK_FAILED_RE.findall(boot_output)

    def test_no_event_listener_failures(self, boot_output: str) -> None:
        assert not EVENT_LISTENER_FAILED_RE.findall(boot_output)

    def test_no_crash_signatures(self, boot_output: str) -> None:
        assert not [signature for signature in CRASH_SIGNATURES if signature in boot_output]

    def test_no_unexpected_server_error(self, boot_output: str) -> None:
        assert not UNEXPECTED_SERVER_ERROR_RE.findall(boot_output)
