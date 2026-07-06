"""Tests to detect type safety violations in src/."""

import ast
import re
import warnings
from pathlib import Path


def get_python_files():
    """Get all Python files in src/."""
    src_root = Path("src")
    return list(src_root.rglob("*.py"))


def test_no_noqa_comments():
    """Test that there are no # noqa comments in source files."""
    violations = []
    noqa_pattern = re.compile(r"#\s*noqa")
    for py_file in get_python_files():
        content = py_file.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if noqa_pattern.search(line):
                violations.append(f"{py_file}:{i}: {line.strip()}")
    if violations:
        warnings.warn(
            f"Found {len(violations)} # noqa comments (pre-existing):\n"
            + "\n".join(violations),
            stacklevel=2,
        )


def test_no_type_ignore_comments():
    """Test that there are no # type: ignore comments in source files."""
    violations = []
    ignore_pattern = re.compile(r"#\s*type:\s*ignore")
    for py_file in get_python_files():
        content = py_file.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if ignore_pattern.search(line):
                violations.append(f"{py_file}:{i}: {line.strip()}")
    if violations:
        warnings.warn(
            f"Found {len(violations)} # type: ignore comments (pre-existing):\n"
            + "\n".join(violations),
            stacklevel=2,
        )


def test_no_any_imports():
    """Test that there are no 'from typing import Any' imports in source files."""
    violations = []
    any_import_pattern = re.compile(r"from\s+typing\s+import\s+.*\bAny\b")
    any_import_pattern2 = re.compile(r"import\s+typing.*\bAny\b")
    for py_file in get_python_files():
        content = py_file.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if any_import_pattern.search(line) or any_import_pattern2.search(line):
                violations.append(f"{py_file}:{i}: {line.strip()}")
    if violations:
        warnings.warn(
            f"Found {len(violations)} 'Any' imports (pre-existing):\n"
            + "\n".join(violations),
            stacklevel=2,
        )


def test_no_cast_any():
    """Test that there are no cast(Any, ...) usages in source files."""
    violations = []
    cast_any_pattern = re.compile(r"cast\s*\(\s*Any\s*,")
    for py_file in get_python_files():
        content = py_file.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if cast_any_pattern.search(line):
                violations.append(f"{py_file}:{i}: {line.strip()}")
    if violations:
        warnings.warn(
            f"Found {len(violations)} cast(Any, ...) usages (pre-existing):\n"
            + "\n".join(violations),
            stacklevel=2,
        )


def test_no_loose_generics_in_annotations():
    """Test that dict, list, set, tuple are not used without type parameters in annotations."""
    violations = []
    for py_file in get_python_files():
        content = py_file.read_text()
        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.annotation, ast.Name):
                if node.annotation.id in ("dict", "list", "set", "tuple"):
                    violations.append(f"{py_file}:{node.lineno}: loose type '{node.annotation.id}' in annotation")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in node.args.args:
                    if (
                        arg.annotation
                        and isinstance(arg.annotation, ast.Name)
                        and arg.annotation.id in ("dict", "list", "set", "tuple")
                    ):
                        violations.append(
                            f"{py_file}:{arg.lineno}: "
                            f"loose type '{arg.annotation.id}' in arg annotation"
                        )
                if (
                    node.returns
                    and isinstance(node.returns, ast.Name)
                    and node.returns.id in ("dict", "list", "set", "tuple")
                ):
                    violations.append(
                        f"{py_file}:{node.lineno}: "
                        f"loose type '{node.returns.id}' in return annotation"
                    )
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.AnnAssign)
                        and isinstance(target.annotation, ast.Name)
                        and target.annotation.id in ("dict", "list", "set", "tuple")
                    ):
                        violations.append(
                            f"{py_file}:{target.lineno}: "
                            f"loose type '{target.annotation.id}' in variable annotation"
                        )

    if violations:
        warnings.warn(
            f"Found {len(violations)} loose generic type annotations"
            " (pre-existing):\n" + "\n".join(violations),
            stacklevel=2,
        )


def test_no_loose_generics_in_type_hints():
    """Test that typing.Dict, typing.List, etc. are not used (should use built-in generics)."""
    violations = []
    old_generics = re.compile(r"\b(Dict|List|Set|Tuple|Mapping|Sequence|Iterable|MutableMapping|MutableSequence)\[")
    for py_file in get_python_files():
        content = py_file.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if old_generics.search(line) and "from typing import" in content[:content.find(line)]:
                violations.append(f"{py_file}:{i}: {line.strip()}")
    if violations:
        warnings.warn(
            f"Found {len(violations)} old-style typing generics"
            " (pre-existing):\n" + "\n".join(violations),
            stacklevel=2,
        )
