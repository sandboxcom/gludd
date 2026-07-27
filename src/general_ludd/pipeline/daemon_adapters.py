"""Daemon-side adapters that bind the pipeline lanes to real subsystems (#77).

These factory functions translate the daemon's concrete subsystems
(AgentDispatcher #42-style, safe_merge #70, git_repo_lock #63, worktree reclaim
#62) into the three injected callables the lanes expect:

  * :func:`make_dispatch_fn`   -> ``DispatchFn``  (launch a role-agent)
  * :func:`make_merge_fn`      -> ``MergeFn``     (safe_merge under the repo lock,
                                                   reclaim the worktree on green)
  * :func:`make_disk_ok`       -> disk-pressure predicate for back-pressure

The merge adapter is deliberately conservative: it merges the agent's changed
files into the repo with :func:`safe_merge_file`, which REFUSES to write a file
on conflict — so a clobber is surfaced as ``clobber_refused`` and the worktree
is left intact for resolution, never silently overwritten.

Kept import-light and free of FastAPI so it is unit-testable in isolation and so
``daemon.py`` only needs a thin call.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from typing import Any

from general_ludd.git_automation.locking import git_repo_lock
from general_ludd.integration.safe_merge import safe_merge
from general_ludd.pipeline.state import CompletedUnit, MergeOutcome

logger = logging.getLogger(__name__)

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


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _read_fork_point(repo_path: str, base_sha: str, relpath: str) -> str | None:
    """Read the file content at *base_sha* via ``git show``.

    Returns ``None`` when the file did not exist at that commit (e.g. new file
    added by the agent) or the git command fails, so the caller falls back to
    the repo-as-base safe_merge path.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", repo_path, "show", f"{base_sha}:{relpath}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout
        return None
    except Exception:
        return None


def make_merge_fn(
    repo_path: str,
    *,
    changed_files: Callable[[CompletedUnit], list[str]] | None = None,
    reclaim: Callable[[str], None] | None = None,
) -> Callable[[CompletedUnit], Awaitable[MergeOutcome]]:
    """Build a ``MergeFn`` that merges a worktree into ``repo_path`` safely.

    For each file the agent changed (relative path), the adapter 3-way-merges
    the worktree version against the repo version using ``safe_merge``. The
    BASE for the merge is the repo's current content (so a divergent base edit
    is detected as a conflict rather than clobbered) — i.e. base==ours==repo,
    theirs==worktree; if both the repo moved and the worktree moved the same
    region, ``safe_merge`` flags a conflict and we REFUSE.

    On a fully clean merge across all files, the merged text is written back to
    the repo and the worktree is reclaimed (``git worktree remove`` via
    ``reclaim``). On ANY conflict, NOTHING is written and the worktree is left
    intact — ``clobber_refused=True``.

    The whole merge runs under ``git_repo_lock(repo_path)`` so it never races a
    concurrent git mutation on the shared tree (#63).
    """

    def _default_changed(unit: CompletedUnit) -> list[str]:
        # Best-effort: ask git for the worktree's changed tracked files.
        import subprocess

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
        import subprocess

        try:
            subprocess.run(
                ["git", "-C", repo_path, "worktree", "remove", "--force", "--", worktree_path],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception:  # pragma: no cover
            with __import__("contextlib").suppress(OSError):
                shutil.rmtree(worktree_path, ignore_errors=True)

    do_reclaim = reclaim or _default_reclaim

    def _read_base(repo: str, unit: CompletedUnit, rel: str) -> str | None:
        if not unit.base_sha:
            return None
        import subprocess

        try:
            result = subprocess.run(
                ["git", "-C", repo, "show", f"{unit.base_sha}:{rel}"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout
        except Exception:
            pass
        return None

    def _merge_sync(unit: CompletedUnit) -> MergeOutcome:
        files = list_changed(unit)
        if not files:
            # Nothing to merge: treat as a clean no-op merge so the unit
            # advances to the gate and its worktree is reclaimed.
            do_reclaim(unit.worktree_path)
            return MergeOutcome(unit_id=unit.unit_id, merged=True, detail="empty")

        merged_texts: dict[str, str] = {}
        with git_repo_lock(repo_path):
            for rel in files:
                repo_file = os.path.join(repo_path, rel)
                wt_file = os.path.join(unit.worktree_path, rel)
                repo_text = _read(repo_file)
                wt_text = _read(wt_file)
                if wt_text is None:
                    # Worktree file vanished — skip; cannot merge what's gone.
                    continue
                if repo_text is None:
                    # New file added by the agent: no base to clobber.
                    merged_texts[repo_file] = wt_text
                    continue
                base_text: str | None = _read_base(repo_path, unit, rel)
                result = safe_merge(base_text or repo_text, repo_text, wt_text)
                if result.conflict:
                    logger.warning(
                        "pipeline merge: REFUSING clobber on %s for unit %s",
                        rel,
                        unit.unit_id,
                    )
                    return MergeOutcome(
                        unit_id=unit.unit_id,
                        merged=False,
                        clobber_refused=True,
                        detail=f"conflict:{rel}",
                    )
                merged_texts[repo_file] = result.text

            # All files merged cleanly — commit the writes inside the lock.
            for path, text in merged_texts.items():
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(text)

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
    """Build a ``PidProvider`` that evaluates gludd's existing load/PID
    controller (:class:`LoadController`) into ``ControllerOutputs`` for the
    DispatchLane's keep-N-busy loop.

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
