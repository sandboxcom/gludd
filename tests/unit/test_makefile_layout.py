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
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "read_text":
                continue
            if any(
                isinstance(part, ast.Constant) and part.value == "Makefile"
                for part in ast.walk(node.func.value)
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    assert violations == [], (
        "tests must use scripts.makefile_layout.compose_makefile for logical "
        f"Makefile reads: {violations}"
    )
