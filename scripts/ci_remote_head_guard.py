#!/usr/bin/env python3
"""Fail closed unless a remote branch points at the exact local HEAD."""

# Native macOS Python3.9 evaluates RunFn at import; retain the Optional contract.
# ruff: noqa: UP045

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Optional

import workflow_state_guard as _workflow_state

RunFn = Callable[[Sequence[str], Optional[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class RemoteHeadState:
    branch: str
    local_head: str
    remote: str
    remote_ref: str
    remote_head: str
    dirty_count: int
    staged_count: int
    untracked_count: int
    status: list[str]

    @property
    def remote_matches_local(self) -> bool:
        return bool(self.remote_head) and self.remote_head == self.local_head

    @property
    def is_clean(self) -> bool:
        return self.dirty_count == 0


class GuardError(RuntimeError):
    def __init__(self, message: str, native_status: int = 1) -> None:
        super().__init__(message)
        self.native_status = native_status


def _run(argv: Sequence[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _stdout(argv: Sequence[str], run: RunFn, cwd: Optional[str] = None) -> str:
    result = run(argv, cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise GuardError(f"{list(argv)}: {detail}", result.returncode)
    return result.stdout.strip()


def _status_lines(run: RunFn, cwd: Optional[str] = None) -> list[str]:
    result = run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git status failed").strip()
        raise GuardError(f"git status --porcelain=v1 --untracked-files=all: {detail}", result.returncode)
    return [line for line in result.stdout.splitlines() if line.strip()]


# Staged/untracked porcelain semantics belong to workflow_state_guard.


def _remote_head(output: str) -> str:
    first = output.splitlines()[0].split() if output.splitlines() else []
    return first[0] if first else ""


def collect_state(
    ref: str = "", remote: str = "sandboxcom", run: RunFn = _run,
    cwd: Optional[str] = None,
) -> RemoteHeadState:
    branch = _stdout(["git", "branch", "--show-current"], run, cwd) or "DETACHED"
    local_head = _stdout(["git", "rev-parse", "--verify", "HEAD"], run, cwd)
    status = _status_lines(run, cwd)
    remote_name = remote or "sandboxcom"
    remote_branch = ref or branch
    remote_ref = f"refs/heads/{remote_branch}"
    remote_output = _stdout(["git", "ls-remote", remote_name, remote_ref], run, cwd)

    return RemoteHeadState(
        branch=branch,
        local_head=local_head,
        remote=remote_name,
        remote_ref=remote_ref,
        remote_head=_remote_head(remote_output),
        dirty_count=len(status),
        staged_count=_workflow_state._staged_count(status),
        untracked_count=_workflow_state._untracked_count(status),
        status=status,
    )


def guard_state(state: RemoteHeadState, *, allow_dirty_unstaged: bool = False) -> list[str]:
    errors: list[str] = []
    if not state.remote_head:
        errors.append(f"remote branch {state.remote}/{state.remote_ref} does not exist")
    elif state.remote_head != state.local_head:
        errors.append(
            f"remote {state.remote}/{state.remote_ref} is {state.remote_head}, not local HEAD {state.local_head}"
        )
    if state.staged_count:
        errors.append(f"{state.staged_count} staged change(s) would not be present in the remote CI run")
    if state.dirty_count and not allow_dirty_unstaged:
        errors.append(f"{state.dirty_count} local dirty path(s) would make local tests differ from remote CI")
    if allow_dirty_unstaged and state.staged_count == 0 and state.dirty_count:
        errors.append(
            "dirty unstaged work exists; use committed-head CI only for remote harness checks, not release evidence"
        )
    return errors


def validate_expected_head(
    state: RemoteHeadState, expected_branch: str, expected_sha: str,
) -> None:
    """Bind optional caller identity to the actual full native source identity."""
    if not expected_branch and not expected_sha:
        return
    if not expected_branch or not _full_sha(expected_sha):
        raise GuardError("expected branch and full40hex SHA must be supplied together", 2)
    if state.branch != expected_branch or state.local_head != expected_sha.lower():
        raise GuardError("native branch/fullSHA differs from the saved expected candidate", 2)


def _full_sha(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", value))


def validate_feature_ci_inputs(
    *, expected_branch: str, expected_sha: str, baseline_ref: str, baseline_sha: str,
    remote: str, repo: str, workflow: str,
) -> None:
    """Reject unsafe diagnostic inputs before any native remote lookup."""
    if not expected_branch or not _full_sha(expected_sha):
        raise GuardError("feature-ci requires the saved expected branch/full40hex SHA", 2)
    if not re.fullmatch(r"(?:feature|diagnostic)/[A-Za-z0-9][A-Za-z0-9._/-]*", expected_branch):
        raise GuardError("feature-ci permits only literal feature/** or diagnostic/** heads", 2)
    if remote != "sandboxcom" or repo != "sandboxcom/gludd" or workflow != "Build and Release":
        raise GuardError("feature-ci requires sandboxcom, sandboxcom/gludd and Build and Release", 2)
    if os.environ.get("FORCE") == "1" or os.environ.get("GLUDD_FORCE_PUSH") == "1":
        raise GuardError("feature-ci does not permit force or policy bypass", 2)
    if (
        baseline_ref not in {"master", "main", "development"}
        and not re.fullmatch(r"release-candidate/[A-Za-z0-9][A-Za-z0-9._/-]*", baseline_ref)
    ) or not _full_sha(baseline_sha):
        raise GuardError("feature-ci needs a published master/main/development or "
                         "release-candidate/** baseline and full40hex SHA", 2)


def _native_success(
    argv: Sequence[str], run: RunFn, cwd: Optional[str], *, refusal: str,
) -> None:
    result = run(argv, cwd)
    if result.returncode:
        detail = (result.stderr or result.stdout or refusal).strip()
        raise GuardError(f"{refusal}: {detail}", result.returncode)


def validate_feature_ci(
    state: RemoteHeadState, *, expected_branch: str, expected_sha: str,
    baseline_ref: str, baseline_sha: str, repo: str = "sandboxcom/gludd",
    workflow: str = "Build and Release", publication: bool = False,
    run: RunFn = _run, cwd: Optional[str] = None,
) -> None:
    """Validate source-only diagnostics against independently published workflow bytes."""
    validate_feature_ci_inputs(
        expected_branch=expected_branch, expected_sha=expected_sha,
        baseline_ref=baseline_ref, baseline_sha=baseline_sha,
        remote=state.remote, repo=repo, workflow=workflow,
    )
    validate_expected_head(state, expected_branch, expected_sha)
    if state.remote_ref != "refs/heads/" + expected_branch:
        raise GuardError("diagnostic heads-ref differs from the saved expected branch", 2)
    _native_success(["git", "check-ref-format", "refs/heads/" + expected_branch], run, cwd,
                    refusal="invalid diagnostic branch")
    _native_success(["git", "check-ref-format", "refs/heads/" + baseline_ref], run, cwd,
                    refusal="invalid diagnostic baseline branch")
    if not state.is_clean:
        raise GuardError("feature-ci requires a clean native committed source", 2)
    canonical_urls = {"git@github.com:sandboxcom/gludd.git", "https://github.com/sandboxcom/gludd.git"}
    local_url = _stdout(["git", "config", "--local", "--get-all", "remote.sandboxcom.url"], run, cwd)
    fetch_url = _stdout(["git", "remote", "get-url", "--all", "sandboxcom"], run, cwd)
    push_url = _stdout(["git", "remote", "get-url", "--push", "--all", "sandboxcom"], run, cwd)
    if local_url not in canonical_urls or fetch_url != local_url or push_url not in canonical_urls:
        raise GuardError("feature-ci remote identity is rewritten, ambiguous or foreign", 2)
    baseline_sha = baseline_sha.lower()
    baseline_head = "refs/heads/" + baseline_ref
    rows = _stdout(["git", "ls-remote", "--exit-code", "--heads", "sandboxcom", baseline_head], run, cwd)
    if [row.split() for row in rows.splitlines()] != [[baseline_sha, baseline_head]]:
        raise GuardError("published baseline ref does not equal the explicit fullSHA", 2)
    local_baseline = _stdout(["git", "rev-parse", "--verify", baseline_sha + "^{commit}"], run, cwd)
    if local_baseline != baseline_sha:
        raise GuardError("baseline local object does not equal the published commit", 2)
    _native_success(["git", "merge-base", "--is-ancestor", baseline_sha, expected_sha.lower()], run, cwd,
                    refusal="published baseline must be an ancestor of the diagnostic source")
    for commit in (baseline_sha, expected_sha.lower()):
        kind = _stdout(["git", "cat-file", "-t", commit + ":.github/workflows/build.yml"], run, cwd)
        if kind != "blob":
            raise GuardError("published Build workflow provenance is not a regular Git blob", 2)
    _native_success(["git", "diff", "--quiet", baseline_sha, expected_sha.lower(), "--", ".github/workflows"],
                    run, cwd, refusal="workflow bytes differ from the published baseline")
    history = _stdout(["git", "log", "--format=%H", "--full-history",
                       baseline_sha + ".." + expected_sha.lower(), "--", ".github/workflows"], run, cwd)
    if history:
        raise GuardError("workflow history changed after the published baseline; "
                         "retain refs and use authorized workflow repair", 2)
    if not publication and state.remote_head != expected_sha.lower():
        raise GuardError("diagnostic remote head differs from the saved expected fullSHA", 2)


def print_state(state: RemoteHeadState, errors: Sequence[str], *, as_json: bool) -> None:
    if as_json:
        payload = asdict(state)
        payload["remote_matches_local"] = state.remote_matches_local
        payload["is_clean"] = state.is_clean
        payload["errors"] = list(errors)
        json.dump(payload, sys.stdout, sort_keys=True)
        print()
        return

    prefix = "REMOTE-HEAD-READY" if not errors else "REMOTE-HEAD-BLOCKED"
    remote_head = state.remote_head or "<missing>"
    print(
        f"{prefix} branch={state.branch} local_head={state.local_head} "
        f"remote={state.remote}/{state.remote_ref} remote_head={remote_head} "
        f"dirty={state.dirty_count} staged={state.staged_count} untracked={state.untracked_count}"
    )
    for error in errors:
        print(f"BLOCKED: {error}")
    for line in state.status[:25]:
        print(f"  {line}")
    if len(state.status) > 25:
        print(f"  ... {len(state.status) - 25} more changed path(s)")



def main(argv: Optional[Sequence[str]] = None, run: RunFn = _run) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="", help="branch ref to verify on the remote; defaults to current branch")
    parser.add_argument("--remote", default="sandboxcom", help="git remote name")
    parser.add_argument("--json", action="store_true", help="emit machine-readable state")
    parser.add_argument("--purpose", choices=("canonical", "feature-ci"), default="canonical")
    parser.add_argument("--publication", action="store_true", help="validate before first feature publication")
    parser.add_argument("--expected-branch", default="")
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--baseline-ref", default="")
    parser.add_argument("--baseline-sha", default="")
    parser.add_argument("--repo", default="sandboxcom/gludd")
    parser.add_argument("--workflow", default="Build and Release")
    parser.add_argument(
        "--allow-dirty-unstaged",
        action="store_true",
        help="allow remote-head checks while local unstaged work exists, but still fail staged work",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.purpose == "feature-ci":
            validate_feature_ci_inputs(
                expected_branch=args.expected_branch, expected_sha=args.expected_sha,
                baseline_ref=args.baseline_ref, baseline_sha=args.baseline_sha,
                remote=args.remote, repo=args.repo, workflow=args.workflow,
            )
        state = collect_state(args.ref, args.remote, run=run)
        validate_expected_head(state, args.expected_branch, args.expected_sha)
        if args.purpose == "feature-ci":
            validate_feature_ci(
                state, expected_branch=args.expected_branch, expected_sha=args.expected_sha,
                baseline_ref=args.baseline_ref, baseline_sha=args.baseline_sha,
                repo=args.repo, workflow=args.workflow, publication=args.publication, run=run,
            )
            errors = []
        else:
            if args.publication:
                raise GuardError("--publication is restricted to explicit feature-ci purpose", 2)
            errors = guard_state(state, allow_dirty_unstaged=args.allow_dirty_unstaged)
    except GuardError as exc:
        print(f"REMOTE-HEAD-UNKNOWN error={exc}", file=sys.stderr if args.purpose == "feature-ci" else sys.stdout)
        return exc.native_status if args.purpose == "feature-ci" else 1

    if args.purpose == "feature-ci" and not args.json:
        print(f"FEATURE-CI-SOURCE-READY branch={state.branch} sha={state.local_head} "
              f"baseline_ref={args.baseline_ref} baseline_sha={args.baseline_sha.lower()} "
              f"publication_started=false gate_evidence=false release_ready=false")
        print("AGENT_NEXT target=git-push-sandboxcom outcome=diagnostic-source-validated "
              "purpose=feature-ci gate_evidence=false release_ready=false: Only native source, "
              "remote binding and published workflow provenance were checked; normal transport, "
              "saved-SHA verification and exact-SHA CI signal remain required.", file=sys.stderr)
    else:
        print_state(state, errors, as_json=args.json)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
