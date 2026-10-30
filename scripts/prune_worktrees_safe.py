#!/usr/bin/env python3
"""Prune clean Git worktrees without reclaiming active logical workstreams."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import owned_git_checkpoint
    from workstream_registry import WorkstreamRegistry, default_registry_path
else:
    from scripts import owned_git_checkpoint
    from scripts.workstream_registry import WorkstreamRegistry, default_registry_path


@dataclass(frozen=True)
class WorktreeRecord:
    """One parsed entry from ``git worktree list --porcelain``."""

    path: Path
    branch: str | None
    locked: bool
    prunable: bool = False


@dataclass(frozen=True)
class PruningDecision:
    """A deterministic decision for one worktree."""

    action: str
    reason: str


def parse_worktrees(output: str) -> list[WorktreeRecord]:
    """Parse Git's stable porcelain worktree format."""
    records: list[WorktreeRecord] = []
    for block in output.strip().split("\n\n"):
        fields = block.splitlines()
        path_line = next((line for line in fields if line.startswith("worktree ")), "")
        if not path_line:
            continue
        branch_line = next((line for line in fields if line.startswith("branch ")), "")
        branch = branch_line.removeprefix("branch refs/heads/") if branch_line else None
        records.append(
            WorktreeRecord(
                path=Path(path_line.removeprefix("worktree ")).resolve(),
                branch=branch,
                locked=any(line == "locked" or line.startswith("locked ") for line in fields),
                prunable=any(
                    line == "prunable" or line.startswith("prunable ")
                    for line in fields
                ),
            )
        )
    return records


def pruning_decision(
    record: WorktreeRecord,
    *,
    active_branches: frozenset[str],
    protected_paths: frozenset[Path],
) -> PruningDecision:
    """Classify a worktree without consulting mutable process state."""
    if record.path in protected_paths:
        return PruningDecision("protect", "current/main checkout")
    if record.locked:
        return PruningDecision("protect", "git worktree lock")
    if record.branch is not None and record.branch in active_branches:
        return PruningDecision("protect", "active logical workstream")
    return PruningDecision("remove", "unregistered clean candidate")


