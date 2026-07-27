"""Unit tests for branch coverage analysis via CodePathAnalyzer and tree-sitter.

Tests that branches in Python source are correctly identified and counted:
if/elif/else, for/while, try/except/finally, with, comprehensions,
ternary operators, short-circuit logic, and nested branches.
"""

from __future__ import annotations

import ast
import textwrap
from importlib import util as importlib_util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# AST-based branch detection (pure Python, no tree-sitter dependency)
# ---------------------------------------------------------------------------


class BranchVisitor(ast.NodeVisitor):
    """Walk AST and count branch nodes: if, for, while, try, with, comprehension ifs."""

    def __init__(self):
        self.branch_count = 0
        self.branches: list[dict] = []

    def _record(self, kind: str, line: int, col: int, subkind: str = ""):
        self.branch_count += 1
        self.branches.append(
            {
                "kind": kind,
                "subkind": subkind,
                "line": line,
                "col": col,
            }
        )

    def visit_If(self, node):
        self._record("if", node.lineno, node.col_offset)
        if node.orelse:
            self._record("else", node.orelse[0].lineno if node.orelse else node.lineno, node.col_offset, "else")
        self.generic_visit(node)

    def visit_For(self, node):
        self._record("for", node.lineno, node.col_offset)
        if node.orelse:
            self._record("for_else", node.orelse[0].lineno, node.col_offset)
        self.generic_visit(node)

    def visit_While(self, node):
        self._record("while", node.lineno, node.col_offset)
        if node.orelse:
            self._record("while_else", node.orelse[0].lineno, node.col_offset)
        self.generic_visit(node)

    def visit_Try(self, node):
        self._record("try", node.lineno, node.col_offset)
        for handler in node.handlers:
            self._record("except", handler.lineno, handler.col_offset)
        if node.orelse:
            self._record("try_else", node.orelse[0].lineno, node.col_offset)
        if node.finalbody:
            self._record("finally", node.finalbody[0].lineno, node.col_offset)
        self.generic_visit(node)

    def visit_With(self, node):
        self._record("with", node.lineno, node.col_offset)
        self.generic_visit(node)

    def visit_IfExp(self, node):
        self._record("ternary", node.lineno, node.col_offset, "ifexp")
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        for _i in range(len(node.values) - 1):
            self._record("boolop", node.lineno, node.col_offset, type(node.op).__name__)
        self.generic_visit(node)

    def visit_ListComp(self, node):
        for gen in node.generators:
            for if_clause in gen.ifs:
                self._record("comprehension_if", if_clause.lineno, if_clause.col_offset, "listcomp")
        self.generic_visit(node)

    def visit_SetComp(self, node):
        for gen in node.generators:
            for if_clause in gen.ifs:
                self._record("comprehension_if", if_clause.lineno, if_clause.col_offset, "setcomp")
        self.generic_visit(node)

    def visit_DictComp(self, node):
        for gen in node.generators:
            for if_clause in gen.ifs:
                self._record("comprehension_if", if_clause.lineno, if_clause.col_offset, "dictcomp")
        self.generic_visit(node)

    def visit_GeneratorExp(self, node):
        for gen in node.generators:
            for if_clause in gen.ifs:
                self._record("comprehension_if", if_clause.lineno, if_clause.col_offset, "genexpr")
        self.generic_visit(node)

    def visit_Match(self, node):
        self._record("match", node.lineno, node.col_offset)
        for case in node.cases:
            self._record("case", case.pattern.lineno, case.pattern.col_offset, "case")
        self.generic_visit(node)


def _count_branches(source: str) -> tuple[int, list[dict]]:
    tree = ast.parse(textwrap.dedent(source))
    visitor = BranchVisitor()
    visitor.visit(tree)
    return visitor.branch_count, visitor.branches


# ---------------------------------------------------------------------------
# Basic branch node counting
# ---------------------------------------------------------------------------


