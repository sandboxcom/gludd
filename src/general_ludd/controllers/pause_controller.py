"""In-RAM pause registry backed by a durable, signed store.

A :class:`PauseController` answers one hot-path question — "is this project /
model currently paused?" — in O(1) from an in-memory set, while keeping the
authoritative record set persisted (via :class:`PauseStore`) so the answer
SURVIVES a daemon restart.  Every mutation (``pause`` / ``resume``) rewrites the
whole record set to disk, and a fresh controller rebuilds its sets from that
store on construction — this is what gives restart survival.

Daemon wiring (SLICE 2+): ``daemon.py`` constructs a single process-wide
instance at ``app.state._pause_controller`` and injects it into the gateway,
the event loop, and the agent dispatcher, so ``is_paused()`` gates model
calls, tick claiming, and dispatch for a paused project/model.

Persistence discipline: every mutation (``pause`` / ``resume``) persists the
CANDIDATE record set to the store FIRST and only mutates in-RAM state after
that write succeeds — a failed or crashed write can therefore never diverge
memory from disk (``is_paused()`` never observes a pause/resume that is not
already durable). A crash between the successful persist and the RAM update
simply leaves this process's RAM one step behind — always a state the store
already durably held; a restart re-reads the store and converges to the
latest value.

The controller does not act on the ``resources`` / ``agent_handles`` a record
can carry — capturing and later releasing those live handles is SLICE 3. Here
they are inert fields that round-trip through the store.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from general_ludd.controllers.pause_store import PauseStore

if TYPE_CHECKING:
    from general_ludd.agents.dispatcher import AgentDispatcher
    from general_ludd.agents.hibernation import (
        HibernationController,
        HibernationHandle,
    )
    from general_ludd.agents.types import AgentTask

PauseKind = Literal["project", "model", "task", "agent", "infra"]


class PauseRecord(BaseModel):
    """A single paused entity (a project or a model) and its parked context.

    ``last_state`` / ``resources`` / ``agent_handles`` capture what a later
    slice needs to cleanly park and resume live work; in SLICE 1 they are simply
    persisted and restored unchanged.
    """

    kind: PauseKind
    target_id: str
    paused_at: float
    reason: str = ""
    last_state: dict[str, object] = Field(default_factory=dict)
    resources: dict[str, object] = Field(default_factory=dict)
    agent_handles: list[object] = Field(default_factory=list)
    quiesce_status: str = "none"
    quiesce_errors: list[str] = Field(default_factory=list)


class QuiesceNoopResult(list[object]):
    """Backward-compatible no-op quiesce result.

    Older callers treat the no-dispatcher result as a plain empty list, while
    D.7.3 callers unpack ``handles, status, errors``.  This preserves both
    contracts for the defensive no-subsystem path.
    """

    def __init__(self) -> None:
        """Initialize an empty successful no-op result."""
        super().__init__()
        self.status = "clean"
        self.errors: list[str] = []

    def __iter__(self) -> Iterator[object]:
        """Yield the backward-compatible handles, status, and errors tuple."""
        yield self[:]
        yield self.status
        yield self.errors


class PauseController:
    """Track paused projects/models with O(1) lookup and durable persistence.

    Args:
        store: The :class:`PauseStore` backing persistence.  When omitted a
            default-located store is used.  On construction the controller
            rebuilds ``_paused_projects`` / ``_paused_models`` from
            ``store.load()`` so a restart re-materializes prior pauses.
    """

    def __init__(self, store: PauseStore | None = None) -> None:
        """Load durable pause records and initialize lock-free indexes."""
        self._store = store if store is not None else PauseStore()
        # Guards the record map + persisted write so a concurrent pause/resume
        # cannot lose an update or persist a torn view.
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], PauseRecord] = {}
        # ``resume()`` must durably remove a pause before work restarts, but
        # rehydration is asynchronous and happens immediately afterward. Keep
        # the removed record transiently so ``resume_rehydrate()`` can consume
        # its handles without leaving the entity marked paused.
        self._resumed_records: dict[tuple[str, str], PauseRecord] = {}
        # Hot-path membership — read by is_paused() WITHOUT the lock.  Each is
        # a frozenset that is only ever REBOUND (never mutated in place) under
        # self._lock, after a successful persist.  Attribute rebinding is an
        # atomic reference store, so a lock-free reader always observes either
        # the prior committed frozenset or the new one — never a torn view.
        self._paused_projects: frozenset[str] = frozenset()
        self._paused_models: frozenset[str] = frozenset()
        self._paused_tasks: frozenset[str] = frozenset()
        self._paused_agents: frozenset[str] = frozenset()
        self._paused_infra: frozenset[str] = frozenset()
        self._rebuild_from_store()

    # ------------------------------------------------------------------
    # Construction / rebuild
    # ------------------------------------------------------------------

    def _rebuild_from_store(self) -> None:
        """Re-materialize the in-RAM state from already-persisted records.

        A record that fails validation (e.g. an unknown ``kind`` from a
        forward-incompatible on-disk schema) is skipped rather than crashing the
        whole controller — one bad record must not wedge every pause. Since
        every entry here was already written by a prior successful ``_persist``,
        no further persistence is needed — this only (re)populates RAM.
        """
        records: dict[tuple[str, str], PauseRecord] = {}
        projects: set[str] = set()
        models: set[str] = set()
        tasks: set[str] = set()
        agents: set[str] = set()
        infra: set[str] = set()
        for raw in self._store.load():
            try:
                record = PauseRecord.model_validate(raw)
            except Exception:
                continue
            records[(record.kind, record.target_id)] = record
            if record.kind == "project":
                projects.add(record.target_id)
            elif record.kind == "model":
                models.add(record.target_id)
            elif record.kind == "task":
                tasks.add(record.target_id)
            elif record.kind == "agent":
                agents.add(record.target_id)
            elif record.kind == "infra":
                infra.add(record.target_id)
        self._records = records
        self._paused_projects = frozenset(projects)
        self._paused_models = frozenset(models)
        self._paused_tasks = frozenset(tasks)
        self._paused_agents = frozenset(agents)
        self._paused_infra = frozenset(infra)

    def _set_for(self, kind: str) -> frozenset[str]:
        return {
            "project": self._paused_projects,
            "model": self._paused_models,
            "task": self._paused_tasks,
            "agent": self._paused_agents,
            "infra": self._paused_infra,
        }[kind]

    def _rebind_add(self, kind: str, target_id: str) -> None:
        if kind == "project":
            self._paused_projects = self._paused_projects | {target_id}
        elif kind == "model":
            self._paused_models = self._paused_models | {target_id}
        elif kind == "task":
            self._paused_tasks = self._paused_tasks | {target_id}
        elif kind == "agent":
            self._paused_agents = self._paused_agents | {target_id}
        elif kind == "infra":
            self._paused_infra = self._paused_infra | {target_id}

    def _rebind_discard(self, kind: str, target_id: str) -> None:
        if kind == "project":
            self._paused_projects = self._paused_projects - {target_id}
        elif kind == "model":
            self._paused_models = self._paused_models - {target_id}
        elif kind == "task":
            self._paused_tasks = self._paused_tasks - {target_id}
        elif kind == "agent":
            self._paused_agents = self._paused_agents - {target_id}
        elif kind == "infra":
            self._paused_infra = self._paused_infra - {target_id}

    def _persist(self, candidate: dict[tuple[str, str], PauseRecord]) -> None:
        """Durably write *candidate* — the would-be post-mutation record set.

        Called under self._lock, BEFORE self._records / the membership
        frozensets are mutated. Raises (propagating the store's exception,
        e.g. :class:`PauseStoreError`) on a write failure, in which case the
        caller must NOT touch in-RAM state — leaving memory exactly as it was
        (a state the store already durably held) is what keeps is_paused()
        from ever observing a non-durable mutation.
        """
        self._store.save([r.model_dump() for r in candidate.values()])

    # ------------------------------------------------------------------
    # D.7.3 — quiesce at dispatcher seam + rehydrating resume
    # ------------------------------------------------------------------

    async def quiesce_project(
        self,
        project_id: str,
        dispatcher: AgentDispatcher | None = None,
        hibernation: HibernationController | None = None,
    ) -> tuple[list[HibernationHandle], str, list[str]] | QuiesceNoopResult:
        """Quiesce in-flight agents at the dispatcher boundary before pausing.

        Two-phase: (1) drain/cancel in-flight tasks via the dispatcher,
        (2) dehydrate their state via hibernation for later resume.

        Returns a 3-tuple ``(handles, status, errors)`` where *status* is
        ``"clean"`` when all tasks quiesce without error, or ``"degraded"``
        when some fail. *errors* lists per-task failure messages.

        When *dispatcher* or *hibernation* is ``None`` returns an empty list
        with status ``"clean"`` — graceful degradation for daemon boots where
        these subsystems are not yet wired.
        """
        if dispatcher is None or hibernation is None:
            return QuiesceNoopResult()
        from general_ludd.agents.hibernation import AgentEnvironmentSnapshot

        await dispatcher.quiesce_project(project_id)

        tasks = await dispatcher.get_active_tasks_for_project(project_id)
        handles: list[HibernationHandle] = []
        errors: list[str] = []
        for task in tasks:
            try:
                snap = AgentEnvironmentSnapshot(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    parent_task_id=task.parent_task_id,
                    invoker_name=task.invoker_name,
                    depth=getattr(task, "depth", 0),
                    messages=getattr(task, "messages", []),
                    scratch={
                        "description": getattr(task, "description", ""),
                        "prompt": getattr(task, "prompt", ""),
                        "project_id": project_id,
                    },
                )
                handle = await hibernation._store.dehydrate_async(snap)
                handles.append(handle)
            except Exception as exc:
                errors.append(f"{task.task_id}: {exc}")
        status = "clean" if not errors else "degraded"
        return handles, status, errors

    async def quiesce_entity(
        self,
        kind: PauseKind,
        target_id: str,
        dispatcher: AgentDispatcher | None = None,
        hibernation: HibernationController | None = None,
    ) -> tuple[list[HibernationHandle], str, list[str]] | QuiesceNoopResult:
        """Quiesce a single agent or task before pausing, capturing depth.

        Finds the active task matching ``(kind, target_id)`` in the
        dispatcher, dehydrates it with its current depth, and returns the
        handle.  Used by agent-level and task-level pause so a resumed
        entity continues at its prior depth — closing the recursion-guard
        bypass where a resumed agent would restart at depth 0.
        """
        if dispatcher is None or hibernation is None:
            return QuiesceNoopResult()
        from general_ludd.agents.hibernation import AgentEnvironmentSnapshot

        tasks: list[AgentTask] = []
        if kind == "agent":
            tasks = await dispatcher.get_active_tasks_by_agent_name(target_id)
        elif kind == "task":
            tasks = await dispatcher.get_active_tasks_by_task_id(target_id)

        handles: list[HibernationHandle] = []
        errors: list[str] = []
        for task in tasks:
            try:
                snap = AgentEnvironmentSnapshot(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    parent_task_id=task.parent_task_id,
                    invoker_name=task.invoker_name,
                    depth=getattr(task, "depth", 0),
                    messages=getattr(task, "messages", []),
                    scratch={
                        "description": getattr(task, "description", ""),
                        "prompt": getattr(task, "prompt", ""),
                        "project_id": getattr(task, "project_id", "") or "",
                    },
                )
                handle = await hibernation._store.dehydrate_async(snap)
                handles.append(handle)
            except Exception as exc:
                errors.append(f"{task.task_id}: {exc}")
        status = "clean" if not errors else "degraded"
        return handles, status, errors

    async def resume_rehydrate(
        self,
        kind: PauseKind,
        target_id: str,
        dispatcher: AgentDispatcher | None = None,
        hibernation: HibernationController | None = None,
    ) -> tuple[list[object], str, list[str]]:
        """Rehydrate paused agents and re-enqueue them after resume.

        Reads the pause record for ``(kind, target_id)``, rehydrates every
        saved handle, and re-enqueues the resulting tasks via the dispatcher.

        Returns a 3-tuple ``(snapshots, status, errors)``.
        """
        key = (kind, target_id)
        with self._lock:
            record = self._records.get(key) or self._resumed_records.get(key)
        if record is None:
            return [], "clean", []
        if dispatcher is None or hibernation is None:
            return [], "clean", []
        from general_ludd.agents.hibernation import (
            AgentEnvironmentSnapshot,
            HibernationHandle,
        )

        snapshots: list[AgentEnvironmentSnapshot] = []
        errors: list[str] = []
        for raw_handle in record.agent_handles:
            try:
                if isinstance(raw_handle, HibernationHandle):
                    handle = raw_handle
                elif isinstance(raw_handle, dict):
                    handle = HibernationHandle.model_validate(raw_handle)
                else:
                    continue
                snap = await hibernation._store.hydrate_async(handle)
                snapshots.append(snap)
            except Exception as exc:
                errors.append(f"rehydrate {getattr(raw_handle, 'task_id', '?')}: {exc}")
        snapshots_by_project: dict[str, list[AgentEnvironmentSnapshot]] = {}
        for snapshot in snapshots:
            raw_project_id = snapshot.scratch.get("project_id")
            project_id = (
                raw_project_id
                if isinstance(raw_project_id, str) and raw_project_id
                else target_id
            )
            snapshots_by_project.setdefault(project_id, []).append(snapshot)
        for project_id, project_snapshots in snapshots_by_project.items():
            await dispatcher.resume_project(project_id, project_snapshots)
        status = "clean" if not errors else "degraded"
        if not errors:
            with self._lock:
                self._resumed_records.pop(key, None)
        return list(snapshots), status, errors

    def _record_quiesce(
        self,
        kind: PauseKind,
        target_id: str,
        handles: list[HibernationHandle],
        status: str,
        errors: list[str],
    ) -> None:
        """Update an existing pause record with quiesce results.

        Acquires the lock, persists the updated record set, and rebinds RAM.
        Used after ``quiesce_project`` to attach dehydrated handles to the
        pause record so they survive a restart.
        """
        key = (kind, target_id)
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return
            record.quiesce_status = status
            record.quiesce_errors = errors
            record.agent_handles = [h.model_dump() for h in handles]
            candidate = dict(self._records)
            self._persist(candidate)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def pause(
        self,
        kind: PauseKind,
        target_id: str,
        *,
        reason: str = "",
        resources: dict[str, object] | None = None,
        last_state: dict[str, object] | None = None,
        agent_handles: list[object] | None = None,
        quiesce_status: str = "none",
        quiesce_errors: list[str] | None = None,
    ) -> PauseRecord:
        """Mark ``(kind, target_id)`` paused, persist, and return its record.

        Idempotent: re-pausing an already-paused entity returns the existing
        record unchanged (its original ``paused_at`` is preserved) and does not
        rewrite the store.

        *resources* and *last_state* are SLICE 3 capture fields: callers
        snapshot live daemon state (spend facet, leases, registries) at pause
        time so a later resume can restore context.

        *agent_handles* (SLICE 3b) are :class:`HibernationHandle` references
        for agents that were quiesced (dehydrated) when the project was paused.
        They are stored in the record so a later resume can rehydrate them.

        Persist-before-mutate: the candidate record set (current + this new
        record) is written to the store FIRST; ``self._records`` and the
        membership frozenset are only updated after that write succeeds. If
        the store raises (e.g. a disk failure), this method propagates the
        exception and leaves in-RAM state untouched — ``is_paused()`` keeps
        reporting the entity as NOT paused, matching what is durably on disk.
        """
        key = (kind, target_id)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                return existing
            stored_agent_handles = (
                agent_handles[:]
                if isinstance(agent_handles, QuiesceNoopResult)
                else agent_handles
                if agent_handles is not None
                else []
            )
            record = PauseRecord(
                kind=kind,
                target_id=target_id,
                paused_at=time.time(),
                reason=reason,
                resources=resources or {},
                last_state=last_state or {},
                agent_handles=stored_agent_handles,
                quiesce_status=quiesce_status,
                quiesce_errors=quiesce_errors if quiesce_errors is not None else [],
            )
            candidate = dict(self._records)
            candidate[key] = record
            self._persist(candidate)  # raises -> in-RAM state stays untouched
            self._records = candidate
            self._resumed_records.pop(key, None)
            self._rebind_add(kind, target_id)
            return record

    def resume(self, kind: PauseKind, target_id: str) -> PauseRecord | None:
        """Clear the pause on ``(kind, target_id)``, persist, and return it.

        Idempotent: returns ``None`` when the entity was not paused (a
        double-resume is a no-op, not an error).

        Persist-before-mutate: the candidate record set (current minus this
        record) is written to the store FIRST; ``self._records`` and the
        membership frozenset are only updated after that write succeeds. If
        the store raises, this method propagates the exception and leaves
        in-RAM state untouched — ``is_paused()`` keeps reporting the entity as
        PAUSED, matching what is durably on disk. A crash in this exact window
        (write succeeded, in-RAM update not yet applied) leaves this process
        showing "paused" one step behind disk's "resumed"; a restart re-reads
        the store and converges to resumed.
        """
        key = (kind, target_id)
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return None
            candidate = dict(self._records)
            del candidate[key]
            self._persist(candidate)  # raises -> in-RAM state stays untouched
            self._records = candidate
            if record.agent_handles:
                self._resumed_records[key] = record
            self._rebind_discard(kind, target_id)
            return record

    def is_paused(self, kind: PauseKind, target_id: str) -> bool:
        """O(1) hot-path check — is ``(kind, target_id)`` currently paused?

        Lock-free by design (called from hot paths in the gateway/dispatcher/
        event loop). Invariant: this NEVER observes a pause/resume mutation
        that is not already durable — ``pause()``/``resume()`` only rebind the
        membership frozenset here after their store write has succeeded, and
        rebinding a frozenset reference is atomic, so a concurrent reader sees
        either the previous durable state or the new durable state, never a
        partial/torn one.
        """
        return target_id in self._set_for(kind)

    def list_paused(self, kind: PauseKind | None = None) -> list[PauseRecord]:
        """Return current pause records, optionally filtered by *kind*."""
        with self._lock:
            if kind is None:
                return list(self._records.values())
            return [r for r in self._records.values() if r.kind == kind]

    def get(self, kind: PauseKind, target_id: str) -> PauseRecord | None:
        """Return the record for ``(kind, target_id)``, or ``None``."""
        with self._lock:
            return self._records.get((kind, target_id))
