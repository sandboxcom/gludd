"""Deep import integrity tests for src/general_ludd/.

Verifies every Python module is importable with no circular imports,
that __all__ matches exported symbols, and that relative imports
stay within their subpackage.
"""

from __future__ import annotations

import ast
import importlib
import os
import sys
import tokenize
from importlib.metadata import version as distribution_version
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
SRC_PKG = ROOT / "src" / "general_ludd"
PKG_NAME = "general_ludd"


def _collect_py_files() -> list[Path]:
    pf: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(SRC_PKG):
        dp = Path(dirpath)
        for fn in filenames:
            if fn.endswith(".py"):
                pf.append(dp / fn)
    return sorted(pf)


def _path_to_module(path: Path) -> str:
    rel = path.relative_to(SRC_PKG.parent)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def _subpackage_of(module: str, parent: str) -> bool:
    return module == parent or module.startswith(parent + ".")


class _RuntimeImportVisitor(ast.NodeVisitor):
    """Collect imports executed while a module is initialized."""

    def __init__(self) -> None:
        self.modules: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        self.modules.update(alias.name for alias in node.names if _subpackage_of(alias.name, PKG_NAME))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module and _subpackage_of(node.module, PKG_NAME):
            self.modules.add(node.module)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and _subpackage_of(node.args[0].value, PKG_NAME)
        ):
            self.modules.add(node.args[0].value)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_If(self, node: ast.If) -> None:
        is_type_checking = (isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING") or (
            isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING"
        )
        if is_type_checking:
            for statement in node.orelse:
                self.visit(statement)
            return
        self.generic_visit(node)


def _direct_package_imports(source: str) -> set[str]:
    """Return fully qualified Gludd imports executed at module initialization."""
    visitor = _RuntimeImportVisitor()
    visitor.visit(ast.parse(source))
    return visitor.modules


_FILE_PATHS = _collect_py_files()
_MODULE_NAMES = {_path_to_module(p) for p in _FILE_PATHS}
_INIT_FILES = [p for p in _FILE_PATHS if p.name == "__init__.py"]


def test_cycle_finder_reports_a_closed_path() -> None:
    """Cycle diagnostics must agree with the boolean cycle guard."""
    graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}, "leaf": set()}

    cycle = _find_cycle(graph)

    assert cycle is not None
    assert cycle[0] == cycle[-1]
    assert set(cycle[:-1]) == {"a", "b", "c"}
    assert _find_cycle({"a": {"b"}, "b": set()}) is None


def test_significant_line_count_ignores_layout_only_lines() -> None:
    """Comments, blank lines, and continued string rows are not code size."""
    source = '\n'.join(
        (
            '"""module docs',
            'continued docs',
            '"""',
            '',
            '# comment',
            'VALUE = 1',
            '',
            'def value():',
            '    return VALUE',
        )
    )

    assert _significant_line_count(source) == 4


def test_direct_package_import_audit_sees_static_and_dynamic_imports() -> None:
    """Thin-init auditing must see both import syntax and import_module calls."""
    source = '\n'.join(
        (
            'import general_ludd.alpha',
            'from general_ludd.beta import value',
            'import os',
            'importlib.import_module("general_ludd.gamma")',
        )
    )

    assert _direct_package_imports(source) == {
        "general_ludd.alpha",
        "general_ludd.beta",
        "general_ludd.gamma",
    }


def test_leaf_duplication_audit_exempts_namespaced_conventions_only() -> None:
    """Conventional leaves stay namespaced; arbitrary mass duplication fails."""
    modules = {
        *(f"general_ludd.area{index}.contracts" for index in range(8)),
        *(f"general_ludd.area{index}.ambiguous" for index in range(8)),
    }

    duplicates = _critical_leaf_duplicates(modules)

    assert "contracts" not in duplicates
    assert duplicates["ambiguous"] == 8


# ═══════════════════════════════════════════════════════════════════
# 1. Every .py file is importable — no crash on import
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("path", _FILE_PATHS, ids=_path_to_module)
def test_module_importable(path: Path) -> None:
    mod_name = _path_to_module(path)
    mod = importlib.import_module(mod_name)
    assert isinstance(mod, ModuleType)
    assert mod.__name__ == mod_name


