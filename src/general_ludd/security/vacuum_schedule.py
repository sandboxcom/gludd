"""MemoryRecord table VACUUM schedule with leader election and rate limiting.

D-26: prevents SQLite fragmentation from sustained write load on the
MemoryRecordModel table by periodically running VACUUM, with a
leader-election lock so only one worker vacuums at a time and a
rate-limit to avoid too-frequent full-table rewrites.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

DEFAULT_MIN_INTERVAL_SEC = 1800.0
DEFAULT_LEADER_LOCK_TIMEOUT_SEC = 300.0


@dataclass
class VacuumResult:
    ran: bool
    skipped_reason: str = ""
    elapsed_sec: float = 0.0
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


class VacuumScheduler:
    def __init__(
        self,
        min_interval_sec: float = DEFAULT_MIN_INTERVAL_SEC,
        leader_lock_timeout_sec: float = DEFAULT_LEADER_LOCK_TIMEOUT_SEC,
    ) -> None:
        self._min_interval_sec = min_interval_sec
        self._leader_lock_timeout_sec = leader_lock_timeout_sec
        self._last_vacuum_epoch: float = 0.0
        self._leader_lock_epoch: float = 0.0

    def should_vacuum(self, now_epoch: float | None = None) -> bool:
        now = now_epoch if now_epoch is not None else time.time()
        return (now - self._last_vacuum_epoch) >= self._min_interval_sec

    def try_acquire_leader(self, now_epoch: float | None = None) -> bool:
        now = now_epoch if now_epoch is not None else time.time()
        if self._leader_lock_epoch == 0.0 or (now - self._leader_lock_epoch) > self._leader_lock_timeout_sec:
            self._leader_lock_epoch = now
            return True
        return False

    def release_leader(self) -> None:
        self._leader_lock_epoch = 0.0

    def vacuum_memory_table(self, session: Session) -> VacuumResult:
        now = time.time()

        if not self.should_vacuum(now_epoch=now):
            remaining = self._min_interval_sec - (now - self._last_vacuum_epoch)
            return VacuumResult(
                ran=False,
                skipped_reason=(f"rate-limited: {remaining:.0f}s until next allowed vacuum"),
            )

        if not self.try_acquire_leader(now_epoch=now):
            return VacuumResult(
                ran=False,
                skipped_reason="leader-election: another worker holds the vacuum lock",
            )

        try:
            t0 = time.monotonic()
            session.execute(text("VACUUM"))
            elapsed = time.monotonic() - t0
            self._last_vacuum_epoch = time.time()
            return VacuumResult(ran=True, elapsed_sec=elapsed)
        finally:
            self.release_leader()

    @property
    def last_vacuum_epoch(self) -> float:
        return self._last_vacuum_epoch

    @property
    def min_interval_sec(self) -> float:
        return self._min_interval_sec

    @property
    def leader_lock_timeout_sec(self) -> float:
        return self._leader_lock_timeout_sec
