"""Deep docstring coverage tests — scan src/general_ludd/ for documentation completeness.

Covers: module docstrings, class docstrings, public function/method docstrings,
param/return documentation, and special forms (dataclasses, enums, exceptions,
properties, static/class methods, async functions).
"""

from __future__ import annotations

import ast
import os
import pathlib

_SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "general_ludd"
_SRC_ROOT_STR = str(_SRC_ROOT)


def _collect_py_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for root, _dirs, filenames in os.walk(_SRC_ROOT_STR):
        if root.endswith("__pycache__"):
            continue
        for name in filenames:
            if name.endswith(".py"):
                files.append(pathlib.Path(root) / name)
    return sorted(files)


def _parse_file(path: pathlib.Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def _has_parameter(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for a in node.args.args:
        if _is_public(a.arg) and a.arg != "self" and a.arg != "cls":
            return True
    if node.args.vararg or node.args.kwarg:
        return True
    return bool(node.args.kwonlyargs)


def _has_return_hint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return node.returns is not None


def _has_yield(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(isinstance(stmt, (ast.Yield, ast.YieldFrom)) for stmt in ast.walk(node))


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(_SRC_ROOT.parent).with_suffix("")
    return str(rel).replace(os.sep, ".")


def _is_dataclass(cls: ast.ClassDef) -> bool:
    for dec in cls.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "dataclass":
            return True
        if isinstance(dec, ast.Call):
            fn = dec.func
            if isinstance(fn, ast.Name) and fn.id == "dataclass":
                return True
            if isinstance(fn, ast.Attribute) and fn.attr == "dataclass":
                return True
    return False


def _is_enum(cls: ast.ClassDef) -> bool:
    for base in cls.bases:
        if isinstance(base, ast.Name) and base.id in ("Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"):
            return True
    return False


def _is_exception(cls: ast.ClassDef) -> bool:
    for base in cls.bases:
        name = None
        if isinstance(base, ast.Name):
            name = base.id
        elif isinstance(base, ast.Attribute):
            name = base.attr
        if name and ("Error" in name or "Exception" in name or "Warning" in name):
            return True
        if name and name in (
            "BaseException",
            "RuntimeError",
            "ValueError",
            "TypeError",
            "KeyError",
            "OSError",
            "IOError",
        ):
            return True
    return False


def _get_decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    names: list[str] = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            names.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            names.append(dec.attr)
    return names


def _count_source_lines(node: ast.AST) -> int:
    end = getattr(node, "end_lineno", None)
    start = getattr(node, "lineno", 0)
    if end is None or start == 0:
        return 1
    return end - start + 1


class TestModuleDocstrings:
    def test_all_modules_have_module_docstring(self) -> None:
        """Every .py file in src/general_ludd/ must have a module-level docstring."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            doc = ast.get_docstring(tree)
            if not doc:
                missing.append(_module_name(path))
        if missing:
            raise AssertionError(f"{len(missing)} module(s) missing module docstring:\n  " + "\n  ".join(missing))

    def test_subpackage_init_files_have_module_docstring(self) -> None:
        """Every __init__.py must describe its subpackage."""
        missing: list[str] = []
        for path in _collect_py_files():
            if path.name != "__init__.py":
                continue
            tree = _parse_file(path)
            if tree is None:
                continue
            doc = ast.get_docstring(tree)
            if not doc:
                missing.append(_module_name(path))
        if missing:
            raise AssertionError(f"{len(missing)} __init__.py file(s) missing docstring:\n  " + "\n  ".join(missing))

    def test_modules_with_classes_have_descriptive_docstrings(self) -> None:
        """Modules containing classes should have non-trivial docstrings (>=20 chars)."""
        stubby: list[tuple[str, str]] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            doc = ast.get_docstring(tree)
            if doc is None:
                continue
            has_class = any(isinstance(n, ast.ClassDef) for n in ast.iter_child_nodes(tree))
            if has_class and len(doc.strip()) < 20:
                stubby.append((_module_name(path), repr(doc)))
        if stubby:
            raise AssertionError(
                f"{len(stubby)} class-bearing module(s) have stub docstrings (<20 chars):\n  "
                + "\n  ".join(f"{m}: {d}" for m, d in stubby)
            )


class TestClassDocstrings:
    def test_public_classes_have_docstrings(self) -> None:
        """Every public class (not starting with _) must have a docstring."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not _is_public(node.name):
                    continue
                if not ast.get_docstring(node):
                    missing.append(f"{_module_name(path)}.{node.name}")
        if missing:
            raise AssertionError(f"{len(missing)} public class(es) missing docstring:\n  " + "\n  ".join(missing))

    def test_dataclass_classes_have_docstrings(self) -> None:
        """Every @dataclass class must have a docstring."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not _is_public(node.name):
                    continue
                if not _is_dataclass(node):
                    continue
                if not ast.get_docstring(node):
                    missing.append(f"{_module_name(path)}.{node.name}")
        if missing:
            raise AssertionError(f"{len(missing)} dataclass(es) missing docstring:\n  " + "\n  ".join(missing))

    def test_enum_classes_have_docstrings(self) -> None:
        """Every Enum class must have a docstring."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not _is_public(node.name):
                    continue
                if not _is_enum(node):
                    continue
                if not ast.get_docstring(node):
                    missing.append(f"{_module_name(path)}.{node.name}")
        if missing:
            raise AssertionError(f"{len(missing)} enum class(es) missing docstring:\n  " + "\n  ".join(missing))

    def test_exception_classes_have_docstrings(self) -> None:
        """Every custom Exception/Error class must have a docstring describing when raised."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not _is_public(node.name):
                    continue
                if not _is_exception(node):
                    continue
                if not ast.get_docstring(node):
                    missing.append(f"{_module_name(path)}.{node.name}")
        if missing:
            raise AssertionError(f"{len(missing)} exception class(es) missing docstring:\n  " + "\n  ".join(missing))


class TestFunctionDocstrings:
    def test_public_functions_have_docstrings(self) -> None:
        """Every public module-level function must have a docstring."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not _is_public(node.name):
                    continue
                if not ast.get_docstring(node):
                    missing.append(f"{_module_name(path)}.{node.name}")
        if missing:
            raise AssertionError(f"{len(missing)} public function(s) missing docstring:\n  " + "\n  ".join(missing))

    def test_public_functions_over_5_lines_have_docstrings(self) -> None:
        """Public functions with >5 lines of body must have docstrings (strict check)."""
        missing: list[tuple[str, int]] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not _is_public(node.name):
                    continue
                if _count_source_lines(node) <= 5:
                    continue
                if not ast.get_docstring(node):
                    missing.append((f"{_module_name(path)}.{node.name}", _count_source_lines(node)))
        if missing:
            raise AssertionError(
                f"{len(missing)} multi-line public function(s) missing docstring:\n  "
                + "\n  ".join(f"{m} ({n} lines)" for m, n in missing)
            )

    def test_async_functions_have_docstrings(self) -> None:
        """Every public async function must have a docstring."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue
                if not _is_public(node.name):
                    continue
                if not ast.get_docstring(node):
                    missing.append(f"{_module_name(path)}.{node.name}")
        if missing:
            raise AssertionError(
                f"{len(missing)} public async function(s) missing docstring:\n  " + "\n  ".join(missing)
            )


class TestMethodDocstrings:
    def test_public_methods_have_docstrings(self) -> None:
        """Every public method (not starting with _) must have a docstring."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for item in node.body:
                    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if not _is_public(item.name):
                        continue
                    if item.name in ("__init__", "__post_init__", "__new__"):
                        if not ast.get_docstring(item):
                            missing.append(f"{_module_name(path)}.{node.name}.{item.name}")
                        continue
                    if item.name.startswith("__") and item.name.endswith("__"):
                        continue
                    if not ast.get_docstring(item):
                        missing.append(f"{_module_name(path)}.{node.name}.{item.name}")
        if missing:
            raise AssertionError(f"{len(missing)} public method(s) missing docstring:\n  " + "\n  ".join(missing))

    def test_init_methods_have_docstrings(self) -> None:
        """Every __init__ and __post_init__ method must have a docstring."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for item in node.body:
                    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if item.name not in ("__init__", "__post_init__"):
                        continue
                    if not ast.get_docstring(item):
                        missing.append(f"{_module_name(path)}.{node.name}.{item.name}")
        if missing:
            raise AssertionError(
                f"{len(missing)} __init__ / __post_init__ method(s) missing docstring:\n  " + "\n  ".join(missing)
            )

    def test_static_methods_have_docstrings(self) -> None:
        """Every public @staticmethod must have a docstring."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for item in node.body:
                    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if not _is_public(item.name):
                        continue
                    decorators = _get_decorator_names(item)
                    if "staticmethod" in decorators and not ast.get_docstring(item):
                        missing.append(f"{_module_name(path)}.{node.name}.{item.name}")
        if missing:
            raise AssertionError(
                f"{len(missing)} public @staticmethod(s) missing docstring:\n  " + "\n  ".join(missing)
            )

    def test_classmethods_have_docstrings(self) -> None:
        """Every public @classmethod must have a docstring."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for item in node.body:
                    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if not _is_public(item.name):
                        continue
                    decorators = _get_decorator_names(item)
                    if "classmethod" in decorators and not ast.get_docstring(item):
                        missing.append(f"{_module_name(path)}.{node.name}.{item.name}")
        if missing:
            raise AssertionError(f"{len(missing)} public @classmethod(s) missing docstring:\n  " + "\n  ".join(missing))

    def test_property_getters_have_docstrings(self) -> None:
        """Every public @property getter must have a docstring."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for item in node.body:
                    if not isinstance(item, ast.FunctionDef):
                        continue
                    if not _is_public(item.name):
                        continue
                    decorators = _get_decorator_names(item)
                    if "property" in decorators and not ast.get_docstring(item):
                        missing.append(f"{_module_name(path)}.{node.name}.{item.name}")
        if missing:
            raise AssertionError(
                f"{len(missing)} public @property getter(s) missing docstring:\n  " + "\n  ".join(missing)
            )


class TestParamReturnDocs:
    def test_functions_with_parameters_have_param_docs(self) -> None:
        """Every public function with parameters should document them via :param: or Args:."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not _is_public(node.name):
                    continue
                if not _has_parameter(node):
                    continue
                doc = ast.get_docstring(node)
                if doc is None:
                    continue
                doc_lower = doc.lower()
                has_param_doc = (
                    ":param" in doc_lower
                    or ":arg" in doc_lower
                    or ":keyword" in doc_lower
                    or "args:" in doc_lower
                    or "parameters:" in doc_lower
                    or "arguments:" in doc_lower
                    or doc_lower.strip().startswith("args")
                    or doc_lower.strip().startswith("param")
                )
                if not has_param_doc:
                    missing.append(f"{_module_name(path)}.{node.name}")
        if missing:
            raise AssertionError(
                f"{len(missing)} function(s) with parameters missing param documentation:\n  " + "\n  ".join(missing)
            )

    def test_functions_with_return_type_have_return_docs(self) -> None:
        """Every public function with a non-None return annotation should document it."""
        missing: list[str] = []
        for path in _collect_py_files():
            tree = _parse_file(path)
            if tree is None:
                continue
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not _is_public(node.name):
                    continue
                if not _has_return_hint(node):
                    continue
                if node.returns is not None and isinstance(node.returns, ast.Constant) and node.returns.value is None:
                    continue
                doc = ast.get_docstring(node)
                if doc is None:
                    continue
                doc_lower = doc.lower()
                has_return_doc = (
                    ":return" in doc_lower
                    or ":returns" in doc_lower
                    or ":rtype" in doc_lower
                    or ":yield" in doc_lower
                    or ":yields" in doc_lower
                    or "returns:" in doc_lower
                    or "return:" in doc_lower
                    or doc_lower.strip().startswith("return")
                )
                if not has_return_doc and not _has_yield(node):
                    missing.append(f"{_module_name(path)}.{node.name}")
        if missing:
            raise AssertionError(
                f"{len(missing)} function(s) with return type missing return documentation:\n  " + "\n  ".join(missing)
            )