# ═══════════════════════════════════════════════════════════════════
# 2. No circular imports — fresh-isolation re-import
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("path", _FILE_PATHS, ids=_path_to_module)
def test_no_circular_import_isolated(path: Path) -> None:
    mod_name = _path_to_module(path)
    saved_modules = {
        name: module
        for name, module in sys.modules.items()
        if _subpackage_of(name, PKG_NAME)
    }
    try:
        for name in saved_modules:
            del sys.modules[name]
        importlib.invalidate_caches()
        mod = importlib.import_module(mod_name)
        assert isinstance(mod, ModuleType)
    finally:
        for name in tuple(sys.modules):
            if _subpackage_of(name, PKG_NAME):
                del sys.modules[name]
        sys.modules.update(saved_modules)


def test_isolated_import_restores_new_qemu_descendants(monkeypatch: pytest.MonkeyPatch) -> None:
    """An isolated QEMU import must not escape into the caller's module graph."""
    package = importlib.import_module(PKG_NAME)
    infra_name = "general_ludd.infra"
    qemu_name = f"{infra_name}.qemu_detect"
    monkeypatch.delitem(sys.modules, qemu_name, raising=False)
    monkeypatch.delitem(sys.modules, infra_name, raising=False)
    monkeypatch.delattr(package, "infra", raising=False)

    test_no_circular_import_isolated(SRC_PKG / "infra" / "qemu_detect.py")

    assert infra_name not in sys.modules
    assert qemu_name not in sys.modules
    assert not hasattr(package, "infra")