def _git(
    *args: str, check: bool = True, timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--no-optional-locks", *args],
        cwd=Path.cwd(),
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def prune(*, registry_path: Path, validate_only: bool) -> int:
    """Protect active workstreams and remove only Git-confirmed clean candidates."""
    records = parse_worktrees(_git("worktree", "list", "--porcelain").stdout)
    if not records:
        print("wt-prune-safe: no worktrees registered")
        return 0
    current = Path(_git("rev-parse", "--show-toplevel").stdout.strip()).resolve()
    protected_paths = frozenset({records[0].path, current})
    active_branches = WorkstreamRegistry(registry_path).active_branches()
    decisions = [
        pruning_decision(
            record,
            active_branches=active_branches,
            protected_paths=protected_paths,
        )
        for record in records
    ]
    removable_prunable = [
        record
        for record, decision in zip(records, decisions, strict=True)
        if record.prunable and decision.action == "remove"
    ]
    protected_prunable = [
        record
        for record, decision in zip(records, decisions, strict=True)
        if record.prunable and decision.action == "protect"
    ]
    pruned_paths: frozenset[Path] = frozenset()
    if removable_prunable and not protected_prunable and not validate_only:
        prune_result = _git("worktree", "prune", "--expire", "now", check=False)
        if prune_result.returncode == 0:
            pruned_paths = frozenset(record.path for record in removable_prunable)
    removed = 0
    protected = 0
    retained = 0
    for record, decision in zip(records, decisions, strict=True):
        if decision.action == "protect":
            protected += 1
            print(f"  protected ({decision.reason}): {record.path}")
            continue
        if record.prunable:
            if validate_only:
                retained += 1
                print(f"  would prune (missing registration): {record.path}")
            elif record.path in pruned_paths:
                removed += 1
                print(f"  pruned (missing registration): {record.path}")
            else:
                retained += 1
                reason = (
                    "protected prunable registration exists"
                    if protected_prunable
                    else "git worktree prune failed"
                )
                print(f"  kept ({reason}): {record.path}")
            continue
        if validate_only:
            retained += 1
            print(f"  would remove (clean check deferred): {record.path}")
            continue
        try:
            _retire_worktree(
                branch=record.branch or "", worktree=record.path, registry_path=registry_path,
                validate_only=False, require_merged=False,
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            retained += 1
            print(f"  kept (native ownership/evidence refusal: {exc}): {record.path}")
        else:
            removed += 1
            print(f"  removed (clean, evidence archived): {record.path}")
    print(
        "wt-prune-safe done: "
        f"removed={removed} protected={protected} retained={retained} "
        f"active_branches={len(active_branches)}"
    )
    return 0



def _retention_identity(
    branch: str, worktree: Path,
) -> tuple[WorktreeRecord, tuple[tuple[int, int, int, int], ...]]:
    """Reuse native checkpoint authority and capture this linked-root identity."""
    owned_git_checkpoint.repository_root()
    inventory = _git("worktree", "list", "--porcelain", "-z", timeout=30).stdout
    owners = owned_git_checkpoint.registered_roots(inventory)
    if (worktree, False) not in owners.values():
        raise ValueError("WORKTREE must be a canonical registered linked checkout")
    common = Path(
        _git("rev-parse", "--path-format=absolute", "--git-common-dir", timeout=30).stdout.strip()
    )
    owned_git_checkpoint.verify_registered_root(worktree, main=False, common=common)
    records = [
        record for record in parse_worktrees(
            _git("worktree", "list", "--porcelain", timeout=30).stdout
        ) if record.path == worktree
    ]
    if (len(records) != 1 or records[0].branch != branch or records[0].prunable
            or _git("-C", str(worktree), "symbolic-ref", "--quiet", "HEAD", timeout=30).stdout.strip()
            != f"refs/heads/{branch}"):
        raise ValueError("WORKTREE registration and BRANCH must match exactly")
    identity = []
    for path, expected in ((worktree, stat.S_ISDIR), (worktree / ".git", stat.S_ISREG)):
        metadata = path.lstat()
        if not expected(metadata.st_mode):
            raise ValueError("WORKTREE or native backlink changed")
        identity.append((metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns, metadata.st_size))
    return records[0], tuple(identity)


def retain(*, branch: str, worktree: Path, reason: str, validate_only: bool) -> int:
    """Positively retain one verified linked checkout using Git's native lock."""
    if (not branch or branch != branch.strip() or not reason.strip()
            or not worktree.is_absolute() or ".." in worktree.parts
            or any(character in branch + str(worktree) + reason for character in "\0\r\n")):
        raise ValueError("explicit literal BRANCH, absolute WORKTREE and reason are required")
    receipt: dict[str, str | bool | int] = {
        "branch": branch, "worktree": str(worktree), "reason": reason,
        "validate_only": validate_only, "native_ownership_verified": 0,
    }
    if validate_only:
        print(json.dumps(receipt, sort_keys=True))
        return 0
    initial = _retention_identity(branch, worktree)
    if _retention_identity(branch, worktree) != initial:
        raise ValueError("WORKTREE identity or registration changed before retention")
    already_locked = initial[0].locked
    if not already_locked:
        _git("worktree", "lock", "--reason", reason, "--", str(worktree), timeout=30)
        confirmed = _retention_identity(branch, worktree)
        if not confirmed[0].locked or confirmed[1] != initial[1]:
            raise ValueError("native retention lock was not verified on the unchanged owner")
    receipt.update(native_ownership_verified=1, locked=True, already_locked=already_locked)
    print(json.dumps(receipt, sort_keys=True))
    return 0


@dataclass(frozen=True)
class _RetirementController:
    """Lossless native identity for one member of this invocation's launch chain."""

    pid: int
    ppid: int
    started: float
    cwd: Path
    executable: Path
    argv: tuple[str, ...]
    uid: tuple[int, int]


def _retirement_controller(pid: int) -> _RetirementController:
    """Query only one concrete process through the already installed library."""
    import psutil

    try:
        process = psutil.Process(pid)
        uids = process.uids()
        record = _RetirementController(
            pid, process.ppid(), process.create_time(), Path(process.cwd()),
            Path(process.exe()), tuple(process.cmdline()), (uids.real, uids.effective),
        )
        if not process.is_running():
            raise ValueError("retirement controller identity changed during inspection")
        return record
    except (psutil.Error, OSError, AttributeError) as exc:
        raise ValueError("retirement controller identity is unavailable") from exc


def _validate_retirement_controllers(
    records: tuple[_RetirementController, ...], *, root: Path, worktree: Path, branch: str,
) -> tuple[_RetirementController, ...]:
    """Allow only the concrete Python, optional selected uv, and public Make chain."""
    if os.environ.get("MAKEFILES"):
        raise ValueError("retirement Make source supplied through MAKEFILES")
    if len(records) not in {2, 3} or len({record.pid for record in records}) != len(records):
        raise ValueError("retirement controller ancestry is unknown")
    for index, record in enumerate(records):
        if (record.pid <= 1 or not math.isfinite(record.started) or record.started <= 0
                or record.cwd != root or record.cwd == worktree
                or record.cwd.is_relative_to(worktree)
                or record.uid != (os.getuid(), os.geteuid()) or not record.argv
                or (index and records[index - 1].ppid != record.pid)):
            raise ValueError("retirement controller identity or cwd is not owned")
    python = records[0]
    if (python.executable.resolve(strict=True) != Path(sys.executable).resolve(strict=True)
            or python.argv[1:3] != ("-m", "scripts.prune_worktrees_safe")):
        raise ValueError("current process is not the native retirement invocation")
    try:
        request = _parser().parse_args(python.argv[3:])
    except SystemExit as exc:
        raise ValueError("retirement controller argv is not a supported request") from exc
    if (not (request.retire_merged or request.retire_self_improve_evaluation)
            or request.branch != branch or request.worktree != worktree):
        raise ValueError("retirement controller argv differs from selected owner")
    if len(records) == 3:
        uv = records[1]
        selected = shutil.which(os.environ.get("UV", "uv"))
        if (selected is None or uv.executable.resolve(strict=True) != Path(selected).resolve(strict=True)
                or uv.argv[1:] != ("run", "python", *python.argv[1:])):
            raise ValueError("retirement uv controller argv or executable differs")
    if TYPE_CHECKING:
        import start_gate_background as gate_launcher
    else:
        from scripts import start_gate_background as gate_launcher
    make = records[-1]
    selected_make = gate_launcher._native_make_executable()
    if make.executable.resolve(strict=True) != selected_make:
        raise ValueError(f"retirement Make executable is unknown: observed={make.executable} selected={selected_make}")
    arguments = list(make.argv[1:])
    source = Path(__file__).resolve().parents[1]
    makefile = root / "Makefile"
    assignments: dict[str, str] = {}
    target_count = 0
    target = ""
    explicit_makefile = False
    while arguments:
        argument = arguments.pop(0)
        if argument in {"--no-print-directory", "--silent", "-s", "-e", "--environment-overrides"}:
            continue
        if argument == "-f" and arguments:
            if explicit_makefile:
                raise ValueError("retirement Make argv names more than one source")
            explicit_makefile = True
            makefile = Path(arguments.pop(0))
            continue
        if argument in {"wt-prune-safe", "agent-cleanup"}:
            target_count += 1
            target = argument
            continue
        key, separator, value = argument.partition("=")
        if (not separator or not key or not key.replace("_", "").isalnum()
                or key[0].isdigit() or key in assignments or key == "MAKEFILES"):
            raise ValueError("retirement Make argv is not an exact single-target invocation")
        assignments[key] = value
    if target == "agent-cleanup":
        fragment = source / "make/40-cross-version-and-worktrees.mk"
        policy_key, policy_value = "AGENT_CLEANUP_VALIDATE_ONLY", request.retire_validate_only
    else:
        fragment = source / "make/30-ci-and-release.mk"
        policy_key, policy_value = "WT_PRUNE_RETIRE_MERGED", "1"
    if target == "agent-cleanup":
        purpose = "self-improve-evaluation" if request.retire_self_improve_evaluation else "merged"
        if (assignments.get("AGENT_CLEANUP_PURPOSE", "merged") != purpose
                or assignments.get("AGENT_CLEANUP_EXPECTED_HEAD", "") != request.expected_head):
            raise ValueError("retirement Make purpose or immutable HEAD does not bind selected owner")
    elif request.retire_self_improve_evaluation or request.expected_head:
        raise ValueError("selected evaluation or pinned retirement requires agent-cleanup")
    if (target_count != 1 or makefile.resolve(strict=True) not in {
            source / "Makefile", fragment,
        } or assignments.get(policy_key) != policy_value
            or assignments.get("BRANCH") != branch or assignments.get("WORKTREE") != str(worktree)):
        raise ValueError("retirement Make argv or source does not bind selected owner")
    return records


def _retirement_controllers(
    *, root: Path, worktree: Path, branch: str,
) -> tuple[_RetirementController, ...]:
    """Capture this exact controller chain freshly, without scanning other trees."""
    current = _retirement_controller(os.getpid())
    parent = _retirement_controller(current.ppid)
    records: tuple[_RetirementController, ...] = (current, parent)
    if parent.argv[1:3] == ("run", "python"):
        records += (_retirement_controller(parent.ppid),)
    return _validate_retirement_controllers(records, root=root, worktree=worktree, branch=branch)


@dataclass(frozen=True)
class _RetirementIdentity:
    """One exact, independently refreshed retirement boundary."""

    record: WorktreeRecord
    root_identity: tuple[int, int]
    backlink_identity: tuple[int, int, int, int]
    head: str
    integration_revisions: tuple[str, ...]
    controllers: tuple[_RetirementController, ...]
    ownership_status: str = ""


def retire_merged(
    *, branch: str, worktree: Path, registry_path: Path, validate_only: bool,
    expected_head: str | None = None,
) -> int:
    """Archive and retire one explicitly selected, clean and integrated owner."""
    return _retire_worktree(
        branch=branch, worktree=worktree, registry_path=registry_path,
        validate_only=validate_only, require_merged=True, expected_head=expected_head,
    )


def retire_self_improve_evaluation(
    *, branch: str, worktree: Path, registry_path: Path, validate_only: bool, expected_head: str,
) -> int:
    """Retire only a pinned released self-improve evaluation materialization."""
    if not branch.startswith("self-improve-codex-") or branch == "self-improve-codex-":
        raise ValueError("self-improve-evaluation requires its actual self-improve-codex-* branch")
    return _retire_worktree(
        branch=branch, worktree=worktree, registry_path=registry_path,
        validate_only=validate_only, require_merged=False, expected_head=expected_head,
    )


def _retire_worktree(
    *, branch: str, worktree: Path, registry_path: Path, validate_only: bool, require_merged: bool,
    expected_head: str | None = None,
) -> int:
    """Share native identity, ownership and archival across both pruning routes."""
    if TYPE_CHECKING:
        import automatic_disk_cleanup as cleanup
        import clean_ci_shard_scratch as process_owner
    else:
        from scripts import automatic_disk_cleanup as cleanup
        from scripts import clean_ci_shard_scratch as process_owner

    if (not branch or branch != branch.strip() or not worktree.is_absolute()
            or ".." in worktree.parts
            or any(character in branch + str(worktree) for character in "\0\r\n")):
        raise ValueError("explicit literal BRANCH and canonical absolute WORKTREE are required")
    if expected_head is not None and re.fullmatch(r"[0-9a-f]{40}", expected_head) is None:
        raise ValueError("a full immutable expected HEAD is required")
    evaluation = not require_merged and expected_head is not None
    root = owned_git_checkpoint.repository_root()
    _git("check-ref-format", f"refs/heads/{branch}", timeout=30)
    registry = WorkstreamRegistry(registry_path)

    def bounded_git(
        *args: str, check: bool = True, timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return _git(*args, check=check, timeout=30 if timeout is None else timeout)

    def observe() -> _RetirementIdentity:
        record, identity = _retention_identity(branch, worktree)
        records = cleanup._registered_worktrees()
        main = records[0].path.resolve(strict=True)
        if worktree in {root, main}:
            raise ValueError("current/main checkout cannot be retired")
        if not cleanup._inside_approved_root(
            worktree, (main / ".claude/worktrees", cleanup.DEFAULT_TMP_WORKTREE_ROOT),
        ):
            raise ValueError("WORKTREE is outside the approved namespace")
        ownership_status = (
            registry.ownership_status(branch, worktree, expected_head) if expected_head is not None else ""
        )
        leases = cleanup._active_workstream_leases(registry)
        matching_active = (
            ownership_status == "active" and branch in leases and leases[branch].worktree == worktree
        )
        if (any(key != branch and lease.worktree == worktree for key, lease in leases.items())
                or (branch in leases and not matching_active)):
            raise ValueError("active logical workstream cannot be retired")
        if record.locked:
            raise ValueError("git worktree lock protects this owner")
        if worktree in owned_git_checkpoint.retained_checkpoint_owners(root):
            raise ValueError("retained checkpoint protects this worktree owner")
        pids = set(process_owner._active_process_pids(worktree))
        controllers = _retirement_controllers(root=root, worktree=worktree, branch=branch) if pids else ()
        protected = pids - {controller.pid for controller in controllers[1:]}
        if protected:
            raise ValueError(f"active processes protect this worktree owner: {sorted(protected)}")
        status = bounded_git(
            "-C", str(worktree), "status", "--porcelain=v1", "-z", "--untracked-files=all",
        )
        if status.stdout:
            raise ValueError("dirty worktree cannot be retired")
        head = bounded_git("-C", str(worktree), "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
        if expected_head is not None and head != expected_head:
            raise ValueError("native worktree HEAD differs from the immutable expected owner")
        points = [] if evaluation else cleanup._integration_points(records)
        integrated = False
        for point in points:
            result = bounded_git(
                "--no-replace-objects", "merge-base", "--is-ancestor", head, point.revision,
                check=False,
            )
            if result.returncode not in {0, 1}:
                raise ValueError("native integration ancestry inspection failed")
            integrated |= result.returncode == 0
        if require_merged and not integrated:
            raise ValueError("unmerged worktree cannot be retired")
        return _RetirementIdentity(
            record, (identity[0][0], identity[0][1]), identity[1], head,
            tuple(point.revision for point in points), controllers, ownership_status,
        )

    initial = observe()
    # Keep native dry-run diagnostics visible without changing terminal stdout.
    with redirect_stdout(sys.stderr):
        preflight = cleanup._remove_worktree_materialization(
            initial.record, True, run_git=bounded_git,
        )
    if not preflight.reclaimable:
        raise ValueError(preflight.reason)
    if initial.ownership_status == "active":
        purpose = "self-improve-evaluation" if evaluation else "merged"
        raise ValueError(
            f"{purpose} owner is active; exact release required: "
            f"branch={branch!r} worktree={str(worktree)!r} head={initial.head}"
        )
    receipt: dict[str, str | bool | int] = {
        "branch": branch, "worktree": str(worktree), "head": initial.head,
        "validate_only": validate_only, "native_ownership_verified": 1,
        "status": "planned" if validate_only else "removed",
    }
    if validate_only:
        print(json.dumps(receipt, sort_keys=True))
        return 0
    if observe() != initial:
        raise ValueError("retirement identity or integration input changed")
    refusal = ""

    def retiring_git(
        *args: str, check: bool = True, timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal refusal
        if args[:2] == ("worktree", "remove"):
            registry.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with registry.lock_path.open("a+", encoding="utf-8") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    if observe() != initial:
                        raise ValueError("retirement identity or integration input changed")
                    with redirect_stdout(sys.stderr):
                        final = cleanup._remove_worktree_materialization(
                            initial.record, True, run_git=bounded_git,
                        )
                    if not final.reclaimable:
                        raise ValueError(final.reason)
                    evidence = bounded_git(
                        "--literal-pathspecs", "-C", str(worktree), "ls-files", "--others",
                        "--ignored", "--exclude-standard", "-z", "--",
                        *sorted(cleanup.ARCHIVABLE_PRESERVED_DATA_NAMES),
                    )
                    if evidence.stdout:
                        raise ValueError("new ignored proof appeared during archival")
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                    refusal = str(exc)
                    return subprocess.CompletedProcess(args, 1, "", refusal)
                return bounded_git(*args, check=check, timeout=timeout)
        return bounded_git(*args, check=check, timeout=timeout)

    outcome = cleanup._remove_worktree_materialization(
        initial.record, False, evidence_archive_root=cleanup._canonical_evidence_archive_root(),
        run_git=retiring_git,
    )
    if not outcome.reclaimable:
        raise ValueError(f"{outcome.reason}: {refusal}" if refusal else outcome.reason)
    print(json.dumps(receipt, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--retain", action="store_true")
    modes.add_argument("--retire-merged", action="store_true")
    modes.add_argument("--retire-self-improve-evaluation", action="store_true")
    parser.add_argument("--expected-head", default="")
    parser.add_argument("--retire-validate-only", choices=("0", "1"), default="1")
    parser.add_argument("--branch", default="")
    parser.add_argument("--worktree", type=Path)
    parser.add_argument("--reason", default="")
    parser.add_argument("--retain-validate-only", choices=("0", "1"), default="1")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the fail-closed worktree pruning command."""
    args = _parser().parse_args(argv)
    if args.retire_merged or args.retire_self_improve_evaluation:
        try:
            if args.worktree is None:
                raise ValueError("WORKTREE is required")
            if args.retire_self_improve_evaluation:
                return retire_self_improve_evaluation(
                    branch=args.branch, worktree=args.worktree,
                    registry_path=args.registry or default_registry_path(),
                    validate_only=args.retire_validate_only == "1", expected_head=args.expected_head,
                )
            return retire_merged(
                branch=args.branch, worktree=args.worktree,
                registry_path=args.registry or default_registry_path(),
                validate_only=args.retire_validate_only == "1", expected_head=args.expected_head or None,
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            print(f"wt-prune-safe: retirement refused: {exc}")
            return 2
    if args.expected_head:
        print("wt-prune-safe: expected HEAD requires a selected retirement mode")
        return 2
    if args.retain:
        try:
            if args.worktree is None:
                raise ValueError("WORKTREE is required")
            return retain(
                branch=args.branch, worktree=args.worktree, reason=args.reason,
                validate_only=args.retain_validate_only == "1",
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            print(f"agent-worktree-retain: refused: {exc}")
            return 2
    return prune(
        registry_path=args.registry or default_registry_path(),
        validate_only=args.validate_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
