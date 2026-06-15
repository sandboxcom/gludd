"""Fresh-interpreter child entrypoint for the A/B runner.

Invoked as ``python -m general_ludd.abtest._child <candidate_root> <workload_json>``.

The child:
  1. inserts ``<candidate_root>/src`` at the FRONT of ``sys.path`` so the
     candidate variant shadows the installed copy,
  2. applies POSIX resource limits (address space + CPU time) when available,
  3. runs the workload (which imports/exercises the candidate),
  4. prints the literal sentinel line ``RESULT_OK`` followed by a JSON line
     ONLY after the workload returns without raising.

If the workload raises, the traceback goes to stderr and the child exits
non-zero WITHOUT printing the sentinel. If the candidate calls ``os._exit`` or
segfaults, the interpreter dies before the sentinel is printed. Either way the
parent fails closed: no sentinel ⇒ not ok.

Nothing in this module is imported by the parent runner at runtime — it only
ever runs inside the child interpreter.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from general_ludd.abtest.workloads import RESULT_SENTINEL as SENTINEL


def _apply_limits(mem_limit_mb: int, cpu_seconds: int) -> None:
    """Best-effort POSIX resource limits. No-op where ``resource`` or a given
    limit is unavailable (e.g. Windows), so the runner still works there with
    only the wall-clock timeout as the backstop."""
    try:
        import resource
    except ImportError:
        return

    if mem_limit_mb > 0 and hasattr(resource, "RLIMIT_AS"):
        nbytes = mem_limit_mb * 1024 * 1024
        try:
            _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            new_hard = nbytes if hard == resource.RLIM_INFINITY else min(nbytes, hard)
            resource.setrlimit(resource.RLIMIT_AS, (nbytes, new_hard))
        except (ValueError, OSError):
            pass

    if cpu_seconds > 0 and hasattr(resource, "RLIMIT_CPU"):
        try:
            _soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
            new_hard = (
                cpu_seconds if hard == resource.RLIM_INFINITY else min(cpu_seconds, hard)
            )
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, new_hard))
        except (ValueError, OSError):
            pass


def _run_workload(workload: dict[str, Any]) -> dict[str, Any]:
    """Execute the workload spec. Raises on any failure; returns a small
    JSON-safe detail dict on success."""
    kind = workload.get("kind")
    if kind == "import_module":
        module_name = workload["module"]
        import importlib

        mod = importlib.import_module(module_name)
        expect_attr = workload.get("expect_attr")
        if expect_attr is not None and not hasattr(mod, expect_attr):
            raise AssertionError(
                f"candidate module {module_name} missing attr {expect_attr!r}"
            )
        return {"imported": module_name}
    raise ValueError(f"unknown workload kind: {kind!r}")


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        sys.stderr.write("usage: _child <candidate_root> <workload_json> [mem_mb] [cpu_s]\n")
        return 2

    candidate_root = argv[1]
    workload = json.loads(argv[2])
    mem_limit_mb = int(argv[3]) if len(argv) > 3 else 0
    cpu_seconds = int(argv[4]) if len(argv) > 4 else 0

    _apply_limits(mem_limit_mb, cpu_seconds)

    # Candidate src shadows the installed package.
    candidate_src = candidate_root.rstrip("/") + "/src"
    sys.path.insert(0, candidate_src)

    try:
        detail = _run_workload(workload)
    except BaseException:  # report ANY failure (incl. SystemExit) as non-OK
        traceback.print_exc()
        return 1

    # Sentinel printed ONLY after the workload's assertions passed.
    sys.stdout.write(SENTINEL + "\n")
    sys.stdout.write(json.dumps(detail) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