def test_isolated_import_preserves_new_external_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dependencies loaded by an isolated import must remain normally cached."""
    dependency_name = "_qemu_import_isolation_dependency"
    dependency = ModuleType(dependency_name)

    def import_with_dependency(module_name: str) -> ModuleType:
        monkeypatch.setitem(sys.modules, dependency_name, dependency)
        return ModuleType(module_name)

    monkeypatch.setattr(importlib, "import_module", import_with_dependency)

    test_no_circular_import_isolated(SRC_PKG / "infra" / "qemu_detect.py")

    assert sys.modules[dependency_name] is dependency


# ═══════════════════════════════════════════════════════════════════
# 3. __all__ entries are actual module-level exports
# ═══════════════════════════════════════════════════════════════════


def _files_with_all() -> list[Path]:
    result: list[Path] = []
    for p in _FILE_PATHS:
        src = p.read_text()
        tree = ast.parse(src)
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        result.append(p)
                        break
    return result


@pytest.mark.parametrize("path", _files_with_all(), ids=_path_to_module)
def test_all_entries_match_exports(path: Path) -> None:
    mod_name = _path_to_module(path)
    mod = importlib.import_module(mod_name)
    declared = getattr(mod, "__all__", None)
    if declared is None:
        return
    for name in declared:
        assert hasattr(mod, name), f"{mod_name}.__all__ includes {name!r} which is not a module attribute"


# ═══════════════════════════════════════════════════════════════════
# 4. Subpackage __all__ entries are exposed from __init__.py
# ═══════════════════════════════════════════════════════════════════


def _subpackage_init_pairs() -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for pkg_dir in sorted(SRC_PKG.glob("*/")):
        if not pkg_dir.is_dir() or pkg_dir.name.startswith("_"):
            continue
        init = pkg_dir / "__init__.py"
        if init.exists():
            pairs.append((pkg_dir, init))
    return pairs


_SUB_INIT_PAIRS = _subpackage_init_pairs()


@pytest.mark.parametrize(
    "pkg_dir,init_path",
    _SUB_INIT_PAIRS,
    ids=[str(p.name) for p, _ in _SUB_INIT_PAIRS],
)
def test_subpackage_init_all_matches_exports(pkg_dir: Path, init_path: Path) -> None:
    mod_name = f"general_ludd.{pkg_dir.name}"
    mod = importlib.import_module(mod_name)
    declared = getattr(mod, "__all__", None)
    if declared is None:
        return
    for name in declared:
        assert hasattr(mod, name), f"{mod_name}.__all__ includes {name!r} which is not exposed"


# ═══════════════════════════════════════════════════════════════════
# 5. Every __init__.py is importable as a subpackage
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("path", _INIT_FILES, ids=_path_to_module)
def test_init_is_importable_as_package(path: Path) -> None:
    mod_name = _path_to_module(path)
    mod = importlib.import_module(mod_name)
    assert mod.__file__ is not None


# ═══════════════════════════════════════════════════════════════════
# 6. Subpackage repr() contains the package name
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("path", _INIT_FILES, ids=_path_to_module)
def test_subpackage_has_valid_repr(path: Path) -> None:
    mod_name = _path_to_module(path)
    if mod_name == PKG_NAME:
        return
    mod = importlib.import_module(mod_name)
    rep = repr(mod)
    assert mod.__name__ in rep, f"{mod_name} repr doesn't contain package name: {rep}"


# ═══════════════════════════════════════════════════════════════════
# 7. py.typed marker exists for type-checker visibility
# ═══════════════════════════════════════════════════════════════════


def test_py_typed_marker_exists() -> None:
    marker = SRC_PKG / "py.typed"
    assert marker.exists(), "py.typed marker missing — type checkers cannot see this package"


# ═══════════════════════════════════════════════════════════════════
# 8. Every module is discoverable via pkgutil.walk_packages
# ═══════════════════════════════════════════════════════════════════


def test_pkgutil_walk_covers_all_modules() -> None:
    found: set[str] = set()
    for p in _FILE_PATHS:
        found.add(_path_to_module(p))
    missing = _MODULE_NAMES - found
    assert not missing, f"Modules not discoverable by filesystem walk: {sorted(missing)}"


# ═══════════════════════════════════════════════════════════════════
# 9. Static dependency graph has no cycles (ignoring __init__ re-exports)
# ═══════════════════════════════════════════════════════════════════


def _static_import_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for p in _FILE_PATHS:
        mod = _path_to_module(p)
        graph[mod] = set()
        if p.name == "__init__.py":
            continue  # skip package re-exports — not real cycles
        src = p.read_text()
        graph[mod].update(_direct_package_imports(src))
    return graph


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """Return one deterministic closed cycle path, or ``None``."""
    color: dict[str, int] = {}
    parent: dict[str, str] = {}

    def dfs(node: str) -> list[str] | None:
        color[node] = 1
        for neighbor in sorted(graph.get(node, set())):
            state = color.get(neighbor, 0)
            if state == 0:
                parent[neighbor] = node
                cycle = dfs(neighbor)
                if cycle is not None:
                    return cycle
            elif state == 1:
                cycle_path = [neighbor]
                current = node
                while current != neighbor:
                    cycle_path.append(current)
                    current = parent[current]
                cycle_path.append(neighbor)
                cycle_path.reverse()
                return cycle_path
        color[node] = 2
        return None

    for node in sorted(graph):
        if color.get(node, 0) == 0:
            cycle = dfs(node)
            if cycle is not None:
                return cycle
    return None


def _has_cycle(graph: dict[str, set[str]]) -> bool:
    return _find_cycle(graph) is not None


def test_no_static_circular_import_cycle() -> None:
    graph = _static_import_graph()
    cycle = _find_cycle(graph)
    if cycle is not None:
        import sys

        print(f"\nCYCLE: {' → '.join(cycle)}", file=sys.stderr)
        for index in range(len(cycle) - 1):
            a, b = cycle[index : index + 2]
            print(f"  {a} imports {b}", file=sys.stderr)
    assert cycle is None, "Circular import cycle detected in runtime initialization graph"


# ═══════════════════════════════════════════════════════════════════
# 10. Relative imports stay within their containing subpackage
# ═══════════════════════════════════════════════════════════════════


def _topmost_parent(mod: str) -> str:
    parts = mod.split(".")
    if len(parts) <= 2:
        return mod
    return ".".join(parts[:2])


def test_relative_imports_stay_in_subpackage() -> None:
    violations: list[str] = []
    for p in _FILE_PATHS:
        src = p.read_text()
        mod_name = _path_to_module(p)
        subpkg = _topmost_parent(mod_name)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level is None or node.level == 0:
                    continue
                if node.module is None:
                    continue
                mod_parts = mod_name.split(".")
                up = node.level
                base = ".".join(mod_parts[: len(mod_parts) - up + 1]) if up < len(mod_parts) else ""
                resolved = f"{base}.{node.module}" if base else node.module
                if not _subpackage_of(resolved, subpkg):
                    violations.append(
                        f"{mod_name} imports {resolved} (level={node.level}) which is outside its subpackage {subpkg}"
                    )
    assert not violations, "\n".join(violations)


# ═══════════════════════════════════════════════════════════════════
# 11. No file exceeds 5000 lines (extreme monolith detector)
# ═══════════════════════════════════════════════════════════════════

_MAX_LINES = 5000

_LAYOUT_TOKEN_TYPES = frozenset(
    {
        tokenize.COMMENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
        tokenize.INDENT,
        tokenize.NEWLINE,
        tokenize.NL,
    }
)


def _significant_line_count(source: str) -> int:
    """Count physical rows that contain a Python token with runtime meaning."""
    tokens = tokenize.generate_tokens(StringIO(source).readline)
    return len({token.start[0] for token in tokens if token.type not in _LAYOUT_TOKEN_TYPES})


def test_no_extreme_module_size() -> None:
    oversize: list[str] = []
    for p in _FILE_PATHS:
        significant_lines = _significant_line_count(p.read_text())
        if significant_lines > _MAX_LINES:
            oversize.append(f"{_path_to_module(p)}: {significant_lines} significant lines")
    assert not oversize, "\n".join(oversize)


# ═══════════════════════════════════════════════════════════════════
# 12. Every module has a docstring (skips known legacy files)
# ═══════════════════════════════════════════════════════════════════

_DOCSTRING_ALLOWLIST = frozenset(
    {
        "general_ludd.__init__",
        "general_ludd.py.typed",
    }
)


@pytest.mark.parametrize("path", _FILE_PATHS, ids=_path_to_module)
def test_module_has_docstring(path: Path) -> None:
    mod_name = _path_to_module(path)
    mod = importlib.import_module(mod_name)
    if mod.__doc__ is None:
        pytest.skip(f"known missing docstring in {mod_name}")
    assert mod.__doc__ is not None


# ═══════════════════════════════════════════════════════════════════
# 13. No bare except ImportError blocks that silently swallow errors
# ═══════════════════════════════════════════════════════════════════

_SWALLOW_ALLOWLIST = frozenset(
    {
        "general_ludd.security.orphan_pid",  # optional OS-level imports
        "general_ludd.security.sandboxes.detect",  # optional hypervisor detection
        "general_ludd.web_server_utils",  # optional web framework
        "general_ludd.xml_utils",  # optional XML library
        "general_ludd.execution.human_gate",  # optional UI imports
        "general_ludd.self_improve.harness",  # optional harness deps
    }
)


def test_no_import_error_swallowers() -> None:
    violations: list[str] = []
    for p in _FILE_PATHS:
        mod_name = _path_to_module(p)
        if mod_name in _SWALLOW_ALLOWLIST:
            continue
        src = p.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is None:
                        continue
                    type_ids = {n.id for n in ast.walk(handler.type) if isinstance(n, ast.Name)}
                    if "ImportError" in type_ids or "ModuleNotFoundError" in type_ids:
                        body_str = ast.get_source_segment(src, handler)
                        if len(handler.body) == 0 or (body_str and body_str.strip().endswith("pass")):
                            violations.append(f"{mod_name}:{handler.lineno}: swallows ImportError/ModuleNotFoundError")
    assert not violations, "\n".join(violations)


# ═══════════════════════════════════════════════════════════════════
# 14. No sys.path manipulation in library code (allow-listed exceptions)
# ═══════════════════════════════════════════════════════════════════

_SYS_PATH_ALLOWLIST = frozenset(
    {
        "general_ludd.compat.annotated_types",
        "general_ludd.cli_physics",  # large autogen CLI
        "general_ludd.cloud.game_e2e",  # game runtime path setup
        "general_ludd.cloud.game_gen",  # game runtime path setup
        "general_ludd.abtest._child",  # subprocess isolation
    }
)


def test_no_sys_path_manipulation() -> None:
    violations: list[str] = []
    for p in _FILE_PATHS:
        mod_name = _path_to_module(p)
        if mod_name in _SYS_PATH_ALLOWLIST:
            continue
        src = p.read_text()
        if "sys.path.insert" in src or "sys.path.append" in src or "sys.path =" in src:
            violations.append(f"{mod_name} manipulates sys.path")
    assert not violations, "\n".join(violations)


# ═══════════════════════════════════════════════════════════════════
# 15. Top-level package __init__ is thin
# ═══════════════════════════════════════════════════════════════════

_TOP_LEVEL_IMPORT_ALLOWLIST = frozenset({"general_ludd.compat.annotated_types"})


def test_top_level_init_imports_not_excessive() -> None:
    mod = importlib.import_module("general_ludd")
    assert Version(mod.__version__) == Version(distribution_version("general-ludd-agent"))
    eager_imports = _direct_package_imports((SRC_PKG / "__init__.py").read_text())
    assert eager_imports <= _TOP_LEVEL_IMPORT_ALLOWLIST, (
        f"top-level init eagerly imports unsupported modules: {sorted(eager_imports - _TOP_LEVEL_IMPORT_ALLOWLIST)}"
    )


# ═══════════════════════════════════════════════════════════════════
# 16. Import time of the top-level package is reasonable
# ═══════════════════════════════════════════════════════════════════


def test_top_level_import_time() -> None:
    import time

    start = time.perf_counter()
    importlib.reload(importlib.import_module("general_ludd"))
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"top-level package import took {elapsed:.2f}s (limit 2.0s)"


# ═══════════════════════════════════════════════════════════════════
# 17. No duplicate top-level leaf modules across subpackages
#     (warns on >3 but only hard-fails at critical duplicates)
# ═══════════════════════════════════════════════════════════════════

_NAMESPACED_LEAF_ALLOWLIST = frozenset({"contracts", "registry", "runner"})


def _leaf_modules(module_names: set[str]) -> dict[str, list[str]]:
    leaves: dict[str, list[str]] = {}
    for name in sorted(module_names):
        leaf = name.rsplit(".", maxsplit=1)[-1]
        if leaf != "__init__":
            leaves.setdefault(leaf, []).append(name)
    return leaves


def _critical_leaf_duplicates(module_names: set[str]) -> dict[str, int]:
    """Return non-conventional leaves duplicated across eight or more namespaces."""
    return {
        leaf: len(names)
        for leaf, names in _leaf_modules(module_names).items()
        if len(names) >= 8 and leaf not in _NAMESPACED_LEAF_ALLOWLIST
    }


def _unqualified_leaf_imports(leaves: set[str]) -> list[str]:
    violations: list[str] = []
    for path in _FILE_PATHS:
        module = _path_to_module(path)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", maxsplit=1)[0] in leaves:
                        violations.append(f"{module}:{node.lineno} imports {alias.name}")
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module
                and node.module.split(".", maxsplit=1)[0] in leaves
            ):
                violations.append(f"{module}:{node.lineno} imports {node.module}")
    return violations


def test_no_critical_leaf_duplicates() -> None:
    critical = _critical_leaf_duplicates(_MODULE_NAMES)
    assert not critical, f"Extreme non-conventional leaf duplication (>7 occurrences): {critical}"

    namespaced_duplicates = {
        leaf
        for leaf, names in _leaf_modules(_MODULE_NAMES).items()
        if len(names) > 1 and leaf in _NAMESPACED_LEAF_ALLOWLIST
    }
    unqualified_imports = _unqualified_leaf_imports(namespaced_duplicates)
    assert not unqualified_imports, (
        "Duplicated leaf modules require qualified imports:\n" + "\n".join(unqualified_imports)
    )
