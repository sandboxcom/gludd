#!/usr/bin/env python3
"""Portable counting semaphore for memory-heavy build operations.

Caps how many MEMORY-heavy operations (pytest-with-xdist, mypy/typecheck) run
concurrently across ALL agents on this host, using ``fcntl.flock`` so it works
identically on macOS and Linux. The old flock-binary wrapper (commit 2d43c54)
was a SILENT NO-OP on macOS, which ships no ``flock`` binary — every concurrent
heavy op ran uncapped and overloaded the machine. ``fcntl.flock`` is in the
Python stdlib on every POSIX host and is always reachable here via ``uv run`` /
``python3``, so the cap is now actually enforced on the dev host too.

Usage::

    python3 scripts/heavy_sem.py <N> <slot_prefix> -- <cmd> [args...]

Mechanism (N-slot pool under /tmp):
  * For i in 0..N-1: open ``/tmp/<slot_prefix>.<i>.lock`` (O_CREAT) and try a
    NON-BLOCKING ``flock(LOCK_EX | LOCK_NB)``. The first free slot wins and we
    keep its fd open.
  * If every slot is busy, BLOCK on slot 0 with a bounded wait (repeated
    LOCK_NB probes with small sleeps, ceiling ~1800s) so a waiter queues
    instead of piling on — and can NEVER hang forever (after the ceiling it
    just proceeds uncapped).
  * Once a slot is held we DO NOT close the fd; we ``os.execvp`` the command so
    it REPLACES this process while keeping the lock fd open. The kernel
    auto-releases the lock the instant the command exits — even on kill/OOM —
    so there are no stale locks and no deadlocks. (fds survive execvp by
    default; that is precisely the point.)
  * Defensive: ANY error acquiring a slot -> just exec the command uncapped.
    The cap is a host-protection optimization, never a correctness gate, so a
    semaphore bug must never block the build.

Because the held fd is inherited across ``execvp`` and the kernel drops the
``flock`` when the last fd referencing the open file description closes (process
exit), the slot is freed automatically with zero cleanup code.
"""

from __future__ import annotations

import fcntl
import os
import sys
import time

# Bounded wait ceiling (seconds) when all slots are busy. After this we proceed
# uncapped rather than let a waiter hang forever.
_MAX_WAIT_SECS = 1800
_POLL_SECS = 0.5


def _eprint(msg: str) -> None:
    """One-line stderr note so the operator can SEE the cap working (the old
    flock-binary version was a silent no-op on macOS)."""
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _exec(cmd: list[str]) -> "int":
    """Replace this process with ``cmd`` (keeps any held lock fd open)."""
    try:
        os.execvp(cmd[0], cmd)
    except OSError as exc:  # cmd not found / not executable
        _eprint(f"heavy_sem: exec failed: {exc}")
        return 127
    return 0  # unreachable on success (execvp does not return)


def main(argv: list[str]) -> int:
    # Parse: <N> <slot_prefix> -- <cmd...>
    if len(argv) < 4 or "--" not in argv:
        _eprint(
            "usage: heavy_sem.py <N> <slot_prefix> -- <cmd> [args...]"
        )
        return 2

    sep = argv.index("--")
    head = argv[:sep]
    cmd = argv[sep + 1 :]
    if len(head) < 2 or not cmd:
        _eprint("usage: heavy_sem.py <N> <slot_prefix> -- <cmd> [args...]")
        return 2

    try:
        n = int(head[0])
    except ValueError:
        _eprint(f"heavy_sem: bad slot count {head[0]!r}; running uncapped")
        return _exec(cmd)
    prefix = head[1]
    if n < 1:
        n = 1

    tmp = os.environ.get("TMPDIR", "/tmp").rstrip("/") or "/tmp"

    # --- Phase 1: non-blocking probe for any free slot ---------------------
    try:
        for i in range(n):
            path = f"{tmp}/{prefix}.{i}.lock"
            try:
                fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
            except OSError as exc:
                _eprint(f"heavy_sem: open {path} failed ({exc}); trying next")
                continue
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                continue
            # Got it. Intentionally LEAK fd into execvp so the lock auto-frees
            # on command exit. (Do NOT set FD_CLOEXEC.)
            _eprint(f"heavy_sem: slot {i}/{n} acquired ({prefix})")
            return _exec(cmd)

        # --- Phase 2: all busy -> bounded block on slot 0 ------------------
        path0 = f"{tmp}/{prefix}.0.lock"
        fd0 = os.open(path0, os.O_CREAT | os.O_RDWR, 0o644)
        _eprint(
            f"heavy_sem: all {n} slots busy ({prefix}); "
            f"waiting up to {_MAX_WAIT_SECS}s for slot 0"
        )
        deadline = time.monotonic() + _MAX_WAIT_SECS
        while True:
            try:
                fcntl.flock(fd0, fcntl.LOCK_EX | fcntl.LOCK_NB)
                _eprint(f"heavy_sem: slot 0/{n} acquired after wait ({prefix})")
                return _exec(cmd)
            except OSError:
                if time.monotonic() >= deadline:
                    _eprint(
                        f"heavy_sem: wait ceiling reached ({prefix}); "
                        "proceeding uncapped"
                    )
                    os.close(fd0)
                    return _exec(cmd)
                time.sleep(_POLL_SECS)
    except Exception as exc:  # noqa: BLE001 - any sem bug must not block build
        _eprint(f"heavy_sem: error ({exc}); running uncapped")
        return _exec(cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
