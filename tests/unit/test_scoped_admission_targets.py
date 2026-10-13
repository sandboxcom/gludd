"""Pin narrow native secret admission and non-destructive index checkpointing."""

from __future__ import annotations

import secrets
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _make(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_scoped_secrets_target_reuses_readonly_native_owner() -> None:
    """Keep both explicit input paths and the canonical baseline unmodified."""
    result = _make(
        "-n", "check-secrets-files",
        "SECRETS_FILES=tests/unit/test_ansible_ee_searx_acceptance.py scripts/check_clean_tree.py",
        "SECRETS_BASELINE_FILE=.secrets.baseline",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/detect_secrets_readonly.py" in result.stdout
    assert '--baseline ".secrets.baseline" --' in result.stdout
    assert '"tests/unit/test_ansible_ee_searx_acceptance.py"' in result.stdout
    assert '"scripts/check_clean_tree.py"' in result.stdout


def test_unstage_target_preserves_worktree_content() -> None:
    """Require the native index-only operation and exact explicit paths."""
    result = _make("-n", "git-unstage-files", "FILES=docs/features/RELEASE_PROOF_THROUGHPUT.md")
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'git restore --staged -- "docs/features/RELEASE_PROOF_THROUGHPUT.md"' in result.stdout
    assert "--worktree" not in result.stdout
    assert "git reset" not in result.stdout


def test_normal_content_admission_requires_native_staged_secrets_hook() -> None:
    """Catch secret failures before repeated collection and push work."""
    source = (ROOT / "make/40-cross-version-and-worktrees.mk").read_text(encoding="utf-8")
    prerequisites = source.split("_pre-commit-content-guard:", 1)[1].splitlines()[0]
    assert "_pre-commit-secrets-guard" in prerequisites


def test_staged_secrets_guard_uses_native_precommit_selection() -> None:
    """Preserve the configured hook's native file filtering and baseline policy."""
    result = _make("-n", "_pre-commit-secrets-guard")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pre-commit run detect-secrets-readonly" in result.stdout
    assert "--all-files" not in result.stdout
    assert "--no-verify" not in result.stdout


@pytest.mark.parametrize(
    ("target", "variable", "message"),
    [
        ("check-secrets-files", "SECRETS_FILES", "SECRETS_FILES must name explicit files"),
        ("git-unstage-files", "FILES", "FILES must name explicit files"),
    ],
)
def test_scoped_targets_refuse_empty_scope(target: str, variable: str, message: str) -> None:
    """Do not silently scan or unstage the whole project."""
    result = _make(target, f"{variable}=")
    assert result.returncode != 0
    assert message in result.stdout + result.stderr


def test_native_scoped_scan_rejects_a_new_finding_without_mutating_baseline(tmp_path: Path) -> None:
    """Run the maintained scanner against a generated non-credential fixture."""
    baseline = ROOT / ".secrets.baseline"
    original = baseline.read_bytes()
    fixture = tmp_path / "keyword.py"
    fixture.write_text(f"password = {secrets.token_hex(32)!r}\n", encoding="utf-8")
    result = _make(
        "check-secrets-files", f"SECRETS_FILES={fixture}",
        "SECRETS_BASELINE_FILE=.secrets.baseline",
    )
    assert result.returncode != 0
    assert "Potential secrets" in result.stdout + result.stderr
    assert baseline.read_bytes() == original


def test_nested_test_files_uses_declared_environment_without_uv_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run real nested UV/pytest without inheriting an unrelated active env."""
    probe = tmp_path / "test_runtime_identity.py"
    probe.write_text(
        "import sys\n"
        "def test_runtime_identity():\n"
        f"    assert sys.prefix == {sys.prefix!r}\n"
        f"    assert sys.version_info[:2] == {sys.version_info[:2]!r}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "unrelated-active-environment"))
    result = _make(
        "test-files", f"TESTFILES={probe}", "PYTEST_ARGS=-q",
        "NPROC=1", "GLUDD_XDIST_WORKERS=1", f"UV_PROJECT_ENVIRONMENT={sys.prefix}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "does not match the project environment" not in result.stdout + result.stderr
    assert "will be ignored" not in result.stdout + result.stderr
