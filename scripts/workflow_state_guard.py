#!/usr/bin/env python3
"""Git workflow state machine guard for local, remote, CI, and worktree evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Optional

RunFn = Callable[[Sequence[str], Optional[str]], subprocess.CompletedProcess[str]]  # noqa: UP045


@dataclass(frozen=True)
class WorkflowState:
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
    unintegrated_worktrees: list[dict[str, object]] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return self.dirty_count == 0

    @property
    def remote_matches_local(self) -> bool:
        return bool(self.remote_head) and self.remote_head == self.head

    @property
    def gha_matches_local(self) -> bool:
        return bool(self.gha_head_sha) and self.gha_head_sha == self.head


class WorkflowError(RuntimeError):
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
    if current.get("path"):
        entries.append(current)
    return entries


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
        head = entry.get("head") or _maybe_stdout(["git", "rev-parse", "--verify", "HEAD"], run, path)
        status = _status_lines(run, path)
        reasons: list[str] = []
        if status:
            reasons.append("dirty")
        if head and target_head and _is_ancestor(head, target_head, run, cwd) is False:
            reasons.append("head_not_merged")
        if reasons:
            unintegrated.append(
                {
                    "path": path,
                    "branch": entry.get("branch", "DETACHED"),
                    "head": head,
                    "dirty_count": len(status),
                    "status": status[:25],
                    "reasons": reasons,
                }
            )
    return unintegrated


def collect_state(
    *,
    remote: str = "sandboxcom",
    ref: str | None = None,
    gha_head_sha: str = "",
    collect_unintegrated_worktrees: bool = False,
    worktree_target_ref: str = "HEAD",
    run: RunFn = _run,
    cwd: str | None = None,
) -> WorkflowState:
    branch = _stdout(["git", "branch", "--show-current"], run, cwd) or "DETACHED"
    head = _stdout(["git", "rev-parse", "--verify", "HEAD"], run, cwd)
    status = _status_lines(run, cwd)
    remote_branch = ref or branch
    remote_ref = f"refs/heads/{remote_branch}"
    remote_output = _maybe_stdout(["git", "ls-remote", remote, remote_ref], run, cwd)
    master_head = _maybe_stdout(["git", "rev-parse", "--verify", "master"], run, cwd)
    development_head = _maybe_stdout(["git", "rev-parse", "--verify", "development"], run, cwd)
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
        unintegrated_worktrees=(
            _collect_unintegrated_worktrees(run=run, cwd=cwd, target_ref=worktree_target_ref)
            if collect_unintegrated_worktrees
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
) -> list[str]:
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
    return errors


def print_state(state: WorkflowState, errors: Sequence[str], *, as_json: bool) -> None:
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
        f"unintegrated_worktrees={len(state.unintegrated_worktrees)}"
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
    for line in state.status[:25]:
        print(f"  {line}")
    if len(state.status) > 25:
        print(f"  ... {len(state.status) - 25} more changed path(s)")


def main(argv: Sequence[str] | None = None, run: RunFn = _run) -> int:
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
        help="ref sibling worktrees must already be merged into",
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
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        state = collect_state(
            remote=args.remote,
            ref=args.ref or None,
            gha_head_sha=args.gha_head_sha,
            collect_unintegrated_worktrees=args.assert_no_unintegrated_worktrees,
            worktree_target_ref=args.worktree_target_ref,
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
        )
    except WorkflowError as exc:
        print(f"WORKFLOW-UNKNOWN error={exc}")
        return 1

    print_state(state, errors, as_json=args.json)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
