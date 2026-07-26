#!/usr/bin/env python3
"""Fail-closed beta release readiness preflight.

The preflight intentionally composes the repository's existing guards instead
of maintaining a second git/CI/task parser.  It emits one stable JSON object
on every path so CI and release tooling can consume the same diagnostics.

Exit codes are part of the interface:

* 0: ready
* 2: current HEAD has no matching successful CI evidence
* 3: worktree is dirty
* 4: detached or unintegrated sibling worktree exists
* 5: project versions are inconsistent
* 6: release-critical TASKS/ledger work is incomplete or invalid
* 7: preflight could not collect required evidence
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRITICAL_PREFIXES = ("T-BETA3-",)

EXIT_OK = 0
EXIT_CI = 2
EXIT_DIRTY = 3
EXIT_WORKTREE = 4
EXIT_VERSION = 5
EXIT_TASKS = 6
EXIT_ERROR = 7

RunFn = Callable[[Sequence[str], str | None], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=cwd, capture_output=True, text=True, check=False, timeout=30
    )


@dataclass
class Readiness:
    branch: str = ""
    head: str = ""
    ci_head_sha: str = ""
    ci_verdict: str = "UNKNOWN"
    ci_head_matches: bool = False
    dirty_count: int = 0
    detached_worktrees: list[dict[str, str]] = field(default_factory=list)
    unintegrated_worktrees: list[dict[str, object]] = field(default_factory=list)
    version_consistent: bool = False
    version_detail: str = ""
    incomplete_release_tasks: list[str] = field(default_factory=list)
    ledger_valid: bool = False
    ledger_detail: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return (
            not self.errors
            and bool(self.branch and self.head)
            and self.ci_head_matches
            and self.ci_verdict == "GREEN"
            and self.dirty_count == 0
            and not self.detached_worktrees
            and not self.unintegrated_worktrees
            and self.version_consistent
            and self.ledger_valid
            and not self.incomplete_release_tasks
        )


def _ci_verdict(head: str, branch: str, run: RunFn) -> tuple[str, str]:
    """Use require_ci_green.verdict_for for exact-SHA CI evidence."""
    try:
        from require_ci_green import verdict_for  # type: ignore[import-not-found]

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            code = verdict_for(head, branch)
        verdict = {0: "GREEN", 1: "RED", 2: "PENDING"}.get(code, "UNKNOWN")
        return verdict, captured.getvalue().strip()
    except Exception as exc:  # pragma: no cover - defensive import fallback
        helper = ROOT / "scripts" / "require_ci_green.py"
        result = run([sys.executable, str(helper), head, branch], str(ROOT))
        detail = (result.stdout or result.stderr or str(exc)).strip()
        verdict = {0: "GREEN", 1: "RED", 2: "PENDING"}.get(result.returncode, "UNKNOWN")
        return verdict, detail


def _version_check(run: RunFn, root: Path) -> tuple[bool, str]:
    """Invoke the canonical version-consistency helper."""
    helper = root / "scripts" / "check_version_consistency.py"
    result = run([sys.executable, str(helper)], str(root))
    detail = (result.stdout or result.stderr or "version helper returned no output").strip()
    return result.returncode == 0, detail


def _ledger_check(run: RunFn, root: Path) -> tuple[bool, str]:
    """Invoke the canonical task-ledger validator."""
    helper = root / "scripts" / "validate_task_ledger.py"
    result = run([sys.executable, str(helper)], str(root))
    detail = (result.stdout or result.stderr or "ledger helper returned no output").strip()
    return result.returncode == 0, detail


def _detached_worktrees(run: RunFn, root: Path) -> list[dict[str, str]]:
    """Reuse workflow_state_guard's porcelain parser for detached siblings."""
    from workflow_state_guard import _worktree_entries  # type: ignore[import-not-found]

    result = run(["git", "worktree", "list", "--porcelain"], str(root))
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "git worktree failed").strip())
    current_result = run(["git", "rev-parse", "--show-toplevel"], str(root))
    current = current_result.stdout.strip() if current_result.returncode == 0 else str(root)
    return [
        {key: str(value) for key, value in entry.items()}
        for entry in _worktree_entries(result.stdout)
        if entry.get("path") != current and not entry.get("branch")
    ]


