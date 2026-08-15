"""Behavioral contract for incremental Ruff docstring enforcement.

The former AST inventory duplicated a maintained linter and made every legacy
omission a separate release-blocking test. This suite pins a file-scoped Ruff
contract so touched production modules become compliant without hiding debt.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCSTRING_FIXTURE = "src/general_ludd/security/xmss.py"


def _run_make(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one repository Make target with bounded captured output."""
    return subprocess.run(
        ["make", *arguments],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


def test_lint_docstrings_runs_ruff_on_explicit_source_file() -> None:
    """The target delegates docstring policy to the locked Ruff executable."""
    result = _run_make("lint-docstrings", f"DOCSTRING_FILES={DOCSTRING_FIXTURE}")
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "All checks passed!" in output


def test_lint_docstrings_requires_explicit_files() -> None:
    """An omitted file scope must fail instead of scanning implicit host state."""
    result = _run_make("lint-docstrings", "DOCSTRING_FILES=")
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "Usage: make lint-docstrings" in output


def test_lint_docstrings_rejects_files_outside_production_package() -> None:
    """Tests, scripts, options, and arbitrary host paths are outside the target."""
    result = _run_make(
        "lint-docstrings",
        "DOCSTRING_FILES=tests/unit/test_docstring_coverage_deep.py",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "only accepts tracked production Python files under src/general_ludd or scripts" in output


def test_docstring_policy_is_registered_and_commit_guarded() -> None:
    """Target metadata and every local commit path share the same policy."""
    contract = json.loads((ROOT / "config" / "make_target_contract.json").read_text())
    entry = next(item for item in contract["targets"] if item["name"] == "lint-docstrings")
    assert entry["make_variables"] == ["DOCSTRING_FILES"]
    assert entry["behavior"] == ("make lint-docstrings DOCSTRING_FILES=src/general_ludd/security/xmss.py")

    makefile = (ROOT / "Makefile").read_text()
    for target in ("git-commit", "commit-no-verify", "repo-commit", "ship-commit"):
        declaration = next(line for line in makefile.splitlines() if line.startswith(f"{target}:"))
        assert "_commit-docstring-guard" in declaration

    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert config["tool"]["ruff"]["lint"]["pydocstyle"]["convention"] == "google"


def test_commit_guard_flattens_multiline_staged_source_paths() -> None:
    """Multiple staged source paths must remain one safe recursive Make argument."""
    lines = (ROOT / "Makefile").read_text().splitlines()
    target_index = lines.index("_commit-docstring-guard:")
    recipe = lines[target_index + 1]
    assert "| tr '\\n' ' '" in recipe


def test_custom_ast_docstring_linter_is_not_reintroduced() -> None:
    """The regression suite must not grow another bespoke AST linter."""
    source = Path(__file__).read_text()
    assert "import " + "ast" not in source
    assert "os." + "walk" not in source
    assert "_" + "collect_py_files" not in source
