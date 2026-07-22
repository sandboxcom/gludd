"""Structural test: verify TODO(integration) markers and document integration gaps."""

import ast
import pathlib

SOURCES = pathlib.Path("src/general_ludd/pricing_intel/sources.py")
PLANNER = pathlib.Path("src/general_ludd/scheduling/planner.py")


def _todo_integration_lines(path: pathlib.Path) -> list[int]:
    return [
        i
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if "TODO(integration)" in line
    ]


class TestTodoIntegrationMarkers:
    def test_sources_has_no_todo_integration_markers(self) -> None:
        """Pricing sources should not retain stale integration TODO markers."""
        lines = _todo_integration_lines(SOURCES)
        assert lines == []

    def test_planner_has_no_todo_integration_markers(self) -> None:
        """Scheduling planner has live FileClaimRegistry wiring, not TODO markers."""
        lines = _todo_integration_lines(PLANNER)
        assert lines == []

    def test_sources_todo_lines_are_not_in_class_blocks(self) -> None:
        """Resolved integration work must not leave TODO markers in classes."""
        lines = _todo_integration_lines(SOURCES)
        tree = ast.parse(SOURCES.read_text())
        todo_nodes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.lineno:
                for line_no in lines:
                    if node.lineno <= line_no <= (node.end_lineno or node.lineno):
                        context = {"class": node.name}
                        # narrow to method if inside one
                        for child in ast.walk(node):
                            if (isinstance(child, ast.FunctionDef) and child.lineno
                                    and child.lineno <= line_no <= (child.end_lineno or child.lineno)):
                                context = {"class": node.name, "method": child.name}
                                break
                        todo_nodes.append((line_no, context))
        assert todo_nodes == []

    def test_planner_todo_references_file_claim_registry(self) -> None:
        """Both planner TODO(integration) markers must reference FileClaimRegistry / #31."""
        text = PLANNER.read_text()
        assert "FileClaimRegistry" in text, "planner.py must reference FileClaimRegistry"
        assert "#31" in text, "planner.py must reference issue #31"

    def test_total_todo_integration_count(self) -> None:
        """Grand total: no stale TODO(integration) markers across both files."""
        total = len(_todo_integration_lines(SOURCES)) + len(_todo_integration_lines(PLANNER))
        assert total == 0
