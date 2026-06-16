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


def _validate_inputs(
    root_str: str, workload: Workload, mem_limit_mb: int, timeout: float
) -> str:
    """Validate caller/config-supplied values BEFORE any child is spawned and
    return the JSON-serialized workload to ship as a single argv element.

    This is the parent-side trust boundary. Every value here may originate from
    a caller or config and is about to become an element of the child's argv.
    We fail closed (raise ``ValueError``) on anything that could corrupt argv,
    smuggle a Python object across the boundary, or weaken the child's limits:

      * ``root_str`` must be a non-empty, non-whitespace string with NO NUL
        byte. A NUL byte would either truncate the path at the OS layer or make
        ``Popen`` raise; an empty/whitespace path cannot name a real ``src``
        dir. We do NOT shell out (argv is always a list passed to ``Popen``), so
        classic shell-metacharacter injection is structurally impossible — but a
        NUL is still rejected defensively. The child path lands ONLY on the
        child's ``sys.path`` (never the parent's), and a traversal-y root can at
        worst point the child at code the caller already controls, so we do not
        forbid ``..``; we DO forbid the bytes that corrupt argv itself.
      * ``workload`` must be a ``dict`` that round-trips through ``json.dumps``
        (no callables/objects/pickles), and its serialized form must contain no
        NUL byte. We never ship a Python callable across the boundary — that is
        the whole reason workloads are JSON specs (see ``workloads.py``).
      * ``mem_limit_mb`` (and thus the derived CPU budget) must be a
        non-negative ``int`` so a negative limit can't widen the child's cap.
      * ``timeout`` must be a positive, finite number so the wall-clock deadline
        is real.

    Returns the validated ``json.dumps(workload)`` string so the caller builds
    argv from exactly the bytes we checked.
    """
    if not isinstance(root_str, str) or not root_str.strip():
        raise ValueError("candidate_root must be a non-empty path string")
    if "\x00" in root_str:
        raise ValueError("candidate_root must not contain a NUL byte")

    if not isinstance(workload, dict):
        raise ValueError("workload must be a dict (JSON-serializable spec)")
    try:
        # allow_nan=False keeps the shipped JSON strict and portable.
        workload_json = json.dumps(workload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"workload is not JSON-serializable: {exc}") from exc
    if "\x00" in workload_json:
        raise ValueError("workload must not contain a NUL byte")

    if not isinstance(mem_limit_mb, int) or isinstance(mem_limit_mb, bool):
        raise ValueError("mem_limit_mb must be an int")
    if mem_limit_mb < 0:
        raise ValueError("mem_limit_mb must be non-negative")

    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        raise ValueError("timeout must be a number")
    if not (timeout > 0) or timeout != timeout or timeout == float("inf"):
        raise ValueError("timeout must be a positive, finite number")

    return workload_json


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
    # Validate every caller/config-supplied value at the parent-side trust
    # boundary BEFORE spawning anything. Fail closed (ValueError) on argv-
    # corrupting paths, non-serializable workloads, or limit-weakening inputs.
    # Returns the exact JSON we'll ship as the workload argv element.
    workload_json = _validate_inputs(root_str, workload, mem_limit_mb, timeout)

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
        workload_json,
        str(mem_limit_mb),
        str(cpu_seconds),
        result_path,
        nonce,
    ]

    # Defense in depth: argv MUST be a flat list of plain str (no extra
    # injected args, no non-string element that could coerce oddly). This is an
    # invariant, not a recoverable input error — a violation is a bug here, not
    # bad caller input, so it asserts rather than ValueErrors.
    assert isinstance(cmd, list) and len(cmd) == 9
    assert all(isinstance(part, str) for part in cmd)

    try:
        start = time.monotonic()
        # shell=False is the default and is required: argv is passed as a list,
        # so no shell parses it and metacharacters in any element are inert.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=False,
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