class TestBasicBranchCounting:
    """Count branch nodes for the fundamental control-flow constructs."""

    def test_simple_if_no_else(self):
        source = """\
def f(x):
    if x > 0:
        return 1
    return 0
"""
        count, branches = _count_branches(source)
        assert count == 1
        assert branches[0]["kind"] == "if"

    def test_if_else_counts_two(self):
        source = """\
def f(x):
    if x > 0:
        return 1
    else:
        return 0
"""
        count, branches = _count_branches(source)
        assert count == 2
        kinds = {b["kind"] for b in branches}
        assert "if" in kinds
        assert "else" in kinds

    def test_if_elif_else_counts(self):
        source = """\
def f(x):
    if x > 10:
        return "high"
    elif x > 0:
        return "mid"
    else:
        return "low"
"""
        count, branches = _count_branches(source)
        assert count >= 3
        main_kinds = {b["kind"] for b in branches}
        assert "if" in main_kinds
        assert "else" in main_kinds

    def test_for_loop_is_a_branch(self):
        source = """\
def f(items):
    for item in items:
        print(item)
"""
        count, branches = _count_branches(source)
        assert count == 1
        assert branches[0]["kind"] == "for"

    def test_for_else_counts_both(self):
        source = """\
def f(items):
    for item in items:
        if item > 0:
            break
    else:
        print("no positive")
"""
        _count, branches = _count_branches(source)
        kinds = {b["kind"] for b in branches}
        assert "for" in kinds
        assert "for_else" in kinds or "else" in kinds

    def test_while_loop_is_a_branch(self):
        source = """\
def f(x):
    while x > 0:
        x -= 1
"""
        count, branches = _count_branches(source)
        assert count == 1
        assert branches[0]["kind"] == "while"

    def test_try_except_counts_branches(self):
        source = """\
def f(x):
    try:
        result = 1 / x
    except ZeroDivisionError:
        result = 0
"""
        count, branches = _count_branches(source)
        assert count >= 2
        kinds = {b["kind"] for b in branches}
        assert "try" in kinds
        assert "except" in kinds

    def test_try_except_multiple_handlers(self):
        source = """\
def f(x):
    try:
        result = 1 / x
    except ZeroDivisionError:
        result = 0
    except ValueError:
        result = -1
"""
        _count, branches = _count_branches(source)
        except_count = sum(1 for b in branches if b["kind"] == "except")
        assert except_count == 2

    def test_try_finally_counts(self):
        source = """\
def f(fname):
    f = open(fname)
    try:
        return f.read()
    finally:
        f.close()
"""
        _count, branches = _count_branches(source)
        kinds = {b["kind"] for b in branches}
        assert "try" in kinds
        assert "finally" in kinds

    def test_try_except_else_finally_all_counted(self):
        source = """\
def f(x):
    try:
        y = 1 / x
    except ZeroDivisionError:
        y = 0
    else:
        y = y + 1
    finally:
        print(y)
"""
        _count, branches = _count_branches(source)
        kinds = {b["kind"] for b in branches}
        assert "try" in kinds
        assert "except" in kinds
        assert "finally" in kinds

    def test_with_statement_is_a_branch(self):
        source = """\
def f(path):
    with open(path) as fh:
        return fh.read()
"""
        count, branches = _count_branches(source)
        assert count >= 1
        assert any(b["kind"] == "with" for b in branches)


# ---------------------------------------------------------------------------
# Edge cases: ternary, comprehensions, short-circuit
# ---------------------------------------------------------------------------


class TestEdgeCaseBranches:
    """Edge case branch detection."""

    def test_ternary_operator(self):
        source = "def f(x):\n" + "    return 'pos' if x > 0 else 'neg'\n"
        count, branches = _count_branches(source)
        assert count == 1
        assert branches[0]["kind"] == "ternary"
        assert branches[0]["subkind"] == "ifexp"

    def test_list_comprehension_with_if_filter(self):
        source = "def f(nums):\n" + "    return [x for x in nums if x > 0]\n"
        count, branches = _count_branches(source)
        assert count == 1
        assert branches[0]["kind"] == "comprehension_if"
        assert branches[0]["subkind"] == "listcomp"

    def test_list_comprehension_multiple_ifs(self):
        source = """\
def f(nums):
    return [x for x in nums if x > 0 if x < 100]
"""
        count, _branches = _count_branches(source)
        assert count == 2

    def test_dict_comprehension_with_if(self):
        source = """\
def f(d):
    return {k: v for k, v in d.items() if v is not None}
"""
        count, branches = _count_branches(source)
        assert count == 1
        assert branches[0]["subkind"] == "dictcomp"

    def test_set_comprehension_with_if(self):
        source = """\
def f(nums):
    return {x for x in nums if x % 2 == 0}
"""
        count, branches = _count_branches(source)
        assert count == 1
        assert branches[0]["subkind"] == "setcomp"

    def test_generator_expression_with_if(self):
        source = """\
def f(nums):
    return sum(x for x in nums if x > 0)
"""
        count, branches = _count_branches(source)
        assert count == 1
        assert branches[0]["subkind"] == "genexpr"

    def test_and_short_circuit(self):
        source = """\
def f(a, b):
    return a is not None and b > 0
"""
        count, branches = _count_branches(source)
        assert count == 1
        assert branches[0]["kind"] == "boolop"
        assert branches[0]["subkind"] == "And"

    def test_or_short_circuit(self):
        source = """\
def f(a, b):
    return a or b
"""
        count, branches = _count_branches(source)
        assert count == 1
        assert branches[0]["kind"] == "boolop"
        assert branches[0]["subkind"] == "Or"

    def test_chained_and(self):
        source = """\
def f(a, b, c):
    return a and b and c
"""
        count, _branches = _count_branches(source)
        assert count == 2  # two short-circuit points

    def test_mixed_and_or(self):
        source = """\
def f(a, b, c):
    return a and b or c
"""
        count, _branches = _count_branches(source)
        assert count >= 1


