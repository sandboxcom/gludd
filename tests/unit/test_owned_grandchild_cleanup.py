"""Owned observed-command descendant cleanup and controller dependency boundaries."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import stream_command as owner
else:
    from scripts import stream_command as owner


@pytest.mark.parametrize("leader_exits_first", (False, True))
def test_timeout_kills_owned_term_ignoring_grandchild_even_after_leader_exit(
    tmp_path: Path, leader_exits_first: bool,
) -> None:
    ready = tmp_path / "gludd-grandchild-ready"
    grandchild = (
        "import os, signal, socket, sys, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "sock=socket.socket(); sock.bind(('127.0.0.1', 0)); "
        "Path(sys.argv[1]).write_text(str(os.getpid())+' '+str(sock.getsockname()[1])); "
        "time.sleep(30)"
    )
    leader = (
        "import os, subprocess, sys, time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}, sys.argv[1]]); "
        "\nwhile not Path(sys.argv[1]).exists(): time.sleep(0.01)\n"
        + ("os._exit(0)\n" if leader_exits_first else "time.sleep(30)\n")
    )
    # This unrelated session is a refusal control: cleanup owns only the lint session.
    with subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True,
    ) as unrelated:
        pid = 0
        try:
            started = time.monotonic()
            result = owner.stream_command(
                [sys.executable, "-c", leader, str(ready)], tmp_path / "bounded.log",
                max_seconds=2, termination_grace_seconds=0.1, quiet=True,
            )
            assert result == owner.TIMEOUT_FAILURE
            assert time.monotonic() - started < 5
            pid_text, port_text = ready.read_text().split()
            pid = int(pid_text)
            assert unrelated.poll() is None, "an unrelated process must never be signalled"
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", int(port_text)))
                except OSError as exc:
                    pytest.fail(f"owned TERM-ignoring grandchild remains after timeout: {exc}")
        finally:
            if pid == 0 and ready.exists():
                pid = int(ready.read_text().split()[0])
            if pid:
                with suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
            unrelated.terminate()
            unrelated.wait(timeout=5)


def test_controller_status_helpers_do_not_require_test_environment_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runpy

    monkeypatch.setitem(sys.modules, "psutil", None)
    namespace = runpy.run_path(owner.__file__, run_name="gludd_controller_status")
    assert callable(namespace["_atomic_write_json"])
