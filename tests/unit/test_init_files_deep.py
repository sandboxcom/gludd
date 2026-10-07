"""Deep completeness tests for all __init__.py files in src/general_ludd/."""

from __future__ import annotations

import ast
import importlib
import os
import re
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator


def _ruff_all_sort_key(name: str) -> tuple[int, tuple[tuple[int, object], ...]]:
    """Reproduce ruff RUF022's isort-style ordering: SCREAMING_SNAKE_CASE
    first, then CamelCase, then everything else; within each group a
    natural (digit-run-numeric, case-sensitive) sort."""
    first = name.lstrip("_")[0] if name else ""
    if first.isupper() and name.upper() == name:
        group = 0
    elif first.isupper():
        group = 1
    else:
        group = 2
    natural: list[tuple[int, object]] = []
    for part in re.split(r"(\d+)", name):
        if part.isdigit():
            natural.append((0, int(part)))
        else:
            natural.append((1, tuple(part)))
    return (group, tuple(natural))


SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "general_ludd"

# Init files with imports but no __all__ for legitimate design reasons
# (e.g. lazy-registration pattern, TYPE_CHECKING-only imports).
NO_ALL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "__init__.py",
        "routers/__init__.py",
        "compat/__init__.py",
    }
)

# Init files whose __all__ is intentionally in category-grouped order, not
# alphabetical. Flagged only for information.
UNSORTED_ALLOWLIST: frozenset[str] = frozenset()

# Init files with names in __all__ that aren't top-level import names because
# they're imported via explicit `from ... import ... as name` that matches
# differently, or imported in nested scopes. Reviewed per-file.
ORPHAN_ALLOWLIST: dict[str, frozenset[str]] = {
    # PEP 562 lazy __getattr__: the model lives in db.models.py and is
    # imported inside __getattr__ to avoid a circular import at package init.
    "remediation/__init__.py": frozenset({"RemediationActionModel"}),
    # PEP 562 lazy __getattr__: weights and small-model-policy names are
    # deferred past package init to break an import cycle with
    # schemas.benchmark (see the __getattr__ comment in the module).
    "routing_roles/__init__.py": frozenset(
        {
            "CapabilityEvidence",
            "CompletionAction",
            "CompletionEvidence",
            "DispatchAction",
            "ModelIdentity",
            "PolicyConfig",
            "RoleWeights",
            "SmallModelTaskPolicy",
            "SmallModelTaskSpec",
            "TaskContract",
            "TaskImpact",
            "task_weights",
            "weights_for",
        }
    ),
}


def _normalize_rel(path: Path) -> str:
    return str(path.relative_to(SRC_ROOT))


def _walk_init_files() -> Generator[tuple[Path, str], None, None]:
    for path in sorted(SRC_ROOT.rglob("__init__.py")):
        yield path, _normalize_rel(path)


def _module_name(path: Path) -> str:
    relative = path.relative_to(SRC_ROOT)
    return ".".join(("general_ludd", *relative.parts))


def _is_discoverable_namespace(path: Path) -> bool:
    """Return whether an unmarked source directory is a PEP 420 package."""
    spec = find_spec(_module_name(path))
    return spec is not None and spec.origin is None and spec.submodule_search_locations is not None


META_MODULES: frozenset[str] = frozenset({"__future__", "typing", "typing_extensions"})
META_NAMES: frozenset[str] = frozenset({"annotations", "TYPE_CHECKING"})


def _parse_names_available(source: str) -> set[str]:
    """Return all names available at module level: imported names AND
    names defined in the init file itself (classes, functions, top-level
    assignments, annotated assignments). Excludes future/typing meta-imports.
    Private (underscore-prefixed) names are included because they may
    legitimately appear in __all__."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module in META_MODULES:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                if name in META_NAMES:
                    continue
                names.add(name)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id != "__all__":
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id != "__all__":
            names.add(node.target.id)
    return names


def _parse_imports_only(source: str) -> set[str]:
    """Return only imported names from general_ludd project packages
    (not stdlib/typing/third-party imports). Private (underscore-prefixed)
    imports are excluded: they are implementation details, not part of the
    public re-export surface."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            if node.module in META_MODULES:
                continue
            if not node.module.startswith("general_ludd.") and node.module != "general_ludd":
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                if name in META_NAMES or name.startswith("_"):
                    continue
                names.add(name)
    return names


