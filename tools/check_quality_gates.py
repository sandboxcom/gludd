"""CLI entry point for quality gate checking."""

from __future__ import annotations

import json
import sys

from agentic_harness.quality.tools import check_quality_gates


def main() -> None:
    config: dict = {
        "python": {
            "enabled": True,
            "line_coverage_min_percent": 90.0,
            "branch_coverage_min_percent": 80.0,
        },
        "molecule": {"enabled": True},
        "playbook_registry": {},
        "scenario_roots": ["molecule/playbooks"],
    }
    result = check_quality_gates(config, line_coverage=0.0, branch_coverage=0.0)
    print(json.dumps({
        "passes": result.passes,
        "python_coverage": {
            "line_coverage": result.python_coverage.line_coverage,
            "branch_coverage": result.python_coverage.branch_coverage,
            "target_line": result.python_coverage.target_line,
            "target_branch": result.python_coverage.target_branch,
            "passes": result.python_coverage.passes,
        },
        "molecule_coverage": {
            "total_registered": result.molecule_coverage.total_registered,
            "total_covered": result.molecule_coverage.total_covered,
            "coverage_percent": result.molecule_coverage.coverage_percent,
        },
    }, indent=2))
    if not result.passes:
        sys.exit(1)


if __name__ == "__main__":
    main()
