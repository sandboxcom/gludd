"""Quality gate tools for coverage and molecule checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentic_harness.quality.molecule_coverage import MoleculeCoverageChecker, MoleculeCoverageReport


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
    py = config.get("python", {})
    target_line = py.get("line_coverage_min_percent", 90.0)
    target_branch = py.get("branch_coverage_min_percent", 80.0)
    passes = line_coverage >= target_line and branch_coverage >= target_branch
    return CoverageResult(
        line_coverage=line_coverage,
        branch_coverage=branch_coverage,
        target_line=target_line,
        target_branch=target_branch,
        passes=passes,
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
