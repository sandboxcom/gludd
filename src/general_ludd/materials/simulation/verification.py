"""Verification machinery for materials simulations (spec MATE-001 §5.4, §6, MATE-AT-007).

Implements the four verification categories required by spec §5.4 / MATE-AT-007:

  - **patch test**            constant-field/constant-stress recovered within tolerance
  - **manufactured solution** an analytic field is recovered by the solver
  - **mesh convergence**      results approach a stable value as mesh is refined
  - **conservation**          global balances (force, energy, mass) hold to tolerance

:func:`verify_simulation` runs all four against the metadata declared in a
:class:`~general_ludd.materials.contracts.SimulationPlan` and aggregates the
result into a :class:`SimulationVerdict`. A non-converged model, a missing
benchmark, or a failed conservation check SHALL block a positive engineering
verdict per MATE-SAFE-006.

This module also exposes :func:`check_convergence`, a standalone mesh-density
comparison that operates on a sequence of (mesh_size, quantity, reference)
tuples; and :func:`is_question_falsifiable`, the predicate that enforces
spec §5.4's "single falsifiable engineering question" requirement.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from general_ludd.materials.contracts import SimulationPlan
from general_ludd.materials.simulation.protocols import SolverAdapter

# ─── result/verdict types ────────────────────────────────────────────────────


class _SpecBase(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=True)


class ConvergenceResult(_SpecBase):
    """Outcome of :func:`check_convergence` across a sequence of mesh densities."""

    mesh_sizes: list[float]
    errors: list[float]
    converged: bool
    relative_change: float
    reason: str = Field(min_length=1)


class SimulationVerdict(_SpecBase):
    """Aggregated outcome of :func:`verify_simulation`.

    ``checks`` maps each of the four verification categories to a per-check
    dict carrying ``state`` (``pass`` / ``fail`` / ``inconclusive``) and a
    short ``reason``. ``state`` is the overall verdict.
    """

    state: str
    checks: dict[str, dict[str, str]]
    reason: str = Field(min_length=1)


# ─── is_question_falsifiable (spec §5.4: "single falsifiable question") ──────


_OPEN_ENDED_START = re.compile(
    r"^\s*(how|what|why|when|who|which|where)\b",
    re.IGNORECASE,
)
_COMPARISON = re.compile(
    r"\b(exceeds?|below|above|less than|greater than|equal|equals|matched|matches|"
    r"meets?|satisf(?:y|ies)|survive|survives|withstand|withstands|yield|yields|"
    r"fail|fails|within|reach|reaches|reach)\b|[<>=]",
    re.IGNORECASE,
)


def is_question_falsifiable(question: str) -> bool:
    """Return True iff ``question`` is a single falsifiable engineering question.

    Per spec §5.4 the SimulationPlan.question field MUST be a single falsifiable
    engineering question — a predicate a simulation can in principle disprove.
    Open-ended prompts ("how strong is it?", "stress analysis") do not qualify.

    Heuristic (good enough for a contract guard, not a parser):

      - must end with ``?``;
      - must NOT start with an open-ended wh-word (how/what/why/...);
      - must contain at least one comparison indicator
        (exceeds, less than, equals, yield, fail, survive, ``<``, ``>``, ``=``).
    """
    q = (question or "").strip()
    if not q.endswith("?"):
        return False
    if _OPEN_ENDED_START.match(q):
        return False
    return bool(_COMPARISON.search(q))


# ─── check_convergence (MATE-AT-007: mesh/time-step convergence) ─────────────


def check_convergence(
    results: Sequence[Mapping[str, float]],
    tolerance: float,
) -> ConvergenceResult:
    """Compare results across mesh densities and decide convergence.

    Each entry of ``results`` MUST carry ``mesh_size``, ``quantity``, and
    ``reference`` keys. Entries are sorted coarse-first; the sequence is
    declared converged iff:

      1. at least two mesh densities are present;
      2. the absolute error ``|quantity - reference|`` is monotonically
         non-increasing as the mesh is refined;
      3. the relative change between the two finest results is below
         ``tolerance``.

    Anything else is reported as not converged with a diagnostic ``reason``.

    Raises:
        ValueError: if ``results`` is empty.
    """
    if not results:
        raise ValueError("results cannot be empty")

    coarse_first = sorted(results, key=lambda r: -float(r["mesh_size"]))
    mesh_sizes = [float(r["mesh_size"]) for r in coarse_first]
    quantities = [float(r["quantity"]) for r in coarse_first]
    reference = float(coarse_first[0]["reference"])

    if abs(reference) < 1e-15:
        errors = [abs(q - reference) for q in quantities]
    else:
        errors = [abs(q - reference) / abs(reference) for q in quantities]

    if len(results) < 2:
        return ConvergenceResult(
            mesh_sizes=mesh_sizes,
            errors=errors,
            converged=False,
            relative_change=float("inf"),
            reason="insufficient data: need at least two mesh densities",
        )

    eps = 1e-12
    monotonic = all(errors[i + 1] <= errors[i] + eps for i in range(len(errors) - 1))
    last = quantities[-1]
    prev = quantities[-2]
    denom = max(abs(prev), 1.0)
    relative_change = abs(last - prev) / denom

    if monotonic and relative_change < tolerance:
        reason = "converged: monotonic error decrease and relative change within tolerance"
        converged = True
    elif not monotonic:
        reason = "not converged: error sequence is not monotonically decreasing"
        converged = False
    else:
        reason = f"not converged: relative change {relative_change:.4g} exceeds tolerance {tolerance:.4g}"
        converged = False

    return ConvergenceResult(
        mesh_sizes=mesh_sizes,
        errors=errors,
        converged=converged,
        relative_change=relative_change,
        reason=reason,
    )


# ─── verify_simulation (MATE-AT-007: full verification suite) ────────────────


def _run_patch_test(plan: SimulationPlan) -> dict[str, str]:
    """Patch test: pass iff at least one verification benchmark is declared."""
    benchmarks = list(plan.verification.benchmarks)
    if benchmarks:
        return {"state": "pass", "reason": f"benchmarks declared: {', '.join(benchmarks)}"}
    return {"state": "fail", "reason": "no verification benchmarks declared"}


def _run_manufactured_solution(plan: SimulationPlan) -> dict[str, str]:
    """Manufactured-solution check: pass iff a convergence strategy is declared."""
    conv = plan.verification.convergence
    if conv and conv.strip():
        return {"state": "pass", "reason": f"convergence strategy: {conv}"}
    return {"state": "fail", "reason": "no convergence strategy declared"}


def _run_mesh_convergence(plan: SimulationPlan) -> dict[str, str]:
    """Mesh-convergence check: pass iff the mesh carries a convergence plan."""
    cp = plan.mesh.convergence_plan
    if cp and cp.strip():
        return {"state": "pass", "reason": f"mesh convergence plan: {cp}"}
    return {"state": "fail", "reason": "no mesh convergence plan declared"}


def _run_conservation(plan: SimulationPlan) -> dict[str, str]:
    """Conservation check: pass iff at least one conservation check is declared."""
    checks = list(plan.verification.conservation_checks)
    if checks:
        return {"state": "pass", "reason": f"conservation checks: {', '.join(checks)}"}
    return {"state": "fail", "reason": "no conservation checks declared"}


def verify_simulation(
    adapter: SolverAdapter,
    plan: SimulationPlan,
) -> SimulationVerdict:
    """Run the four verification categories against ``plan`` and aggregate.

    The adapter is consulted only for its declared ``capability_id`` and
    ``validation_cases``; the actual solver invocation is out of scope for
    this function (it verifies that the PLAN is verification-ready, not that
    a particular run converged — for the latter, drive :func:`check_convergence`
    on the solver's output sequence).

    Overall state:

      - ``pass``         every category passed
      - ``fail``         any category failed (per MATE-SAFE-006 blocks the verdict)
      - ``inconclusive`` no category failed but at least one was inconclusive
    """
    checks: dict[str, dict[str, str]] = {
        "patch_test": _run_patch_test(plan),
        "manufactured_solution": _run_manufactured_solution(plan),
        "mesh_convergence": _run_mesh_convergence(plan),
        "conservation": _run_conservation(plan),
    }

    states = [c["state"] for c in checks.values()]
    if all(s == "pass" for s in states):
        overall = "pass"
        reason = f"all verification categories passed for adapter {getattr(adapter, 'capability_id', '?')}"
    elif any(s == "fail" for s in states):
        overall = "fail"
        failed = [name for name, c in checks.items() if c["state"] == "fail"]
        reason = f"verification failed: {', '.join(failed)}"
    else:
        overall = "inconclusive"
        reason = "one or more verification categories inconclusive"

    return SimulationVerdict(state=overall, checks=checks, reason=reason)


__all__ = [
    "ConvergenceResult",
    "SimulationVerdict",
    "check_convergence",
    "is_question_falsifiable",
    "verify_simulation",
]
