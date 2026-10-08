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
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from general_ludd.git_automation.locking import git_repo_lock
from general_ludd.pipeline.state import CompletedUnit, MergeOutcome

logger = logging.getLogger(__name__)

_MAX_CHANGED_FILES = 256
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_DIAGNOSTIC_BYTES = 4096
_GIT_TIMEOUT_SECONDS = 15
_FULL_OBJECT_ID = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")

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


@dataclass(frozen=True)
class _ContentRead:
    status: str
    data: bytes | None = None


def _read_bounded(path: str) -> _ContentRead:
    """Read one regular file without following its final symlink."""
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return _ContentRead("absent")
    except OSError:
        return _ContentRead("error")

    if not stat.S_ISREG(before.st_mode):
        return _ContentRead("error")
    if before.st_size > _MAX_FILE_BYTES:
        return _ContentRead("too_large")

    try:
        with open(path, "rb") as fh:
            opened = os.fstat(fh.fileno())
            data = fh.read(_MAX_FILE_BYTES + 1)
        after = os.lstat(path)
    except OSError:
        return _ContentRead("error")

    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_opened = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_opened or identity_opened != identity_after:
        return _ContentRead("changed")
    if len(data) > _MAX_FILE_BYTES:
        return _ContentRead("too_large")
    return _ContentRead("ok", data)


def _bounded_diagnostic(value: bytes | str | None) -> str:
    if value is None:
        return ""
    raw = value.encode("utf-8", "replace") if isinstance(value, str) else value
    bounded = raw[:_MAX_DIAGNOSTIC_BYTES]
    return bounded.decode("utf-8", "replace").replace("\x00", "?").strip()


def _run_git(repo_path: str, *args: str) -> subprocess.CompletedProcess[bytes] | None:
    """Run one bounded Git plumbing command without a shell."""
    command = ["git", "-C", repo_path, *args]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "pipeline merge: git %s failed: %s",
            args[0] if args else "command",
            type(exc).__name__,
        )
        return None


def _log_git_error(
    operation: str,
    relpath: str,
    result: subprocess.CompletedProcess[bytes] | None,
) -> None:
    returncode = "exception" if result is None else str(result.returncode)
    diagnostic = "" if result is None else _bounded_diagnostic(result.stderr)
    logger.warning(
        "pipeline merge: %s failed for %s (rc=%s, diagnostic=%s)",
        operation,
        relpath,
        returncode,
        diagnostic or "unavailable",
    )


def _read_fork_point(repo_path: str, base_sha: str, relpath: str) -> _ContentRead:
    """Read *relpath* from the exact fork-point tree, distinguishing absence.

    ``ls-tree`` proves whether the literal path existed.  Only then is its blob
    materialised, after a size check, so a bad object/repository cannot be
    mistaken for a newly added file and large blobs are never read eagerly.
    """
    if _FULL_OBJECT_ID.fullmatch(base_sha) is None:
        return _ContentRead("invalid_base")

    listed = _run_git(
        repo_path,
        "ls-tree",
        "-z",
        "--full-tree",
        base_sha,
        "--",
        f":(literal){relpath}",
    )
    if listed is None or listed.returncode != 0:
        _log_git_error("ls-tree", relpath, listed)
        return _ContentRead("tool_error")
    if len(listed.stdout) > len(os.fsencode(relpath)) + 256:
        return _ContentRead("tool_error")

    entries = [entry for entry in listed.stdout.split(b"\x00") if entry]
    if not entries:
        return _ContentRead("absent")
    if len(entries) != 1:
        return _ContentRead("tool_error")
    try:
        header, raw_path = entries[0].split(b"\t", 1)
        _mode, object_type, object_id = header.split(b" ", 2)
    except ValueError:
        return _ContentRead("tool_error")
    if raw_path != os.fsencode(relpath) or object_type != b"blob":
        return _ContentRead("tool_error")

    object_name = object_id.decode("ascii", "strict")
    sized = _run_git(repo_path, "cat-file", "-s", object_name)
    if sized is None or sized.returncode != 0:
        _log_git_error("cat-file-size", relpath, sized)
        return _ContentRead("tool_error")
    try:
        size = int(sized.stdout.strip())
    except ValueError:
        return _ContentRead("tool_error")
    if size < 0:
        return _ContentRead("tool_error")
    if size > _MAX_FILE_BYTES:
        return _ContentRead("too_large")

    content = _run_git(repo_path, "cat-file", "blob", object_name)
    if content is None or content.returncode != 0:
        _log_git_error("cat-file-blob", relpath, content)
        return _ContentRead("tool_error")
    if len(content.stdout) != size or len(content.stdout) > _MAX_FILE_BYTES:
        return _ContentRead("tool_error")
    return _ContentRead("ok", content.stdout)


