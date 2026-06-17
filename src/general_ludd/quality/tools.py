"""Quality gate tools for coverage and molecule checks.

These dict-based free functions are thin compatibility shims. The python
coverage comparison delegates to ``QualityGateChecker.check_python_coverage``
(``quality/gate.py``), which is the single canonical implementation of the
line/branch pass-fail logic. The shim adapts the canonical pydantic-backed
method to the legacy raw-dict signature and ``CoverageResult`` return shape so
existing callers/tests keep working.

NOTE on molecule: ``check_molecule_coverage`` here is NOT the same operation as
``QualityGateChecker.check_molecule_coverage``. The checker method evaluates a
pre-computed ``covered``/``required`` ratio into a pass/fail dict; this function
*discovers* coverage by scanning the filesystem (registry + scenario roots) and
returns a ``MoleculeCoverageReport``. They have different inputs, outputs, and
purpose, so delegation would be unsafe — it is intentionally left independent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from general_ludd.quality.gate import QualityGateChecker
from general_ludd.quality.molecule_coverage import MoleculeCoverageChecker, MoleculeCoverageReport
from general_ludd.schemas.quality_gate import PythonQualityGate, QualityGateConfig


@dataclass
class CoverageResult:
    line_coverage: float
    branch_coverage: float
    target_line: float
    target_branch: float
    passes: bool


@dataclass
class QualityGateResult:
    passes: bool
    python_coverage: CoverageResult
    molecule_coverage: MoleculeCoverageReport


def check_python_coverage(
    config: dict[str, Any],
    line_coverage: float = 0.0,
    branch_coverage: float = 0.0,
) -> CoverageResult:
    """Compare python line/branch coverage against configured minimums.

    Delegates the pass-fail decision to the canonical
    ``QualityGateChecker.check_python_coverage`` so there is a single source of
    truth for the comparison. The raw-dict ``config`` is adapted into a
    ``QualityGateConfig``; ``enabled`` is forced True to preserve this shim's
    historical "always compare" behavior (the legacy free function never
    honored an ``enabled`` flag, and always compared branch coverage).
    """
    py = config.get("python", {})
    target_line = py.get("line_coverage_min_percent", 90.0)
    target_branch = py.get("branch_coverage_min_percent", 80.0)

    checker = QualityGateChecker(
        QualityGateConfig(
            python=PythonQualityGate(
                enabled=True,
                line_coverage_min_percent=target_line,
                branch_coverage_min_percent=target_branch,
            )
        )
    )
    # Pass branch_coverage as a concrete float (never None) so the canonical
    # method always evaluates branch coverage, matching legacy semantics.
    method_result = checker.check_python_coverage(
        coverage_percent=line_coverage, branch_percent=branch_coverage
    )

    return CoverageResult(
        line_coverage=line_coverage,
        branch_coverage=branch_coverage,
        target_line=target_line,
        target_branch=target_branch,
        passes=method_result["passed"],
    )


def check_molecule_coverage(config: dict[str, Any]) -> MoleculeCoverageReport:
    registry: dict[str, str] = config.get("playbook_registry", {})
    roots: list[str] = config.get("scenario_roots", [])
    checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=roots)
    return checker.compute_coverage()


def check_quality_gates(
    config: dict[str, Any],
    line_coverage: float = 0.0,
    branch_coverage: float = 0.0,
) -> QualityGateResult:
    cov = check_python_coverage(config, line_coverage=line_coverage, branch_coverage=branch_coverage)
    mol = check_molecule_coverage(config)
    passes = cov.passes
    return QualityGateResult(
        passes=passes,
        python_coverage=cov,
        molecule_coverage=mol,
    )
