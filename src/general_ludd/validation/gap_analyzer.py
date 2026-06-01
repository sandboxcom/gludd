"""Gap analyzer — detects missing tests, molecule scenarios, and coverage gaps."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class GapItem:
    category: str
    description: str
    severity: str
    suggested_action: str


@dataclass
class GapReport:
    total_gaps: int
    gaps: list[GapItem] = field(default_factory=list)


class GapAnalyzer:
    def analyze(self, sprint_path: str, repo_root: str) -> GapReport:
        gaps: list[GapItem] = []

        impl_without_tests = _find_impl_without_tests(repo_root)
        gaps.extend(impl_without_tests)

        missing_molecule = _find_missing_molecule(repo_root)
        gaps.extend(missing_molecule)

        return GapReport(total_gaps=len(gaps), gaps=gaps)


def _find_impl_without_tests(repo_root: str) -> list[GapItem]:
    gaps: list[GapItem] = []
    src_dir = os.path.join(repo_root, "src")
    if not os.path.isdir(src_dir):
        return gaps

    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            if not fname.endswith(".py") or fname == "__init__.py":
                continue
            impl_path = os.path.join(root, fname)
            test_name = f"test_{fname}"
            test_found = _test_exists(repo_root, test_name)
            if not test_found:
                rel = os.path.relpath(impl_path, repo_root)
                gaps.append(GapItem(
                    category="missing_tests",
                    description=f"Implementation {rel} has no corresponding test file {test_name}",
                    severity="medium",
                    suggested_action=f"Create {test_name} to cover {rel}",
                ))

    return gaps


def _test_exists(repo_root: str, test_name: str) -> bool:
    tests_dir = os.path.join(repo_root, "tests")
    if not os.path.isdir(tests_dir):
        return False
    return any(test_name in files for _root, _dirs, files in os.walk(tests_dir))


def _find_missing_molecule(repo_root: str) -> list[GapItem]:
    gaps: list[GapItem] = []
    pb_dir = os.path.join(repo_root, "playbooks")
    mol_dir = os.path.join(repo_root, "molecule")

    if not os.path.isdir(pb_dir):
        return gaps

    for fname in sorted(os.listdir(pb_dir)):
        if not fname.endswith(".yml"):
            continue
        scenario_name = fname.replace(".yml", "")
        if os.path.isdir(os.path.join(mol_dir, scenario_name)):
            continue
        if os.path.isdir(os.path.join(mol_dir, "playbooks", scenario_name)):
            continue
        gaps.append(GapItem(
            category="missing_molecule",
            description=f"Playbook {fname} has no molecule scenario",
            severity="high",
            suggested_action=f"Create molecule scenario for {fname}",
        ))

    return gaps