def _safe_path(root: str, relpath: str) -> str | None:
    """Resolve a canonical repo-relative path without metadata/traversal escape."""
    if not relpath or "\x00" in relpath or "\\" in relpath:
        return None
    pure = PurePosixPath(relpath)
    if pure.is_absolute() or pure.parts[0] == ".git" or ".." in pure.parts:
        return None
    canonical = pure.as_posix()
    if canonical != relpath or canonical == ".":
        return None

    root_real = os.path.realpath(root)
    candidate = os.path.join(root_real, *pure.parts)
    parent_real = os.path.realpath(os.path.dirname(candidate))
    try:
        if os.path.commonpath((root_real, parent_real)) != root_real:
            return None
    except ValueError:
        return None
    return candidate


@dataclass(frozen=True)
class _FileMerge:
    status: str
    data: bytes | None = None


def _merge_file_with_git(
    repo_path: str,
    relpath: str,
    ours: bytes,
    base: bytes,
    theirs: bytes,
    temp_root: str,
) -> _FileMerge:
    """Run one sequential, non-mutating ``git merge-file --stdout`` call."""
    paths = [os.path.join(temp_root, name) for name in ("ours", "base", "theirs")]
    try:
        for path, content in zip(paths, (ours, base, theirs), strict=True):
            with open(path, "wb") as fh:
                fh.write(content)
        result = _run_git(
            repo_path,
            "merge-file",
            "--stdout",
            "--",
            paths[0],
            paths[1],
            paths[2],
        )
    except OSError:
        return _FileMerge("tool_error")

    if result is None:
        return _FileMerge("tool_error")
    if result.returncode == 0:
        if len(result.stdout) > _MAX_FILE_BYTES:
            return _FileMerge("too_large")
        return _FileMerge("ok", result.stdout)
    if 1 <= result.returncode <= 127:
        return _FileMerge("conflict")
    _log_git_error("merge-file", relpath, result)
    return _FileMerge("tool_error")


