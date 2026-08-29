#!/usr/bin/env python3
"""Git workflow state machine guard for local, remote, CI, and worktree evidence."""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

RunFn = Callable[[Sequence[str], Optional[str]], subprocess.CompletedProcess[str]]  # noqa: UP045


@dataclass(frozen=True)
class WorkflowState:
    """Immutable local, remote, CI, and sibling-worktree workflow evidence."""
    branch: str
    head: str
    dirty_count: int
    staged_count: int
    untracked_count: int
    status: list[str]
    remote: str
    remote_ref: str
    remote_head: str
    master_head: str
    development_head: str

    master_is_ancestor_of_development: bool | None
    gha_head_sha: str
    reconciled_preserve_heads: list[str] = field(default_factory=list)
    unintegrated_worktrees: list[dict[str, object]] = field(default_factory=list)
    unintegrated_branches: list[dict[str, object]] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """Return whether the current worktree has no changed paths."""
        return self.dirty_count == 0

    @property
    def remote_matches_local(self) -> bool:
        """Return whether the requested remote ref equals local HEAD."""
        return bool(self.remote_head) and self.remote_head == self.head

    @property
    def gha_matches_local(self) -> bool:
        """Return whether supplied hosted-CI evidence equals local HEAD."""
        return bool(self.gha_head_sha) and self.gha_head_sha == self.head


class WorkflowError(RuntimeError):
    """Signal that required workflow evidence could not be collected."""

    pass


