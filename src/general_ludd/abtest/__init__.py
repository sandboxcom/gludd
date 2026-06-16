"""Crash-isolated A/B testing primitive for self-modifying code.

A candidate code variant is run in a FRESH interpreter child process (never
imported into the parent/daemon), under resource limits, so a variant that is
built to crash the whole process — ``os._exit``, a segfault, an infinite loop,
or an OOM allocation — CANNOT take down the parent. The parent observes the
child's exit status and a parent-controlled result file, and fails closed: only
exit 0 PLUS the child framework writing the parent's per-run random nonce into
that file — which it does ONLY after the workload's assertions pass — yields
``ok=True``. The nonce rides a dedicated parent-named channel the candidate
cannot reach, so a candidate cannot forge success on its own stdout.
"""

from __future__ import annotations

from general_ludd.abtest.compare import ABVerdict, run_ab
from general_ludd.abtest.runner import Result, run_candidate_in_subprocess
from general_ludd.abtest.workloads import import_module_workload

__all__ = [
    "ABVerdict",
    "Result",
    "import_module_workload",
    "run_ab",
    "run_candidate_in_subprocess",
]
