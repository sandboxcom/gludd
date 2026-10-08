#!/usr/bin/env python3
"""Emit a bounded, read-only branch-reconciliation inventory."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from general_ludd.self_update.signing import verify_signature as _verify_signature

if TYPE_CHECKING:
    import branch_reconciliation_plan_types as _plan_types
else:
    if not __package__:
        import importlib

        _plan_types = importlib.import_module("branch_reconciliation_plan_types")
    else:
        from scripts import branch_reconciliation_plan_types as _plan_types

APPROVAL_KEYRING_JSON_CHAR_LIMIT = _plan_types.APPROVAL_KEYRING_JSON_CHAR_LIMIT
REMOTE_GIT_OUTPUT_CHAR_LIMIT = _plan_types.REMOTE_GIT_OUTPUT_CHAR_LIMIT
REMOTE_JSON_CHAR_LIMIT = _plan_types.REMOTE_JSON_CHAR_LIMIT
REMOTE_REF_SCAN_LIMIT = _plan_types.REMOTE_REF_SCAN_LIMIT
if TYPE_CHECKING:
    from branch_reconciliation_plan_types import (
        BranchRecord,
        CollapsedMergeQueueGroup,
        ConflictPreflight,
        CurrentSummaryPayload,
        HeadSemanticSummary,
        InventoryCounts,
        InventoryPayload,
        MergePlanCollision,
        MergePlanCounts,
        MergePlanGroup,
        MergePlanHead,
        MergePlanPayload,
        MergeQueueCounts,
        MergeQueueEntry,
        MergeRehearsalPlan,
        ReceiptReplayPayload,
        ReconciliationReceipt,
        ReconciliationReceiptBody,
        ReconciliationReceiptTarget,
        ReconciliationSnapshotPayload,
        RemoteTrackingPayload,
        SemanticCurrentSummaryPayload,
        SemanticSummaryPayload,
        SummaryCounts,
        SummaryCountsPayload,
        SummaryPayload,
        TargetRecord,
    )
    from branch_reconciliation_plan_types import InventoryBounds as InventoryBounds
    from branch_reconciliation_plan_types import MergeQueueBounds as MergeQueueBounds
    from branch_reconciliation_plan_types import MergeQueuePayload as MergeQueuePayload
    from branch_reconciliation_plan_types import (
        ReceiptReplayBounds as ReceiptReplayBounds,
    )
    from branch_reconciliation_plan_types import SummaryGroup as SummaryGroup
else:
    for _exported_type in (
        "BranchRecord", "CollapsedMergeQueueGroup", "ConflictPreflight",
        "CurrentSummaryPayload", "HeadSemanticSummary", "InventoryBounds",
        "InventoryCounts", "InventoryPayload", "MergePlanCollision",
        "MergePlanCounts", "MergePlanGroup", "MergePlanHead", "MergePlanPayload",
        "MergeQueueBounds", "MergeQueueCounts", "MergeQueueEntry",
        "MergeQueuePayload", "MergeRehearsalPlan", "ReconciliationReceipt",
        "ReconciliationReceiptBody", "ReconciliationReceiptTarget",
        "ReconciliationSnapshotPayload", "ReceiptReplayBounds",
        "ReceiptReplayPayload", "RemoteTrackingPayload",
        "SemanticCurrentSummaryPayload", "SemanticSummaryPayload",
        "SummaryCounts", "SummaryCountsPayload", "SummaryGroup",
        "SummaryPayload", "TargetRecord",
    ):
        globals()[_exported_type] = getattr(_plan_types, _exported_type)
    del _exported_type
block_rehearsal_prediction = _plan_types.block_rehearsal_prediction
build_merge_rehearsal = _plan_types.build_merge_rehearsal
build_plan_snapshot_basis = _plan_types.build_plan_snapshot_basis
plan_collision = _plan_types.plan_collision
_build_reconciliation_snapshot = _plan_types.build_reconciliation_snapshot
_build_remote_tracking_inventory = _plan_types.build_remote_tracking_inventory
_canonical_document_digest = _plan_types.canonical_document_digest
_is_shared_infrastructure_path = _plan_types.is_shared_infrastructure_path
_merge_plan_diff_argv = _plan_types.merge_plan_diff_argv

SCHEMA_VERSION = 2
MAX_LIMIT = 100
COMMIT_SCAN_LIMIT = 500
LOCAL_REF_SCAN_LIMIT = 10_000
MERGE_QUEUE_HEAD_LIMIT = 256
CONFLICT_PATH_SCAN_LIMIT = 10_000
CONFLICT_PATH_LIMIT = 100
CONFLICT_PATH_CHAR_LIMIT = 240
MERGE_TREE_OUTPUT_CHAR_LIMIT = 262_144
RECEIPT_VERSION = 2
RECEIPT_JSON_CHAR_LIMIT = 16_777_216
GIT_TIMEOUT_SECONDS = 10
# Semantic inspection reuses the authoritative, already-bounded ref snapshot.
SEMANTIC_HEAD_LIMIT = LOCAL_REF_SCAN_LIMIT
SEMANTIC_PATH_LIMIT = 100
SEMANTIC_SUBJECT_CHAR_LIMIT = 200
SEMANTIC_PATH_CHAR_LIMIT = 240
SEMANTIC_GIT_OUTPUT_CHAR_LIMIT = 262_144
MERGE_PLAN_PATH_SCAN_LIMIT = 10_000
MERGE_PLAN_PATH_LIMIT = 100
MERGE_PLAN_COLLISION_LIMIT = 1_000
MERGE_PLAN_COLLISION_PATH_LIMIT = 20
MERGE_PLAN_GIT_OUTPUT_CHAR_LIMIT = 262_144
MERGE_PLAN_JSON_CHAR_LIMIT = 1_048_576
MERGE_REHEARSAL_GROUP_LIMIT = MERGE_QUEUE_HEAD_LIMIT
MERGE_REHEARSAL_BRANCH_LIMIT = LOCAL_REF_SCAN_LIMIT
MERGE_REHEARSAL_PATH_LIMIT = MERGE_PLAN_PATH_SCAN_LIMIT
MERGE_REHEARSAL_PATH_DISPLAY_LIMIT = MERGE_PLAN_PATH_LIMIT
MERGE_REHEARSAL_PREDICTION_CHECK_LIMIT = 64
MERGE_REHEARSAL_PREDICTION_OUTPUT_CHAR_LIMIT = MERGE_TREE_OUTPUT_CHAR_LIMIT
MERGE_REHEARSAL_PREDICTION_PATH_SCAN_LIMIT = CONFLICT_PATH_SCAN_LIMIT
MERGE_REHEARSAL_PREDICTION_PATH_LIMIT = CONFLICT_PATH_LIMIT
_OBJECT_ID_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_RECEIPT_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

RunFn = Callable[
    [Sequence[str], str | None],
    subprocess.CompletedProcess[str],
]
ProgressFn = Callable[[str], None]


class InventoryError(RuntimeError):
    """Raised when Git evidence cannot prove a safe classification."""

    pass


def _run(
    argv: Sequence[str], cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(argv),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(list(argv), 124, "", str(exc))


def _checked_stdout(
    argv: Sequence[str],
    *,
    run: RunFn,
    cwd: str | None,
    label: str,
) -> str:
    result = run(argv, cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        raise InventoryError(f"{label}: {detail[:400]}")
    return result.stdout


def _valid_object_id(value: str) -> bool:
    return bool(_OBJECT_ID_RE.fullmatch(value))


def _resolve_target(
    target: str,
    *,
    run: RunFn,
    cwd: str | None,
) -> TargetRecord:
    if (
        not target
        or target != target.strip()
        or target.startswith("-")
        or any(character.isspace() for character in target)
    ):
        raise InventoryError(f"invalid target ref: {target}")

    symbolic = run(
        [
            "git",
            "rev-parse",
            "--symbolic-full-name",
            "--verify",
            "--quiet",
            "--end-of-options",
            target,
        ],
        cwd,
    )
    ref_lines = symbolic.stdout.strip().splitlines()
    if (
        symbolic.returncode != 0
        or len(ref_lines) != 1
        or not ref_lines[0].startswith("refs/")
    ):
        raise InventoryError(f"invalid target ref: {target}")
    target_ref = ref_lines[0]

    head = run(
        [
            "git",
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{target_ref}^{{commit}}",
        ],
        cwd,
    )
    target_head = head.stdout.strip()
    if head.returncode != 0 or not _valid_object_id(target_head):
        raise InventoryError(f"invalid target ref: {target}")
    return {"head": target_head, "input": target, "ref": target_ref}


def _validate_after(
    after: str,
    *,
    run: RunFn,
    cwd: str | None,
) -> str | None:
    if after == "":
        return None
    branch_name = after.removeprefix("refs/heads/")
    if (
        after != after.strip()
        or any(character.isspace() for character in after)
        or not after.startswith("refs/heads/")
        or not branch_name
        or branch_name.startswith("-")
    ):
        raise InventoryError(f"invalid pagination cursor: {after}")
    result = run(["git", "check-ref-format", after], cwd)
    if result.returncode != 0:
        raise InventoryError(f"invalid pagination cursor: {after}")
    return after


def _parse_branch_entries(
    output: str,
    *,
    allow_namespace_boundary: bool,
) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    previous_ref: str | None = None
    outside_heads = False
    for line in output.splitlines():
        fields = line.split("\t")
        if (
            len(fields) != 2
            or not fields[0].startswith("refs/")
            or not _valid_object_id(fields[1])
            or (previous_ref is not None and fields[0] <= previous_ref)
        ):
            raise InventoryError("malformed local branch inventory")
        previous_ref = fields[0]
        if not fields[0].startswith("refs/heads/"):
            if not allow_namespace_boundary:
                raise InventoryError("malformed local branch inventory")
            outside_heads = True
            continue
        if outside_heads or not fields[0].removeprefix("refs/heads/"):
            raise InventoryError("malformed local branch inventory")
        entries.append((fields[0], fields[1]))
    return entries


def _bounded_sorted_local_scan(
    after: str,
    *,
    run: RunFn,
    cwd: str | None,
) -> list[tuple[str, str]]:
    output = _checked_stdout(
        [
            "git",
            "for-each-ref",
            f"--count={LOCAL_REF_SCAN_LIMIT + 1}",
            "--sort=refname",
            "--format=%(refname)%09%(objectname)",
            "refs/heads",
        ],
        run=run,
        cwd=cwd,
        label="bounded legacy branch enumeration failed",
    )
    scanned_entries = _parse_branch_entries(
        output,
        allow_namespace_boundary=False,
    )
    if len(scanned_entries) > LOCAL_REF_SCAN_LIMIT:
        raise InventoryError("local branch scan exceeded pagination bound")
    return [entry for entry in scanned_entries if entry[0] > after]


def _bounded_branches(
    target_ref: str,
    limit: int,
    after: str | None,
    *,
    run: RunFn,
    cwd: str | None,
) -> tuple[list[tuple[str, str]], bool]:
    command = [
        "git",
        "for-each-ref",
        f"--count={limit + 2}",
        "--format=%(refname)%09%(objectname)",
    ]
    if after is None:
        command.append("refs/heads")
    else:
        command.append(f"--start-after={after}")
    result = run(command, cwd)
    unsupported_start_after = (
        after is not None
        and result.returncode != 0
        and "unknown option" in result.stderr
        and "start-after" in result.stderr
    )
    if unsupported_start_after:
        assert after is not None
        entries = _bounded_sorted_local_scan(
            after,
            run=run,
            cwd=cwd,
        )
    else:
        if result.returncode != 0:
            detail = (
                result.stderr or result.stdout or "git command failed"
            ).strip()
            raise InventoryError(f"local branch enumeration failed: {detail[:400]}")
        entries = _parse_branch_entries(
            result.stdout,
            allow_namespace_boundary=after is not None,
        )
    if after is not None:
        if any(ref < after for ref, _head in entries):
            entries = _bounded_sorted_local_scan(
                after,
                run=run,
                cwd=cwd,
            )
        # Some supported Git releases/backends return the boundary ref itself
        # even for --start-after. It was classified on the preceding page, so
        # drop only that exact duplicate while retaining every unseen ref.
        entries = [entry for entry in entries if entry[0] != after]
    candidates = [
        entry for entry in entries if entry[0] != target_ref
    ]
    return candidates[:limit], len(candidates) > limit


def _ancestor(
    head: str,
    target_head: str,
    *,
    run: RunFn,
    cwd: str | None,
) -> bool:
    result = run(
        ["git", "merge-base", "--is-ancestor", head, target_head],
        cwd,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = (result.stderr or result.stdout or "git command failed").strip()
    raise InventoryError(f"ancestor classification failed: {detail[:400]}")


def _commit_count(
    target_head: str,
    head: str,
    *,
    run: RunFn,
    cwd: str | None,
) -> int:
    output = _checked_stdout(
        [
            "git",
            "rev-list",
            "--count",
            f"--max-count={COMMIT_SCAN_LIMIT + 1}",
            f"{target_head}..{head}",
        ],
        run=run,
        cwd=cwd,
        label="commit bound check failed",
    ).strip()
    try:
        count = int(output)
    except ValueError as exc:
        raise InventoryError("malformed commit count") from exc
    if count < 0:
        raise InventoryError("malformed commit count")
    return count


def _cherry_counts(
    target_head: str,
    head: str,
    *,
    run: RunFn,
    cwd: str | None,
) -> tuple[int, int]:
    output = _checked_stdout(
        ["git", "cherry", target_head, head],
        run=run,
        cwd=cwd,
        label="patch classification failed",
    )
    equivalent = 0
    unique = 0
    rows = [line for line in output.splitlines() if line]
    if len(rows) > COMMIT_SCAN_LIMIT:
        raise InventoryError("patch classification exceeded commit bound")
    for row in rows:
        fields = row.split()
        if (
            len(fields) != 2
            or fields[0] not in {"+", "-"}
            or not _valid_object_id(fields[1])
        ):
            raise InventoryError("malformed patch classification")
        if fields[0] == "-":
            equivalent += 1
        else:
            unique += 1
    return equivalent, unique


def _classify_branch(
    ref: str,
    head: str,
    target_head: str,
    *,
    run: RunFn,
    cwd: str | None,
) -> BranchRecord:
    name = ref.removeprefix("refs/heads/")
    if _ancestor(head, target_head, run=run, cwd=cwd):
        return {
            "classification": "ancestor",
            "head": head,
            "lifecycle": "historical",
            "name": name,
            "patch_equivalent_commits": 0,
            "ref": ref,
            "unique_commits": 0,
        }
    commit_count = _commit_count(target_head, head, run=run, cwd=cwd)
    if commit_count == 0 or commit_count > COMMIT_SCAN_LIMIT:
        return {
            "classification": "unique",
            "head": head,
            "lifecycle": "current",
            "name": name,
            "patch_equivalent_commits": 0,
            "ref": ref,
            "unique_commits": 0,
        }
    equivalent, unique = _cherry_counts(
        target_head,
        head,
        run=run,
        cwd=cwd,
    )
    patch_equivalent = equivalent > 0 and unique == 0
    return {
        "classification": "patch-equivalent" if patch_equivalent else "unique",
        "head": head,
        "lifecycle": "historical" if patch_equivalent else "current",
        "name": name,
        "patch_equivalent_commits": equivalent,
        "ref": ref,
        "unique_commits": unique,
    }


def _counts(branches: Sequence[BranchRecord]) -> InventoryCounts:
    return {
        "ancestor": sum(branch["classification"] == "ancestor" for branch in branches),
        "current": sum(branch["lifecycle"] == "current" for branch in branches),
        "historical": sum(
            branch["lifecycle"] == "historical" for branch in branches
        ),
        "patch_equivalent": sum(
            branch["classification"] == "patch-equivalent" for branch in branches
        ),
        "returned": len(branches),
        "unique": sum(branch["classification"] == "unique" for branch in branches),
    }


def _verify_page_snapshot(
    branches: Sequence[BranchRecord],
    target: TargetRecord,
    *,
    run: RunFn,
    cwd: str | None,
) -> None:
    """Fail when a target or classified branch moved during one page."""
    expected = {target["ref"]: target["head"]}
    for branch in branches:
        if branch["ref"] in expected:
            raise InventoryError("malformed page ref snapshot")
        expected[branch["ref"]] = branch["head"]

    result = run(
        ["git", "show-ref", "--verify", "--", *expected],
        cwd,
    )
    if result.returncode != 0:
        if result.returncode == 1:
            raise InventoryError("page refs changed during classification")
        detail = (result.stderr or result.stdout or "git command failed").strip()
        raise InventoryError(f"page ref verification failed: {detail[:400]}")

    observed: dict[str, str] = {}
    for row in result.stdout.splitlines():
        fields = row.split(" ", maxsplit=1)
        if (
            len(fields) != 2
            or not _valid_object_id(fields[0])
            or fields[1] not in expected
            or fields[1] in observed
        ):
            raise InventoryError("malformed page ref snapshot")
        observed[fields[1]] = fields[0]
    if observed != expected:
        raise InventoryError("page refs changed during classification")


def _verify_terminal_snapshot(
    branches: Sequence[BranchRecord],
    target: TargetRecord,
    *,
    run: RunFn,
    cwd: str | None,
) -> None:
    terminal_target = _resolve_target(target["input"], run=run, cwd=cwd)
    if terminal_target != target:
        raise InventoryError("target changed during exhaustive inventory")
    terminal_entries = _bounded_sorted_local_scan("", run=run, cwd=cwd)
    terminal_branches = [
        entry for entry in terminal_entries if entry[0] != target["ref"]
    ]
    observed_branches = [(branch["ref"], branch["head"]) for branch in branches]
    if terminal_branches != observed_branches:
        raise InventoryError("local branch refs changed during exhaustive inventory")


def collect_inventory(
    target: str,
    limit: int,
    *,
    after: str = "",
    run: RunFn = _run,
    cwd: str | None = None,
    progress: ProgressFn | None = None,
) -> InventoryPayload:
    """Classify one bounded page and verify its exact ref snapshot."""
    if limit < 1 or limit > MAX_LIMIT:
        raise InventoryError(f"limit must be between 1 and {MAX_LIMIT}")
    after_ref = _validate_after(after, run=run, cwd=cwd)
    target_record = _resolve_target(target, run=run, cwd=cwd)
    entries, truncated = _bounded_branches(
        target_record["ref"],
        limit,
        after_ref,
        run=run,
        cwd=cwd,
    )
    branches: list[BranchRecord] = []
    for index, (ref, head) in enumerate(entries, start=1):
        if progress is not None:
            progress(f"classify={index}/{len(entries)} ref={ref}")
        branches.append(
            _classify_branch(
                ref,
                head,
                target_record["head"],
                run=run,
                cwd=cwd,
            )
        )
    if progress is not None:
        progress("verify=page-ref-snapshot")
    _verify_page_snapshot(
        branches,
        target_record,
        run=run,
        cwd=cwd,
    )
    return {
        "after": after_ref,
        "bounds": {
            "branch_limit": limit,
            "commit_scan_limit": COMMIT_SCAN_LIMIT,
            "local_ref_scan_limit": LOCAL_REF_SCAN_LIMIT,
        },
        "branches": branches,
        "counts": _counts(branches),
        "limit": limit,
        "next_cursor": branches[-1]["ref"] if truncated else None,
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "target": target_record,
        "truncated": truncated,
    }


def collect_summary(
    target: str,
    page_size: int,
    *,
    run: RunFn = _run,
    cwd: str | None = None,
    progress: ProgressFn | None = None,
) -> SummaryPayload:
    """Collect every bounded page and verify one terminal ref snapshot."""
    if page_size < 1 or page_size > MAX_LIMIT:
        raise InventoryError(f"limit must be between 1 and {MAX_LIMIT}")
    after = ""
    branches: list[BranchRecord] = []
    seen_refs: set[str] = set()
    pages = 0
    target_record: TargetRecord | None = None
    while True:
        if progress is not None:
            progress(f"page={pages + 1} after={after or '<start>'}")
        page = collect_inventory(
            target,
            page_size,
            after=after,
            run=run,
            cwd=cwd,
            progress=progress,
        )
        pages += 1
        if target_record is None:
            target_record = page["target"]
        elif page["target"] != target_record:
            raise InventoryError("target changed during exhaustive inventory")
        for branch in page["branches"]:
            if branch["ref"] in seen_refs:
                raise InventoryError("duplicate ref across inventory pages")
            seen_refs.add(branch["ref"])
            branches.append(branch)
        if len(branches) > LOCAL_REF_SCAN_LIMIT:
            raise InventoryError("local branch scan exceeded exhaustive bound")
        if not page["truncated"]:
            break
        next_cursor = page["next_cursor"]
        if next_cursor is None or (after and next_cursor <= after):
            raise InventoryError("pagination cursor did not advance")
        after = next_cursor
    assert target_record is not None
    if progress is not None:
        progress("verify=terminal-ref-snapshot")
    _verify_terminal_snapshot(
        branches,
        target_record,
        run=run,
        cwd=cwd,
    )
    grouped: dict[tuple[str, str], SummaryGroup] = {}
    for branch in branches:
        key = (branch["classification"], branch["head"])
        group = grouped.get(key)
        if group is None:
            group = {
                "branch_count": 0,
                "classification": branch["classification"],
                "head": branch["head"],
                "lifecycle": branch["lifecycle"],
                "names": [],
                "patch_equivalent_commits": branch["patch_equivalent_commits"],
                "refs": [],
                "unique_commits": branch["unique_commits"],
            }
            grouped[key] = group
        elif (
            group["lifecycle"] != branch["lifecycle"]
            or group["patch_equivalent_commits"]
            != branch["patch_equivalent_commits"]
            or group["unique_commits"] != branch["unique_commits"]
        ):
            raise InventoryError("conflicting classifications for shared branch head")
        group["branch_count"] += 1
        group["names"].append(branch["name"])
        group["refs"].append(branch["ref"])
    base_counts = _counts(branches)
    summary_counts: SummaryCounts = {
        **base_counts,
        "deduplicated_heads": len(grouped),
    }
    return {
        "bounds": {
            "branch_limit": page_size,
            "commit_scan_limit": COMMIT_SCAN_LIMIT,
            "local_ref_scan_limit": LOCAL_REF_SCAN_LIMIT,
        },
        "counts": summary_counts,
        "groups": list(grouped.values()),
        "mode": "exhaustive-summary",
        "ok": True,
        "page_size": page_size,
        "pages": pages,
        "schema_version": SCHEMA_VERSION,
        "target": target_record,
        "terminal": True,
        "truncated": False,
    }


def _merge_tree_conflict_preflight(
    target: str,
    source: str,
    *,
    run: RunFn,
    cwd: str | None,
) -> ConflictPreflight:
    """Return bounded conflict evidence from Git's native merge machinery."""
    result = run(
        [
            "git",
            "merge-tree",
            "--write-tree",
            "--name-only",
            "--no-messages",
            "-z",
            target,
            source,
        ],
        cwd,
    )
    if (
        len(result.stdout) > MERGE_TREE_OUTPUT_CHAR_LIMIT
        or len(result.stderr) > MERGE_TREE_OUTPUT_CHAR_LIMIT
    ):
        raise InventoryError("merge-tree conflict preflight output exceeded bound")
    if result.returncode not in {0, 1}:
        raise InventoryError("merge-tree conflict preflight unavailable")
    if result.stderr or not result.stdout.endswith("\0"):
        raise InventoryError("invalid merge-tree conflict preflight evidence")

    records = result.stdout.split("\0")
    result_tree = records[0]
    if not _valid_object_id(result_tree):
        raise InventoryError("invalid merge-tree conflict preflight evidence")
    if result.returncode == 0:
        if records != [result_tree, ""]:
            raise InventoryError("invalid merge-tree conflict preflight evidence")
        raw_paths: list[str] = []
        status: Literal["clean", "conflicted"] = "clean"
    else:
        raw_paths = records[1:-1]
        status = "conflicted"

    if len(raw_paths) > CONFLICT_PATH_SCAN_LIMIT:
        raise InventoryError("merge-tree conflict path bound exceeded")
    conflict_paths, path_redactions = _validated_bounded_paths(
        raw_paths,
        output_limit=CONFLICT_PATH_LIMIT,
        char_limit=CONFLICT_PATH_CHAR_LIMIT,
        error="invalid merge-tree conflict preflight evidence",
        require_unique=True,
    )
    return {
        "conflict_path_count": len(raw_paths),
        "conflict_paths": conflict_paths,
        "conflict_paths_truncated": len(raw_paths) > CONFLICT_PATH_LIMIT,
        "expected_source": source,
        "expected_target": target,
        "path_redactions": path_redactions,
        "result_tree": result_tree,
        "status": status,
    }


