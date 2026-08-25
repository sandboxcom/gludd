"""Direct helper coverage for the commit-time TDD checker."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest


class CheckerModule(Protocol):
    """Typed surface used from the standalone checker."""

    PROJECT_ROOT: Path
    SRC_DIR: Path
    TESTS_DIR: Path

    def _module_path(self, src_file: Path) -> str: ...

    def _candidate_test_paths(self, src_file: Path) -> list[Path]: ...

    def _is_init_in_empty_dir(self, path: Path) -> bool: ...

    def _test_imports_module(self, test_file: Path, module_path: str) -> bool: ...

    def _test_has_functions(self, test_file: Path) -> bool: ...

    def _find_unused_import_names(self, test_file: Path, module_path: str) -> list[str]: ...

    def _find_valid_test(
        self,
        src_file: Path,
        module_path: str,
        staged_set: set[str],
    ) -> tuple[Path | None, str]: ...

    def _parse_root(self, argv: list[str]) -> Path: ...

    def main(self, argv: list[str]) -> int: ...


def _load_checker() -> CheckerModule:
    """Load the standalone checker as a typed module."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "check_tdd_compliance.py"
    spec = importlib.util.spec_from_file_location("check_tdd_compliance_helper_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(CheckerModule, module)


def _set_root(checker: CheckerModule, root: Path) -> None:
    """Point the checker at an isolated repository layout."""
    checker.PROJECT_ROOT = root
    checker.SRC_DIR = root / "src" / "general_ludd"
    checker.TESTS_DIR = root / "tests"


def test_path_mapping_matches_nested_and_connector_candidates(tmp_path: Path) -> None:
    """Candidate paths retain canonical nested and connector fallbacks."""
    checker = _load_checker()
    _set_root(checker, tmp_path)
    source = tmp_path / "src" / "general_ludd" / "connectors" / "widget.py"
    candidates = checker._candidate_test_paths(source)
    assert checker._module_path(source) == "general_ludd.connectors.widget"
    assert tmp_path / "tests" / "unit" / "test_general_ludd_connectors_widget.py" in candidates
    assert tmp_path / "tests" / "unit" / "test_connector_widget.py" in candidates
    assert tmp_path / "tests" / "unit" / "test_widget.py" in candidates
    assert checker._parse_root(["checker", "--root", str(tmp_path)]) == tmp_path.resolve()


def test_empty_package_detection_is_exact(tmp_path: Path) -> None:
    """Only an init file with no sibling Python implementation is exempt."""
    checker = _load_checker()
    package = tmp_path / "package"
    package.mkdir()
    init_file = package / "__init__.py"
    init_file.write_text("")
    assert checker._is_init_in_empty_dir(init_file)
    assert not checker._is_init_in_empty_dir(package / "module.py")
    (package / "module.py").write_text("VALUE = 1\n")
    assert not checker._is_init_in_empty_dir(init_file)
    assert not checker._is_init_in_empty_dir(tmp_path / "missing" / "__init__.py")


def test_ast_helpers_cover_import_forms_and_read_failures(tmp_path: Path) -> None:
    """Import, test-function, and usage checks share one parsed source contract."""
    checker = _load_checker()
    test_file = tmp_path / "test_widget.py"
    test_file.write_text(
        "from general_ludd.widget import value as alias\n\n"
        "def test_value():\n"
        "    assert alias() == 1\n"
    )
    assert checker._test_imports_module(test_file, "general_ludd.widget")
    assert checker._test_has_functions(test_file)
    assert checker._find_unused_import_names(test_file, "general_ludd.widget") == []

    test_file.write_text("import general_ludd.widget as widget\n\ndef helper():\n    return 1\n")
    assert checker._test_imports_module(test_file, "general_ludd.widget")
    assert not checker._test_has_functions(test_file)
    assert checker._find_unused_import_names(test_file, "general_ludd.widget") == ["widget"]

    missing = tmp_path / "missing.py"
    assert not checker._test_imports_module(missing, "general_ludd.widget")
    assert not checker._test_has_functions(missing)
    assert checker._find_unused_import_names(missing, "general_ludd.widget") == []


def test_valid_test_reasons_are_fail_closed(tmp_path: Path) -> None:
    """Each invalid candidate state yields its specific actionable reason."""
    checker = _load_checker()
    _set_root(checker, tmp_path)
    source = tmp_path / "src" / "general_ludd" / "widget.py"
    source.parent.mkdir(parents=True)
    source.write_text("def value():\n    return 1\n")
    test_file = tmp_path / "tests" / "unit" / "test_widget.py"

    assert checker._find_valid_test(source, "general_ludd.widget", set()) == (None, "no_test_file")
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_other():\n    assert True\n")
    _, reason = checker._find_valid_test(source, "general_ludd.widget", set())
    assert "does not import" in reason

    test_file.write_text("from general_ludd.widget import value\n")
    _, reason = checker._find_valid_test(source, "general_ludd.widget", set())
    assert "has no test_* functions" in reason

    test_file.write_text(
        "from general_ludd.widget import value\n\n"
        "def test_other():\n"
        "    assert True\n"
    )
    _, reason = checker._find_valid_test(source, "general_ludd.widget", set())
    assert "was NOT modified" in reason

    staged = {str(test_file)}
    _, reason = checker._find_valid_test(source, "general_ludd.widget", staged)
    assert "never uses: value" in reason

    test_file.write_text(
        "from general_ludd.widget import value\n\n"
        "def test_value():\n"
        "    assert value() == 1\n"
    )
    assert checker._find_valid_test(source, "general_ludd.widget", staged) == (test_file, "ok")


def test_main_without_staged_sources_is_observable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """An empty staged source set exits successfully with an explicit message."""
    checker = _load_checker()
    monkeypatch.setattr(checker, "_git_changed_source_files", lambda: [])
    assert checker.main(["checker", "--root", str(tmp_path)]) == 0
    assert "no source files staged" in capsys.readouterr().out
