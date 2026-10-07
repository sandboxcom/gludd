"""Contracts for the deterministic split Makefile layout."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAYOUT_SCRIPT = ROOT / "scripts" / "makefile_layout.py"


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> set[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {
        child.id
        for target in targets
        for child in ast.walk(target)
        if isinstance(child, ast.Name)
    }


def _contains_makefile_literal(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Constant) and child.value == "Makefile"
        for child in ast.walk(node)
    )


def _is_makefile_alias_target(name: str, value: ast.AST) -> bool:
    if name == name.upper() and "MAKEFILE" in name:
        return True
    return "makefile" in name.casefold() and any(
        isinstance(child, ast.Name) and child.id == "ROOT"
        for child in ast.walk(value)
    )


def _reads_makefile_alias(node: ast.AST, aliases: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Attribute):
        return node.attr in aliases
    return _contains_makefile_literal(node)


def _opens_root_makefile(node: ast.Call, aliases: set[str]) -> bool:
    if not isinstance(node.func, ast.Name) or node.func.id != "open" or not node.args:
        return False
    candidate = node.args[0]
    if _reads_makefile_alias(candidate, aliases):
        return any(
            isinstance(child, ast.Name) and child.id.upper().endswith("ROOT")
            for child in ast.walk(candidate)
        )
    return False


def _read_text_wrappers(tree: ast.Module) -> set[str]:
    wrappers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parameters = {argument.arg for argument in node.args.args}
        if any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "read_text"
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id in parameters
            for child in ast.walk(node)
        ):
            wrappers.add(node.name)
    return wrappers


def _logical_makefile_read_violations(path: Path, source: str) -> list[str]:
    tree = ast.parse(source, filename=str(path))
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        assigned = _assigned_names(node)
        if value is not None and (
            _contains_makefile_literal(value)
            or any(
                isinstance(child, ast.Name) and child.id in aliases
                for child in ast.walk(value)
            )
        ):
            aliases.update(
                name for name in assigned if _is_makefile_alias_target(name, value)
            )

    wrappers = _read_text_wrappers(tree)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        direct_read = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "read_text"
            and _reads_makefile_alias(node.func.value, aliases)
        )
        wrapped_read = (
            isinstance(node.func, ast.Name)
            and node.func.id in wrappers
            and any(_reads_makefile_alias(argument, aliases) for argument in node.args)
        )
        if direct_read or wrapped_read or _opens_root_makefile(node, aliases):
            violations.append(f"{path}:{node.lineno}")
    return violations


def _load_layout() -> ModuleType:
    assert LAYOUT_SCRIPT.is_file(), "Makefile layout helper must exist"
    spec = importlib.util.spec_from_file_location("makefile_layout", LAYOUT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_explicit_fragments_are_composed_in_declared_order(tmp_path: Path) -> None:
    layout = _load_layout()
    entrypoint = _write(
        tmp_path / "Makefile",
        "# entrypoint\ninclude make/00-vars.mk\ninclude make/10-targets.mk\n",
    )
    first = _write(tmp_path / "make/00-vars.mk", "VALUE := first\n")
    second = _write(tmp_path / "make/10-targets.mk", "all:\n\t@echo $(VALUE)\n")

    assert layout.makefile_sources(entrypoint) == (entrypoint, first, second)
    assert layout.compose_makefile(entrypoint) == (
        "# entrypoint\nVALUE := first\nall:\n\t@echo $(VALUE)\n"
    )


@pytest.mark.parametrize(
    "directive",
    [
        "-include make/00.mk",
        "sinclude make/00.mk",
        "include make/*.mk",
        "include $(MAKE_PART)",
        "include ../outside.mk",
        "include /tmp/outside.mk",
        "include make/00.mk make/10.mk",
    ],
)
def test_dynamic_optional_or_unsafe_includes_fail_closed(
    tmp_path: Path,
    directive: str,
) -> None:
    layout = _load_layout()
    entrypoint = _write(tmp_path / "Makefile", directive + "\n")

    with pytest.raises(layout.MakefileLayoutError):
        layout.makefile_sources(entrypoint)


def test_missing_duplicate_and_nested_fragments_fail_closed(tmp_path: Path) -> None:
    layout = _load_layout()
    missing = _write(tmp_path / "Makefile", "include make/missing.mk\n")
    with pytest.raises(layout.MakefileLayoutError, match="missing"):
        layout.makefile_sources(missing)

    duplicate = _write(
        tmp_path / "Makefile",
        "include make/00.mk\ninclude make/00.mk\n",
    )
    _write(tmp_path / "make/00.mk", "all:\n\t@:\n")
    with pytest.raises(layout.MakefileLayoutError, match="duplicate"):
        layout.makefile_sources(duplicate)

    nested = _write(tmp_path / "Makefile", "include make/10.mk\n")
    _write(tmp_path / "make/10.mk", "include make/20.mk\n")
    _write(tmp_path / "make/20.mk", "all:\n\t@:\n")
    with pytest.raises(layout.MakefileLayoutError, match="nested"):
        layout.makefile_sources(nested)


def test_unsplit_fixture_remains_supported_for_quality_tool_tests(tmp_path: Path) -> None:
    layout = _load_layout()
    entrypoint = _write(tmp_path / "Makefile", "all:\n\t@:\n")

    assert layout.makefile_sources(entrypoint) == (entrypoint,)
    assert layout.compose_makefile(entrypoint) == "all:\n\t@:\n"


def test_repository_makefile_is_a_small_explicit_fragment_entrypoint() -> None:
    layout = _load_layout()
    entrypoint = ROOT / "Makefile"
    sources = layout.makefile_sources(entrypoint)

    assert len(entrypoint.read_text(encoding="utf-8").splitlines()) < 2500
    assert len(sources) >= 3
    assert all(path.parent == ROOT / "make" for path in sources[1:])
    assert all(len(path.read_text(encoding="utf-8").splitlines()) < 2500 for path in sources)
    composed = layout.compose_makefile(entrypoint)
    assert "help:" in composed
    assert "check-file-line-limits:" in composed
    assert "release-promote:" in composed


def test_tests_read_the_logical_makefile_instead_of_only_the_entrypoint() -> None:
    violations: list[str] = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        violations.extend(
            _logical_makefile_read_violations(
                path.relative_to(ROOT),
                path.read_text(encoding="utf-8"),
            )
        )

    assert violations == [], (
        "tests must use scripts.makefile_layout.compose_makefile for logical "
        f"Makefile reads: {violations}"
    )


def test_logical_makefile_guard_detects_path_alias_reads() -> None:
    source = '''\
from pathlib import Path
MAKEFILE = Path(__file__).parents[1] / "Makefile"
CONTENT = MAKEFILE.read_text(encoding="utf-8")
'''

    assert _logical_makefile_read_violations(Path("fixture.py"), source) == [
        "fixture.py:3",
    ]


def test_logical_makefile_guard_detects_lowercase_path_alias_reads() -> None:
    source = '''\
from pathlib import Path
ROOT = Path(__file__).parents[1]
makefile = ROOT / "Makefile"
content = makefile.read_text(encoding="utf-8")
'''

    assert _logical_makefile_read_violations(Path("fixture.py"), source) == [
        "fixture.py:4",
    ]


def test_logical_makefile_guard_detects_aliases_passed_to_read_wrappers() -> None:
    source = '''\
from pathlib import Path
MAKEFILE = Path(__file__).parents[1] / "Makefile"
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")
CONTENT = _read(MAKEFILE)
'''

    assert _logical_makefile_read_violations(Path("fixture.py"), source) == [
        "fixture.py:5",
    ]


def test_logical_makefile_guard_detects_builtin_open_of_root_makefile() -> None:
    source = '''\
import os
PROJECT_ROOT = os.path.dirname(__file__)
with open(os.path.join(PROJECT_ROOT, "Makefile")) as stream:
    CONTENT = stream.read()
'''

    assert _logical_makefile_read_violations(Path("fixture.py"), source) == [
        "fixture.py:3",
    ]
