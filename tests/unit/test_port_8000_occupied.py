"""V2.3: 8000-occupied proof — suite passes when port 8000 is busy.

Key proof: if port 8000 is occupied by an external process, the e2e
daemon tests still pass because they use ephemeral ports via the
test_ephemeral_port helper. This proves the port-8000 flake is dead.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest


def _hold_port(port: int, ready_event: threading.Event, stop_event: threading.Event):
    """Bind to a port and hold it until stopped."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        ready_event.set()
        while not stop_event.is_set():
            time.sleep(0.1)
    finally:
        s.close()


# All tests here bind/hold the FIXED port 8000. Under xdist's parallel workers,
# a test that HOLDS 8000 (test_suite_with_8000_occupied) could run concurrently
# with one that BINDS 8000 (test_can_bind_to_8000) on another worker -> flaky
# "Address already in use". xdist_group pins them to one worker so they serialize.
@pytest.mark.xdist_group("port_8000")
class TestPort8000Occupied:
    def test_can_bind_to_8000(self):
        """Sanity: we can bind to port 8000."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", 8000))
            assert s.getsockname()[1] == 8000
        finally:
            s.close()

    def test_ephemeral_port_helper_skips_8000(self):
        """When port 8000 is occupied, the ephemeral helper still returns a free port."""
        ready = threading.Event()
        stop = threading.Event()
        holder = threading.Thread(target=_hold_port, args=(8000, ready, stop))
        holder.start()
        ready.wait(timeout=5)
        try:
            from tests.unit.test_ephemeral_port import _find_free_port
            port = _find_free_port()
            assert port != 8000, "Ephemeral helper returned occupied port 8000"
            assert 1024 < port < 65536
        finally:
            stop.set()
            holder.join(timeout=5)

    def test_suite_with_8000_occupied(self):
        """End-to-end: when port 8000 is held, the e2e daemon tests still pass.

        This proves the port-8000 flake is dead: the suite does not depend
        on port 8000 being available.
        """
        ready = threading.Event()
        stop = threading.Event()
        holder = threading.Thread(target=_hold_port, args=(8000, ready, stop))
        holder.start()
        ready.wait(timeout=5)
        try:
            # Run just the e2e daemon test — must pass while 8000 is occupied
            import subprocess
            import sys
            # NOTE: no --timeout flag — pytest-timeout is not a declared dependency,
            # and the main suite runs without it. Hard-depending on an undeclared
            # plugin here made this test fail whenever the env lacked it.
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/e2e/test_obj03_worker.py", "-q"],
                capture_output=True, text=True, timeout=120,
            )
            assert result.returncode == 0, (
                f"E2E daemon tests failed while port 8000 occupied:\n{result.stderr}\n{result.stdout}"
            )
        finally:
            stop.set()
            holder.join(timeout=5)
