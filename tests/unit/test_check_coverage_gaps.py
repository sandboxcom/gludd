"""Regression tests for the static coverage-gap mapper."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import check_coverage_gaps as checker


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    source = root / "src" / "general_ludd" / "widgets"
    tests = root / "tests" / "unit"
    source.mkdir(parents=True)
    tests.mkdir(parents=True)
    (source / "engine.py").write_text("class Engine:\n    pass\n")
    (source / "other.py").write_text("class Other:\n    pass\n")
    (source / "__init__.py").write_text(
        "from general_ludd.widgets.engine import Engine\n"
        "from general_ludd.widgets.other import Other\n"
    )
    monkeypatch.setattr(checker, "PROJECT_ROOT", root)
    monkeypatch.setattr(checker, "SRC_DIR", root / "src" / "general_ludd")
    monkeypatch.setattr(checker, "TESTS_DIR", tests)
    return root


def _status(project: Path, module: str = "engine.py") -> checker.CoverageResult:
    return checker._check_module(project / "src" / "general_ludd" / "widgets" / module)


def test_finds_direct_import_in_differently_named_test(project: Path) -> None:
    (project / "tests" / "unit" / "test_widget_behavior.py").write_text(
        "from general_ludd.widgets.engine import Engine\n\n"
        "def test_engine():\n    assert Engine() is not None\n"
    )

    result = _status(project)

    assert result["status"] == "OK"
    assert result["test_file"] == "tests/unit/test_widget_behavior.py"


def test_resolves_from_package_reexport_to_defining_module(
    project: Path,
) -> None:
    (project / "tests" / "unit" / "test_public_api.py").write_text(
        "from general_ludd.widgets import Engine\n\n"
        "def test_engine():\n    assert Engine() is not None\n"
    )

    assert _status(project)["status"] == "OK"


def test_resolves_used_attribute_from_imported_package_alias(
    project: Path,
) -> None:
    (project / "tests" / "unit" / "test_public_api.py").write_text(
        "import general_ludd.widgets as widgets\n\n"
        "def test_engine():\n    assert widgets.Engine() is not None\n"
    )

    assert _status(project)["status"] == "OK"
    assert _status(project, "other.py")["status"] == "UNTESTED"


def test_recognizes_file_path_import_used_by_isolated_module_tests(
    project: Path,
) -> None:
    (project / "tests" / "unit" / "test_widget_behavior.py").write_text(
        "import importlib.util\n"
        "import os\n\n"
        "_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))\n"
        "_ENGINE = os.path.join(_ROOT, 'src', 'general_ludd', 'widgets', 'engine.py')\n"
        "_SPEC = importlib.util.spec_from_file_location('engine_under_test', _ENGINE)\n"
        "assert _SPEC is not None and _SPEC.loader is not None\n"
        "engine = importlib.util.module_from_spec(_SPEC)\n"
        "_SPEC.loader.exec_module(engine)\n\n"
        "def test_engine():\n    assert engine.Engine() is not None\n"
    )

    assert _status(project)["status"] == "OK"


def test_module_name_in_text_or_unrelated_reexport_is_not_coverage(
    project: Path,
) -> None:
    (project / "tests" / "unit" / "test_public_api.py").write_text(
        '"""general_ludd.widgets.engine is intentionally mentioned only in prose."""\n'
        "from general_ludd.widgets import Other\n\n"
        "def test_other():\n    assert Other() is not None\n"
    )

    assert _status(project)["status"] == "UNTESTED"


def test_named_candidate_importing_another_module_remains_no_import(
    project: Path,
) -> None:
    (project / "tests" / "unit" / "test_widgets_engine.py").write_text(
        "from general_ludd.widgets.other import Other\n\n"
        "def test_other():\n    assert Other() is not None\n"
    )

    assert _status(project)["status"] == "NO_IMPORT"


def test_repository_chemistry_installed_import_is_mapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the installed-package import used by chemistry acceptance suites."""
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(checker, "PROJECT_ROOT", root)
    monkeypatch.setattr(checker, "SRC_DIR", root / "src" / "general_ludd")
    monkeypatch.setattr(checker, "TESTS_DIR", root / "tests" / "unit")
    result = checker._check_module(
        root / "src" / "general_ludd" / "chemistry" / "analytical.py"
    )

    assert result["status"] == "OK"
    assert result["test_file"] == "tests/unit/test_chemistry_analytical.py"