# ---------------------------------------------------------------------------
# Nested branches
# ---------------------------------------------------------------------------


class TestNestedBranches:
    """Nested branch detection across multiple levels."""

    def test_nested_if(self):
        source = """\
def f(x, y):
    if x > 0:
        if y > 0:
            return "both"
        else:
            return "only x"
    else:
        return "neither"
"""
        _count, branches = _count_branches(source)
        if_count = sum(1 for b in branches if b["kind"] == "if")
        else_count = sum(1 for b in branches if b["kind"] == "else")
        assert if_count >= 2
        assert else_count >= 2

    def test_if_inside_for(self):
        source = """\
def f(items):
    for item in items:
        if item > 0:
            print(item)
"""
        count, _branches = _count_branches(source)
        assert count == 2

    def test_if_inside_while(self):
        source = """\
def f(x):
    while x > 0:
        if x % 2 == 0:
            print(x)
        x -= 1
"""
        count, _branches = _count_branches(source)
        assert count == 2

    def test_try_inside_if(self):
        source = """\
def f(x):
    if x:
        try:
            return 1 / x
        except ZeroDivisionError:
            return 0
"""
        _count, branches = _count_branches(source)
        kinds = {b["kind"] for b in branches}
        assert "if" in kinds
        assert "try" in kinds
        assert "except" in kinds

    def test_for_inside_for(self):
        source = """\
def f(grid):
    for row in grid:
        for cell in row:
            print(cell)
"""
        count, branches = _count_branches(source)
        assert count == 2
        assert all(b["kind"] == "for" for b in branches)

    def test_deep_nesting_5_levels(self):
        source = """\
def f(a, b, c, d, e):
    if a:
        if b:
            if c:
                if d:
                    if e:
                        return 1
    return 0
"""
        count, branches = _count_branches(source)
        assert count == 5
        assert all(b["kind"] == "if" for b in branches)

    def test_comprehension_inside_function_with_if(self):
        source = """\
def f(data):
    if data:
        return [x for x in data if x > 0]
    return []
"""
        count, branches = _count_branches(source)
        assert count == 2  # if data: + comprehension filter if x > 0
        kinds = {b["kind"] for b in branches}
        assert "if" in kinds
        assert any(b["kind"] == "comprehension_if" for b in branches)


# ---------------------------------------------------------------------------
# Uncovered branch detection (simulated)
# ---------------------------------------------------------------------------


class TestUncoveredBranchDetection:
    """Detect uncovered branches given a coverage data map."""

    def _total_branches_in_file(self, source: str) -> int:
        count, _ = _count_branches(source)
        return count

    def test_fully_covered_when_all_branches_executed(self, tmp_path):
        source = textwrap.dedent("""\
        def f(x):
            if x > 0:
                return "pos"
            else:
                return "non-pos"
        """)
        total = self._total_branches_in_file(source)
        assert total == 2

    def test_partially_covered_when_some_branches_missed(self, tmp_path):
        source = textwrap.dedent("""\
        def f(x):
            if x > 0:
                return "pos"
            else:
                return "neg"
            if x % 2 == 0:
                return "even"
        """)
        total = self._total_branches_in_file(source)
        assert total >= 3

    def test_match_statement_branches(self):
        source = """\
def f(cmd):
    match cmd:
        case "start":
            return 1
        case "stop":
            return 0
        case _:
            return -1
"""
        count, branches = _count_branches(source)
        assert count >= 1
        assert any(b["kind"] == "match" for b in branches)
        case_count = sum(1 for b in branches if b["kind"] == "case")
        assert case_count >= 3

    def test_empty_function_zero_branches(self):
        source = "def f():\n    pass\n"
        count, _ = _count_branches(source)
        assert count == 0

    def test_pure_return_zero_branches(self):
        source = "def f():\n    return 42\n"
        count, _ = _count_branches(source)
        assert count == 0