def _parse_names_defined(source: str) -> set[str]:
    """Return names of classes, functions, and public top-level assignments
    defined in the init file."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.FunctionDef):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id != "__all__" and not target.id.startswith("_"):
                    names.add(target.id)
    return names


def _parse_all(source: str) -> tuple[list[str] | None, int | None]:
    """Return (names, lineno) for __all__ if present, else (None, None)."""
    tree = ast.parse(source)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "__all__"
                    and isinstance(node.value, (ast.List, ast.Tuple))
                ):
                    items: list[str] = []
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            items.append(elt.value)
                    return items, node.lineno
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "__all__":
            return [], node.lineno
    return None, None


# ── tests ──────────────────────────────────────────────────────────────────


class TestPackageDirectoriesDiscoverable:
    """Every module directory must be a regular or PEP 420 package."""

    @pytest.fixture
    def undiscoverable(self) -> list[str]:
        undiscoverable: list[str] = []
        module_directories = sorted({path.parent for path in SRC_ROOT.rglob("*.py")})
        for directory in module_directories:
            if directory == SRC_ROOT or (directory / "__init__.py").is_file():
                continue
            if not _is_discoverable_namespace(directory):
                undiscoverable.append(str(directory.relative_to(SRC_ROOT)))
        return undiscoverable

    def test_module_directories_are_discoverable(self, undiscoverable: list[str]) -> None:
        assert not undiscoverable, f"Python module directories are not importable packages: {undiscoverable}"


class TestInitFilesParse:
    """Package markers may be empty, but every __init__.py must parse."""

    @pytest.fixture
    def invalid(self) -> dict[str, str]:
        invalid: dict[str, str] = {}
        for path, rel in _walk_init_files():
            try:
                ast.parse(path.read_text(), filename=str(path))
            except SyntaxError as exc:
                invalid[rel] = str(exc)
        return invalid

    def test_init_files_parse(self, invalid: dict[str, str]) -> None:
        assert not invalid, f"Invalid __init__.py files: {invalid}"


class TestAllDeclared:
    """__init__.py files with public imports should declare __all__."""

    @pytest.fixture
    def missing_all(self) -> list[str]:
        missing: list[str] = []
        for path, rel in _walk_init_files():
            if rel in NO_ALL_ALLOWLIST:
                continue
            source = path.read_text()
            imported = _parse_imports_only(source)
            all_list, _ = _parse_all(source)
            if imported and all_list is None:
                missing.append(rel)
        return missing

    def test_init_with_imports_has_all(self, missing_all: list[str]) -> None:
        assert not missing_all, f"Init files with imports but no __all__: {missing_all}"


class TestAllEveryNameResolves:
    """Every name in __all__ must correspond to an import in the file."""

    @pytest.fixture
    def orphans(self) -> dict[str, list[str]]:
        orphans: dict[str, list[str]] = {}
        for path, rel in _walk_init_files():
            source = path.read_text()
            imported = _parse_names_available(source)
            all_list, _ = _parse_all(source)
            if all_list is None:
                continue
            allowed = ORPHAN_ALLOWLIST.get(rel, frozenset())
            missing = [n for n in all_list if n not in imported and n not in allowed]
            if missing:
                orphans[rel] = missing
        return orphans

    def test_all_names_are_imported(self, orphans: dict[str, list[str]]) -> None:
        lines = []
        for rel, names in sorted(orphans.items()):
            lines.append(f"  {rel}: {names}")
        assert not orphans, "Names in __all__ not backed by imports:\n" + "\n".join(lines)


class TestAllEveryImportReExported:
    """Every public top-level import should appear in __all__."""

    @pytest.fixture
    def unexported(self) -> dict[str, list[str]]:
        unexported: dict[str, list[str]] = {}
        for path, rel in _walk_init_files():
            source = path.read_text()
            imported = _parse_imports_only(source)
            all_list, _ = _parse_all(source)
            if all_list is None or not imported:
                continue
            missing = [n for n in imported if n not in all_list]
            if missing:
                unexported[rel] = missing
        return unexported

    def test_all_imports_are_re_exported(self, unexported: dict[str, list[str]]) -> None:
        lines = []
        for rel, names in sorted(unexported.items()):
            lines.append(f"  {rel}: {names}")
        assert not unexported, "Imported names not in __all__:\n" + "\n".join(lines)


class TestAllNoDuplicates:
    """__all__ must not contain duplicate entries."""

    @pytest.fixture
    def duplicates(self) -> dict[str, list[str]]:
        dups: dict[str, list[str]] = {}
        for path, rel in _walk_init_files():
            all_list, _ = _parse_all(path.read_text())
            if all_list is None:
                continue
            seen: set[str] = set()
            dup_names: list[str] = []
            for n in all_list:
                if n in seen:
                    dup_names.append(n)
                seen.add(n)
            if dup_names:
                dups[rel] = dup_names
        return dups

    def test_no_duplicates_in_all(self, duplicates: dict[str, list[str]]) -> None:
        lines = []
        for rel, names in sorted(duplicates.items()):
            lines.append(f"  {rel}: duplicate(s): {names}")
        assert not duplicates, "Duplicate names in __all__:\n" + "\n".join(lines)


class TestAllIsListOrTuple:
    """__all__ must be a list or tuple literal, not another type."""

    @pytest.fixture
    def bad_types(self) -> dict[str, str]:
        bad: dict[str, str] = {}
        for path, rel in _walk_init_files():
            source = path.read_text()
            tree = ast.parse(source)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id == "__all__"
                            and not isinstance(node.value, (ast.List, ast.Tuple))
                        ):
                            bad[rel] = type(node.value).__name__
        return bad

    def test_all_is_list_or_tuple(self, bad_types: dict[str, str]) -> None:
        assert not bad_types, f"__all__ is not list/tuple: {bad_types}"


class TestAllSorted:
    """__all__ entries should be sorted isort-style (SCREAMING_SNAKE_CASE,
    then CamelCase, then the rest; natural sort within each group) for
    readability — the same order ruff's RUF022 enforces."""

    @pytest.fixture
    def unsorted(self) -> dict[str, str]:
        unsorted: dict[str, str] = {}
        for path, rel in _walk_init_files():
            if rel in UNSORTED_ALLOWLIST:
                continue
            all_list, _ = _parse_all(path.read_text())
            if all_list is None or len(all_list) < 2:
                continue
            sorted_list = sorted(all_list, key=_ruff_all_sort_key)
            if all_list != sorted_list:
                unsorted[rel] = f"unsorted ({len(all_list)} entries)"
        return unsorted

    def test_all_is_sorted(self, unsorted: dict[str, str]) -> None:
        lines = []
        for rel, info in sorted(unsorted.items()):
            lines.append(f"  {rel}: {info}")
        assert not unsorted, "__all__ entries not sorted alphabetically:\n" + "\n".join(lines)