def _verify_merge_queue_preconditions(
    groups: Sequence[SummaryGroup],
    target: TargetRecord,
    *,
    run: RunFn,
    cwd: str | None,
) -> None:
    """Revalidate the exact target and queued tips immediately before emission."""
    terminal_target = _resolve_target(target["input"], run=run, cwd=cwd)
    if terminal_target != target:
        raise InventoryError("target changed before merge queue emission")

    terminal_entries = _bounded_sorted_local_scan("", run=run, cwd=cwd)
    expected_refs: dict[str, str] = {}
    for group in groups:
        for ref in group["refs"]:
            previous = expected_refs.setdefault(ref, group["head"])
            if previous != group["head"]:
                raise InventoryError("conflicting reconciled tip identities")
    terminal_sources = [
        entry for entry in terminal_entries if entry[0] != target["ref"]
    ]
    if terminal_sources != sorted(expected_refs.items()):
        raise InventoryError("reconciled refs changed before merge queue emission")


def _invalid_receipt() -> InventoryError:
    """Return the single content-free receipt validation error."""
    return InventoryError("invalid reconciliation receipt")


def _receipt_dict(value: object, keys: frozenset[str]) -> dict[str, object]:
    """Return an exact-key string dictionary or reject the receipt."""
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise _invalid_receipt()
    result = cast(dict[str, object], value)
    if set(result) != keys:
        raise _invalid_receipt()
    return result


