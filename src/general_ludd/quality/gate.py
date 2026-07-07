"""Quality gate enforcement module."""

from __future__ import annotations

import logging

from general_ludd.schemas.quality_gate import QualityGateConfig

logger = logging.getLogger(__name__)


class QualityGateChecker:
    def __init__(self, config: QualityGateConfig | None = None) -> None:
        self.config = config or QualityGateConfig()

    def check_python_coverage(
        self, coverage_percent: float, branch_percent: float | None = None
    ) -> dict[str, object]:
        checks: list[dict[str, object]] = []
        result: dict[str, object] = {
            "gate": "python_coverage",
            "passed": True,
            "checks": checks,
        }
        py = self.config.python
        if not py.enabled:
            checks.append({"check": "python_coverage", "skipped": True})
            return result

        if coverage_percent < py.line_coverage_min_percent:
            result["passed"] = False
            checks.append({
                "check": "line_coverage",
                "actual": coverage_percent,
                "required": py.line_coverage_min_percent,
                "status": "failed",
            })
        else:
            checks.append({
                "check": "line_coverage",
                "actual": coverage_percent,
                "required": py.line_coverage_min_percent,
                "status": "passed",
            })

        if branch_percent is not None and branch_percent < py.branch_coverage_min_percent:
            result["passed"] = False
            checks.append({
                "check": "branch_coverage",
                "actual": branch_percent,
                "required": py.branch_coverage_min_percent,
                "status": "failed",
            })

        return result

    def check_molecule_coverage(
        self, covered: int, required: int
    ) -> dict[str, object]:
        mol = self.config.molecule
        if not mol.enabled:
            return {"gate": "molecule_coverage", "passed": True, "skipped": True}

        if required == 0:
            return {"gate": "molecule_coverage", "passed": True, "covered": 0, "required": 0}

        percent = (covered / required) * 100
        passed = percent >= mol.coverage_min_percent
        return {
            "gate": "molecule_coverage",
            "passed": passed,
            "percent": percent,
            "required_percent": mol.coverage_min_percent,
            "covered": covered,
            "required": required,
        }

    def enforce(self, gate_results: list[dict[str, object]]) -> dict[str, object]:
        all_passed = all(g.get("passed", True) for g in gate_results)
        return {
            "all_passed": all_passed,
            "gates": gate_results,
            "blocks_completion": self.config.enforcement.block_todo_complete and not all_passed,
            "blocks_commit": self.config.enforcement.block_commit and not all_passed,
            "blocks_merge": self.config.enforcement.block_merge and not all_passed,
            "blocks_push": self.config.enforcement.block_push and not all_passed,
            "blocks_reload": self.config.enforcement.block_reload and not all_passed,
        }
