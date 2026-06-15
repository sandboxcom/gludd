"""A/B comparison: run a baseline (A) and a candidate (B) under the same
workload in isolated children, then decide whether B may be promoted.

Promotion is deliberately conservative (fail-closed):

  * A (the current/baseline code) MUST pass — if the baseline itself can't run
    the workload, the comparison is meaningless and B is never promoted.
  * B passes ONLY if it ran ok, did not crash, did not time out, and finished
    within a duration SLACK multiple of A (no large performance regression).

Any crash/timeout in B ⇒ ``promote=False``. This is the gate the Ansible
``self_improve_ab_test`` role keys off; a variant built to crash cannot be
promoted because the crash is observed as ``crashed=True`` in an isolated child.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from general_ludd.abtest.runner import Result, run_candidate_in_subprocess
from general_ludd.abtest.workloads import Workload

# B may be at most this multiple of A's duration (plus a small floor) before it
# counts as a performance regression that blocks promotion.
_DURATION_SLACK = 3.0
_DURATION_FLOOR_S = 0.5


@dataclass
class ABVerdict:
    a: Result
    b: Result
    promote: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        def _r(r: Result) -> dict[str, object]:
            return {
                "ok": r.ok,
                "crashed": r.crashed,
                "timed_out": r.timed_out,
                "exit_code": r.exit_code,
                "signal": r.signal,
                "duration_s": round(r.duration_s, 4),
            }

        return {
            "a": _r(self.a),
            "b": _r(self.b),
            "promote": self.promote,
            "reason": self.reason,
        }


def decide(a: Result, b: Result) -> tuple[bool, str]:
    """Pure promotion decision over two results (no I/O)."""
    if not a.ok or a.crashed:
        return False, "baseline (A) did not pass — comparison invalid"
    if b.crashed:
        return False, "candidate (B) crashed"
    if b.timed_out:
        return False, "candidate (B) timed out"
    if not b.ok:
        return False, "candidate (B) did not report ok"
    budget = max(a.duration_s * _DURATION_SLACK, _DURATION_FLOOR_S)
    if b.duration_s > budget:
        return False, (
            f"candidate (B) too slow: {b.duration_s:.3f}s > budget {budget:.3f}s"
        )
    return True, "candidate (B) ok and within duration slack"


def run_ab(
    baseline_root: str | os.PathLike[str],
    candidate_root: str | os.PathLike[str],
    workload: Workload,
    timeout: float = 60.0,
    mem_limit_mb: int = 512,
) -> ABVerdict:
    """Run baseline A then candidate B against ``workload`` in isolated children
    and return the promotion verdict."""
    a = run_candidate_in_subprocess(
        baseline_root, workload, timeout=timeout, mem_limit_mb=mem_limit_mb
    )
    b = run_candidate_in_subprocess(
        candidate_root, workload, timeout=timeout, mem_limit_mb=mem_limit_mb
    )
    promote, reason = decide(a, b)
    return ABVerdict(a=a, b=b, promote=promote, reason=reason)
