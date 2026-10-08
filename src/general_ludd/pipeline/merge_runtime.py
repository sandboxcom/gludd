"""Transactional admission engine for one completed pipeline worktree."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from general_ludd.pipeline.state import CompletedUnit, MergeOutcome

logger = logging.getLogger(__name__)


def _normalize_paths(
    files: list[str],
    *,
    repo_path: str,
    worktree_path: str,
    safe_path: Callable[[str, str], str | None],
) -> tuple[list[tuple[str, str, str]], str | None]:
    """Resolve unique safe paths for a completed unit."""
    normalized: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for relpath in files:
        if not isinstance(relpath, str) or relpath in seen:
            return [], "invalid_changed_files"
        repo_file = safe_path(repo_path, relpath)
        worktree_file = safe_path(worktree_path, relpath)
        if repo_file is None or worktree_file is None:
            return [], f"unsafe_path:{relpath}"
        seen.add(relpath)
        normalized.append((relpath, repo_file, worktree_file))
    return normalized, None


def _merge_one_file(
    unit: CompletedUnit,
    *,
    repo_path: str,
    relpath: str,
    repo_file: str,
    worktree_file: str,
    temp_root: str,
    account: Callable[[bytes], bool],
    merged_files: dict[str, bytes],
    originals: dict[str, bytes | None],
    read_bounded: Callable[[str], Any],
    read_fork_point: Callable[[str, str, str], Any],
    merge_file: Callable[[str, str, bytes, bytes, bytes, str], Any],
) -> str | None:
    """Merge one admitted path, returning a refusal detail on failure."""
    repo_read = read_bounded(repo_file)
    worktree_read = read_bounded(worktree_file)
    if worktree_read.status == "absent" and repo_read.status == "absent":
        return None
    if repo_read.status == "too_large" or worktree_read.status == "too_large":
        return f"file_too_large:{relpath}"
    if repo_read.status not in {"ok", "absent"}:
        return f"repo_read_error:{relpath}"
    if worktree_read.status != "ok":
        return f"worktree_read_error:{relpath}"
    if not unit.base_sha:
        logger.warning("pipeline merge: no base_sha for unit %s; refusing", unit.unit_id)
        return "no_base_sha"

    theirs = worktree_read.data
    assert theirs is not None
    if not account(theirs):
        return "total_size_exceeded"
    ours = repo_read.data
    if ours is not None and not account(ours):
        return "total_size_exceeded"
    base_read = read_fork_point(repo_path, unit.base_sha, relpath)
    if base_read.status == "too_large":
        return f"file_too_large:{relpath}"
    if base_read.status in {"invalid_base", "tool_error"}:
        return f"base_tool_error:{relpath}"
    if base_read.status == "absent":
        if ours is None or ours == theirs:
            merged_files[repo_file] = theirs
            originals[repo_file] = ours
            return None
        return f"conflict:{relpath}"

    base = base_read.data
    assert base is not None
    if not account(base):
        return "total_size_exceeded"
    if ours is None:
        return None if theirs == base else f"conflict:{relpath}"
    result = merge_file(repo_path, relpath, ours, base, theirs, temp_root)
    if result.status == "conflict":
        logger.warning(
            "pipeline merge: REFUSING clobber on %s for unit %s",
            relpath,
            unit.unit_id,
        )
        return f"conflict:{relpath}"
    if result.status == "tool_error":
        return f"merge_tool_error:{relpath}"
    if result.status == "too_large":
        return f"file_too_large:{relpath}"
    merged = result.data
    assert merged is not None
    if not account(merged):
        return "total_size_exceeded"
    merged_files[repo_file] = merged
    originals[repo_file] = ours
    return None


def merge_completed_unit(
    unit: CompletedUnit,
    *,
    repo_path: str,
    list_changed: Callable[[CompletedUnit], list[str]],
    reclaim: Callable[[str], None],
    refuse: Callable[[CompletedUnit, str], MergeOutcome],
    max_changed_files: int,
    max_total_bytes: int,
    repo_lock: Callable[[str], AbstractContextManager[Any]],
    safe_path: Callable[[str, str], str | None],
    read_bounded: Callable[[str], Any],
    read_fork_point: Callable[[str, str, str], Any],
    merge_file: Callable[[str, str, bytes, bytes, bytes, str], Any],
    commit_files: Callable[[dict[str, bytes], dict[str, bytes | None]], bool],
) -> MergeOutcome:
    """Admit, merge, and atomically commit one bounded completed unit."""
    try:
        files = list_changed(unit)
    except Exception as exc:
        logger.warning(
            "pipeline merge: changed-file discovery failed for unit %s: %s",
            unit.unit_id,
            type(exc).__name__,
        )
        return refuse(unit, "changed_files_error")
    if not files:
        reclaim(unit.worktree_path)
        return MergeOutcome(unit_id=unit.unit_id, merged=True, detail="empty")
    if len(files) > max_changed_files:
        return refuse(unit, "too_many_files")

    normalized, path_error = _normalize_paths(
        files,
        repo_path=repo_path,
        worktree_path=unit.worktree_path,
        safe_path=safe_path,
    )
    if path_error is not None:
        return refuse(unit, path_error)

    merged_files: dict[str, bytes] = {}
    originals: dict[str, bytes | None] = {}
    total_bytes = 0

    def account(content: bytes) -> bool:
        nonlocal total_bytes
        total_bytes += len(content)
        return total_bytes <= max_total_bytes

    with repo_lock(repo_path):
        try:
            with tempfile.TemporaryDirectory(prefix="gludd-pipeline-merge-") as temp:
                for relpath, repo_file, worktree_file in normalized:
                    failure = _merge_one_file(
                        unit,
                        repo_path=repo_path,
                        relpath=relpath,
                        repo_file=repo_file,
                        worktree_file=worktree_file,
                        temp_root=temp,
                        account=account,
                        merged_files=merged_files,
                        originals=originals,
                        read_bounded=read_bounded,
                        read_fork_point=read_fork_point,
                        merge_file=merge_file,
                    )
                    if failure is not None:
                        return refuse(unit, failure)
        except OSError as exc:
            logger.warning(
                "pipeline merge: temporary-file failure for unit %s: %s",
                unit.unit_id,
                type(exc).__name__,
            )
            return refuse(unit, "temporary_file_error")

        if not commit_files(merged_files, originals):
            return refuse(unit, "write_error")

    reclaim(unit.worktree_path)
    return MergeOutcome(unit_id=unit.unit_id, merged=True, detail="merged")
