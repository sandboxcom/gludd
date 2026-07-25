"""Gate-gated commit (ship-commit) and test-then-commit (test-and-commit).

Ports the Makefile targets into callable Python functions wired through
GitAutomation, with gate-freshness checking.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from general_ludd.git_automation.repo import GitAutomation

_GATE_PASSED_MARKER = "=== GATE: PASSED ==="
_GATE_FAILED_MARKER = "=== GATE: FAILED ==="

__test__ = False


class ShipCommitError(Exception):
    """Raised when a gated commit precondition is not met."""


def gate_is_green(gate_path: str) -> bool:
    """True when ``gate_path`` exists and contains the PASSED terminal marker."""
    p = Path(gate_path)
    if not p.exists():
        return False
    try:
        content = p.read_text()
    except Exception:
        return False
    if _GATE_PASSED_MARKER not in content:
        return False
    tail = content.split(_GATE_PASSED_MARKER, 1)[1]
    return _GATE_FAILED_MARKER not in tail


def collect_check(repo_root: str) -> None:
    """Run pytest --collect-only to verify no collection errors.

    Analogous to ``make collect-check``. Raises ``ShipCommitError`` on failure.
    """
    proc = subprocess.run(
        ["python", "-m", "pytest", "--collect-only", "-q", str(Path(repo_root) / "tests")],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        raise ShipCommitError(
            f"Collation check failed (exit {proc.returncode}): {stderr or stdout[:500]}"
        )


def ship_commit(
    message: str,
    *,
    git: GitAutomation | None = None,
    files: list[str] | None = None,
    push: bool = False,
    skip_gate: bool = False,
    repo_root: str | None = None,
    gate_path_override: str | None = None,
) -> str:
    """Commit staged changes, gated on a green .gate-status (Makefile ship-commit).

    By default does NOT push — push requires explicit ``push=True``.  The
    gate-freshness check reads ``.gate-status`` for ``=== GATE: PASSED ===``;
    if the gate is red or missing the commit is refused with ``ShipCommitError``.

    ``skip_gate=True`` allows meta-commits (analogous to Makefile's
    ``repo-commit``).  Call ``collect_check()`` separately for the pre-commit
    collection guardrail (analogous to ``make collect-check``).

    Returns the new commit SHA.
    """
    if git is None:
        from general_ludd.git_automation.repo import GitAutomation

        git = GitAutomation(repo_root or ".")

    root = repo_root or os.getcwd()
    gate_path = gate_path_override or os.path.join(root, ".gate-status")

    if not skip_gate and not gate_is_green(gate_path):
        raise ShipCommitError(
            f"Gate is red or missing at {gate_path}. "
            "Run 'make gate' and ensure it passes before committing."
        )

    if files:
        for f in files:
            git._run_git("add", "--", f)

    sha = git.commit(message)
    if push:
        git.push()
    return sha


def test_and_commit(
    message: str,
    test_command: list[str],
    *,
    git: GitAutomation | None = None,
    files: list[str] | None = None,
    push: bool = False,
    skip_gate: bool = False,
    repo_root: str | None = None,
    gate_path_override: str | None = None,
) -> str:
    """Run tests, then ship-commit only if they pass (Makefile test-and-commit).

    The gate is checked first (like ``test-and-commit``'s ``preflight`` step),
    then ``test_command`` is run via ``subprocess.run``.  If tests fail the
    commit is refused with ``ShipCommitError``.  On success calls
    :func:`ship_commit`.

    Returns the new commit SHA.
    """
    if git is None:
        from general_ludd.git_automation.repo import GitAutomation

        git = GitAutomation(repo_root or ".")

    root = repo_root or os.getcwd()
    gate_path = gate_path_override or os.path.join(root, ".gate-status")

    if not skip_gate and not gate_is_green(gate_path):
        raise ShipCommitError(
            f"Gate is red or missing at {gate_path}. "
            "Run 'make gate' and ensure it passes before committing."
        )

    proc = subprocess.run(
        test_command,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        raise ShipCommitError(
            f"Tests failed (exit {proc.returncode}): {stderr or stdout[:500]}"
        )

    return ship_commit(
        message,
        git=git,
        files=files,
        push=push,
        skip_gate=skip_gate,
        repo_root=root,
        gate_path_override=gate_path,
    )
