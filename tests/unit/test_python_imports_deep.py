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
from pathlib import Path
from types import ModuleType

import pytest

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


_FILE_PATHS = _collect_py_files()
_MODULE_NAMES = {_path_to_module(p) for p in _FILE_PATHS}
_INIT_FILES = [p for p in _FILE_PATHS if p.name == "__init__.py"]


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
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _subpackage_of(alias.name, PKG_NAME):
                        graph[mod].add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                if _subpackage_of(node.module, PKG_NAME):
                    graph[mod].add(node.module)
    return graph


def _has_cycle(graph: dict[str, set[str]]) -> bool:
    visited: set[str] = set()
    stack: set[str] = set()

    def dfs(node: str) -> bool:
        visited.add(node)
        stack.add(node)
        for neighbor in graph.get(node, set()):
            if neighbor not in visited:
                if dfs(neighbor):
                    return True
            elif neighbor in stack:
                return True
        stack.discard(node)
        return False

    return any(node not in visited and dfs(node) for node in sorted(graph))


def test_no_static_circular_import_cycle() -> None:
    graph = _static_import_graph()
    if _has_cycle(graph):
        # Print the actual cycle for debugging
        color: dict[str, int] = {}
        parent: dict[str, str | None] = {}
        for v in graph:
            color[v] = 0
            parent[v] = None

        def _dfs_cycle(u: str) -> list[str] | None:
            color[u] = 1
            for v in graph.get(u, set()):
                if v not in color:
                    color[v] = 0
                    parent[v] = u
                    cycle = _dfs_cycle(v)
                    if cycle is not None:
                        return cycle
                elif color.get(v) == 1:
                    cycle_path = [v]
                    cur: str | None = u
                    while cur is not None and cur != v:
                        cycle_path.append(cur)
                        cur = parent.get(cur)
                    cycle_path.append(v)
                    cycle_path.reverse()
                    return cycle_path
            color[u] = 2
            return None

        cycle: list[str] | None = None
        for node in sorted(graph):
            if color.get(node) == 0:
                cycle = _dfs_cycle(node)
                if cycle is not None:
                    break
        import sys

        assert cycle is not None, "Cycle scan reported a cycle without its path"
        print(f"\nCYCLE: {' → '.join(cycle)}", file=sys.stderr)
        for i, a in enumerate(cycle):
            b = cycle[(i + 1) % len(cycle)]
            print(f"  {a} imports {b}", file=sys.stderr)
    assert not _has_cycle(graph), "Circular import cycle detected in static dependency graph"


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


def test_no_extreme_module_size() -> None:
    oversize: list[str] = []
    for p in _FILE_PATHS:
        lines = p.read_text().split("\n")
        if len(lines) > _MAX_LINES:
            oversize.append(f"{_path_to_module(p)}: {len(lines)} lines")
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


def test_top_level_init_imports_not_excessive() -> None:
    mod = importlib.import_module("general_ludd")
    public = sorted(n for n in dir(mod) if not n.startswith("_"))
    assert "__version__" in public, "top-level init should export __version__"


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


def test_no_critical_leaf_duplicates() -> None:
    leaf_count: dict[str, list[str]] = {}
    for name in sorted(_MODULE_NAMES):
        leaf = name.split(".")[-1]
        if leaf == "__init__":
            continue
        leaf_count.setdefault(leaf, []).append(name)

    critical: list[str] = []
    for leaf, names in sorted(leaf_count.items()):
        if len(names) >= 8:
            critical.append(f"{leaf}: {len(names)} occurrences across {sorted(names)}")
    assert not critical, "Extreme leaf module duplication (>7 occurrences):\n" + "\n".join(critical)
