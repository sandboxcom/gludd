"""Parent-side runner for crash-isolated A/B candidate execution.

``run_candidate_in_subprocess`` spawns a FRESH interpreter child
(``python -m general_ludd.abtest._child``) — it NEVER imports candidate code
into this process. The parent observes only the child's exit status and a
bounded slice of its combined output, then maps that to a ``Result``:

  * exit 0 AND the explicit ``RESULT_OK`` sentinel present  ⇒ ok=True
  * wall-clock timeout                                       ⇒ timed_out, crashed
  * negative return code (killed by signal N)                ⇒ signal=N, crashed
  * positive non-zero return code                            ⇒ crashed
  * exit 0 but NO sentinel                                   ⇒ crashed (fail-closed)

Fail-closed is the whole point: the only path to ``ok=True`` is the child
printing the sentinel after its workload's assertions pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass

from general_ludd.abtest.workloads import RESULT_SENTINEL as SENTINEL
from general_ludd.abtest.workloads import Workload

# Bounded output capture so a chatty/looping candidate cannot blow up memory.
_MAX_OUTPUT_BYTES = 16 * 1024


@dataclass
class Result:
    """Outcome of running one candidate variant in an isolated child."""

    ok: bool
    crashed: bool
    timed_out: bool
    exit_code: int | None
    output: str
    duration_s: float
    signal: int | None = None


def _bounded_tail(text: str) -> str:
    data = text.encode("utf-8", errors="replace")
    if len(data) > _MAX_OUTPUT_BYTES:
        data = data[-_MAX_OUTPUT_BYTES:]
    return data.decode("utf-8", errors="replace")


def run_candidate_in_subprocess(
    candidate_root: str | os.PathLike[str],
    workload: Workload,
    timeout: float = 60.0,
    mem_limit_mb: int = 512,
) -> Result:
    """Run ``workload`` against the candidate at ``candidate_root`` in a fresh
    isolated child interpreter and return a :class:`Result`.

    ``candidate_root`` is the directory whose ``src`` holds the variant; it may
    be a ``str`` or ``os.PathLike``. The candidate is NOT imported into this
    process.
    """
    root_str = str(candidate_root)
    # CPU limit is a coarse backstop for runaway compute; the wall-clock
    # timeout below is the authoritative deadline.
    cpu_seconds = max(1, int(timeout) + 1)

    cmd = [
        sys.executable,
        "-m",
        "general_ludd.abtest._child",
        root_str,
        json.dumps(workload),
        str(mem_limit_mb),
        str(cpu_seconds),
    ]

    start = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        timed_out = True
    duration = time.monotonic() - start

    output = _bounded_tail(out or "")
    returncode = proc.returncode

    if timed_out:
        return Result(
            ok=False,
            crashed=True,
            timed_out=True,
            exit_code=returncode,
            output=output,
            duration_s=duration,
            signal=-returncode if (returncode is not None and returncode < 0) else None,
        )

    if returncode is not None and returncode < 0:
        # Killed by a signal (e.g. SIGSEGV ⇒ -11).
        return Result(
            ok=False,
            crashed=True,
            timed_out=False,
            exit_code=returncode,
            output=output,
            duration_s=duration,
            signal=-returncode,
        )

    sentinel_present = any(
        line.strip() == SENTINEL for line in output.splitlines()
    )
    if returncode == 0 and sentinel_present:
        return Result(
            ok=True,
            crashed=False,
            timed_out=False,
            exit_code=0,
            output=output,
            duration_s=duration,
        )

    # Non-zero exit, OR a zero exit with no sentinel — fail closed.
    return Result(
        ok=False,
        crashed=True,
        timed_out=False,
        exit_code=returncode,
        output=output,
        duration_s=duration,
    )
