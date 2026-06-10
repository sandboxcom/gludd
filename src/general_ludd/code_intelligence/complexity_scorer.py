"""Code complexity scorer — uses AST block extraction to compute complexity metrics and suggest task types."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from general_ludd.schemas.benchmark import TaskType

log = logging.getLogger(__name__)


class ComplexityScore(BaseModel):
    path: str
    cyclomatic_complexity: int = 0
    max_nesting_depth: int = 0
    function_count: int = 0
    class_count: int = 0
    loc: int = 0


class DirectoryComplexityReport(BaseModel):
    path: str
    file_scores: list[ComplexityScore] = Field(default_factory=list)
    total_loc: int = 0
    avg_complexity: float = 0.0
    file_count: int = 0


class _ComplexityVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.cyclomatic_complexity: int = 1
        self.max_nesting_depth: int = 0
        self.function_count: int = 0
        self.class_count: int = 0
        self._current_depth: int = 0

    def _visit_branch(self, node: ast.AST) -> None:
        self.cyclomatic_complexity += 1
        self._current_depth += 1
        if self._current_depth > self.max_nesting_depth:
            self.max_nesting_depth = self._current_depth
        self.generic_visit(node)
        self._current_depth -= 1

    def visit_If(self, node: ast.If) -> None:
        self._visit_branch(node)

    def visit_For(self, node: ast.For) -> None:
        self._visit_branch(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_branch(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.cyclomatic_complexity += 1
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        self._current_depth += 1
        if self._current_depth > self.max_nesting_depth:
            self.max_nesting_depth = self._current_depth
        self.generic_visit(node)
        self._current_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_count += 1
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_count += 1
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_count += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.cyclomatic_complexity += len(node.values) - 1
        self.generic_visit(node)


class CodeComplexityScorer:
    def score_file(self, path: str) -> ComplexityScore:
        try:
            with open(path) as f:
                source = f.read()
        except OSError:
            log.debug("Cannot read file: %s", path)
            return ComplexityScore(path=path)

        loc = len([line for line in source.splitlines() if line.strip()])

        try:
            tree = ast.parse(source)
        except SyntaxError:
            log.debug("Syntax error in file: %s", path)
            return ComplexityScore(path=path, loc=loc)

        visitor = _ComplexityVisitor()
        visitor.visit(tree)

        return ComplexityScore(
            path=path,
            cyclomatic_complexity=visitor.cyclomatic_complexity,
            max_nesting_depth=visitor.max_nesting_depth,
            function_count=visitor.function_count,
            class_count=visitor.class_count,
            loc=loc,
        )

    def score_directory(self, path: str) -> DirectoryComplexityReport:
        dir_path = Path(path)
        if not dir_path.is_dir():
            return DirectoryComplexityReport(path=path)

        file_scores: list[ComplexityScore] = []
        for py_file in sorted(dir_path.rglob("*.py")):
            score = self.score_file(str(py_file))
            if score.loc > 0:
                file_scores.append(score)

        total_loc = sum(s.loc for s in file_scores)
        avg_complexity = 0.0
        if file_scores:
            avg_complexity = sum(s.cyclomatic_complexity for s in file_scores) / len(file_scores)

        return DirectoryComplexityReport(
            path=path,
            file_scores=file_scores,
            total_loc=total_loc,
            avg_complexity=round(avg_complexity, 2),
            file_count=len(file_scores),
        )

    def suggest_task_type(self, complexity: ComplexityScore) -> TaskType:
        if complexity.max_nesting_depth >= 5:
            return TaskType.BUG_FIX

        if complexity.cyclomatic_complexity >= 20:
            return TaskType.SECURITY_FIX

        if complexity.cyclomatic_complexity >= 6:
            return TaskType.REFACTOR

        return TaskType.FEATURE
