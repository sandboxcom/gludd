#!/usr/bin/env python3
"""Emit a bounded, read-only branch-reconciliation inventory."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import Literal, TypedDict

SCHEMA_VERSION = 2
MAX_LIMIT = 100
COMMIT_SCAN_LIMIT = 500
LOCAL_REF_SCAN_LIMIT = 10_000
GIT_TIMEOUT_SECONDS = 10
SEMANTIC_HEAD_LIMIT = 256
SEMANTIC_PATH_LIMIT = 100
SEMANTIC_SUBJECT_CHAR_LIMIT = 200
SEMANTIC_PATH_CHAR_LIMIT = 240
SEMANTIC_GIT_OUTPUT_CHAR_LIMIT = 262_144
_OBJECT_ID_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")

RunFn = Callable[
    [Sequence[str], str | None],
    subprocess.CompletedProcess[str],
]
ProgressFn = Callable[[str], None]


class InventoryError(RuntimeError):
    """Raised when Git cannot prove a safe reconciliation classification."""


class BranchRecord(TypedDict):
    """Machine-readable classification for one local branch."""

    classification: Literal["ancestor", "patch-equivalent", "unique"]
    head: str
    lifecycle: Literal["current", "historical"]
    name: str
    patch_equivalent_commits: int
    ref: str
    unique_commits: int


class InventoryCounts(TypedDict):
    """Counts for the bounded returned branch set."""

    ancestor: int
    current: int
    historical: int
    patch_equivalent: int
    returned: int
    unique: int


class TargetRecord(TypedDict):
    """Resolved target identity."""

    head: str
    input: str
    ref: str


class InventoryBounds(TypedDict):
    """Hard bounds applied to Git traversal and JSON output."""

    branch_limit: int
    commit_scan_limit: int
    local_ref_scan_limit: int


class InventoryPayload(TypedDict):
    """Top-level JSON contract."""

    after: str | None
    bounds: InventoryBounds
    branches: list[BranchRecord]
    counts: InventoryCounts
    limit: int
    next_cursor: str | None
    ok: bool
    schema_version: int
    target: TargetRecord
    truncated: bool


class SummaryCounts(InventoryCounts):
    """Counts for a terminal exhaustive inventory."""

    deduplicated_heads: int


class SummaryGroup(TypedDict):
    """Branches sharing one classified commit identity."""

    branch_count: int
    classification: Literal["ancestor", "patch-equivalent", "unique"]
    head: str
    lifecycle: Literal["current", "historical"]
    names: list[str]
    patch_equivalent_commits: int
    refs: list[str]
    unique_commits: int


class HeadSemanticSummary(TypedDict):
    """Bounded review evidence for one deduplicated branch head."""

    changed_path_count: int
    changed_paths: list[str]
    changed_paths_truncated: bool
    head: str
    path_redactions: int
    subject: str
    subject_truncated: bool


class SummaryPayload(TypedDict):
    """Terminal, deduplicated view across every bounded page."""

    bounds: InventoryBounds
    counts: SummaryCounts
    groups: list[SummaryGroup]
    mode: Literal["exhaustive-summary"]
    ok: bool
    page_size: int
    pages: int
    schema_version: int
    target: TargetRecord
    terminal: bool
    truncated: bool


class SummaryCountsPayload(TypedDict):
    """Terminal exhaustive inventory without expanded branch groups."""

    bounds: InventoryBounds
    counts: SummaryCounts
    mode: Literal["exhaustive-counts"]
    ok: bool
    page_size: int
    pages: int
    schema_version: int
    target: TargetRecord
    terminal: bool
    truncated: bool


class CurrentSummaryPayload(TypedDict):
    """Terminal exhaustive inventory restricted to current unique heads."""

    bounds: InventoryBounds
    counts: SummaryCounts
    groups: list[SummaryGroup]
    mode: Literal["exhaustive-current"]
    ok: bool
    page_size: int
    pages: int
    schema_version: int
    selected_branches: int
    selected_heads: int
    target: TargetRecord
    terminal: bool
    truncated: bool


class SemanticSummaryPayload(SummaryPayload):
    """Expanded terminal summary with opt-in semantic head evidence."""

    head_summaries: list[HeadSemanticSummary]


class SemanticCurrentSummaryPayload(CurrentSummaryPayload):
    """Current-only terminal summary with opt-in semantic head evidence."""

    head_summaries: list[HeadSemanticSummary]


def _run(
    argv: Sequence[str], cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one bounded list-form Git command."""
    try:
        return subprocess.run(
            list(argv),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(list(argv), 124, "", str(exc))


def _checked_stdout(
    argv: Sequence[str],
    *,
    run: RunFn,
    cwd: str | None,
    label: str,
) -> str:
    """Return stdout or convert a Git failure into bounded audit evidence."""
    result = run(argv, cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        raise InventoryError(f"{label}: {detail[:400]}")
    return result.stdout


def _valid_object_id(value: str) -> bool:
    """Return whether value is a full SHA-1 or SHA-256 object ID."""
    return bool(_OBJECT_ID_RE.fullmatch(value))


def _resolve_target(
    target: str,
    *,
    run: RunFn,
    cwd: str | None,
) -> TargetRecord:
    """Resolve one symbolic Git ref and reject revisions or option shapes."""
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
    """Validate a canonical local-ref cursor without requiring it to exist."""
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
    """Parse ordered for-each-ref output and retain only local branches."""
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


def _bounded_branches(
    target_ref: str,
    limit: int,
    after: str | None,
    *,
    run: RunFn,
    cwd: str | None,
) -> tuple[list[tuple[str, str]], bool]:
    """Return at most limit local branches plus a truncation signal."""
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
        assert after is not None
        entries = [entry for entry in scanned_entries if entry[0] > after]
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
    if after is not None and any(ref <= after for ref, _head in entries):
        raise InventoryError("pagination cursor did not advance")
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
    """Use merge-base to prove ancestry, distinguishing false from failure."""
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
    """Count head-side commits up to one beyond the patch scan bound."""
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
    """Count equivalent and unique rows from bounded git cherry output."""
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
    """Classify one branch without changing repository state."""
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
    """Summarize only the bounded branch set returned to the caller."""
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


def collect_inventory(
    target: str,
    limit: int,
    *,
    after: str = "",
    run: RunFn = _run,
    cwd: str | None = None,
    progress: ProgressFn | None = None,
) -> InventoryPayload:
    """Collect the bounded reconciliation inventory."""
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
    """Collect every bounded page and group branches by classified head."""
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
    if any(
        not path
        or path.startswith("/")
        or any(part in {"", ".", ".."} for part in path.split("/"))
        for path in raw_paths
    ):
        raise InventoryError("malformed semantic path evidence")

    changed_paths: list[str] = []
    path_redactions = 0
    for path in raw_paths[:SEMANTIC_PATH_LIMIT]:
        safe_path, _truncated, was_redacted = _redact_bounded(
            path,
            SEMANTIC_PATH_CHAR_LIMIT,
        )
        changed_paths.append(safe_path)
        path_redactions += was_redacted
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


def main(
    argv: Sequence[str] | None = None,
    *,
    run: RunFn = _run,
) -> int:
    """Run the inventory CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="symbolic target ref")
    parser.add_argument("--limit", required=True, help="maximum branch records")
    parser.add_argument(
        "--after",
        required=True,
        help="canonical local ref cursor, or empty for the first page",
    )
    parser.add_argument(
        "--all-pages",
        action="store_true",
        help="emit one terminal summary across every bounded page",
    )
    parser.add_argument(
        "--counts-only",
        action="store_true",
        help="omit expanded groups from an all-pages summary",
    )
    parser.add_argument(
        "--current-only",
        action="store_true",
        help="emit only unique current groups from an all-pages summary",
    )
    parser.add_argument(
        "--quiet-progress",
        action="store_true",
        help="suppress progress narration on stderr while retaining errors",
    )
    parser.add_argument(
        "--head-semantics",
        action="store_true",
        help="add bounded commit subjects and changed paths per summary head",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        limit = int(args.limit)
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
        )
        if args.all_pages:
            if args.after:
                raise InventoryError("all-pages summary requires an empty cursor")
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
            if args.current_only:
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