def _receipt_integer(value: object, *, minimum: int, maximum: int) -> int:
    """Return one bounded receipt integer, excluding booleans."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise _invalid_receipt()
    if value < minimum or value > maximum:
        raise _invalid_receipt()
    return value


def _receipt_ref(value: object) -> str:
    """Validate one canonical local ref without passing receipt text to Git."""
    if not isinstance(value, str):
        raise _invalid_receipt()
    branch_name = value.removeprefix("refs/heads/")
    invalid_component = any(
        not component
        or component.startswith(".")
        or component.endswith(".")
        or component.endswith(".lock")
        for component in branch_name.split("/")
    )
    if (
        not branch_name
        or not value.startswith("refs/heads/")
        or len(value) > 1024
        or value != value.strip()
        or invalid_component
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
        or any(character in "~^:?*[" for character in value)
        or any(marker in value for marker in ("..", "@{", "\\"))
    ):
        raise _invalid_receipt()
    return value


def build_reconciliation_snapshot(
    request_value: object,
    *,
    approval_trust: object | None = None,
    verification_time: object | None = None,
    verify_signature: Callable[[str, str, str], bool] = _verify_signature,
) -> ReconciliationSnapshotPayload:
    """Validate and classify one digest-bound plan/fresh-inventory handoff."""
    try:
        return _build_reconciliation_snapshot(
            request_value,
            valid_ref=_receipt_ref,
            valid_object_id=_valid_object_id,
            approval_trust=approval_trust,
            verification_time=verification_time,
            verify_signature=verify_signature,
        )
    except (InventoryError, ValueError) as exc:
        raise InventoryError("invalid reconciliation snapshot") from exc


def collect_remote_tracking_inventory(
    remote: object,
    freshness_evidence: object,
    verification_time: object,
    *,
    run: RunFn = _run,
    cwd: str | None = None,
) -> RemoteTrackingPayload:
    """Compare local and local remote-tracking refs without network or mutation."""
    try:
        return _build_remote_tracking_inventory(
            remote,
            freshness_evidence,
            verification_time,
            run=run,
            cwd=cwd,
            valid_ref=_receipt_ref,
            valid_object_id=_valid_object_id,
            ref_limit=REMOTE_REF_SCAN_LIMIT,
            git_output_char_limit=REMOTE_GIT_OUTPUT_CHAR_LIMIT,
            json_char_limit=REMOTE_JSON_CHAR_LIMIT,
        )
    except (InventoryError, ValueError) as exc:
        raise InventoryError("invalid remote tracking inventory") from exc


def canonical_document_digest(value: object) -> str:
    """Expose the snapshot handoff digest without duplicating serialization."""
    return _canonical_document_digest(value)


def _read_approval_trust(path_value: str) -> object:
    """Read one explicit trust store with an independent hard character bound."""
    try:
        with Path(path_value).open(encoding="utf-8") as stream:
            encoded = stream.read(APPROVAL_KEYRING_JSON_CHAR_LIMIT + 1)
    except (OSError, UnicodeError) as exc:
        raise InventoryError("invalid reconciliation snapshot") from exc
    if not encoded or len(encoded) > APPROVAL_KEYRING_JSON_CHAR_LIMIT:
        raise InventoryError("invalid reconciliation snapshot")
    try:
        return json.loads(encoded)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise InventoryError("invalid reconciliation snapshot") from exc


def _receipt_refs(value: object) -> list[str]:
    """Validate a sorted, unique, bounded nonempty receipt ref list."""
    if (
        not isinstance(value, list)
        or not value
        or len(value) > LOCAL_REF_SCAN_LIMIT
    ):
        raise _invalid_receipt()
    refs = [_receipt_ref(item) for item in value]
    if refs != sorted(set(refs)):
        raise _invalid_receipt()
    return refs


def _canonical_receipt_body(body: object) -> str:
    """Serialize receipt state deterministically under a hard character bound."""
    try:
        canonical = json.dumps(
            body,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise _invalid_receipt() from exc
    if len(canonical) > RECEIPT_JSON_CHAR_LIMIT:
        raise _invalid_receipt()
    return canonical


def _seal_receipt(body: ReconciliationReceiptBody) -> ReconciliationReceipt:
    """Return a deterministic SHA-256 receipt for validated state."""
    canonical = _canonical_receipt_body(body)
    return {
        "algorithm": "sha256",
        "body": body,
        "digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _copy_conflict_preflight(preflight: ConflictPreflight) -> ConflictPreflight:
    """Copy one merge-tree result into independently sealed receipt state."""
    return {
        "conflict_path_count": preflight["conflict_path_count"],
        "conflict_paths": list(preflight["conflict_paths"]),
        "conflict_paths_truncated": preflight["conflict_paths_truncated"],
        "expected_source": preflight["expected_source"],
        "expected_target": preflight["expected_target"],
        "path_redactions": preflight["path_redactions"],
        "result_tree": preflight["result_tree"],
        "status": preflight["status"],
    }


def _copy_queue_entry(entry: MergeQueueEntry) -> MergeQueueEntry:
    """Copy one queue entry so receipt and display payloads cannot alias."""
    return {
        "branch_count": entry["branch_count"],
        "expected_tip": entry["expected_tip"],
        "order": entry["order"],
        "preflight": _copy_conflict_preflight(entry["preflight"]),
        "refs": list(entry["refs"]),
        "source_ref": entry["source_ref"],
        "unique_commits": entry["unique_commits"],
    }


def _copy_collapsed_group(
    group: CollapsedMergeQueueGroup,
) -> CollapsedMergeQueueGroup:
    """Copy one collapsed group into independently sealed state."""
    return {
        "branch_count": group["branch_count"],
        "classification": group["classification"],
        "expected_tip": group["expected_tip"],
        "refs": list(group["refs"]),
    }


def _new_receipt_body(
    queue: Sequence[MergeQueueEntry],
    collapsed: Sequence[CollapsedMergeQueueGroup],
    target: ReconciliationReceiptTarget,
    cursor: int,
) -> ReconciliationReceiptBody:
    """Copy canonical queue state into one independently sealed body."""
    return {
        "collapsed": [_copy_collapsed_group(group) for group in collapsed],
        "cursor": cursor,
        "queue": [_copy_queue_entry(entry) for entry in queue],
        "target": {
            "base_head": target["base_head"],
            "checkpoint_head": target["checkpoint_head"],
            "input": target["input"],
            "ref": target["ref"],
        },
        "version": RECEIPT_VERSION,
    }


def _parse_receipt_preflight(value: object, expected_source: str) -> ConflictPreflight:
    """Validate bounded merge-tree evidence from an untrusted receipt."""
    preflight = _receipt_dict(
        value,
        frozenset(
            {
                "conflict_path_count",
                "conflict_paths",
                "conflict_paths_truncated",
                "expected_source",
                "expected_target",
                "path_redactions",
                "result_tree",
                "status",
            }
        ),
    )
    expected_source_value = preflight["expected_source"]
    expected_target_value = preflight["expected_target"]
    result_tree_value = preflight["result_tree"]
    status_value = preflight["status"]
    truncated_value = preflight["conflict_paths_truncated"]
    if (
        expected_source_value != expected_source
        or not isinstance(expected_target_value, str)
        or not _valid_object_id(expected_target_value)
        or not isinstance(result_tree_value, str)
        or not _valid_object_id(result_tree_value)
        or not isinstance(status_value, str)
        or status_value not in {"clean", "conflicted"}
        or not isinstance(truncated_value, bool)
    ):
        raise _invalid_receipt()

    raw_paths = preflight["conflict_paths"]
    if not isinstance(raw_paths, list) or len(raw_paths) > CONFLICT_PATH_LIMIT:
        raise _invalid_receipt()
    paths: list[str] = []
    for raw_path in raw_paths:
        if (
            not isinstance(raw_path, str)
            or not raw_path
            or len(raw_path) > CONFLICT_PATH_CHAR_LIMIT
            or not raw_path.isprintable()
            or raw_path.startswith("/")
            or any(part in {"", ".", ".."} for part in raw_path.split("/"))
        ):
            raise _invalid_receipt()
        paths.append(raw_path)

    path_count = _receipt_integer(
        preflight["conflict_path_count"],
        minimum=0,
        maximum=CONFLICT_PATH_SCAN_LIMIT,
    )
    path_redactions = _receipt_integer(
        preflight["path_redactions"],
        minimum=0,
        maximum=len(paths),
    )
    if (
        len(paths) != min(path_count, CONFLICT_PATH_LIMIT)
        or truncated_value != (path_count > CONFLICT_PATH_LIMIT)
        or (
            status_value == "clean"
            and (path_count or paths or path_redactions or truncated_value)
        )
    ):
        raise _invalid_receipt()
    return {
        "conflict_path_count": path_count,
        "conflict_paths": paths,
        "conflict_paths_truncated": truncated_value,
        "expected_source": expected_source,
        "expected_target": expected_target_value,
        "path_redactions": path_redactions,
        "result_tree": result_tree_value,
        "status": cast(Literal["clean", "conflicted"], status_value),
    }


def _parse_receipt_queue(value: object) -> list[MergeQueueEntry]:
    """Validate ordered novel heads from an untrusted receipt body."""
    if not isinstance(value, list) or len(value) > MERGE_QUEUE_HEAD_LIMIT:
        raise _invalid_receipt()
    queue: list[MergeQueueEntry] = []
    keys = frozenset(
        {
            "branch_count",
            "expected_tip",
            "order",
            "preflight",
            "refs",
            "source_ref",
            "unique_commits",
        }
    )
    for position, raw_entry in enumerate(value, start=1):
        entry = _receipt_dict(raw_entry, keys)
        refs = _receipt_refs(entry["refs"])
        expected_tip = entry["expected_tip"]
        source_ref = entry["source_ref"]
        if (
            not isinstance(expected_tip, str)
            or not _valid_object_id(expected_tip)
            or not isinstance(source_ref, str)
            or source_ref != refs[0]
        ):
            raise _invalid_receipt()
        branch_count = _receipt_integer(
            entry["branch_count"], minimum=1, maximum=LOCAL_REF_SCAN_LIMIT
        )
        if branch_count != len(refs):
            raise _invalid_receipt()
        order = _receipt_integer(
            entry["order"], minimum=1, maximum=MERGE_QUEUE_HEAD_LIMIT
        )
        if order != position:
            raise _invalid_receipt()
        queue.append(
            {
                "branch_count": branch_count,
                "expected_tip": expected_tip,
                "order": order,
                "preflight": _parse_receipt_preflight(
                    entry["preflight"],
                    expected_tip,
                ),
                "refs": refs,
                "source_ref": source_ref,
                "unique_commits": _receipt_integer(
                    entry["unique_commits"],
                    minimum=0,
                    maximum=COMMIT_SCAN_LIMIT,
                ),
            }
        )
    keys_in_order = [
        (entry["source_ref"], entry["expected_tip"]) for entry in queue
    ]
    if keys_in_order != sorted(keys_in_order):
        raise _invalid_receipt()
    return queue


def _parse_receipt_collapsed(
    value: object,
) -> list[CollapsedMergeQueueGroup]:
    """Validate collapsed historical heads from an untrusted receipt body."""
    if not isinstance(value, list) or len(value) > LOCAL_REF_SCAN_LIMIT:
        raise _invalid_receipt()
    collapsed: list[CollapsedMergeQueueGroup] = []
    keys = frozenset(
        {"branch_count", "classification", "expected_tip", "refs"}
    )
    for raw_group in value:
        group = _receipt_dict(raw_group, keys)
        refs = _receipt_refs(group["refs"])
        classification_value = group["classification"]
        expected_tip = group["expected_tip"]
        if (
            classification_value not in {"ancestor", "patch-equivalent"}
            or not isinstance(expected_tip, str)
            or not _valid_object_id(expected_tip)
        ):
            raise _invalid_receipt()
        branch_count = _receipt_integer(
            group["branch_count"], minimum=1, maximum=LOCAL_REF_SCAN_LIMIT
        )
        if branch_count != len(refs):
            raise _invalid_receipt()
        collapsed.append(
            {
                "branch_count": branch_count,
                "classification": classification_value,
                "expected_tip": expected_tip,
                "refs": refs,
            }
        )
    keys_in_order = [
        (group["classification"], group["refs"][0], group["expected_tip"])
        for group in collapsed
    ]
    if keys_in_order != sorted(keys_in_order):
        raise _invalid_receipt()
    return collapsed


def _parse_receipt_body(value: object) -> ReconciliationReceiptBody:
    """Validate all state and accounting invariants in a receipt body."""
    body = _receipt_dict(
        value,
        frozenset({"collapsed", "cursor", "queue", "target", "version"}),
    )
    _receipt_integer(
        body["version"], minimum=RECEIPT_VERSION, maximum=RECEIPT_VERSION
    )
    queue = _parse_receipt_queue(body["queue"])
    collapsed = _parse_receipt_collapsed(body["collapsed"])
    cursor = _receipt_integer(body["cursor"], minimum=0, maximum=len(queue))

    target_data = _receipt_dict(
        body["target"],
        frozenset({"base_head", "checkpoint_head", "input", "ref"}),
    )
    target_input = target_data["input"]
    target_ref_value = target_data["ref"]
    base_head = target_data["base_head"]
    checkpoint_head = target_data["checkpoint_head"]
    if (
        not isinstance(target_input, str)
        or not target_input
        or target_input != target_input.strip()
        or target_input.startswith("-")
        or any(character.isspace() for character in target_input)
        or not isinstance(target_ref_value, str)
        or not isinstance(base_head, str)
        or not isinstance(checkpoint_head, str)
        or not _valid_object_id(base_head)
        or not _valid_object_id(checkpoint_head)
    ):
        raise _invalid_receipt()
    target_ref = _receipt_ref(target_ref_value)

    all_entries: list[tuple[str, str]] = []
    for entry in queue:
        all_entries.extend((ref, entry["expected_tip"]) for ref in entry["refs"])
    for group in collapsed:
        all_entries.extend((ref, group["expected_tip"]) for ref in group["refs"])
    all_refs = [ref for ref, _head in all_entries]
    all_heads = [entry["expected_tip"] for entry in queue] + [
        group["expected_tip"] for group in collapsed
    ]
    if (
        len(all_entries) > LOCAL_REF_SCAN_LIMIT
        or target_ref in all_refs
        or len(all_refs) != len(set(all_refs))
        or len(all_heads) != len(set(all_heads))
        or any(
            entry["preflight"]["expected_target"] != base_head for entry in queue
        )
    ):
        raise _invalid_receipt()

    target: ReconciliationReceiptTarget = {
        "base_head": base_head,
        "checkpoint_head": checkpoint_head,
        "input": target_input,
        "ref": target_ref,
    }
    return _new_receipt_body(queue, collapsed, target, cursor)


def _validate_receipt(value: object) -> ReconciliationReceipt:
    """Verify the canonical digest before accepting untrusted receipt state."""
    receipt = _receipt_dict(
        value,
        frozenset({"algorithm", "body", "digest"}),
    )
    algorithm = receipt["algorithm"]
    digest = receipt["digest"]
    if (
        algorithm != "sha256"
        or not isinstance(digest, str)
        or not _RECEIPT_DIGEST_RE.fullmatch(digest)
    ):
        raise _invalid_receipt()
    canonical = _canonical_receipt_body(receipt["body"])
    expected_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest, expected_digest):
        raise _invalid_receipt()
    body = _parse_receipt_body(receipt["body"])
    return {"algorithm": "sha256", "body": body, "digest": digest}


def _build_merge_queue(
    summary: SummaryPayload,
    *,
    run: RunFn,
    cwd: str | None,
    progress: ProgressFn | None,
) -> MergeQueuePayload:
    """Build one deterministic, fully accounted sequential merge queue."""
    novel_groups = sorted(
        (
            group
            for group in summary["groups"]
            if group["classification"] == "unique"
        ),
        key=lambda group: (group["refs"][0], group["head"]),
    )
    if len(novel_groups) > MERGE_QUEUE_HEAD_LIMIT:
        raise InventoryError(
            "merge queue head bound exceeded: "
            f"{len(novel_groups)} > {MERGE_QUEUE_HEAD_LIMIT}"
        )

    queue: list[MergeQueueEntry] = []
    for order, group in enumerate(novel_groups, start=1):
        refs = sorted(group["refs"])
        if progress is not None:
            progress(f"preflight={order}/{len(novel_groups)} head={group['head']}")
        preflight = _merge_tree_conflict_preflight(
            summary["target"]["head"],
            group["head"],
            run=run,
            cwd=cwd,
        )
        queue.append(
            {
                "branch_count": group["branch_count"],
                "expected_tip": group["head"],
                "order": order,
                "preflight": preflight,
                "refs": refs,
                "source_ref": refs[0],
                "unique_commits": group["unique_commits"],
            }
        )

    historical_groups = sorted(
        (
            group
            for group in summary["groups"]
            if group["classification"] != "unique"
        ),
        key=lambda group: (group["classification"], group["refs"][0], group["head"]),
    )
    collapsed: list[CollapsedMergeQueueGroup] = []
    for group in historical_groups:
        classification = group["classification"]
        if classification == "unique":
            raise InventoryError("unique head leaked into collapsed merge queue")
        collapsed.append(
            {
                "branch_count": group["branch_count"],
                "classification": classification,
                "expected_tip": group["head"],
                "refs": sorted(group["refs"]),
            }
        )

    ancestor_groups = [
        group for group in collapsed if group["classification"] == "ancestor"
    ]
    patch_groups = [
        group
        for group in collapsed
        if group["classification"] == "patch-equivalent"
    ]
    counts: MergeQueueCounts = {
        "ancestor_branches": sum(group["branch_count"] for group in ancestor_groups),
        "ancestor_heads": len(ancestor_groups),
        "collapsed_branches": sum(group["branch_count"] for group in collapsed),
        "collapsed_heads": len(collapsed),
        "observed_branches": summary["counts"]["returned"],
        "observed_heads": summary["counts"]["deduplicated_heads"],
        "patch_equivalent_branches": sum(
            group["branch_count"] for group in patch_groups
        ),
        "patch_equivalent_heads": len(patch_groups),
        "queued_branches": sum(entry["branch_count"] for entry in queue),
        "queued_heads": len(queue),
    }
    if (
        counts["queued_branches"] + counts["collapsed_branches"]
        != counts["observed_branches"]
        or counts["queued_heads"] + counts["collapsed_heads"]
        != counts["observed_heads"]
    ):
        raise InventoryError("merge queue accounting did not cover every branch")

    if progress is not None:
        progress("verify=merge-queue-preconditions")
    _verify_merge_queue_preconditions(
        summary["groups"],
        summary["target"],
        run=run,
        cwd=cwd,
    )
    receipt_target: ReconciliationReceiptTarget = {
        "base_head": summary["target"]["head"],
        "checkpoint_head": summary["target"]["head"],
        "input": summary["target"]["input"],
        "ref": summary["target"]["ref"],
    }
    receipt = _seal_receipt(
        _new_receipt_body(queue, collapsed, receipt_target, cursor=0)
    )
    return {
        "bounds": {
            **summary["bounds"],
            "conflict_path_char_limit": CONFLICT_PATH_CHAR_LIMIT,
            "conflict_path_limit": CONFLICT_PATH_LIMIT,
            "conflict_path_scan_limit": CONFLICT_PATH_SCAN_LIMIT,
            "merge_queue_head_limit": MERGE_QUEUE_HEAD_LIMIT,
            "merge_tree_output_char_limit": MERGE_TREE_OUTPUT_CHAR_LIMIT,
        },
        "collapsed": collapsed,
        "counts": counts,
        "mode": "sequential-merge-queue",
        "ok": True,
        "page_size": summary["page_size"],
        "pages": summary["pages"],
        "queue": queue,
        "receipt": receipt,
        "schema_version": summary["schema_version"],
        "target": summary["target"],
        "terminal": True,
        "truncated": False,
    }


def collect_merge_queue(
    target: str,
    page_size: int,
    *,
    run: RunFn = _run,
    cwd: str | None = None,
    progress: ProgressFn | None = None,
) -> MergeQueuePayload:
    """Collect and verify a deterministic queue of novel branch heads."""
    summary = collect_summary(
        target,
        page_size,
        run=run,
        cwd=cwd,
        progress=progress,
    )
    return _build_merge_queue(
        summary,
        run=run,
        cwd=cwd,
        progress=progress,
    )


def _merge_plan_paths(
    target: str,
    head: str,
    *,
    run: RunFn,
    cwd: str | None,
) -> list[str]:
    """Return the complete bounded branch delta used for collision planning."""
    result = run(_merge_plan_diff_argv(target, head), cwd)
    if (
        len(result.stdout) > MERGE_PLAN_GIT_OUTPUT_CHAR_LIMIT
        or len(result.stderr) > MERGE_PLAN_GIT_OUTPUT_CHAR_LIMIT
    ):
        raise InventoryError("merge queue plan Git output exceeded bound")
    if result.returncode != 0:
        raise InventoryError("merge queue plan path inspection failed")
    if result.stdout and not result.stdout.endswith("\0"):
        raise InventoryError("malformed merge queue plan path evidence")
    raw_paths = result.stdout[:-1].split("\0") if result.stdout else []
    if len(raw_paths) > MERGE_PLAN_PATH_SCAN_LIMIT:
        raise InventoryError("merge queue plan path scan bound exceeded")
    if len(raw_paths) != len(set(raw_paths)) or any(
        not path
        or path.startswith("/")
        or any(part in {"", ".", ".."} for part in path.split("/"))
        for path in raw_paths
    ):
        raise InventoryError("malformed merge queue plan path evidence")
    return sorted(raw_paths)


def _display_plan_paths(
    paths: Sequence[str],
    limit: int,
) -> tuple[list[str], bool, int]:
    """Render a bounded prefix while retaining explicit truncation evidence."""
    displayed: list[str] = []
    redactions = 0
    for path in paths[:limit]:
        safe_path, _truncated, was_redacted = _redact_bounded(
            path,
            SEMANTIC_PATH_CHAR_LIMIT,
        )
        displayed.append(safe_path)
        redactions += was_redacted
    return displayed, len(paths) > limit, redactions


def _build_group_rehearsal(
    order: int,
    indexes: Sequence[int],
    candidates: Sequence[MergePlanHead],
    raw_path_sets: Sequence[frozenset[str]],
    infrastructure_sets: Sequence[frozenset[str]],
    target: TargetRecord,
    run: RunFn,
    cwd: str | None,
) -> MergeRehearsalPlan:
    """Build a deterministic, unexecuted synthetic-candidate recipe."""
    return build_merge_rehearsal(
        order,
        indexes,
        candidates,
        raw_path_sets,
        infrastructure_sets,
        target,
        branch_limit=MERGE_REHEARSAL_BRANCH_LIMIT,
        group_limit=MERGE_REHEARSAL_GROUP_LIMIT,
        head_limit=MERGE_QUEUE_HEAD_LIMIT,
        path_display_limit=MERGE_REHEARSAL_PATH_DISPLAY_LIMIT,
        path_limit=MERGE_REHEARSAL_PATH_LIMIT,
        display_paths=_display_plan_paths,
        error_factory=InventoryError,
        prediction_run=run,
        prediction_cwd=cwd,
        prediction_check_limit=MERGE_REHEARSAL_PREDICTION_CHECK_LIMIT,
        prediction_conflict_path_display_limit=MERGE_REHEARSAL_PREDICTION_PATH_LIMIT,
        prediction_conflict_path_scan_limit=MERGE_REHEARSAL_PREDICTION_PATH_SCAN_LIMIT,
        prediction_output_char_limit=MERGE_REHEARSAL_PREDICTION_OUTPUT_CHAR_LIMIT,
        prediction_timeout_seconds=GIT_TIMEOUT_SECONDS,
        valid_object_id=_valid_object_id,
    )


def _build_merge_queue_plan(
    summary: SummaryPayload,
    *,
    run: RunFn,
    cwd: str | None,
    progress: ProgressFn | None,
) -> MergePlanPayload:
    """Group exhaustive unique/current heads by conservative path independence."""
    if any(
        (group["classification"] == "unique")
        != (group["lifecycle"] == "current")
        for group in summary["groups"]
    ):
        raise InventoryError("merge queue plan lifecycle evidence is inconsistent")
    current_groups = sorted(
        (
            group
            for group in summary["groups"]
            if group["classification"] == "unique"
        ),
        key=lambda group: (group["refs"][0], group["head"]),
    )
    if len(current_groups) > MERGE_QUEUE_HEAD_LIMIT:
        raise InventoryError(
            "merge queue plan head bound exceeded: "
            f"{len(current_groups)} > {MERGE_QUEUE_HEAD_LIMIT}"
        )

    candidates: list[MergePlanHead] = []
    raw_path_sets: list[frozenset[str]] = []
    infrastructure_sets: list[frozenset[str]] = []
    for index, group in enumerate(current_groups, start=1):
        if progress is not None:
            progress(f"plan-paths={index}/{len(current_groups)} head={group['head']}")
        raw_paths = _merge_plan_paths(
            summary["target"]["head"],
            group["head"],
            run=run,
            cwd=cwd,
        )
        infrastructure = [
            path for path in raw_paths if _is_shared_infrastructure_path(path)
        ]
        changed_display, changed_truncated, changed_redactions = _display_plan_paths(
            raw_paths, MERGE_PLAN_PATH_LIMIT
        )
        infra_display, infra_truncated, infra_redactions = _display_plan_paths(
            infrastructure, MERGE_PLAN_PATH_LIMIT
        )
        refs = sorted(group["refs"])
        candidates.append(
            {
                "branch_count": group["branch_count"],
                "changed_path_count": len(raw_paths),
                "changed_paths": changed_display,
                "changed_paths_truncated": changed_truncated,
                "expected_tip": group["head"],
                "path_redactions": changed_redactions + infra_redactions,
                "refs": refs,
                "shared_infrastructure_path_count": len(infrastructure),
                "shared_infrastructure_paths": infra_display,
                "shared_infrastructure_paths_truncated": infra_truncated,
                "source_ref": refs[0],
                "unique_commits": group["unique_commits"],
            }
        )
        raw_path_sets.append(frozenset(raw_paths))
        infrastructure_sets.append(frozenset(infrastructure))

    collisions: list[MergePlanCollision] = []
    colliding_pairs: set[tuple[int, int]] = set()
    collision_pairs = 0
    changed_path_collision_pairs = 0
    shared_infrastructure_collision_pairs = 0
    for left_index, left in enumerate(candidates):
        for right_index in range(left_index + 1, len(candidates)):
            collision = plan_collision(
                left,
                raw_path_sets[left_index],
                infrastructure_sets[left_index],
                candidates[right_index],
                raw_path_sets[right_index],
                infrastructure_sets[right_index],
                collision_path_limit=MERGE_PLAN_COLLISION_PATH_LIMIT,
                display_paths=_display_plan_paths,
            )
            if collision is None:
                continue
            collision_pairs += 1
            changed_path_collision_pairs += "changed-path" in collision["reasons"]
            shared_infrastructure_collision_pairs += (
                "shared-infrastructure" in collision["reasons"]
            )
            colliding_pairs.add((left_index, right_index))
            if len(collisions) < MERGE_PLAN_COLLISION_LIMIT:
                collisions.append(collision)

    grouped_indexes: list[list[int]] = []
    for candidate_index in range(len(candidates)):
        for group_indexes in grouped_indexes:
            if all(
                (min(candidate_index, member), max(candidate_index, member))
                not in colliding_pairs
                for member in group_indexes
            ):
                group_indexes.append(candidate_index)
                break
        else:
            grouped_indexes.append([candidate_index])
    if len(grouped_indexes) > MERGE_REHEARSAL_GROUP_LIMIT:
        raise InventoryError("merge rehearsal group bound exceeded")
    groups: list[MergePlanGroup] = []
    for order, indexes in enumerate(grouped_indexes, start=1):
        groups.append(
            {
                "branch_count": sum(candidates[index]["branch_count"] for index in indexes),
                "entries": [
                    {
                        "expected_tip": candidates[index]["expected_tip"],
                        "refs": list(candidates[index]["refs"]),
                        "source_ref": candidates[index]["source_ref"],
                    }
                    for index in indexes
                ],
                "head_count": len(indexes),
                "order": order,
                "rehearsal": _build_group_rehearsal(
                    order,
                    indexes,
                    candidates,
                    raw_path_sets,
                    infrastructure_sets,
                    summary["target"],
                    run,
                    cwd,
                ),
            }
        )

    if progress is not None:
        progress("verify=merge-queue-plan-preconditions")
    freshness_current = True
    try:
        _verify_merge_queue_preconditions(
            summary["groups"], summary["target"], run=run, cwd=cwd
        )
    except InventoryError:
        freshness_current = False
        for planned_group in groups:
            block_rehearsal_prediction(planned_group["rehearsal"])
    ancestor_groups = [
        group for group in summary["groups"] if group["classification"] == "ancestor"
    ]
    patch_groups = [
        group
        for group in summary["groups"]
        if group["classification"] == "patch-equivalent"
    ]
    counts: MergePlanCounts = {
        "ancestor_branches": sum(
            group["branch_count"] for group in ancestor_groups
        ),
        "ancestor_heads": len(ancestor_groups),
        "candidate_branches": sum(item["branch_count"] for item in candidates),
        "candidate_heads": len(candidates),
        "changed_path_collision_pairs": changed_path_collision_pairs,
        "collision_pairs": collision_pairs,
        "observed_branches": summary["counts"]["returned"],
        "observed_heads": summary["counts"]["deduplicated_heads"],
        "patch_equivalent_branches": sum(
            group["branch_count"] for group in patch_groups
        ),
        "patch_equivalent_heads": len(patch_groups),
        "planned_groups": len(groups),
        "shared_infrastructure_collision_pairs": (
            shared_infrastructure_collision_pairs
        ),
    }
    payload: MergePlanPayload = {
        "bounds": {
            **summary["bounds"],
            "collision_limit": MERGE_PLAN_COLLISION_LIMIT,
            "collision_path_limit": MERGE_PLAN_COLLISION_PATH_LIMIT,
            "git_output_char_limit": MERGE_PLAN_GIT_OUTPUT_CHAR_LIMIT,
            "json_char_limit": MERGE_PLAN_JSON_CHAR_LIMIT,
            "merge_queue_head_limit": MERGE_QUEUE_HEAD_LIMIT,
            "path_display_limit": MERGE_PLAN_PATH_LIMIT,
            "path_scan_limit": MERGE_PLAN_PATH_SCAN_LIMIT,
            "rehearsal_branch_limit": MERGE_REHEARSAL_BRANCH_LIMIT,
            "rehearsal_group_limit": MERGE_REHEARSAL_GROUP_LIMIT,
            "rehearsal_path_display_limit": MERGE_REHEARSAL_PATH_DISPLAY_LIMIT,
            "rehearsal_path_limit": MERGE_REHEARSAL_PATH_LIMIT,
        },
        "candidates": candidates,
        "collisions": collisions,
        "collisions_truncated": collision_pairs > len(collisions),
        "counts": counts,
        "groups": groups,
        "mode": "merge-queue-plan",
        "ok": freshness_current,
        "page_size": summary["page_size"],
        "pages": summary["pages"],
        "schema_version": summary["schema_version"],
        "snapshot_basis": build_plan_snapshot_basis(
            summary["groups"], summary["target"], groups
        ),
        "target": summary["target"],
        "terminal": freshness_current,
        "truncated": False,
    }
    if len(json.dumps(payload, sort_keys=True)) > MERGE_PLAN_JSON_CHAR_LIMIT:
        raise InventoryError("merge queue plan JSON output exceeded character bound")
    return payload


def collect_merge_queue_plan(
    target: str,
    page_size: int,
    *,
    run: RunFn = _run,
    cwd: str | None = None,
    progress: ProgressFn | None = None,
) -> MergePlanPayload:
    """Collect a bounded read-only grouping plan over unique/current heads."""
    return _build_merge_queue_plan(
        collect_summary(
            target,
            page_size,
            run=run,
            cwd=cwd,
            progress=progress,
        ),
        run=run,
        cwd=cwd,
        progress=progress,
    )


def replay_merge_receipt(
    receipt_value: object,
    *,
    expected_target: str | None = None,
    run: RunFn = _run,
    cwd: str | None = None,
    progress: ProgressFn | None = None,
) -> ReceiptReplayPayload:
    """Verify one checkpoint and expose the next ordered merge queue item."""
    receipt = _validate_receipt(receipt_value)
    body = receipt["body"]
    target_state = body["target"]
    if expected_target is not None and expected_target != target_state["input"]:
        raise InventoryError("reconciliation receipt target selection changed")

    try:
        current_target = _resolve_target(target_state["input"], run=run, cwd=cwd)
    except InventoryError as exc:
        raise InventoryError(
            "reconciliation receipt target verification failed"
        ) from exc
    if current_target["ref"] != target_state["ref"]:
        raise InventoryError("reconciliation receipt target identity changed")

    expected_entries: list[tuple[str, str]] = []
    for entry in body["queue"]:
        expected_entries.extend(
            (ref, entry["expected_tip"]) for ref in entry["refs"]
        )
    for group in body["collapsed"]:
        expected_entries.extend(
            (ref, group["expected_tip"]) for ref in group["refs"]
        )
    expected_entries.sort()
    if progress is not None:
        progress(
            "receipt=verify "
            f"cursor={body['cursor']}/{len(body['queue'])} "
            f"refs={len(expected_entries)}"
        )
    try:
        current_entries = _bounded_sorted_local_scan("", run=run, cwd=cwd)
    except InventoryError as exc:
        raise InventoryError(
            "reconciliation receipt source verification failed"
        ) from exc
    current_sources = [
        entry for entry in current_entries if entry[0] != current_target["ref"]
    ]
    if current_sources != expected_entries:
        raise InventoryError("reconciliation receipt source refs changed")

    checkpoint_head = target_state["checkpoint_head"]
    current_head = current_target["head"]
    target_moved = current_head != checkpoint_head
    try:
        if target_moved and not _ancestor(
            checkpoint_head,
            current_head,
            run=run,
            cwd=cwd,
        ):
            raise InventoryError("reconciliation receipt target movement rejected")
        integrated_status = [
            _ancestor(
                entry["expected_tip"],
                current_head,
                run=run,
                cwd=cwd,
            )
            for entry in body["queue"]
        ]
    except InventoryError as exc:
        if str(exc).startswith("reconciliation receipt"):
            raise
        raise InventoryError(
            "reconciliation receipt ancestry verification failed"
        ) from exc

    old_cursor = body["cursor"]
    if not all(integrated_status[:old_cursor]):
        raise InventoryError("reconciliation receipt cursor no longer matches target")
    new_cursor = old_cursor
    while (
        new_cursor < len(integrated_status) and integrated_status[new_cursor]
    ):
        new_cursor += 1
    if any(integrated_status[new_cursor:]):
        raise InventoryError("reconciliation receipt heads integrated out of order")
    if target_moved and new_cursor == old_cursor:
        raise InventoryError(
            "reconciliation receipt target movement lacks ordered integration"
        )
    if not target_moved and new_cursor != old_cursor:
        raise InventoryError("reconciliation receipt target and cursor disagree")

    next_entry = (
        _copy_queue_entry(body["queue"][new_cursor])
        if new_cursor < len(body["queue"])
        else None
    )
    renewed_target: ReconciliationReceiptTarget = {
        "base_head": target_state["base_head"],
        "checkpoint_head": current_head,
        "input": target_state["input"],
        "ref": target_state["ref"],
    }
    renewed_receipt = _seal_receipt(
        _new_receipt_body(
            body["queue"],
            body["collapsed"],
            renewed_target,
            cursor=new_cursor,
        )
    )
    if progress is not None:
        progress(f"receipt=replayed cursor={new_cursor}/{len(body['queue'])}")
    return {
        "bounds": {
            "conflict_path_char_limit": CONFLICT_PATH_CHAR_LIMIT,
            "conflict_path_limit": CONFLICT_PATH_LIMIT,
            "conflict_path_scan_limit": CONFLICT_PATH_SCAN_LIMIT,
            "local_ref_scan_limit": LOCAL_REF_SCAN_LIMIT,
            "merge_queue_head_limit": MERGE_QUEUE_HEAD_LIMIT,
            "merge_tree_output_char_limit": MERGE_TREE_OUTPUT_CHAR_LIMIT,
            "receipt_json_char_limit": RECEIPT_JSON_CHAR_LIMIT,
        },
        "complete": new_cursor == len(body["queue"]),
        "cursor": new_cursor,
        "integrated": [
            _copy_queue_entry(entry) for entry in body["queue"][:new_cursor]
        ],
        "mode": "reconciliation-receipt-replay",
        "newly_integrated": [
            _copy_queue_entry(entry)
            for entry in body["queue"][old_cursor:new_cursor]
        ],
        "next": next_entry,
        "ok": True,
        "receipt": renewed_receipt,
        "remaining_heads": len(body["queue"]) - new_cursor,
        "schema_version": SCHEMA_VERSION,
        "target": current_target,
    }


def _bounded_semantic_stdout(
    argv: Sequence[str],
    *,
    run: RunFn,
    cwd: str | None,
    label: str,
) -> str:
    """Return bounded Git output for opt-in semantic evidence."""
    output = _checked_stdout(argv, run=run, cwd=cwd, label=label)
    if len(output) > SEMANTIC_GIT_OUTPUT_CHAR_LIMIT:
        raise InventoryError("semantic Git output exceeded character bound")
    return output


def _redact_bounded(value: str, limit: int) -> tuple[str, bool, bool]:
    """Redact control characters and cap one human-facing evidence string."""
    redacted = "".join(
        character if character.isprintable() else "�" for character in value
    )
    truncated = len(redacted) > limit
    if truncated:
        redacted = f"{redacted[: limit - 1]}…"
    return redacted, truncated, redacted != value


def _validated_bounded_paths(
    raw_paths: list[str],
    *,
    output_limit: int,
    char_limit: int,
    error: str,
    require_unique: bool = False,
) -> tuple[list[str], int]:
    if (require_unique and len(raw_paths) != len(set(raw_paths))) or any(
        not path
        or path.startswith("/")
        or any(part in {"", ".", ".."} for part in path.split("/"))
        for path in raw_paths
    ):
        raise InventoryError(error)
    bounded: list[str] = []
    redactions = 0
    for path in raw_paths[:output_limit]:
        safe_path, _truncated, was_redacted = _redact_bounded(path, char_limit)
        bounded.append(safe_path)
        redactions += was_redacted
    return bounded, redactions


def _semantic_paths(
    head: str,
    *,
    run: RunFn,
    cwd: str | None,
) -> tuple[list[str], int, bool, int]:
    """Return bounded, redacted, repository-relative paths for one head."""
    output = _bounded_semantic_stdout(
        [
            "git",
            "diff-tree",
            "--root",
            "--first-parent",
            "--no-commit-id",
            "--name-only",
            "--no-renames",
            "-z",
            "-r",
            head,
        ],
        run=run,
        cwd=cwd,
        label="semantic path inspection failed",
    )
    if output and not output.endswith("\0"):
        raise InventoryError("malformed semantic path evidence")
    raw_paths = output[:-1].split("\0") if output else []
    changed_paths, path_redactions = _validated_bounded_paths(
        raw_paths,
        output_limit=SEMANTIC_PATH_LIMIT,
        char_limit=SEMANTIC_PATH_CHAR_LIMIT,
        error="malformed semantic path evidence",
    )
    return (
        changed_paths,
        len(raw_paths),
        len(raw_paths) > SEMANTIC_PATH_LIMIT,
        path_redactions,
    )


def _semantic_head_summary(
    head: str,
    *,
    run: RunFn,
    cwd: str | None,
) -> HeadSemanticSummary:
    """Collect one bounded subject and first-parent changed-path summary."""
    subject_output = _bounded_semantic_stdout(
        [
            "git",
            "show",
            "--no-show-signature",
            "--no-patch",
            "--format=%s",
            head,
        ],
        run=run,
        cwd=cwd,
        label="semantic subject inspection failed",
    )
    subject = subject_output.removesuffix("\n")
    if "\0" in subject:
        raise InventoryError("malformed semantic subject evidence")
    safe_subject, subject_truncated, _subject_redacted = _redact_bounded(
        subject,
        SEMANTIC_SUBJECT_CHAR_LIMIT,
    )
    changed_paths, path_count, paths_truncated, path_redactions = _semantic_paths(
        head,
        run=run,
        cwd=cwd,
    )
    return {
        "changed_path_count": path_count,
        "changed_paths": changed_paths,
        "changed_paths_truncated": paths_truncated,
        "head": head,
        "path_redactions": path_redactions,
        "subject": safe_subject,
        "subject_truncated": subject_truncated,
    }


def collect_head_summaries(
    groups: Sequence[SummaryGroup],
    *,
    run: RunFn = _run,
    cwd: str | None = None,
    progress: ProgressFn | None = None,
) -> list[HeadSemanticSummary]:
    """Collect semantic evidence for each distinct emitted head."""
    heads = list(dict.fromkeys(group["head"] for group in groups))
    if len(heads) > SEMANTIC_HEAD_LIMIT:
        raise InventoryError(
            f"semantic head bound exceeded: {len(heads)} > {SEMANTIC_HEAD_LIMIT}"
        )
    if any(not _valid_object_id(head) for head in heads):
        raise InventoryError("malformed semantic head evidence")

    summaries: list[HeadSemanticSummary] = []
    for index, head in enumerate(heads, start=1):
        if progress is not None:
            progress(f"semantic={index}/{len(heads)} head={head}")
        summaries.append(_semantic_head_summary(head, run=run, cwd=cwd))
    return summaries


def _progress(message: str) -> None:
    """Emit bounded progress without contaminating JSON stdout."""
    print(f"BRANCH-RECONCILIATION {message}", file=sys.stderr, flush=True)


def _read_json_stdin(error: InventoryError, limit: int) -> object:
    """Read one bounded JSON handoff while retaining its content-free error."""
    try:
        encoded = sys.stdin.read(limit + 1)
    except (OSError, UnicodeError) as exc:
        raise error from exc
    if not encoded or len(encoded) > limit:
        raise error
    try:
        return json.loads(encoded)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise error from exc


def main(
    argv: Sequence[str] | None = None,
    *,
    run: RunFn = _run,
) -> int:
    """Run the inventory CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="symbolic target ref")
    parser.add_argument("--limit", required=True, help="maximum branch records")
    parser.add_argument("--after", required=True,
                        help="canonical local ref cursor, or empty for first page")
    boolean_modes = {
        "--all-pages": "emit one terminal summary across every bounded page",
        "--counts-only": "omit expanded groups from an all-pages summary",
        "--current-only": "emit only unique current groups from a summary",
        "--quiet-progress": "suppress stderr progress while retaining errors",
        "--head-semantics": "add bounded commit subjects and changed paths",
        "--merge-queue": "emit a verified sequential novel-head queue",
        "--merge-queue-plan": "group current heads by changed-path independence",
        "--replay-receipt": "verify a bounded receipt from stdin",
        "--reconciliation-snapshot": "classify a plan against fresh inventory from stdin",
        "--remote-tracking": "compare local and local remote-tracking refs",
    }
    for flag, help_text in boolean_modes.items():
        parser.add_argument(flag, action="store_true", help=help_text)
    parser.add_argument("--remote-name", help="exact remote namespace to compare")
    parser.add_argument("--remote-verification-time",
                        help="canonical UTC time for fetch-freshness validation")
    parser.add_argument(
        "--retirement-keyring",
        help="trusted bounded Ed25519 reviewer keyring for retirement approvals",
    )
    parser.add_argument(
        "--retirement-verification-time",
        help="canonical UTC time used to validate signed retirement approvals",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        limit = int(args.limit)
        approval_options = bool(
            args.retirement_keyring or args.retirement_verification_time
        )
        if approval_options and not args.reconciliation_snapshot:
            raise InventoryError("retirement approval options require a snapshot")
        if bool(args.retirement_keyring) != bool(args.retirement_verification_time):
            raise InventoryError("invalid reconciliation snapshot")
        remote_options = bool(args.remote_name or args.remote_verification_time)
        if remote_options != bool(args.remote_tracking) or (
            args.remote_tracking
            and (not args.remote_name or not args.remote_verification_time)
        ):
            raise InventoryError("invalid remote tracking inventory")
        progress: ProgressFn | None = None if args.quiet_progress else _progress
        if progress is not None:
            progress(
                f"target={args.target} limit={limit} "
                f"after={args.after or '<start>'}"
            )
        payload: (
            InventoryPayload
            | SummaryPayload
            | SummaryCountsPayload
            | CurrentSummaryPayload
            | SemanticSummaryPayload
            | SemanticCurrentSummaryPayload
            | MergeQueuePayload
            | MergePlanPayload
            | ReceiptReplayPayload
            | ReconciliationSnapshotPayload
            | RemoteTrackingPayload
        )
        handoff_modes = sum(
            (args.replay_receipt, args.reconciliation_snapshot, args.remote_tracking)
        )
        if handoff_modes:
            if limit < 1 or limit > MAX_LIMIT:
                raise InventoryError(f"limit must be between 1 and {MAX_LIMIT}")
            if (
                args.after
                or args.all_pages
                or args.counts_only
                or args.current_only
                or args.head_semantics
                or args.merge_queue
                or args.merge_queue_plan
                or handoff_modes != 1
            ):
                raise InventoryError("handoff cannot be combined with inventory modes")
            error = (
                _invalid_receipt()
                if args.replay_receipt
                else InventoryError(
                    "invalid reconciliation snapshot"
                    if args.reconciliation_snapshot
                    else "invalid remote tracking inventory"
                )
            )
            handoff_value = _read_json_stdin(error, RECEIPT_JSON_CHAR_LIMIT)
            if args.remote_tracking:
                payload = collect_remote_tracking_inventory(
                    args.remote_name,
                    handoff_value,
                    args.remote_verification_time,
                    run=run,
                )
            elif args.reconciliation_snapshot:
                approval_trust = (
                    _read_approval_trust(args.retirement_keyring)
                    if args.retirement_keyring
                    else None
                )
                payload = build_reconciliation_snapshot(
                    handoff_value,
                    approval_trust=approval_trust,
                    verification_time=args.retirement_verification_time,
                )
                if payload["target"]["input"] != args.target:
                    raise InventoryError("invalid reconciliation snapshot")
            else:
                payload = replay_merge_receipt(
                    handoff_value,
                    expected_target=args.target,
                    run=run,
                    progress=progress,
                )
        elif args.all_pages:
            if args.after:
                raise InventoryError("all-pages summary requires an empty cursor")
            if args.merge_queue and (
                args.counts_only or args.current_only or args.head_semantics
            ):
                raise InventoryError(
                    "merge queue cannot be combined with counts-only, "
                    "current-only, or head semantics"
                )
            if args.merge_queue_plan and (
                args.counts_only
                or args.current_only
                or args.head_semantics
                or args.merge_queue
            ):
                raise InventoryError(
                    "merge queue plan cannot be combined with counts-only, "
                    "current-only, head semantics, or merge queue"
                )
            if args.counts_only and args.current_only:
                raise InventoryError("current-only cannot be combined with counts-only")
            if args.counts_only and args.head_semantics:
                raise InventoryError("head semantics require expanded groups")
            summary = collect_summary(
                args.target,
                limit,
                run=run,
                progress=progress,
            )
            if args.merge_queue_plan:
                payload = _build_merge_queue_plan(
                    summary,
                    run=run,
                    cwd=None,
                    progress=progress,
                )
            elif args.merge_queue:
                payload = _build_merge_queue(
                    summary,
                    run=run,
                    cwd=None,
                    progress=progress,
                )
            elif args.current_only:
                current_groups = [
                    group
                    for group in summary["groups"]
                    if group["classification"] == "unique"
                ]
                current_payload: CurrentSummaryPayload = {
                    "bounds": summary["bounds"],
                    "counts": summary["counts"],
                    "groups": current_groups,
                    "mode": "exhaustive-current",
                    "ok": summary["ok"],
                    "page_size": summary["page_size"],
                    "pages": summary["pages"],
                    "schema_version": summary["schema_version"],
                    "selected_branches": sum(
                        group["branch_count"] for group in current_groups
                    ),
                    "selected_heads": len(current_groups),
                    "target": summary["target"],
                    "terminal": summary["terminal"],
                    "truncated": summary["truncated"],
                }
                if args.head_semantics:
                    semantic_current_payload: SemanticCurrentSummaryPayload = {
                        **current_payload,
                        "head_summaries": collect_head_summaries(
                            current_groups,
                            run=run,
                            progress=progress,
                        ),
                    }
                    payload = semantic_current_payload
                else:
                    payload = current_payload
            elif args.counts_only:
                payload = {
                    "bounds": summary["bounds"],
                    "counts": summary["counts"],
                    "mode": "exhaustive-counts",
                    "ok": summary["ok"],
                    "page_size": summary["page_size"],
                    "pages": summary["pages"],
                    "schema_version": summary["schema_version"],
                    "target": summary["target"],
                    "terminal": summary["terminal"],
                    "truncated": summary["truncated"],
                }
            elif args.head_semantics:
                semantic_payload: SemanticSummaryPayload = {
                    **summary,
                    "head_summaries": collect_head_summaries(
                        summary["groups"],
                        run=run,
                        progress=progress,
                    ),
                }
                payload = semantic_payload
            else:
                payload = summary
        else:
            if args.merge_queue_plan:
                raise InventoryError(
                    "merge queue plan requires an all-pages summary"
                )
            if args.merge_queue:
                raise InventoryError("merge queue requires an all-pages summary")
            if args.head_semantics:
                raise InventoryError("head semantics require an all-pages summary")
            if args.counts_only or args.current_only:
                raise InventoryError(
                    "counts-only and current-only require an all-pages summary"
                )
            payload = collect_inventory(
                args.target,
                limit,
                after=args.after,
                run=run,
                progress=progress,
            )
    except (InventoryError, ValueError) as exc:
        json.dump(
            {
                "error": str(exc),
                "ok": False,
                "schema_version": SCHEMA_VERSION,
            },
            sys.stdout,
            sort_keys=True,
        )
        print()
        return 2
    json.dump(payload, sys.stdout, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
