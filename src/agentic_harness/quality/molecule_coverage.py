"""Molecule coverage checker for playbook scenarios."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class MoleculeCoverageReport:
    total_registered: int
    total_covered: int
    coverage_percent: float
    covered: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)


class MoleculeCoverageChecker:
    def __init__(self, playbook_registry: dict[str, str], scenario_roots: list[str]) -> None:
        self.playbook_registry = playbook_registry
        self.scenario_roots = scenario_roots

    def get_registered_playbooks(self) -> list[str]:
        return sorted(self.playbook_registry.keys())

    def get_covered_playbooks(self) -> list[str]:
        covered: list[str] = []
        for root in self.scenario_roots:
            if not os.path.isdir(root):
                continue
            for playbook_name in self.playbook_registry:
                playbook_base = os.path.splitext(playbook_name)[0]
                scenario_dir = os.path.join(root, playbook_base)
                if os.path.isdir(scenario_dir):
                    for scenario_name in os.listdir(scenario_dir):
                        candidate = os.path.join(scenario_dir, scenario_name)
                        if os.path.isfile(os.path.join(candidate, "molecule.yml")):
                            covered.append(playbook_name)
                            break
        return sorted(set(covered))

    def get_uncovered_playbooks(self) -> list[str]:
        covered = set(self.get_covered_playbooks())
        return sorted(p for p in self.get_registered_playbooks() if p not in covered)

    def compute_coverage(self) -> MoleculeCoverageReport:
        registered = self.get_registered_playbooks()
        covered = self.get_covered_playbooks()
        total = len(registered)
        n_covered = len(covered)
        pct = (n_covered / total * 100.0) if total > 0 else 0.0
        uncovered = [p for p in registered if p not in set(covered)]
        return MoleculeCoverageReport(
            total_registered=total,
            total_covered=n_covered,
            coverage_percent=round(pct, 2),
            covered=covered,
            uncovered=uncovered,
        )
