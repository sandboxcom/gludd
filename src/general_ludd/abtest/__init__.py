"""Crash-isolated A/B testing primitive for self-modifying code.

A candidate code variant is run in a FRESH interpreter child process (never
imported into the parent/daemon), under resource limits, so a variant that is
built to crash the whole process — ``os._exit``, a segfault, an infinite loop,
or an OOM allocation — CANNOT take down the parent. The parent observes only
the child's exit status and a bounded slice of its output, and fails closed:
only an explicit ``RESULT_OK`` sentinel printed after the workload's assertions
pass yields ``ok=True``.
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
