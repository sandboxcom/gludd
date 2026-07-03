"""In-RAM pause registry backed by a durable, signed store.

A :class:`PauseController` answers one hot-path question — "is this project /
model currently paused?" — in O(1) from an in-memory set, while keeping the
authoritative record set persisted (via :class:`PauseStore`) so the answer
SURVIVES a daemon restart.  Every mutation (``pause`` / ``resume``) rewrites the
whole record set to disk, and a fresh controller rebuilds its sets from that
store on construction — this is what gives restart survival.

SLICE 1 scope: the controller is pure (no daemon wiring, no app.state) and does
not act on the ``resources`` / ``agent_handles`` a record can carry — capturing
and later releasing those live handles is SLICE 3.  Here they are inert fields
that round-trip through the store.
"""

from __future__ import annotations

import threading
import time
from typing import Literal

from pydantic import BaseModel, Field

from general_ludd.controllers.pause_store import PauseStore

PauseKind = Literal["project", "model"]


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


class PauseController:
    """Track paused projects/models with O(1) lookup and durable persistence.

    Args:
        store: The :class:`PauseStore` backing persistence.  When omitted a
            default-located store is used.  On construction the controller
            rebuilds ``_paused_projects`` / ``_paused_models`` from
            ``store.load()`` so a restart re-materializes prior pauses.
    """

    def __init__(self, store: PauseStore | None = None) -> None:
        self._store = store if store is not None else PauseStore()
        # Guards the record map + persisted write so a concurrent pause/resume
        # cannot lose an update or persist a torn view.
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], PauseRecord] = {}
        # Hot-path membership sets — read by is_paused() without the lock.
        self._paused_projects: set[str] = set()
        self._paused_models: set[str] = set()
        self._rebuild_from_store()

    # ------------------------------------------------------------------
    # Construction / rebuild
    # ------------------------------------------------------------------

    def _rebuild_from_store(self) -> None:
        """Re-materialize the in-RAM sets from persisted records.

        A record that fails validation (e.g. an unknown ``kind`` from a
        forward-incompatible on-disk schema) is skipped rather than crashing the
        whole controller — one bad record must not wedge every pause.
        """
        for raw in self._store.load():
            try:
                record = PauseRecord.model_validate(raw)
            except Exception:  # tolerate a single bad record, don't wedge all
                continue
            self._index(record)

    def _index(self, record: PauseRecord) -> None:
        self._records[(record.kind, record.target_id)] = record
        self._set_for(record.kind).add(record.target_id)

    def _set_for(self, kind: str) -> set[str]:
        return self._paused_projects if kind == "project" else self._paused_models

    def _persist(self) -> None:
        # Called under self._lock.
        self._store.save([r.model_dump() for r in self._records.values()])

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
    ) -> PauseRecord:
        """Mark ``(kind, target_id)`` paused, persist, and return its record.

        Idempotent: re-pausing an already-paused entity returns the existing
        record unchanged (its original ``paused_at`` is preserved) and does not
        rewrite the store.

        *resources* and *last_state* are SLICE 3 capture fields: callers
        snapshot live daemon state (spend facet, leases, registries) at pause
        time so a later resume can restore context.
        """
        key = (kind, target_id)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                return existing
            record = PauseRecord(
                kind=kind,
                target_id=target_id,
                paused_at=time.time(),
                reason=reason,
                resources=resources or {},
                last_state=last_state or {},
            )
            self._index(record)
            self._persist()
            return record

    def resume(self, kind: PauseKind, target_id: str) -> PauseRecord | None:
        """Clear the pause on ``(kind, target_id)``, persist, and return it.

        Idempotent: returns ``None`` when the entity was not paused (a
        double-resume is a no-op, not an error).
        """
        key = (kind, target_id)
        with self._lock:
            record = self._records.pop(key, None)
            if record is None:
                return None
            self._set_for(kind).discard(target_id)
            self._persist()
            return record

    def is_paused(self, kind: PauseKind, target_id: str) -> bool:
        """O(1) hot-path check — is ``(kind, target_id)`` currently paused?"""
        return target_id in self._set_for(kind)

    def list_paused(self) -> list[PauseRecord]:
        """Return all current pause records (projects and models)."""
        with self._lock:
            return list(self._records.values())

    def get(self, kind: PauseKind, target_id: str) -> PauseRecord | None:
        """Return the record for ``(kind, target_id)``, or ``None``."""
        with self._lock:
            return self._records.get((kind, target_id))
