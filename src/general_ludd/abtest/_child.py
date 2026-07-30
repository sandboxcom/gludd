"""Fresh-interpreter child entrypoint for the A/B runner.

Invoked as ``python -m general_ludd.abtest._child <candidate_root>
<workload_json> <mem_mb> <cpu_s> <result_path> <nonce>``.

The child:
  1. inserts ``<candidate_root>/src`` at the FRONT of ``sys.path`` so the
     candidate variant shadows the installed copy,
  2. applies POSIX resource limits (address space + CPU time) when available,
  3. runs the workload (which imports/exercises the candidate),
  4. writes the parent-generated NONCE into the parent-created result file, and
     prints the literal sentinel line ``RESULT_OK`` followed by a JSON line,
     ONLY after the workload returns without raising.

The result-file nonce — NOT the stdout sentinel — is the authoritative success
signal. The parent generates a fresh random nonce per run and hands the child
both the nonce and a dedicated result-file path that the candidate body cannot
observe. The candidate body runs during step 3 (import), strictly BEFORE the
framework writes the nonce in step 4. A candidate that forges ``RESULT_OK`` onto
stdout and ``os._exit(0)``s dies before step 4 ever runs, so the result file
never receives the nonce and the parent fails closed.

If the workload raises, the traceback goes to stderr and the child exits
non-zero WITHOUT writing the nonce. If the candidate calls ``os._exit`` or
segfaults, the interpreter dies before the nonce is written. Either way the
parent fails closed: no nonce in the result file ⇒ not ok.

Nothing in this module is imported by the parent runner at runtime — it only
ever runs inside the child interpreter.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import cast

from general_ludd.abtest.workloads import RESULT_SENTINEL as SENTINEL
from general_ludd.system.rlimit import apply_limits


def _apply_limits(mem_limit_mb: int, cpu_seconds: int) -> None:
    """Best-effort POSIX resource limits. No-op where ``resource`` or a given
    limit is unavailable (e.g. Windows), so the runner still works there with
    only the wall-clock timeout as the backstop.

    Thin wrapper preserved for the A/B child's local naming; the implementation
    now lives in the shared ``general_ludd.system.rlimit`` module so the same
    clamped, fail-open logic can back the adaptive test runner too."""
    apply_limits(mem_limit_mb, cpu_seconds)


def _run_workload(workload: dict[str, object]) -> dict[str, object]:
    """Execute the workload spec. Raises on any failure; returns a small
    JSON-safe detail dict on success."""
    kind = workload.get("kind")
    if kind == "import_module":
        module_name = cast(str, workload["module"])
        import importlib

        mod = importlib.import_module(module_name)
        expect_attr = cast(str | None, workload.get("expect_attr"))
        if expect_attr is not None and not hasattr(mod, expect_attr):
            raise AssertionError(
                f"candidate module {module_name} missing attr {expect_attr!r}"
            )
        return {"imported": module_name}
    raise ValueError(f"unknown workload kind: {kind!r}")


def _write_result_nonce(result_path: str, nonce: str, detail: dict[str, object]) -> None:
    """Write the parent-generated ``nonce`` (plus a JSON detail blob) into the
    parent-created ``result_path``.

    This runs ONLY after ``_run_workload`` returns without raising, i.e. strictly
    AFTER the candidate body has been imported and the workload's assertions have
    passed. The candidate body cannot reach this code: a forged stdout sentinel +
    ``os._exit(0)`` from the candidate terminates the interpreter before we get
    here, leaving the result file without the nonce so the parent fails closed.
    """
    import os

    payload = json.dumps({"nonce": nonce, "detail": detail})
    # Write atomically (tmp + os.replace) so the parent never reads a partial
    # nonce, and fsync so the bytes are durable before the child exits 0.
    tmp_path = result_path + ".tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp_path, result_path)


def main(
    argv: list[str], *, apply_resource_limits: bool = False
) -> int:
    """Execute one child workload.

    Direct Python callers default to *not* applying process-wide resource
    limits. POSIX hard limits are irreversible for an unprivileged process, so
    applying them during an in-process API call would poison the caller after
    this function returned. The module entrypoint below explicitly opts in;
    that path always runs in the fresh interpreter created by
    :func:`run_candidate_in_subprocess`.
    """
    # argv: <prog> <candidate_root> <workload_json> <mem_mb> <cpu_s>
    #       <result_path> <nonce>
    if len(argv) < 7:
        sys.stderr.write(
            "usage: _child <candidate_root> <workload_json> <mem_mb> <cpu_s> "
            "<result_path> <nonce>\n"
        )
        return 2

    candidate_root = argv[1]
    try:
        workload = json.loads(argv[2])
    except json.JSONDecodeError:
        sys.stderr.write(f"invalid workload JSON: {argv[2]!r}\n")
        return 1
    mem_limit_mb = int(argv[3])
    cpu_seconds = int(argv[4])
    result_path = argv[5]
    nonce = argv[6]

    if apply_resource_limits:
        _apply_limits(mem_limit_mb, cpu_seconds)
    # Candidate src shadows the installed package.
    candidate_src = candidate_root.rstrip("/") + "/src"
    sys.path.insert(0, candidate_src)

    try:
        detail = _run_workload(workload)
    except BaseException:  # report ANY failure (incl. SystemExit) as non-OK
        traceback.print_exc()
        return 1

    # AUTHORITATIVE success signal: write the parent's nonce into the parent's
    # result file, ONLY after the workload's assertions passed. A candidate that
    # never let _run_workload return cannot have reached this line.
    try:
        _write_result_nonce(result_path, nonce, detail)
    except OSError:
        traceback.print_exc()
        return 1

    # Stdout sentinel kept for human-readable logs only; the parent no longer
    # trusts it as proof of success (it rides candidate-writable stdout).
    sys.stdout.write(SENTINEL + "\n")
    sys.stdout.write(json.dumps(detail) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv, apply_resource_limits=True))
