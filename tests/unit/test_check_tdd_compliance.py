"""
Unit tests for check_tdd_compliance.py — test-modification and unused-import checks.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_tdd_compliance.py"


def _run_checker(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(cwd)],
        capture_output=True, text=True, cwd=str(cwd),
    )


# ---------------------------------------------------------------------------
# Fixture: disposable git repo under tests/unit/scratch/
# ---------------------------------------------------------------------------

@pytest.fixture
def scratch_repo(tmp_path: Path):
    """Create a minimal git repository with src/general_ludd/ and tests/unit/."""
    repo = tmp_path / "repo"
    src_dir = repo / "src" / "general_ludd"
    test_dir = repo / "tests" / "unit"
    src_dir.mkdir(parents=True)
    test_dir.mkdir(parents=True)

    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), capture_output=True, check=True,
    )

    # Initial commit so we can diff against HEAD
    (repo / "README.md").write_text("# scratch\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)

    return repo


def _stage_all(repo: Path):
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)


# ---------------------------------------------------------------------------
# Enhancement 1 — test file must also be modified
# ---------------------------------------------------------------------------

def test_source_modified_test_not_staged_is_violation(scratch_repo: Path):
    """When a source file is staged but its test file is NOT, the checker
    should report a violation."""
    r = scratch_repo

    src_file = r / "src" / "general_ludd" / "widgets.py"
    test_file = r / "tests" / "unit" / "test_widgets.py"

    src_file.write_text("def foo():\n    return 42\n")
    test_file.write_text(
        "from general_ludd.widgets import foo\n\ndef test_foo():\n    assert foo() == 42\n"
    )
    _stage_all(r)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(r), capture_output=True, check=True)

    # Now modify source but NOT test, and stage only source
    src_file.write_text("def foo():\n    return 43\n")

    subprocess.run(["git", "add", str(src_file)], cwd=str(r), capture_output=True, check=True)

    proc = _run_checker(r)
    assert proc.returncode == 1, f"expected violation, got {proc.stdout}\n{proc.stderr}"
    assert "was NOT modified alongside source file" in proc.stdout


def test_source_and_test_both_staged_is_ok(scratch_repo: Path):
    """When both source and test are staged, the checker should pass."""
    r = scratch_repo

    src_file = r / "src" / "general_ludd" / "gadgets.py"
    test_file = r / "tests" / "unit" / "test_gadgets.py"

    src_file.write_text("def bar():\n    return 7\n")
    test_file.write_text(
        "from general_ludd.gadgets import bar\n\ndef test_bar():\n    assert bar() == 7\n"
    )
    _stage_all(r)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(r), capture_output=True, check=True)

    # Modify both and stage both (content must change for git to track)
    src_file.write_text("def bar():\n    return 8\n")
    test_file.write_text(
        "from general_ludd.gadgets import bar\n\ndef test_bar():\n    assert bar() == 8\n\ndef test_baz():\n    pass\n"
    )

    subprocess.run(["git", "add", str(src_file), str(test_file)], cwd=str(r), capture_output=True, check=True)

    proc = _run_checker(r)
    assert proc.returncode == 0, f"expected pass, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert "valid test coverage" in proc.stdout or "no source files" in proc.stdout or "no checkable" in proc.stdout


def test_connector_prefix_test_name_is_recognized(scratch_repo: Path):
    """Established ``test_connector_<module>`` files satisfy the TDD guard."""
    r = scratch_repo
    connector_dir = r / "src" / "general_ludd" / "connectors"
    connector_dir.mkdir()
    src_file = connector_dir / "widget.py"
    test_file = r / "tests" / "unit" / "test_connector_widget.py"

    src_file.write_text("def value():\n    return 1\n")
    test_file.write_text(
        "from general_ludd.connectors.widget import value\n\n"
        "def test_value():\n    assert value() == 1\n"
    )
    _stage_all(r)
    subprocess.run(
        ["git", "commit", "-m", "base"],
        cwd=str(r),
        capture_output=True,
        check=True,
    )

    src_file.write_text("def value():\n    return 2\n")
    test_file.write_text(
        "from general_ludd.connectors.widget import value\n\n"
        "def test_value():\n    assert value() == 2\n\n"
        "def test_positive():\n    assert value() > 0\n"
    )
    subprocess.run(
        ["git", "add", str(src_file), str(test_file)],
        cwd=str(r),
        capture_output=True,
        check=True,
    )

    proc = _run_checker(r)
    assert proc.returncode == 0, (
        f"expected connector-prefixed test to pass, got "
        f"{proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# Enhancement 2 — import-only stub detection
# ---------------------------------------------------------------------------

def test_imported_but_never_used_is_violation(scratch_repo: Path):
    """A test that imports a name but never actually references it is flagged."""
    r = scratch_repo

    src_file = r / "src" / "general_ludd" / "stuff.py"
    test_file = r / "tests" / "unit" / "test_stuff.py"

    src_file.write_text("def spam():\n    return 1\n\ndef eggs():\n    return 2\n")
    # Import spam but never use it in a test function body
    test_file.write_text(
        "from general_ludd.stuff import spam, eggs\n\ndef test_eggs():\n    assert eggs() == 2\n"
    )
    _stage_all(r)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(r), capture_output=True, check=True)

    # Modify both and stage both (content must change for git to track)
    src_file.write_text("def spam():\n    return 1\n\ndef eggs():\n    return 3\n")
    test_file.write_text(
        "from general_ludd.stuff import spam, eggs\n\n"
        "def test_eggs():\n    assert eggs() == 3\n\n"
        "def test_extra():\n    pass\n"
    )
    subprocess.run(["git", "add", str(src_file), str(test_file)], cwd=str(r), capture_output=True, check=True)

    proc = _run_checker(r)
    assert proc.returncode == 1, f"expected violation, got {proc.stdout}\n{proc.stderr}"
    assert "never uses" in proc.stdout
    assert "spam" in proc.stdout


def test_all_imports_used_is_ok(scratch_repo: Path):
    """When every imported name is actually referenced, the checker passes."""
    r = scratch_repo

    src_file = r / "src" / "general_ludd" / "things.py"
    test_file = r / "tests" / "unit" / "test_things.py"

    src_file.write_text("def a():\n    return 1\n\ndef b():\n    return 2\n")
    test_file.write_text(
        "from general_ludd.things import a, b\n\ndef test_both():\n    assert a() == 1\n    assert b() == 2\n"
    )
    _stage_all(r)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(r), capture_output=True, check=True)

    src_file.write_text("def a():\n    return 10\n\ndef b():\n    return 20\n")
    test_file.write_text(
        "from general_ludd.things import a, b\n\n"
        "def test_both():\n    assert a() == 10\n    assert b() == 20\n\n"
        "def test_extra():\n    pass\n"
    )
    subprocess.run(["git", "add", str(src_file), str(test_file)], cwd=str(r), capture_output=True, check=True)

    proc = _run_checker(r)
    assert proc.returncode == 0, f"expected pass, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}"


def test_import_usage_with_alias(scratch_repo: Path):
    """Imported names used via 'as' aliases are tracked correctly."""
    r = scratch_repo

    src_file = r / "src" / "general_ludd" / "renamed.py"
    test_file = r / "tests" / "unit" / "test_renamed.py"

    src_file.write_text("class Foo:\n    pass\n")
    test_file.write_text(
        "from general_ludd.renamed import Foo as Bar\n\ndef test_foo():\n    b = Bar()\n    assert b is not None\n"
    )
    _stage_all(r)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(r), capture_output=True, check=True)

    src_file.write_text("class Foo:\n    x: int = 1\n")
    test_file.write_text(
        "from general_ludd.renamed import Foo as Bar\n\n"
        "def test_foo():\n    b = Bar()\n    assert b is not None\n\n"
        "def test_extra():\n    pass\n"
    )
    subprocess.run(["git", "add", str(src_file), str(test_file)], cwd=str(r), capture_output=True, check=True)

    proc = _run_checker(r)
    assert proc.returncode == 0, f"expected pass, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}"


def test_allowlisted_files_skipped(scratch_repo: Path):
    """__init__.py and other allowlisted paths are always skipped."""
    r = scratch_repo

    init_file = r / "src" / "general_ludd" / "__init__.py"
    init_file.write_text("# package\n")
    _stage_all(r)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(r), capture_output=True, check=True)

    init_file.write_text("# updated\n")
    subprocess.run(["git", "add", str(init_file)], cwd=str(r), capture_output=True, check=True)

    proc = _run_checker(r)
    assert proc.returncode == 0, f"expected pass, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