class TestInitImportResolves:
    """Every __init__.py can be imported without ImportError (no broken references)."""

    @pytest.fixture
    def failures(self) -> dict[str, str]:
        failures: dict[str, str] = {}
        for _path, rel in _walk_init_files():
            if rel == "__init__.py":
                try:
                    importlib.import_module("general_ludd")
                except Exception as exc:
                    failures[rel] = f"{type(exc).__name__}: {exc}"
                continue
            mod_path = "general_ludd." + rel.replace(os.sep, ".").replace(".__init__.py", "")
            try:
                importlib.import_module(mod_path)
            except Exception as exc:
                failures[rel] = f"{type(exc).__name__}: {exc}"
        return failures

    def test_all_inits_importable(self, failures: dict[str, str]) -> None:
        if not failures:
            return
        lines = []
        for rel, msg in sorted(failures.items()):
            lines.append(f"  {rel}: {msg}")
        pytest.fail("Init files fail to import:\n" + "\n".join(lines))


class TestInitModuleDocstring:
    """Every non-empty __init__.py should document its package API."""

    @pytest.fixture
    def missing_docstring(self) -> list[str]:
        missing: list[str] = []
        for path, rel in _walk_init_files():
            source = path.read_text()
            if not source.strip():
                continue
            tree = ast.parse(source)
            doc = ast.get_docstring(tree)
            if doc is None:
                missing.append(rel)
        return missing

    def test_init_has_docstring(self, missing_docstring: list[str]) -> None:
        assert not missing_docstring, f"__init__.py files missing docstring: {missing_docstring}"


