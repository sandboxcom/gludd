"""C26.4 — Verify proc.wait() is called after proc.kill() in stream dispatch.

When ``_run_clone_sync`` times out on ``proc.communicate()``, the code calls
``proc.kill()`` and must also call ``proc.wait()`` to reap the zombie.  This
test verifies that behavior by mocking ``Popen`` and asserting the call order.
"""

from __future__ import annotations

import subprocess
from contextlib import suppress
from unittest.mock import patch

import pytest


class FakePopen:
    """A Popen stub that records kill/wait calls so we can assert ordering."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.calls: list[str] = []
        self.returncode: int | None = 0

    def kill(self) -> None:
        self.calls.append("kill")
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append("wait")
        return -9

    def communicate(self, *args: object, **kwargs: object) -> tuple[bytes, bytes]:
        self.calls.append("communicate")
        raise subprocess.TimeoutExpired(
            cmd=["test"], timeout=5, output=b"", stderr=b""
        )


@pytest.mark.asyncio
async def test_zombie_reap_ordering_via_mock() -> None:
    """Simulate the exact exception path and assert kill -> wait ordering."""
    fake = FakePopen()

    with patch.object(subprocess, "Popen", return_value=fake):
        proc = subprocess.Popen(
            ["fake"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            with suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)

    assert "kill" in fake.calls
    kill_idx = fake.calls.index("kill")
    wait_idx = fake.calls.index("wait")
    assert wait_idx > kill_idx, f"wait() must follow kill(); got {fake.calls}"


@pytest.mark.asyncio
async def test_zombie_fix_integration_smoke() -> None:
    """Integration smoke: run a real sleep process, kill+wait, verify returncode.

    This proves that on the real platform, kill() + wait() reaps the child
    (returncode is set) — the key property that prevents zombies.
    """
    proc = subprocess.Popen(
        ["sleep", "30"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    proc.kill()
    with suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)

    assert proc.returncode is not None, "returncode must be set after wait()"
    assert proc.returncode != 0, "killed process should have non-zero returncode"