def _run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _stdout(argv: Sequence[str], run: RunFn, cwd: str | None = None) -> str:
    result = run(argv, cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise WorkflowError(f"{list(argv)}: {detail}")
    return result.stdout.strip()


def _maybe_stdout(argv: Sequence[str], run: RunFn, cwd: str | None = None) -> str:
    result = run(argv, cwd)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _status_lines(run: RunFn, cwd: str | None = None) -> list[str]:
    result = run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git status failed").strip()
        raise WorkflowError(f"git status --porcelain=v1 --untracked-files=all: {detail}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _staged_count(lines: Sequence[str]) -> int:
    return sum(1 for line in lines if line[:2] != "??" and line[:1] not in {"", " "})


def _untracked_count(lines: Sequence[str]) -> int:
    return sum(1 for line in lines if line.startswith("??"))


def _remote_head(output: str) -> str:
    lines = output.splitlines()
    if not lines:
        return ""
    parts = lines[0].split()
    return parts[0] if parts else ""


def _is_ancestor(ancestor: str, descendant: str, run: RunFn, cwd: str | None = None) -> bool | None:
    if not ancestor or not descendant:
        return None
    result = run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = (result.stderr or result.stdout or "merge-base failed").strip()
    raise WorkflowError(f"git merge-base --is-ancestor {ancestor} {descendant}: {detail}")


def _short_branch(ref: str) -> str:
    prefix = "refs/heads/"
    return ref[len(prefix):] if ref.startswith(prefix) else (ref or "DETACHED")


def _worktree_entries(porcelain_output: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in porcelain_output.splitlines():
        if not line.strip():
            if current.get("path"):
                entries.append(current)
            current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line.removeprefix("worktree ")
        elif line.startswith("HEAD "):
            current["head"] = line.removeprefix("HEAD ")
        elif line.startswith("branch "):
            current["branch"] = _short_branch(line.removeprefix("branch "))
        elif line.startswith("prunable"):
            current["prunable"] = line.removeprefix("prunable").strip() or "unknown"
    if current.get("path"):
        entries.append(current)
    return entries


def _is_protected_trunk_branch(branch: str) -> bool:
    return branch in {"development", "main", "master"}


def _collect_unintegrated_worktrees(
    *,
    run: RunFn,
    cwd: str | None = None,
    target_ref: str = "HEAD",
) -> list[dict[str, object]]:
    current_path = _stdout(["git", "rev-parse", "--show-toplevel"], run, cwd)
    target_head = _stdout(["git", "rev-parse", "--verify", target_ref], run, cwd)
    output = _maybe_stdout(["git", "worktree", "list", "--porcelain"], run, cwd)
    unintegrated: list[dict[str, object]] = []
    for entry in _worktree_entries(output):
        path = entry.get("path", "")
        if not path or path == current_path:
            continue
        branch = entry.get("branch", "DETACHED")
        if entry.get("prunable"):
            unintegrated.append(
                {
                    "path": path,
                    "branch": branch,
                    "head": entry.get("head", ""),
                    "dirty_count": 0,
                    "status": [],
                    "reasons": ["prunable_registration"],
                    "detail": entry["prunable"],
                }
            )
            continue
        head = entry.get("head") or _maybe_stdout(["git", "rev-parse", "--verify", "HEAD"], run, path)
        status = _status_lines(run, path)
        reasons: list[str] = []
        if status:
            reasons.append("dirty")
        if (
            head
            and target_head
            and _is_ancestor(head, target_head, run, cwd) is False
            and not _is_protected_trunk_branch(branch)
        ):
            reasons.append("head_not_merged")
        if reasons:
            unintegrated.append(
                {
                    "path": path,
                    "branch": branch,
                    "head": head,
                    "dirty_count": len(status),
                    "status": status[:25],
                    "reasons": reasons,
                }
            )
    return unintegrated
DEFAULT_PRESERVE_BRANCH_PATTERNS = ("main-dirty-preserve-*", "preserve-*")
DEFAULT_RECONCILED_PRESERVE_HEAD_FILE = "config/reconciled_preserved_heads.txt"


def _branch_matches(branch: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(branch, pattern) for pattern in patterns)


def _branch_entries(ref_output: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in ref_output.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        entries.append({"branch": parts[0], "head": parts[1]})
    return entries


def _protected_branch_names(entries: Sequence[dict[str, str]]) -> list[str]:
    return [entry["branch"] for entry in entries if _is_protected_trunk_branch(entry["branch"])]


def _branch_unique_commits(
    branch: str,
    target_head: str,
    *,
    exclude_branches: Sequence[str] = (),
    run: RunFn,
    cwd: str | None = None,
) -> list[str]:
    argv = [
        "git",
        "rev-list",
        "--cherry-pick",
        "--right-only",
        "--no-merges",
        f"{target_head}...{branch}",
    ]
    for excluded in exclude_branches:
        argv.append(f"^{excluded}")
    output = _maybe_stdout(argv, run, cwd)
    return [line.strip() for line in output.splitlines() if line.strip()]


def _reconciled_preserve_head_tokens(text: str) -> set[str]:
    heads: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            heads.add(line.split()[0])
    return heads


def _load_reconciled_preserve_heads(
    *,
    run: RunFn,
    cwd: str | None = None,
    head_file: str = DEFAULT_RECONCILED_PRESERVE_HEAD_FILE,
    explicit_heads: Sequence[str] = (),
) -> set[str]:
    heads = {head.strip() for head in explicit_heads if head.strip()}
    if not head_file:
        return heads

    raw_path = Path(head_file)
    candidates: list[Path] = [raw_path] if raw_path.is_absolute() else []
    if not raw_path.is_absolute():
        if cwd:
            candidates.append(Path(cwd) / raw_path)
        candidates.append(Path.cwd() / raw_path)
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            heads.update(_reconciled_preserve_head_tokens(candidate.read_text(encoding="utf-8")))
            return heads
        except FileNotFoundError:
            continue

    if not raw_path.is_absolute():
        repo_root = _maybe_stdout(["git", "rev-parse", "--show-toplevel"], run, cwd)
        repo_candidate = Path(repo_root) / raw_path if repo_root else None
        if repo_candidate is not None and repo_candidate not in seen and repo_candidate.exists():
            heads.update(
                _reconciled_preserve_head_tokens(repo_candidate.read_text(encoding="utf-8"))
            )
    return heads


def _collect_unintegrated_branches(
    *,
    run: RunFn,
    cwd: str | None = None,
    target_ref: str = "HEAD",
    branch_patterns: Sequence[str] = DEFAULT_PRESERVE_BRANCH_PATTERNS,
    reconciled_preserve_heads: Sequence[str] = (),
) -> list[dict[str, object]]:
    reconciled_heads = {head.strip() for head in reconciled_preserve_heads if head.strip()}
    current_branch = _stdout(["git", "branch", "--show-current"], run, cwd) or "DETACHED"
    target_head = _stdout(["git", "rev-parse", "--verify", target_ref], run, cwd)
    ref_output = _maybe_stdout(
        ["git", "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"],
        run,
        cwd,
    )
    entries = _branch_entries(ref_output)
    protected_branches = _protected_branch_names(entries)
    unintegrated: list[dict[str, object]] = []
    for entry in entries:
        branch = entry["branch"]
        head = entry["head"]
        if (
            branch == current_branch
            or _is_protected_trunk_branch(branch)
            or not _branch_matches(branch, branch_patterns)
            or head in reconciled_heads
        ):
            continue
        unique_commits = _branch_unique_commits(
            branch,
            target_head,
            exclude_branches=protected_branches,
            run=run,
            cwd=cwd,
        )
        if unique_commits:
            unintegrated.append(
                {
                    "branch": branch,
                    "head": head,
                    "unique_count": len(unique_commits),
                    "commits": unique_commits[:25],
                    "reasons": ["preserved_branch_not_reconciled"],
                }
            )
    return unintegrated


def collect_state(
    *,
    remote: str = "sandboxcom",
    ref: str | None = None,
    gha_head_sha: str = "",
    collect_unintegrated_worktrees: bool = False,
    collect_unintegrated_branches: bool = False,
    worktree_target_ref: str = "HEAD",
    preserve_branch_patterns: Sequence[str] = DEFAULT_PRESERVE_BRANCH_PATTERNS,
    reconciled_preserve_heads: Sequence[str] = (),
    reconciled_preserve_head_file: str = DEFAULT_RECONCILED_PRESERVE_HEAD_FILE,
    run: RunFn = _run,
    cwd: str | None = None,
) -> WorkflowState:
    """Collect one explicit snapshot of release-relevant Git workflow state."""
    branch = _stdout(["git", "branch", "--show-current"], run, cwd) or "DETACHED"
    head = _stdout(["git", "rev-parse", "--verify", "HEAD"], run, cwd)
    status = _status_lines(run, cwd)
    remote_branch = ref or branch
    remote_ref = f"refs/heads/{remote_branch}"
    remote_output = _maybe_stdout(["git", "ls-remote", remote, remote_ref], run, cwd)
    master_head = _maybe_stdout(["git", "rev-parse", "--verify", "master"], run, cwd)
    development_head = _maybe_stdout(["git", "rev-parse", "--verify", "development"], run, cwd)
    loaded_reconciled_heads = (
        _load_reconciled_preserve_heads(
            run=run,
            cwd=cwd,
            head_file=reconciled_preserve_head_file,
            explicit_heads=reconciled_preserve_heads,
        )
        if collect_unintegrated_branches or reconciled_preserve_heads
        else set()
    )
    return WorkflowState(
        branch=branch,
        head=head,
        dirty_count=len(status),
        staged_count=_staged_count(status),
        untracked_count=_untracked_count(status),
        status=status,
        remote=remote,
        remote_ref=remote_ref,
        remote_head=_remote_head(remote_output),
        master_head=master_head,
        development_head=development_head,
        master_is_ancestor_of_development=_is_ancestor(master_head, development_head, run, cwd),
        gha_head_sha=gha_head_sha,
        reconciled_preserve_heads=sorted(loaded_reconciled_heads),
        unintegrated_worktrees=(
            _collect_unintegrated_worktrees(run=run, cwd=cwd, target_ref=worktree_target_ref)
            if collect_unintegrated_worktrees
            else []
        ),
        unintegrated_branches=(
            _collect_unintegrated_branches(
                run=run,
                cwd=cwd,
                target_ref=worktree_target_ref,
                branch_patterns=preserve_branch_patterns,
                reconciled_preserve_heads=tuple(loaded_reconciled_heads),
            )
            if collect_unintegrated_branches
            else []
        ),
    )


def workflow_errors(
    state: WorkflowState,
    *,
    assert_clean: bool = False,
    assert_no_feature_on_master: bool = False,
    assert_merge_ready: bool = False,
    assert_remote_head: bool = False,
    assert_gha_matches_local: bool = False,
    assert_no_unintegrated_worktrees: bool = False,
    assert_no_unintegrated_branches: bool = False,
) -> list[str]:
    """Return stable fail-closed diagnostics for requested workflow assertions."""
    errors: list[str] = []
    if assert_clean and not state.is_clean:
        errors.append(
            f"{state.dirty_count} dirty path(s) make local test evidence "
            "unreproducible in GHA"
        )
    if assert_no_feature_on_master and state.branch == "master" and not state.is_clean:
        errors.append(
            "feature or guardrail edits are present on master; "
            "move work to development or a release-sync worktree"
        )
    if assert_merge_ready:
        if state.master_is_ancestor_of_development is None:
            errors.append(
                "cannot prove master is contained in development; "
                "both branches must exist before release merge"
            )
        elif not state.master_is_ancestor_of_development:
            errors.append(
                "master has commits not contained in development; "
                "repair topology before release merge, do not cherry-pick"
            )
    if assert_remote_head:
        if not state.remote_head:
            errors.append(f"remote branch {state.remote}/{state.remote_ref} does not exist")
        elif state.remote_head != state.head:
            errors.append(
                f"remote {state.remote}/{state.remote_ref} is {state.remote_head}, not local HEAD {state.head}"
            )
    if assert_gha_matches_local:
        if not state.gha_head_sha:
            errors.append("latest GHA head SHA was not provided; cannot prove CI is testing this commit")
        elif state.gha_head_sha != state.head:
            errors.append(f"latest GHA head {state.gha_head_sha} does not match local HEAD {state.head}")
    if assert_no_unintegrated_worktrees and state.unintegrated_worktrees:
        paths = ", ".join(str(item.get("path", "<unknown>")) for item in state.unintegrated_worktrees[:5])
        errors.append(
            f"{len(state.unintegrated_worktrees)} sibling worktree(s) contain unintegrated changes: {paths}"
        )
    if assert_no_unintegrated_branches and state.unintegrated_branches:
        branches = ", ".join(str(item.get("branch", "<unknown>")) for item in state.unintegrated_branches[:5])
        errors.append(
            f"{len(state.unintegrated_branches)} preserved branch(es) contain unreconciled patches: {branches}"
        )
    return errors


def print_state(state: WorkflowState, errors: Sequence[str], *, as_json: bool) -> None:
    """Print the workflow snapshot and any assertion failures."""
    if as_json:
        payload = asdict(state)
        payload["is_clean"] = state.is_clean
        payload["remote_matches_local"] = state.remote_matches_local
        payload["gha_matches_local"] = state.gha_matches_local
        payload["errors"] = list(errors)
        json.dump(payload, sys.stdout, sort_keys=True)
        print()
        return
    prefix = "WORKFLOW-READY" if not errors else "WORKFLOW-BLOCKED"
    remote_head = state.remote_head or "<missing>"
    gha_head = state.gha_head_sha or "<unknown>"
    print(
        f"{prefix} branch={state.branch} head={state.head} dirty={state.dirty_count} "
        f"staged={state.staged_count} untracked={state.untracked_count} "
        f"remote={state.remote}/{state.remote_ref} remote_head={remote_head} gha_head={gha_head} "
        f"master_in_development={state.master_is_ancestor_of_development} "
        f"unintegrated_worktrees={len(state.unintegrated_worktrees)} "
        f"unintegrated_branches={len(state.unintegrated_branches)}"
    )
    for error in errors:
        print(f"BLOCKED: {error}")
    for item in state.unintegrated_worktrees[:10]:
        reasons_value = item.get("reasons", [])
        if isinstance(reasons_value, list):
            reasons = ",".join(str(reason) for reason in reasons_value)
        else:
            reasons = str(reasons_value)
        path = str(item.get("path", "<unknown>"))
        branch = str(item.get("branch", "DETACHED"))
        head = str(item.get("head", ""))
        dirty_count = item.get("dirty_count", 0)
        print(
            f"UNINTEGRATED: path={path} branch={branch} head={head} "
            f"dirty={dirty_count} reasons={reasons}"
        )
    for item in state.unintegrated_branches[:10]:
        reasons_value = item.get("reasons", [])
        if isinstance(reasons_value, list):
            reasons = ",".join(str(reason) for reason in reasons_value)
        else:
            reasons = str(reasons_value)
        branch = str(item.get("branch", "<unknown>"))
        head = str(item.get("head", ""))
        unique_count = item.get("unique_count", 0)
        print(
            f"UNINTEGRATED-BRANCH: branch={branch} head={head} "
            f"unique_commits={unique_count} reasons={reasons}"
        )
    for line in state.status[:25]:
        print(f"  {line}")
    if len(state.status) > 25:
        print(f"  ... {len(state.status) - 25} more changed path(s)")


def main(argv: Sequence[str] | None = None, run: RunFn = _run) -> int:
    """Run the workflow-state CLI and return a stable process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable state")
    parser.add_argument("--remote", default="sandboxcom", help="git remote name")
    parser.add_argument(
        "--ref",
        default="",
        help="remote branch ref to compare with local HEAD",
    )
    parser.add_argument(
        "--gha-head-sha",
        default="",
        help="latest GitHub Actions head SHA to compare",
    )
    parser.add_argument(
        "--worktree-target-ref",
        default="HEAD",
        help="ref sibling worktrees and preserved branches must already be merged into",
    )

    parser.add_argument(
        "--preserve-branch-pattern",
        action="append",
        default=[],
        help="branch glob to require cherry-equivalent reconciliation for",
    )
    parser.add_argument(
        "--reconciled-preserve-head",
        action="append",
        default=[],
        help="preserved branch HEAD SHA already audited and reconciled",
    )
    parser.add_argument(
        "--reconciled-preserve-head-file",
        default=DEFAULT_RECONCILED_PRESERVE_HEAD_FILE,
        help="repo-relative file listing audited preserved branch HEAD SHAs",
    )
    parser.add_argument(
        "--assert-clean",
        action="store_true",
        help="fail if the worktree is dirty",
    )
    parser.add_argument(
        "--assert-no-feature-on-master",
        action="store_true",
        help="fail if master has local edits",
    )
    parser.add_argument(
        "--assert-merge-ready",
        action="store_true",
        help="fail if development cannot merge cleanly to master topology",
    )
    parser.add_argument(
        "--assert-remote-head",
        action="store_true",
        help="fail if remote ref is missing or not local HEAD",
    )
    parser.add_argument(
        "--assert-gha-matches-local",
        action="store_true",
        help="fail if provided GHA head SHA is not local HEAD",
    )
    parser.add_argument(
        "--assert-no-unintegrated-worktrees",
        action="store_true",
        help="fail if sibling worktrees contain dirty or unmerged changes",
    )
    parser.add_argument(
        "--assert-no-unintegrated-branches",
        action="store_true",
        help="fail if preserved local branches contain unreconciled patches",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:

        state = collect_state(
            remote=args.remote,
            ref=args.ref or None,
            gha_head_sha=args.gha_head_sha,
            collect_unintegrated_worktrees=args.assert_no_unintegrated_worktrees,
            collect_unintegrated_branches=args.assert_no_unintegrated_branches,
            worktree_target_ref=args.worktree_target_ref,
            preserve_branch_patterns=tuple(args.preserve_branch_pattern) or DEFAULT_PRESERVE_BRANCH_PATTERNS,
            reconciled_preserve_heads=tuple(args.reconciled_preserve_head),
            reconciled_preserve_head_file=args.reconciled_preserve_head_file,
            run=run,
        )
        errors = workflow_errors(
            state,
            assert_clean=args.assert_clean,
            assert_no_feature_on_master=args.assert_no_feature_on_master,
            assert_merge_ready=args.assert_merge_ready,
            assert_remote_head=args.assert_remote_head,
            assert_gha_matches_local=args.assert_gha_matches_local,
            assert_no_unintegrated_worktrees=args.assert_no_unintegrated_worktrees,
            assert_no_unintegrated_branches=args.assert_no_unintegrated_branches,
        )
    except WorkflowError as exc:
        print(f"WORKFLOW-UNKNOWN error={exc}")
        return 1

    print_state(state, errors, as_json=args.json)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