def _incomplete_tasks(root: Path) -> list[str]:
    from validate_task_ledger import extract_tasks  # type: ignore[import-not-found]

    tasks_path = root / "TASKS.md"
    if not tasks_path.exists():
        raise RuntimeError("TASKS.md is missing")
    _, unchecked = extract_tasks(tasks_path)
    ids: list[str] = []
    for task in unchecked:
        for task_id in task.get("ids", []):
            if any(task_id.startswith(prefix) for prefix in CRITICAL_PREFIXES):
                ids.append(task_id)
    return sorted(set(ids))


def assess(
    *,
    root: Path = ROOT,
    run: RunFn = _run,
    gha_head_sha: str = "",
) -> Readiness:
    """Collect all release evidence without mutating the repository."""
    result = Readiness()
    try:
        from workflow_state_guard import collect_state

        state = collect_state(
            remote="sandboxcom",
            gha_head_sha=gha_head_sha,
            collect_unintegrated_worktrees=True,
            collect_unintegrated_branches=True,
            run=run,
            cwd=str(root),
        )
        result.branch = state.branch
        result.head = state.head
        result.ci_head_sha = gha_head_sha or state.head
        result.ci_head_matches = not gha_head_sha or gha_head_sha == state.head
        result.dirty_count = state.dirty_count
        result.unintegrated_worktrees = state.unintegrated_worktrees

        detached = _detached_worktrees(run, root)
        result.detached_worktrees = detached

        verdict, _ci_detail = _ci_verdict(state.head, state.branch, run)
        result.ci_verdict = verdict
        if not result.ci_head_matches or verdict != "GREEN":
            result.errors.append(
                f"CI evidence is not a successful run for HEAD {state.head} "
                f"(head_match={result.ci_head_matches}, verdict={verdict})"
            )
        if state.dirty_count:
            result.errors.append(f"worktree has {state.dirty_count} dirty path(s)")
        if detached or state.unintegrated_worktrees or state.unintegrated_branches:
            result.errors.append(
                "detached or unintegrated sibling worktree/branch exists"
            )

        result.version_consistent, result.version_detail = _version_check(run, root)
        if not result.version_consistent:
            result.errors.append("project version files are inconsistent")

        result.incomplete_release_tasks = _incomplete_tasks(root)
        result.ledger_valid, result.ledger_detail = _ledger_check(run, root)
        if result.incomplete_release_tasks:
            result.errors.append(
                "release-critical TASKS.md items are incomplete: "
                + ", ".join(result.incomplete_release_tasks)
            )
        if not result.ledger_valid:
            result.errors.append("TASKS.md ledger validation failed")
    except Exception as exc:
        result.errors.append(f"preflight evidence collection failed: {exc}")

    return result


def _exit_code(result: Readiness) -> int:
    if result.ready:
        return EXIT_OK
    if any(error.startswith("CI evidence") for error in result.errors) or (
        result.head and not (result.ci_head_matches and result.ci_verdict == "GREEN")
    ):
        return EXIT_CI
    if any(error.startswith("worktree has") for error in result.errors):
        return EXIT_DIRTY
    if any("detached or unintegrated" in error for error in result.errors):
        return EXIT_WORKTREE
    if any("version" in error for error in result.errors):
        return EXIT_VERSION
    if any("TASKS" in error or "ledger" in error for error in result.errors):
        return EXIT_TASKS
    return EXIT_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gha-head-sha", default="", help="expected CI HEAD SHA")
    parser.add_argument("--human", action="store_true", help="also print a short human summary")
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = assess(gha_head_sha=args.gha_head_sha)
    payload = asdict(result)
    payload["ready"] = result.ready
    exit_code = _exit_code(result)
    payload["exit_code"] = exit_code
    print(json.dumps(payload, sort_keys=True))
    if args.human:
        print("RELEASE-READY" if result.ready else "RELEASE-BLOCKED")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