def _atomic_replace(path: str, content: bytes) -> None:
    """Replace one admitted file from a same-directory temporary file."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".gludd-merge-", dir=parent)
    try:
        with os.fdopen(descriptor, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
        temporary = ""
    finally:
        if temporary:
            with contextlib.suppress(OSError):
                os.unlink(temporary)


def _commit_with_rollback(
    merged: dict[str, bytes], originals: dict[str, bytes | None]
) -> bool:
    """Apply admitted files and restore prior content on a write failure."""
    applied: list[str] = []
    try:
        for path, content in merged.items():
            _atomic_replace(path, content)
            applied.append(path)
    except OSError as exc:
        logger.warning("pipeline merge: repository write failed: %s", type(exc).__name__)
        for path in reversed(applied):
            original = originals[path]
            try:
                if original is None:
                    os.unlink(path)
                else:
                    _atomic_replace(path, original)
            except OSError as rollback_exc:
                logger.error(
                    "pipeline merge: rollback failed: %s", type(rollback_exc).__name__
                )
        return False
    return True


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
        try:
            files = list_changed(unit)
        except Exception as exc:
            logger.warning(
                "pipeline merge: changed-file discovery failed for unit %s: %s",
                unit.unit_id,
                type(exc).__name__,
            )
            return _refuse(unit, "changed_files_error")
        if not files:
            # Nothing to merge: treat as a clean no-op merge so the unit
            # advances to the gate and its worktree is reclaimed.
            do_reclaim(unit.worktree_path)
            return MergeOutcome(unit_id=unit.unit_id, merged=True, detail="empty")
        if len(files) > _MAX_CHANGED_FILES:
            return _refuse(unit, "too_many_files")

        normalized: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for rel in files:
            if not isinstance(rel, str) or rel in seen:
                return _refuse(unit, "invalid_changed_files")
            repo_file = _safe_path(repo_path, rel)
            wt_file = _safe_path(unit.worktree_path, rel)
            if repo_file is None or wt_file is None:
                return _refuse(unit, f"unsafe_path:{rel}")
            seen.add(rel)
            normalized.append((rel, repo_file, wt_file))

        merged_files: dict[str, bytes] = {}
        originals: dict[str, bytes | None] = {}
        total_bytes = 0

        def account(content: bytes) -> bool:
            nonlocal total_bytes
            total_bytes += len(content)
            return total_bytes <= _MAX_TOTAL_BYTES

        with git_repo_lock(repo_path):
            try:
                with tempfile.TemporaryDirectory(prefix="gludd-pipeline-merge-") as temp:
                    for rel, repo_file, wt_file in normalized:
                        repo_read = _read_bounded(repo_file)
                        wt_read = _read_bounded(wt_file)
                        if wt_read.status == "absent" and repo_read.status == "absent":
                            continue
                        if repo_read.status == "too_large" or wt_read.status == "too_large":
                            return _refuse(unit, f"file_too_large:{rel}")
                        if repo_read.status not in {"ok", "absent"}:
                            return _refuse(unit, f"repo_read_error:{rel}")
                        if wt_read.status != "ok":
                            return _refuse(unit, f"worktree_read_error:{rel}")
                        if not unit.base_sha:
                            logger.warning(
                                "pipeline merge: no base_sha for unit %s; refusing",
                                unit.unit_id,
                            )
                            return _refuse(unit, "no_base_sha")

                        theirs = wt_read.data
                        assert theirs is not None
                        if not account(theirs):
                            return _refuse(unit, "total_size_exceeded")
                        ours = repo_read.data
                        if ours is not None and not account(ours):
                            return _refuse(unit, "total_size_exceeded")

                        base_read = _read_fork_point(repo_path, unit.base_sha, rel)
                        if base_read.status == "too_large":
                            return _refuse(unit, f"file_too_large:{rel}")
                        if base_read.status in {"invalid_base", "tool_error"}:
                            return _refuse(unit, f"base_tool_error:{rel}")

                        if base_read.status == "absent":
                            if ours is None or ours == theirs:
                                merged_files[repo_file] = theirs
                                originals[repo_file] = ours
                                continue
                            return _refuse(unit, f"conflict:{rel}")

                        base = base_read.data
                        assert base is not None
                        if not account(base):
                            return _refuse(unit, "total_size_exceeded")
                        if ours is None:
                            if theirs == base:
                                continue
                            return _refuse(unit, f"conflict:{rel}")

                        result = _merge_file_with_git(
                            repo_path, rel, ours, base, theirs, temp
                        )
                        if result.status == "conflict":
                            logger.warning(
                                "pipeline merge: REFUSING clobber on %s for unit %s",
                                rel,
                                unit.unit_id,
                            )
                            return _refuse(unit, f"conflict:{rel}")
                        if result.status == "tool_error":
                            return _refuse(unit, f"merge_tool_error:{rel}")
                        if result.status == "too_large":
                            return _refuse(unit, f"file_too_large:{rel}")
                        merged = result.data
                        assert merged is not None
                        if not account(merged):
                            return _refuse(unit, "total_size_exceeded")
                        merged_files[repo_file] = merged
                        originals[repo_file] = ours
            except OSError as exc:
                logger.warning(
                    "pipeline merge: temporary-file failure for unit %s: %s",
                    unit.unit_id,
                    type(exc).__name__,
                )
                return _refuse(unit, "temporary_file_error")

            if not _commit_with_rollback(merged_files, originals):
                return _refuse(unit, "write_error")

        # Reclaim the worktree (disk safety #62) only after a clean merge.
        do_reclaim(unit.worktree_path)
        return MergeOutcome(unit_id=unit.unit_id, merged=True, detail="merged")

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
