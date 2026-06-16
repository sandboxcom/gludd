"""Parent-side runner for crash-isolated A/B candidate execution.

``run_candidate_in_subprocess`` spawns a FRESH interpreter child
(``python -m general_ludd.abtest._child``) — it NEVER imports candidate code
into this process. The parent observes the child's exit status and a
parent-controlled result FILE, then maps that to a ``Result``:

  * exit 0 AND the result file contains the parent's per-run NONCE  ⇒ ok=True
  * wall-clock timeout                                              ⇒ timed_out, crashed
  * negative return code (killed by signal N)                       ⇒ signal=N, crashed
  * positive non-zero return code                                   ⇒ crashed
  * exit 0 but result file missing / nonce wrong                    ⇒ crashed (fail-closed)

Fail-closed is the whole point, and the success signal is UNFORGEABLE by the
candidate: the parent generates a fresh random nonce per run
(``secrets.token_hex``) and a dedicated result-file path, and hands BOTH to the
child. The child framework writes the nonce into that file ONLY after the
workload's post-import assertions pass — strictly AFTER the candidate module
body has run. A malicious candidate that writes ``RESULT_OK`` to stdout and
``os._exit(0)``s at import time dies before the framework's nonce-write, so the
result file never receives the nonce and the parent fails closed. ``exit 0`` is
kept as a necessary-but-insufficient signal; it is also candidate-controllable,
so it can never alone yield ``ok=True``.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

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


def _result_nonce_matches(result_path: str, expected_nonce: str) -> bool:
    """Return True iff the child wrote the parent's exact ``expected_nonce`` into
    ``result_path``.

    The result file is parent-created and parent-named; only the child framework
    (``_child._write_result_nonce``), running AFTER the candidate import and the
    workload's assertions, writes the nonce. A candidate that ``os._exit``s at
    import time never reaches that write, so the file stays empty/absent and this
    returns False (fail-closed). The comparison is constant-time to avoid leaking
    nonce bytes via timing (defensive; the nonce is single-use per run anyway).
    """
    try:
        with open(result_path, "rb") as fh:
            raw = fh.read(_MAX_OUTPUT_BYTES)
    except OSError:
        return False
    if not raw:
        return False
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeError):
        return False
    got = parsed.get("nonce") if isinstance(parsed, dict) else None
    if not isinstance(got, str):
        return False
    return secrets.compare_digest(got, expected_nonce)


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

    # Per-run UNFORGEABLE success token. The parent generates a fresh random
    # nonce (no Math.random/Date — cryptographic RNG) and a dedicated result
    # file. The child framework writes the nonce into that file ONLY after the
    # candidate has been imported AND the workload's assertions pass. The
    # candidate body cannot know the nonce or reach the framework's write, so it
    # cannot forge success.
    nonce = secrets.token_hex(32)
    result_fd, result_path = tempfile.mkstemp(prefix="abtest_result_", suffix=".json")
    os.close(result_fd)

    cmd = [
        sys.executable,
        "-m",
        "general_ludd.abtest._child",
        root_str,
        json.dumps(workload),
        str(mem_limit_mb),
        str(cpu_seconds),
        result_path,
        nonce,
    ]

    try:
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

        # AUTHORITATIVE check: the result file must contain the exact nonce the
        # parent generated. exit 0 is necessary-but-insufficient. The stdout
        # ``RESULT_OK`` line is NOT consulted — it rides candidate-writable
        # stdout and is forgeable.
        nonce_ok = _result_nonce_matches(result_path, nonce)
        if returncode == 0 and nonce_ok:
            return Result(
                ok=True,
                crashed=False,
                timed_out=False,
                exit_code=0,
                output=output,
                duration_s=duration,
            )

        # Non-zero exit, OR a zero exit without the framework-written nonce —
        # fail closed.
        return Result(
            ok=False,
            crashed=True,
            timed_out=False,
            exit_code=returncode,
            output=output,
            duration_s=duration,
        )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(result_path)
