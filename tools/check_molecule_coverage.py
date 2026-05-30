"""CLI entry point for molecule coverage checking."""

from __future__ import annotations

import json
import sys

from agentic_harness.quality.molecule_coverage import MoleculeCoverageChecker


def main() -> None:
    registry: dict[str, str] = {}
    roots: list[str] = ["molecule/playbooks"]
    checker = MoleculeCoverageChecker(playbook_registry=registry, scenario_roots=roots)
    report = checker.compute_coverage()
    print(json.dumps({
        "total_registered": report.total_registered,
        "total_covered": report.total_covered,
        "coverage_percent": report.coverage_percent,
        "covered": report.covered,
        "uncovered": report.uncovered,
    }, indent=2))
    if report.total_registered > 0 and report.coverage_percent < 100.0:
        sys.exit(1)


if __name__ == "__main__":
    main()