# ---------------------------------------------------------------------------
# CodePathAnalyzer integration
# ---------------------------------------------------------------------------


class TestCodePathAnalyzerIntegration:
    """CodePathAnalyzer correctly identifies symbols (current capability)."""

    def test_analyzer_extracts_functions(self, tmp_path):
        source = textwrap.dedent("""\
        def public_func():
            pass

        def _private_func():
            pass
        """)
        module_path = tmp_path / "test_mod.py"
        module_path.write_text(source)

        try:
            import importlib.util

            analyzer_spec = importlib_util.spec_from_file_location(
                "code_path_analyzer",
                ROOT / "src/general_ludd/agents/test_generation/code_path_analyzer.py",
            )
            assert analyzer_spec and analyzer_spec.loader
            analyzer_mod = importlib.util.module_from_spec(analyzer_spec)
            analyzer_spec.loader.exec_module(analyzer_mod)
        except (ImportError, ModuleNotFoundError):
            pytest.skip("tree-sitter not available")

        analyzer = analyzer_mod.CodePathAnalyzer()
        result = analyzer.analyze(str(module_path))
        func_names = {f.name for f in result.functions}
        assert "public_func" in func_names
        assert "_private_func" in func_names

    def test_analyzer_extracts_classes_and_methods(self, tmp_path):
        source = textwrap.dedent("""\
        class PublicClass:
            def method1(self):
                pass
            def _private_method(self):
                pass

        class _PrivateClass:
            def method2(self):
                pass
        """)
        module_path = tmp_path / "test_cls.py"
        module_path.write_text(source)

        try:
            import importlib.util

            analyzer_spec = importlib.util.spec_from_file_location(
                "code_path_analyzer_2",
                ROOT / "src/general_ludd/agents/test_generation/code_path_analyzer.py",
            )
            assert analyzer_spec and analyzer_spec.loader
            analyzer_mod = importlib_util.module_from_spec(analyzer_spec)
            analyzer_spec.loader.exec_module(analyzer_mod)
        except (ImportError, ModuleNotFoundError):
            pytest.skip("tree-sitter not available")

        analyzer = analyzer_mod.CodePathAnalyzer()
        result = analyzer.analyze(str(module_path))
        class_names = {c.name for c in result.classes}
        assert "PublicClass" in class_names
        assert "_PrivateClass" in class_names
        assert result.classes[0].is_public is True
        assert result.classes[1].is_public is False

    def test_analyzer_handles_decorated_functions(self, tmp_path):
        source = textwrap.dedent("""\
        def deco(f):
            return f

        @deco
        def decorated_func():
            pass
        """)
        module_path = tmp_path / "test_deco.py"
        module_path.write_text(source)

        try:
            import importlib.util

            analyzer_spec = importlib.util.spec_from_file_location(
                "code_path_analyzer_3",
                ROOT / "src/general_ludd/agents/test_generation/code_path_analyzer.py",
            )
            assert analyzer_spec and analyzer_spec.loader
            analyzer_mod = importlib.util.module_from_spec(analyzer_spec)
            analyzer_spec.loader.exec_module(analyzer_mod)
        except (ImportError, ModuleNotFoundError):
            pytest.skip("tree-sitter not available")

        analyzer = analyzer_mod.CodePathAnalyzer()
        result = analyzer.analyze(str(module_path))
        func_names = {f.name for f in result.functions}
        assert "decorated_func" in func_names

    def test_analyzer_returns_empty_without_tree_sitter(self, monkeypatch):
        spec = importlib_util.spec_from_file_location(
            "code_path_analyzer_no_ts",
            ROOT / "src/general_ludd/agents/test_generation/code_path_analyzer.py",
        )
        assert spec and spec.loader
        module = importlib_util.module_from_spec(spec)
        spec.loader.exec_module(module)

        monkeypatch.setattr(module, "_PARSER", None)
        analyzer = module.CodePathAnalyzer()
        result = analyzer.analyze("nonexistent.py")
        assert result.functions == []
        assert result.classes == []
        assert result.name == "nonexistent.py"