class TestInitAllStringEntries:
    """Every entry in __all__ must be a string literal."""

    @pytest.fixture
    def non_strings(self) -> dict[str, list[int]]:
        bad: dict[str, list[int]] = {}
        for path, rel in _walk_init_files():
            source = path.read_text()
            tree = ast.parse(source)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id == "__all__"
                            and isinstance(node.value, (ast.List, ast.Tuple))
                        ):
                            for i, elt in enumerate(node.value.elts):
                                if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
                                    bad.setdefault(rel, []).append(i)
        return bad

    def test_all_entries_are_strings(self, non_strings: dict[str, list[int]]) -> None:
        lines = []
        for rel, indices in sorted(non_strings.items()):
            lines.append(f"  {rel}: non-string at positions {indices}")
        assert not non_strings, "__all__ entries that are not string literals:\n" + "\n".join(lines)


class TestInitAllNotEmpty:
    """__all__ should not be empty when the file has imports (outside allowlist)."""

    @pytest.fixture
    def empty_all(self) -> dict[str, int]:
        empty: dict[str, int] = {}
        for path, rel in _walk_init_files():
            if rel in NO_ALL_ALLOWLIST:
                continue
            source = path.read_text()
            imported = _parse_names_available(source)
            all_list, _ = _parse_all(source)
            if all_list is not None and len(all_list) == 0 and imported:
                empty[rel] = len(imported)
        return empty

    def test_all_not_empty_when_imports_exist(self, empty_all: dict[str, int]) -> None:
        lines = []
        for rel, count in sorted(empty_all.items()):
            lines.append(f"  {rel}: {count} imports, 0 names in __all__")
        assert not empty_all, "Empty __all__ despite having imports:\n" + "\n".join(lines)


class TestInitNoCircularImports:
    """Importing all __init__.py files in a batch does not cause circular import errors."""

    def test_no_circular_on_batch_import(self) -> None:
        errors: list[str] = []
        for _path, rel in sorted(_walk_init_files()):
            if rel == "__init__.py":
                mod_path = "general_ludd"
            else:
                mod_path = "general_ludd." + rel.replace(os.sep, ".").replace(".__init__.py", "")
            try:
                mod = sys.modules.get(mod_path)
                if mod is not None:
                    importlib.reload(mod)
                else:
                    importlib.import_module(mod_path)
            except ImportError as exc:
                msg = str(exc).lower()
                if "circular" in msg or "most likely due to a circular import" in msg:
                    errors.append(f"{rel}: {exc}")
            except Exception:
                pass
        assert not errors, "Circular imports detected:\n" + "\n".join(errors)


class TestInitFileTotalCount:
    """Smoke test: the expected number of __init__.py files in the codebase."""

    def test_init_file_count_reasonable(self) -> None:
        count = len(list(_walk_init_files()))
        assert count > 70, f"Expected >70 __init__.py files, found {count}"
        assert count < 200, f"Expected <200 __init__.py files, found {count}"
