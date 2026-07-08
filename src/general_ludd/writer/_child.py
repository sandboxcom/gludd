"""Slice-1 STUB child entrypoint for the writer subprocess.

Invoked as::

    python -m general_ludd.writer._child <config_path> <ready_path> <nonce>

The Slice-1 child does only two things:

  1. (Optionally) write the parent-generated ``nonce`` into ``ready_path`` as
     a JSON ``{"nonce": "<hex>"}`` payload — the readiness handshake.
  2. Sleep forever (until killed by the parent's SIGTERM/SIGKILL).

The real EventLoop integration (load config, connect to the broker/WriteQueue,
open a DB write session, drain the queue, etc.) lands in Slice 3 per
``docs/STABILIZATION_PLAN.md`` WP-B1. For Slice 1 the child exists ONLY to
exercise the parent's lifecycle: spawn, handshake, stop.

Test hooks (read from ``config_path``): the test suite may set
``skip_ready=True`` (child never writes the nonce → exercises the parent's
handshake-timeout path) or ``ignore_sigterm=True`` (child installs a no-op
SIGTERM handler → exercises the parent's SIGKILL-escalation path).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from typing import Any


def _load_config(config_path: str) -> dict[str, Any]:
    with open(config_path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"writer child config must be a JSON object, got {type(data)!r}")
    return data


def _write_ready(ready_path: str, nonce: str) -> None:
    """Atomically write ``{"nonce": nonce}`` into ``ready_path``.

    Atomic write (tmp + os.replace) so the parent never reads a partial
    nonce — a partial write would be misread as a non-match and the parent
    would fail-closed unnecessarily on a slow disk.
    """
    payload = json.dumps({"nonce": nonce}).encode("utf-8")
    tmp_path = ready_path + ".tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp_path, ready_path)


def main(argv: list[str]) -> int:
    # argv: <prog> <config_path> <ready_path> <nonce>
    if len(argv) < 4:
        sys.stderr.write(
            "usage: _child <config_path> <ready_path> <nonce>\n"
        )
        return 2

    config_path = argv[1]
    ready_path = argv[2]
    nonce = argv[3]

    config = _load_config(config_path)

    # Test hook: simulate a child that crashes / hangs before completing the
    # readiness handshake. Parent's start() must time out and kill us.
    skip_ready = bool(config.get("skip_ready", False))

    # Test hook: simulate a child that ignores SIGTERM so the parent must
    # escalate to SIGKILL.
    ignore_sigterm = bool(config.get("ignore_sigterm", False))
    if ignore_sigterm:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    if not skip_ready:
        _write_ready(ready_path, nonce)

    # Slice-1 stub behavior: sleep until killed. Slice 3 will replace this
    # with the real broker/queue/EventLoop drain loop.
    try:
        while True:
            time.sleep(3600)
    except InterruptedError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
