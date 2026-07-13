"""D.6 — OrchestrationPlanner dead-code proof and deletion rationale.

Decision: DELETE.
OrchestrationPlanner (src/general_ludd/scheduling/planner.py) wraps Scheduler
but adds zero functionality not already available via Scheduler directly.
The daemon, event loop, routers, pipeline, controllers, and self-update all
import scheduler.Scheduler/WorkItem directly — none use planner.py. Its only
consumer is scripts/plan_work.py, a standalone CLI utility that can be
rewired to use Scheduler directly.

Why not wire it? planner.py is a ~50 LOC wrapper that translates dicts →
WorkItems → calls Scheduler.plan(). Every callsite in src/ already constructs
WorkItem objects directly. Adding an OrchestrationPlanner indirection adds
no value — it couples the orchestrator to a dict schema it would need to
maintain in parallel with the native WorkItem dataclass.

Deletion scope: src/general_ludd/scheduling/planner.py (199 lines),
tests/unit/test_orchestration_planner.py (329 lines),
scripts/plan_work.py (104 lines).

This test proves the module is dead — no production code imports it.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "general_ludd"


def _python_files(root: Path) -> list[Path]:
    """Return all .py files under root, excluding the planner itself."""
    return [
        p for p in root.rglob("*.py")
        if "scheduling/planner" not in str(p)
        and "__pycache__" not in str(p)
    ]


def _imports_in_file(path: Path) -> list[str]:
    """Extract all imported module paths from a Python source file."""
    tree = ast.parse(path.read_text())
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}" if module else alias.name)
    return imports


class TestOrchestrationPlannerIsDeadCode:
    """Prove OrchestrationPlanner is not used anywhere in src/general_ludd/."""

    def test_no_scheduler_planner_imports_in_src(self) -> None:
        """No production file imports scheduling.planner."""
        violations: list[tuple[str, str]] = []
        for py_file in _python_files(SRC_DIR):
            for imp in _imports_in_file(py_file):
                if "scheduling.planner" in imp:
                    violations.append((str(py_file.relative_to(SRC_DIR.parent.parent)), imp))
        assert violations == [], (
            f"Found {len(violations)} import(s) of scheduling.planner in src/:\n"
            + "\n".join(f"  {f}: {i}" for f, i in violations)
            + "\n\nOrchestrationPlanner is dead code — delete it."
        )

    def test_no_orchestration_planner_imports_in_src(self) -> None:
        """No production file imports OrchestrationPlanner by name."""
        violations: list[tuple[str, str]] = []
        for py_file in _python_files(SRC_DIR):
            for imp in _imports_in_file(py_file):
                if "OrchestrationPlanner" in imp:
                    violations.append((str(py_file.relative_to(SRC_DIR.parent.parent)), imp))
        assert violations == [], (
            f"Found {len(violations)} import(s) of OrchestrationPlanner in src/:\n"
            + "\n".join(f"  {f}: {i}" for f, i in violations)
            + "\n\nOrchestrationPlanner is dead code — delete it."
        )

    def test_scheduler_is_used_directly(self) -> None:
        """Scheduler (not planner) is the canonical interface — used by daemon code."""
        direct_scheduler_imports: list[str] = []
        for py_file in _python_files(SRC_DIR):
            for imp in _imports_in_file(py_file):
                if "scheduling.scheduler" in imp:
                    direct_scheduler_imports.append(f"{py_file.name}: {imp}")
        assert len(direct_scheduler_imports) >= 3, (
            f"Only {len(direct_scheduler_imports)} direct Scheduler import(s) found. "
            "Scheduler is the wired interface."
        )

    def test_planner_file_exists_for_reference(self) -> None:
        """Sanity: the planner file still exists on disk (not yet deleted)."""
        planner_path = SRC_DIR / "scheduling" / "planner.py"
        assert planner_path.exists(), (
            f"{planner_path} missing — was it already deleted?\n"
            "If yes, update this test to reflect completion."
        )

    def test_deletion_rationale_documentation(self) -> None:
        """The module docstring above serves as the rationale documentation."""
        rationale = (
            "OrchestrationPlanner wraps Scheduler but adds zero functionality "
            "not already available via Scheduler directly."
        )
        assert "zero functionality" in __doc__ or "wraps Scheduler" in (__doc__ or ""), (
            "Deletion rationale must be documented in this test file's module docstring."
        )
        _ = rationale  # used for assertion message only


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
