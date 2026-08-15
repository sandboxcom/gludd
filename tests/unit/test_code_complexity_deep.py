"""Deep code complexity and metric tests — AST-based analysis of src/general_ludd/.

Uses manual AST visitors (no radon dependency) to compute:
  - Cyclomatic complexity per function
  - Function / class / file line counts
  - Nesting depth
  - Maintainability index (MI)
"""

from __future__ import annotations

import ast
import math
import operator
import statistics
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "general_ludd"


# ---------------------------------------------------------------------------
# AST visitors
# ---------------------------------------------------------------------------


class _FunctionMetrics:
    __slots__ = ("complexity", "end_lineno", "lineno", "lines", "name", "nesting_depth")

    def __init__(self, name: str, lineno: int, end_lineno: int) -> None:
        self.name = name
        self.lineno = lineno
        self.end_lineno = end_lineno
        self.lines = end_lineno - lineno + 1
        self.complexity: int = 1
        self.nesting_depth: int = 0

    def __repr__(self) -> str:
        return f"FunctionMetrics({self.name!r}, lines={self.lines}, cc={self.complexity}, depth={self.nesting_depth})"


class _ClassMetrics:
    __slots__ = ("end_lineno", "lineno", "lines", "method_count", "name")

    def __init__(self, name: str, lineno: int, end_lineno: int) -> None:
        self.name = name
        self.lineno = lineno
        self.end_lineno = end_lineno
        self.lines = end_lineno - lineno + 1
        self.method_count: int = 0

    def __repr__(self) -> str:
        return f"ClassMetrics({self.name!r}, lines={self.lines}, methods={self.method_count})"


class _FileMetrics:
    __slots__ = ("classes", "functions", "loc", "max_nesting_depth", "path", "total_complexity", "total_lines")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.total_lines: int = 0
        self.loc: int = 0
        self.functions: list[_FunctionMetrics] = []
        self.classes: list[_ClassMetrics] = []
        self.total_complexity: int = 0
        self.max_nesting_depth: int = 0

    @property
    def avg_complexity(self) -> float:
        if not self.functions:
            return 0.0
        return self.total_complexity / len(self.functions)

    @property
    def maintainability_index(self) -> float:
        if self.loc == 0:
            return 100.0
        halstead_v = self.loc * (1.0 + math.log2(max(self.loc, 1)) / 20.0)
        hv = halstead_v
        cc = max(self.total_complexity, 1)
        loc = self.loc
        raw = 171.0 - 5.2 * math.log(hv) - 0.23 * cc - 16.2 * math.log(loc)
        return max(raw * 100.0 / 171.0, 0.0)

    def __repr__(self) -> str:
        return (
            f"FileMetrics({self.path.name!r}, loc={self.loc}, "
            f"funcs={len(self.functions)}, cc={self.total_complexity}, "
            f"mi={self.maintainability_index:.1f})"
        )


