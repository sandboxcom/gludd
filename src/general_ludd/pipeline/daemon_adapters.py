"""Daemon-side adapters that bind the pipeline lanes to real subsystems (#77).

These factory functions translate the daemon's concrete subsystems
(AgentDispatcher #42-style, safe_merge #70, git_repo_lock #63, worktree reclaim
#62) into the three injected callables the lanes expect:

  * :func:`make_dispatch_fn`   -> ``DispatchFn``  (launch a role-agent)
  * :func:`make_merge_fn`      -> ``MergeFn``     (safe_merge under the repo lock,
                                                   reclaim the worktree on green)
  * :func:`make_disk_ok`       -> disk-pressure predicate for back-pressure

The merge adapter is deliberately conservative: it obtains every BASE from the
recorded worktree fork point and invokes Git's bounded ``merge-file --stdout``
plumbing.  A missing provenance value, Git error, resource-limit breach, or
content conflict refuses the whole admission before any repository file is
written.  The worktree is then left intact for diagnosis or retry.

Kept import-light and free of FastAPI so it is unit-testable in isolation and so
``daemon.py`` only needs a thin call.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from typing import Any

from general_ludd.git_automation.locking import git_repo_lock
from general_ludd.pipeline import merge_io as _merge_io
from general_ludd.pipeline.merge_runtime import merge_completed_unit
from general_ludd.pipeline.state import CompletedUnit, MergeOutcome

logger = logging.getLogger(__name__)

_MAX_CHANGED_FILES = 256
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_DIAGNOSTIC_BYTES = 4096
_GIT_TIMEOUT_SECONDS = 15
_FULL_OBJECT_ID = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")

_ContentRead = _merge_io.ContentRead
_FileMerge = _merge_io.FileMerge

__all__ = [
    "make_disk_ok",
    "make_dispatch_fn",
    "make_merge_fn",
    "make_pid_provider",
]


def make_dispatch_fn(
    dispatcher: Any,
    *,
    agent_name: str = "general",
    invoker_name: str = "build",
    project_id: str | None = None,
    task_builder: Callable[[str], object] | None = None,
) -> Callable[[str], Awaitable[object]]:
    """Build a ``DispatchFn`` that launches a role-agent for a backlog unit id.

    ``dispatcher`` is an ``AgentDispatcher`` (its ``dispatch_one`` coroutine
    runs the agent under its own per-role concurrency semaphore). The pipeline
    DispatchLane handles overall ``target``/``floor`` saturation; the
    dispatcher's semaphore is a secondary per-role cap.

    ``agent_name`` defaults to ``"general"`` — the built-in general-purpose
    subagent registered by ``default_registry()``. It was previously
    ``"general-purpose"``, which is NOT a registered agent name: every pipeline
    dispatch would have been rejected by the dispatcher's ``config is None``
    (not-found) guard. ``"general"`` is the real registered target.

    ``invoker_name`` defaults to ``"build"`` — the primary agent that
    ``default_registry()`` grants ``can_dispatch_subagents=True`` with
    ``allowed_subagents=["*"]``. Threading a trusted invoker ACTIVATES the
    dispatcher's ``can_invoke`` permission gate for pipeline dispatch: an empty
    invoker bypasses the gate entirely (the matrix stays inert), whereas a
    registered, dispatch-capable invoker makes the gate enforce. ``"build"``
    covers every registered target via its ``["*"]`` allow-list, so a
    legitimate pipeline dispatch is never denied while an unregistered target
    is still fail-closed.

    ``project_id`` threads the owning project through to the ``AgentTask`` so
    the dispatcher's pause gate can block dispatch for a paused project (#51).
    """
    from general_ludd.agents.types import AgentTask

    def _default_builder(unit_id: str) -> object:
        return AgentTask(
            task_id=f"pipeline-{unit_id}",
            agent_name=agent_name,
            description=f"pipeline unit {unit_id}",
            prompt=f"Work backlog unit {unit_id}",
            invoker_name=invoker_name,
            project_id=project_id,
        )

    build = task_builder or _default_builder

    async def dispatch(unit_id: str) -> object:
        task = build(unit_id)
        return await dispatcher.dispatch_one(task)

    return dispatch


def _read_bounded(path: str) -> _ContentRead:
    """Read one regular file without following its final symlink."""
    return _merge_io.read_bounded(path, max_file_bytes=_MAX_FILE_BYTES)


def _bounded_diagnostic(value: bytes | str | None) -> str:
    return _merge_io.bounded_diagnostic(value, max_bytes=_MAX_DIAGNOSTIC_BYTES)


def _run_git(repo_path: str, *args: str) -> subprocess.CompletedProcess[bytes] | None:
    """Run one bounded Git plumbing command without a shell."""
    return _merge_io.run_git(
        repo_path,
        *args,
        timeout_seconds=_GIT_TIMEOUT_SECONDS,
    )


def _log_git_error(
    operation: str,
    relpath: str,
    result: subprocess.CompletedProcess[bytes] | None,
) -> None:
    _merge_io.log_git_error(
        operation,
        relpath,
        result,
        max_diagnostic_bytes=_MAX_DIAGNOSTIC_BYTES,
    )


def _read_fork_point(repo_path: str, base_sha: str, relpath: str) -> _ContentRead:
    """Read *relpath* from the exact fork-point tree, distinguishing absence.

    ``ls-tree`` proves whether the literal path existed.  Only then is its blob
    materialised, after a size check, so a bad object/repository cannot be
    mistaken for a newly added file and large blobs are never read eagerly.
    """
    return _merge_io.read_fork_point(
        repo_path,
        base_sha,
        relpath,
        full_object_id=_FULL_OBJECT_ID,
        max_file_bytes=_MAX_FILE_BYTES,
        run_git_fn=_run_git,
        log_git_error_fn=_log_git_error,
    )


def _safe_path(root: str, relpath: str) -> str | None:
    """Resolve a canonical repo-relative path without metadata/traversal escape."""
    return _merge_io.safe_path(root, relpath)


def _merge_file_with_git(
    repo_path: str,
    relpath: str,
    ours: bytes,
    base: bytes,
    theirs: bytes,
    temp_root: str,
) -> _FileMerge:
    """Run one sequential, non-mutating ``git merge-file --stdout`` call."""
    return _merge_io.merge_file_with_git(
        repo_path,
        relpath,
        ours,
        base,
        theirs,
        temp_root,
        max_file_bytes=_MAX_FILE_BYTES,
        run_git_fn=_run_git,
        log_git_error_fn=_log_git_error,
    )


def _atomic_replace(path: str, content: bytes) -> None:
    """Replace one admitted file from a same-directory temporary file."""
    _merge_io.atomic_replace(path, content)


def _commit_with_rollback(
    merged: dict[str, bytes], originals: dict[str, bytes | None]
) -> bool:
    """Apply admitted files and restore prior content on a write failure."""
    return _merge_io.commit_with_rollback(
        merged,
        originals,
        atomic_replace_fn=_atomic_replace,
    )


def make_merge_fn(
    repo_path: str,
    *,
    changed_files: Callable[[CompletedUnit], list[str]] | None = None,
    reclaim: Callable[[str], None] | None = None,
) -> Callable[[CompletedUnit], Awaitable[MergeOutcome]]:
    """Build a ``MergeFn`` that merges a worktree into ``repo_path`` safely.

    For every changed relative path, ``base`` is materialised from the exact
    ``CompletedUnit.base_sha`` tree, ``ours`` is the current repository file,
    and ``theirs`` is the worktree file.  Git's ``merge-file --stdout`` performs
    the same three-way content merge without modifying an input file.

    Admission is bounded to 256 files, 8 MiB for any version/result, 64 MiB for
    all materialised content, and one sequential 15-second Git invocation at a
    time.  On a fully clean admission, files are atomically replaced with
    best-effort rollback on write failure and the worktree is reclaimed.  On
    any missing provenance, conflict, tool failure, unsafe path, or resource
    breach, nothing is written and the worktree is preserved.

    The whole merge runs under ``git_repo_lock(repo_path)`` so it never races a
    concurrent git mutation on the shared tree (#63).
    """

    def _default_changed(unit: CompletedUnit) -> list[str]:
        # Best-effort: ask git for the worktree's changed tracked files.
        try:
            out = subprocess.run(
                ["git", "-C", unit.worktree_path, "diff", "--name-only", "HEAD"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            return [ln for ln in out.stdout.splitlines() if ln.strip()]
        except Exception:  # pragma: no cover - git absent / odd worktree
            return []

    list_changed = changed_files or _default_changed

    def _default_reclaim(worktree_path: str) -> None:
        try:
            subprocess.run(
                ["git", "-C", repo_path, "worktree", "remove", "--force", "--", worktree_path],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception:  # pragma: no cover
            with contextlib.suppress(OSError):
                shutil.rmtree(worktree_path, ignore_errors=True)

    do_reclaim = reclaim or _default_reclaim

    def _refuse(unit: CompletedUnit, detail: str) -> MergeOutcome:
        return MergeOutcome(
            unit_id=unit.unit_id,
            merged=False,
            clobber_refused=True,
            detail=detail,
        )

    def _merge_sync(unit: CompletedUnit) -> MergeOutcome:
        return merge_completed_unit(
            unit,
            repo_path=repo_path,
            list_changed=list_changed,
            reclaim=do_reclaim,
            refuse=_refuse,
            max_changed_files=_MAX_CHANGED_FILES,
            max_total_bytes=_MAX_TOTAL_BYTES,
            repo_lock=git_repo_lock,
            safe_path=_safe_path,
            read_bounded=_read_bounded,
            read_fork_point=_read_fork_point,
            merge_file=_merge_file_with_git,
            commit_files=_commit_with_rollback,
        )

    async def merge(unit: CompletedUnit) -> MergeOutcome:
        # Off-load blocking git + file I/O so the integrate lane never stalls
        # the event loop while holding (or waiting on) the repo lock.
        return await asyncio.to_thread(_merge_sync, unit)

    return merge


def make_pid_provider(
    queues: list[Any],
    *,
    scrape: Callable[[], Any] | None = None,
    controller: Any = None,
) -> Callable[[], Any | None]:
    """Build a ``PidProvider`` from gludd's existing load/PID controller.

    The controller becomes ``ControllerOutputs`` for the DispatchLane's
    keep-N-busy loop.

    Each call scrapes a fresh :class:`LoadSnapshot` and runs
    ``LoadController.evaluate_snapshot`` over the configured ``queues`` — the
    SAME path the event loop's ``_phase_evaluate_pid_controllers`` uses to cap
    the legacy dispatcher — so the pipeline's desired concurrency is driven by
    the live load controller (resource-profile-aware per-queue bucketing),
    NOT a frozen static target. The lane then honours its ``pid_group`` against
    ``desired_active_buckets_by_queue`` and falls back to
    ``desired_total_active_buckets`` for the aggregate.

    ``scrape`` / ``controller`` are injectable for unit tests; the defaults use
    the real :func:`scrape_system_load` and a default ``LoadController``. On any
    scrape/eval error the provider yields ``None`` so the lane safely falls back
    to its static ``config.target`` rather than starving dispatch.
    """
    from general_ludd.controllers.load_scrape import scrape_system_load
    from general_ludd.controllers.pid import LoadController

    do_scrape = scrape or scrape_system_load
    ctrl = controller if controller is not None else LoadController()

    def provider() -> Any | None:
        try:
            snapshot = do_scrape()
            return ctrl.evaluate_snapshot(snapshot, queues)
        except Exception as exc:  # pragma: no cover - defensive: never starve dispatch
            logger.debug("pipeline PID provider: evaluation skipped: %s", exc)
            return None

    return provider


def make_disk_ok(
    repo_path: str,
    *,
    floor_mib: int = 2048,
) -> Callable[[], bool]:
    """Build a disk-pressure predicate: False when free space is below floor.

    Mirrors the Makefile ``disk-guard`` floor so the pipeline applies the same
    back-pressure the agent batch tooling does (#62 disk discipline).
    """

    def disk_ok() -> bool:
        try:
            usage = shutil.disk_usage(repo_path)
            free_mib = usage.free // (1024 * 1024)
            return free_mib >= floor_mib
        except OSError:  # pragma: no cover
            return True

    return disk_ok
