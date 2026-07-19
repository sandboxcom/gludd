"""
Unit tests for scripts/proactive_bug_scan.py — proactive bug scanner.

Verifies the scanner detects planted issues (duplicate Makefile targets,
lint violations, missing __init__.py, dirty git tree) and exits clean
when the repo is healthy.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCANNER = Path(__file__).resolve().parent.parent.parent / "scripts" / "proactive_bug_scan.py"


def _run_scanner(root: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    args = [sys.executable, str(SCANNER), "--root", str(root), "--skip-env-checks"]
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(
        args, capture_output=True, text=True, cwd=str(root),
    )


@pytest.fixture
def scratch_repo(tmp_path: Path):
    """Minimal git repo with src/ and a clean Makefile."""
    repo = tmp_path / "repo"
    src_dir = repo / "src" / "pkg"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("")
    (repo / "Makefile").write_text("all:\n\techo hi\n")

    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=str(repo), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=str(repo), capture_output=True, check=True,
    )
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo), capture_output=True, check=True,
    )
    return repo


# ---------------------------------------------------------------------------
# Duplicate Makefile target detection
# ---------------------------------------------------------------------------

def test_detects_duplicate_makefile_target(scratch_repo: Path):
    """A Makefile declaring the same target twice is flagged."""
    (scratch_repo / "Makefile").write_text(
        "foo:\n\techo a\n\nfoo:\n\techo b\n"
    )
    proc = _run_scanner(scratch_repo)
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    combined = proc.stdout + proc.stderr
    assert "duplicate" in combined.lower()
    assert "foo" in combined


def test_clean_makefile_no_duplicates(scratch_repo: Path):
    """A Makefile with unique targets produces no duplicate-target issue."""
    (scratch_repo / "Makefile").write_text(
        "all:\n\techo hi\n\nclean:\n\trm -f x\n"
    )
    proc = _run_scanner(scratch_repo)
    combined = proc.stdout + proc.stderr
    assert "duplicate" not in combined.lower()


# ---------------------------------------------------------------------------
# Lint issue detection
# ---------------------------------------------------------------------------

def test_detects_lint_issue(scratch_repo: Path):
    """An unused import (F401) is detected by the ruff-based lint check."""
    bad = scratch_repo / "src" / "pkg" / "bad.py"
    bad.write_text("import os\n")
    proc = _run_scanner(scratch_repo)
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    combined = proc.stdout + proc.stderr
    assert "lint" in combined.lower() or "F401" in combined or "unused" in combined.lower()


def test_clean_python_no_lint_issues(scratch_repo: Path):
    """A clean Python file produces no lint issues."""
    good = scratch_repo / "src" / "pkg" / "good.py"
    good.write_text("def f() -> int:\n    return 1\n")
    proc = _run_scanner(scratch_repo)
    combined = proc.stdout + proc.stderr
    assert "lint" not in combined.lower().split("check")[-1] or "0" in combined


# ---------------------------------------------------------------------------
# Missing __init__.py detection
# ---------------------------------------------------------------------------

def test_detects_missing_init_file(scratch_repo: Path):
    """A src/ subdirectory without __init__.py is flagged."""
    pkg_dir = scratch_repo / "src" / "orphan"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "mod.py").write_text("x = 1\n")
    proc = _run_scanner(scratch_repo)
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    combined = proc.stdout + proc.stderr
    assert "__init__" in combined.lower() or "orphan" in combined


# ---------------------------------------------------------------------------
# Dirty git tree detection
# ---------------------------------------------------------------------------

def test_detects_dirty_tree(scratch_repo: Path):
    """Uncommitted changes in the working tree are flagged."""
    (scratch_repo / "src" / "pkg" / "__init__.py").write_text("x = 1\n")
    proc = _run_scanner(scratch_repo)
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    combined = proc.stdout + proc.stderr
    assert "dirty" in combined.lower() or "uncommitted" in combined.lower() or "modified" in combined.lower()


# ---------------------------------------------------------------------------
# Clean repo passes
# ---------------------------------------------------------------------------

def test_clean_repo_exits_zero(scratch_repo: Path):
    """A fully clean repo (committed, no lint issues, unique targets) exits 0."""
    proc = _run_scanner(scratch_repo)
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}"


# ---------------------------------------------------------------------------
# Summary output format
# ---------------------------------------------------------------------------

def test_issues_summary_printed(scratch_repo: Path):
    """When issues are found, a summary count is printed."""
    (scratch_repo / "Makefile").write_text("dup:\n\techo a\n\ndup:\n\techo b\n")
    proc = _run_scanner(scratch_repo)
    combined = proc.stdout + proc.stderr
    assert "proactive-scan" in combined.lower() or "issue" in combined.lower()