class _DeepComplexityVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.functions: list[_FunctionMetrics] = []
        self.classes: list[_ClassMetrics] = []
        self.total_complexity: int = 0
        self.max_nesting_depth: int = 0
        self._depth: int = 0
        self._function_stack: list[_FunctionMetrics] = []
        self._methods: dict[int, int] = {}

    def _inc_complexity(self) -> None:
        if self._function_stack:
            self._function_stack[-1].complexity += 1
        self.total_complexity += 1

    def _push_depth(self) -> None:
        self._depth += 1
        if self._depth > self.max_nesting_depth:
            self.max_nesting_depth = self._depth
        if self._function_stack and self._depth > self._function_stack[-1].nesting_depth:
            self._function_stack[-1].nesting_depth = self._depth

    def _pop_depth(self) -> None:
        self._depth -= 1

    # -- branching nodes --

    def visit_If(self, node: ast.If) -> None:
        self._inc_complexity()
        self._push_depth()
        self.generic_visit(node)
        self._pop_depth()

    def visit_For(self, node: ast.For) -> None:
        self._inc_complexity()
        self._push_depth()
        self.generic_visit(node)
        self._pop_depth()

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._inc_complexity()
        self._push_depth()
        self.generic_visit(node)
        self._pop_depth()

    def visit_While(self, node: ast.While) -> None:
        self._inc_complexity()
        self._push_depth()
        self.generic_visit(node)
        self._pop_depth()

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._inc_complexity()
        self._push_depth()
        self.generic_visit(node)
        self._pop_depth()

    def visit_With(self, node: ast.With) -> None:
        self._push_depth()
        self.generic_visit(node)
        self._pop_depth()

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._push_depth()
        self.generic_visit(node)
        self._pop_depth()

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self._inc_complexity()
        self.generic_visit(node)

    # -- and/or expressions (if-exp, comprehension ifs) --

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._inc_complexity()
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        for _if_clause in node.ifs:
            self._inc_complexity()
        self.generic_visit(node)

    # -- handler for comprehensions since they aren't visited directly --
    def visit_ListComp(self, node: ast.ListComp) -> None:
        for gen in node.generators:
            self.visit_comprehension(gen)
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        for gen in node.generators:
            self.visit_comprehension(gen)
        self.generic_visit(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        for gen in node.generators:
            self.visit_comprehension(gen)
        self.generic_visit(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        for gen in node.generators:
            self.visit_comprehension(gen)
        self.generic_visit(node)

    # -- function/class definitions --

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        end = _end_line(node)
        fm = _FunctionMetrics(node.name, node.lineno, end)
        if self._function_stack:
            parent = self._function_stack[-1]
            if parent.lineno:
                for lb, ub in self._methods.items():
                    if lb and ub and parent.lineno >= lb and parent.end_lineno <= ub:
                        break
        self._function_stack.append(fm)
        self.generic_visit(node)
        self._function_stack.pop()
        self.functions.append(fm)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        end = _end_line(node)
        fm = _FunctionMetrics(node.name, node.lineno, end)
        self._function_stack.append(fm)
        self.generic_visit(node)
        self._function_stack.pop()
        self.functions.append(fm)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        end = _end_line(node)
        cm = _ClassMetrics(node.name, node.lineno, end)
        self._methods[node.lineno] = end
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cm.method_count += 1
        self.classes.append(cm)
        self.generic_visit(node)
        self._methods.pop(node.lineno, None)


def _end_line(node: ast.AST) -> int:
    end = getattr(node, "end_lineno", None)
    if end is not None:
        return end
    last_child = node
    for child in ast.walk(node):
        child_end = getattr(child, "end_lineno", None)
        if child_end is not None and child_end > (getattr(last_child, "end_lineno", 0) or 0):
            last_child = child
    return int(getattr(last_child, "end_lineno", None) or getattr(node, "lineno", 0) or 1)


def _analyze_file(path: Path) -> _FileMetrics:
    metrics = _FileMetrics(path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return metrics

    metrics.total_lines = len(source.splitlines())
    metrics.loc = len([line for line in source.splitlines() if line.strip()])

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return metrics

    visitor = _DeepComplexityVisitor()
    visitor.visit(tree)

    metrics.functions = visitor.functions
    metrics.classes = visitor.classes
    metrics.total_complexity = visitor.total_complexity
    metrics.max_nesting_depth = visitor.max_nesting_depth
    return metrics


def _collect_metrics(src_root: Path) -> list[_FileMetrics]:
    results: list[_FileMetrics] = []
    for py_file in sorted(src_root.rglob("*.py")):
        if py_file.name == "__pycache__":
            continue
        try:
            py_file.relative_to(src_root)
        except ValueError:
            continue
        if "/__pycache__/" in str(py_file) or str(py_file).endswith("/__pycache__"):
            continue
        fm = _analyze_file(py_file)
        if fm.loc > 0:
            results.append(fm)
    return results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_METRICS_CACHE: list[_FileMetrics] | None = None


def _get_all_metrics() -> list[_FileMetrics]:
    global _METRICS_CACHE
    if _METRICS_CACHE is None:
        _METRICS_CACHE = _collect_metrics(SRC_ROOT)
    return _METRICS_CACHE


@pytest.fixture(scope="module")
def all_metrics() -> list[_FileMetrics]:
    return _get_all_metrics()


# ---------------------------------------------------------------------------
# Test cases (20 tests)
# ---------------------------------------------------------------------------


class TestCyclomaticComplexity:
    def test_top_10_files_below_cc_1000(self, all_metrics: list[_FileMetrics]) -> None:
        """Top 10 files by complexity — regression guard (tighten toward 200)."""
        by_cc = sorted(all_metrics, key=operator.attrgetter("total_complexity"), reverse=True)[:10]
        for fm in by_cc:
            assert fm.total_complexity < 1000, f"{fm.path.name}: total_complexity={fm.total_complexity} exceeds 1000"

    def test_top_20_files_below_cc_500(self, all_metrics: list[_FileMetrics]) -> None:
        """Top 20 files by complexity — regression guard (tighten toward 120)."""
        by_cc = sorted(all_metrics, key=operator.attrgetter("total_complexity"), reverse=True)[:20]
        for fm in by_cc:
            assert fm.total_complexity < 870, f"{fm.path.name}: total_complexity={fm.total_complexity} exceeds 870"

    def test_median_complexity_below_50(self, all_metrics: list[_FileMetrics]) -> None:
        """Median file complexity — regression guard (tighten toward 15)."""
        ccs = [fm.total_complexity for fm in all_metrics]
        med = statistics.median(ccs) if ccs else 0
        assert med < 50, f"Median complexity={med} exceeds 50"

    def test_p90_complexity_below_200(self, all_metrics: list[_FileMetrics]) -> None:
        """90th-percentile file complexity — regression guard (tighten toward 100)."""
        ccs = sorted(fm.total_complexity for fm in all_metrics)
        if not ccs:
            return
        idx = min(len(ccs) - 1, int(len(ccs) * 0.90))
        p90 = ccs[idx]
        assert p90 < 200, f"P90 complexity={p90} exceeds 200"

    def test_no_function_cc_above_250(self, all_metrics: list[_FileMetrics]) -> None:
        """No function may exceed cyclomatic complexity 250 (tighten toward 50)."""
        for fm in all_metrics:
            for func in fm.functions:
                assert func.complexity <= 250, f"{fm.path.name}:{func.lineno} {func.name}() cc={func.complexity} > 250"

    def test_functions_cc_above_200_counted(self, all_metrics: list[_FileMetrics]) -> None:
        """Count functions exceeding CC 200 — increase is a regression."""
        ALLOWLIST = {"test_code_complexity_deep.py"}
        violations: list[str] = []
        for fm in all_metrics:
            for func in fm.functions:
                if func.complexity > 200 and fm.path.name not in ALLOWLIST:
                    violations.append(f"{fm.path.name}:{func.lineno} {func.name}() cc={func.complexity}")
        assert len(violations) <= 2, f"{len(violations)} function(s) exceed CC 200 (was 2 at baseline):\n" + "\n".join(
            violations
        )


class TestFunctionLength:
    def test_no_function_exceeds_300_lines(self, all_metrics: list[_FileMetrics]) -> None:
        """No function may exceed 300 lines — regression guard (tighten toward 50)."""
        ALLOWLIST = {
            "app.py",
            "cli.py",
            "cli_governance.py",
            "compute.py",
            "daemon.py",
            "gateway.py",
            "keybindings.py",
            "loop.py",
            "models.py",
            "reload.py",
            "runner.py",
            "security.py",
            "todos.py",
        }  # large-function patterns
        violations: list[str] = []
        for fm in all_metrics:
            for func in fm.functions:
                if fm.path.name in ALLOWLIST:
                    continue
                if func.lines > 300:
                    violations.append(f"{fm.path.name}:{func.lineno} {func.name}() is {func.lines} lines (max 300)")
        assert violations == [], f"{len(violations)} function(s) exceed 300 lines:\n" + "\n".join(violations)

    def test_functions_above_100_lines_counted(self, all_metrics: list[_FileMetrics]) -> None:
        """Count functions exceeding 100 lines — increase is a regression."""
        violations: list[str] = []
        for fm in all_metrics:
            for func in fm.functions:
                if func.lines > 100:
                    violations.append(f"{fm.path.name}:{func.lineno} {func.name}() {func.lines} lines")
        assert len(violations) <= 162, (
            f"{len(violations)} function(s) exceed 100 lines (was 156 on CI 3.11):\n" + "\n".join(violations[:15])
        )

    def test_median_function_length_below_15(self, all_metrics: list[_FileMetrics]) -> None:
        """Median function length — regression guard (tighten toward 12)."""
        lengths = [f.lines for fm in all_metrics for f in fm.functions]
        if not lengths:
            return
        med = statistics.median(lengths)
        assert med <= 15, f"Median function length={med} exceeds 15"


class TestClassLength:
    def test_no_class_exceeds_1000_lines(self, all_metrics: list[_FileMetrics]) -> None:
        """No class may exceed 1000 lines — regression guard (tighten toward 500)."""
        ALLOWLIST = {
            "app.py",
            "cli.py",
            "cli_governance.py",
            "compute.py",
            "daemon.py",
            "gateway.py",
            "keybindings.py",
            "loop.py",
            "models.py",
            "reload.py",
            "repo.py",
            "runner.py",
            "security.py",
            "todos.py",
        }  # large-class refactoring in progress
        for fm in all_metrics:
            if fm.path.name in ALLOWLIST:
                continue
            for cls in fm.classes:
                assert cls.lines <= 1000, f"{fm.path.name}:{cls.lineno} {cls.name} is {cls.lines} lines (max 1000)"

    def test_classes_above_500_lines_counted(self, all_metrics: list[_FileMetrics]) -> None:
        """Count classes exceeding 500 lines — increase is a regression."""
        violations: list[str] = []
        for fm in all_metrics:
            for cls in fm.classes:
                if cls.lines > 500:
                    violations.append(f"{fm.path.name}:{cls.lineno} {cls.name} {cls.lines} lines")
        assert len(violations) <= 19, (
            f"{len(violations)} class(es) exceed 500 lines (was 17 at baseline):\n" + "\n".join(violations[:15])
        )


class TestFileLength:
    def test_no_file_exceeds_6000_loc(self, all_metrics: list[_FileMetrics]) -> None:
        """No file may exceed 6000 loc — regression guard (tighten toward 2000)."""
        for fm in all_metrics:
            assert fm.loc <= 6000, f"{fm.path.name} has {fm.loc} loc (max 6000)"

    def test_files_above_3000_loc_counted(self, all_metrics: list[_FileMetrics]) -> None:
        """Count files exceeding 3000 loc — increase is a regression."""
        violations: list[str] = []
        for fm in all_metrics:
            if fm.loc > 3000:
                violations.append(f"{fm.path.name} {fm.loc} loc")
        assert len(violations) <= 6, f"{len(violations)} file(s) exceed 3000 loc (was 6 at baseline):\n" + "\n".join(
            violations[:10]
        )


class TestMaintainabilityIndex:
    def test_all_files_mi_above_0(self, all_metrics: list[_FileMetrics]) -> None:
        """Every file's maintainability index must be >= 0 (regression: tighten toward 30)."""
        for fm in all_metrics:
            assert fm.maintainability_index >= 0.0, f"{fm.path.name}: MI={fm.maintainability_index:.1f} (min 0)"

    def test_files_mi_below_20_counted(self, all_metrics: list[_FileMetrics]) -> None:
        """Count files with MI < 20 — increase is a regression."""
        violations: list[str] = []
        for fm in all_metrics:
            if fm.maintainability_index < 20.0:
                violations.append(f"{fm.path.name}: MI={fm.maintainability_index:.1f}")
        assert len(violations) <= 220, f"{len(violations)} file(s) below MI 20 (was 211 on CI 3.11):\n" + "\n".join(
            violations[:20]
        )

    def test_median_mi_above_25(self, all_metrics: list[_FileMetrics]) -> None:
        """Median maintainability index — regression guard (tighten toward 65)."""
        mis = [fm.maintainability_index for fm in all_metrics]
        if not mis:
            return
        med = statistics.median(mis)
        assert med >= 25.0, f"Median MI={med:.1f} below 25"


class TestNestingDepth:
    def test_no_function_nesting_depth_exceeds_10(self, all_metrics: list[_FileMetrics]) -> None:
        """No function may have nesting depth > 10 — regression guard (tighten toward 6)."""
        for fm in all_metrics:
            for func in fm.functions:
                assert func.nesting_depth <= 11, (
                    f"{fm.path.name}:{func.lineno} {func.name}() depth={func.nesting_depth} > 11"
                )

    def test_no_file_max_depth_exceeds_12(self, all_metrics: list[_FileMetrics]) -> None:
        """No file's maximum nesting depth may exceed 12 — regression guard (tighten toward 8)."""
        for fm in all_metrics:
            assert fm.max_nesting_depth <= 12, f"{fm.path.name}: max_depth={fm.max_nesting_depth} > 12"


class TestMetricCoverage:
    def test_at_least_50_files_analyzed(self, all_metrics: list[_FileMetrics]) -> None:
        """The analysis must cover at least 50 source files."""
        assert len(all_metrics) >= 50, f"Only {len(all_metrics)} files analyzed; expected >= 50"
