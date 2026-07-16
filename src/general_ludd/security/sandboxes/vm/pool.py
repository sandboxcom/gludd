"""VM sandbox pool manager — NF.2 P8 pre-warmed instance pool.

Wraps :class:`~general_ludd.security.sandboxes.vm.lifecycle.VMSandboxManager`
to maintain a pool of pre-warmed VM instances ready for immediate checkout.
Eliminates the ~hundreds-of-ms boot latency on the dispatch hot path by
booting instances ahead of demand and reaping them when idle.

Capabilities:

* :meth:`VMSandboxPool.prewarm` — boot ``prewarm_count`` instances up front
  so the first checkouts are instant.
* :meth:`VMSandboxPool.checkout` — lease an idle RUNNING instance; auto-boots
  a replacement when the available count drops below ``min_idle`` (bounded by
  ``max_size``).
* :meth:`VMSandboxPool.return_instance` — return a checked-out instance to
  the available pool (idempotent).
* :meth:`VMSandboxPool.auto_scale` — top up the pool to ``min_idle`` (capped
  at ``max_size``); called automatically on checkout and safe to call on a
  timer.
* :meth:`VMSandboxPool.reap_idle` — release instances idle longer than
  ``idle_timeout_seconds``, never dropping below ``min_idle``.
* :meth:`VMSandboxPool.stats` — snapshot for observability endpoints.

See ``docs/specs/FEATURE_UNIKERNEL_SANDBOX.md`` §4 P3 (pool layer).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import SandboxTarget
from general_ludd.security.sandboxes.vm.lifecycle import (
    VMLifecycleState,
    VMSandboxManager,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class PoolConfig:
    """Tuning knobs for :class:`VMSandboxPool`.

    ``min_idle`` is the floor the pool tries to maintain available (auto-scale
    target). ``max_size`` is the hard cap on total live instances (available +
    checked-out). ``prewarm_count`` is how many to boot on :meth:`prewarm`
    (usually == ``min_idle`` but may be lower for lazy warm-up).
    ``idle_timeout_seconds`` is the staleness threshold for :meth:`reap_idle`.
    """

    min_idle: int = 1
    max_size: int = 5
    prewarm_count: int = 1
    idle_timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.max_size < 1:
            raise ValueError(
                f"max_size must be >= 1 (got {self.max_size})"
            )
        if self.min_idle < 0:
            raise ValueError(
                f"min_idle must be >= 0 (got {self.min_idle})"
            )
        if self.min_idle > self.max_size:
            raise ValueError(
                f"min_idle ({self.min_idle}) cannot exceed max_size "
                f"({self.max_size})"
            )
        if self.prewarm_count < 0:
            raise ValueError(
                f"prewarm_count must be >= 0 (got {self.prewarm_count})"
            )
        if self.prewarm_count > self.max_size:
            self.prewarm_count = self.max_size
        if self.idle_timeout_seconds < 0:
            raise ValueError(
                f"idle_timeout_seconds must be >= 0 "
                f"(got {self.idle_timeout_seconds})"
            )


@dataclass
class PoolStats:
    """Point-in-time snapshot of pool state for observability."""

    available: int = 0
    checked_out: int = 0
    failed: int = 0
    total: int = 0
    min_idle: int = 0
    max_size: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "available": self.available,
            "checked_out": self.checked_out,
            "failed": self.failed,
            "total": self.total,
            "min_idle": self.min_idle,
            "max_size": self.max_size,
        }


class VMSandboxPool:
    """Pre-warmed pool of VM sandbox instances.

    Holds available (idle, ready) and checked-out (in-use) instance IDs over
    a backing :class:`VMSandboxManager`. Failed instances are quarantined in
    ``_failed`` so checkout never hands out a broken VM.

    Thread-unsafe by design — the daemon serialises mutations through the
    event loop, matching the underlying manager's contract.
    """

    def __init__(
        self,
        backend_name: str,
        spec: PermissionSpec,
        target: SandboxTarget,
        config: PoolConfig | None = None,
        manager: VMSandboxManager | None = None,
    ) -> None:
        self.backend_name = backend_name
        self.spec = spec
        self.target = target
        self.config = config if config is not None else PoolConfig()
        self.manager = manager if manager is not None else VMSandboxManager()

        self._available: deque[str] = deque()
        self._checked_out: set[str] = set()
        self._failed: set[str] = set()
        self._last_used: dict[str, float] = {}
        self._shutdown: bool = False

    # ------------------------------------------------------------------
    # Pre-warm
    # ------------------------------------------------------------------

    def prewarm(self) -> int:
        """Boot up to ``prewarm_count`` instances if not already warmed.

        Idempotent: only boots the deficit between current available count
        and ``prewarm_count``. Returns the number of instances booted.
        """
        if self._shutdown:
            return 0
        deficit = self.config.prewarm_count - len(self._available)
        if deficit <= 0:
            return 0
        booted = 0
        for _ in range(deficit):
            if self._total_live() >= self.config.max_size:
                break
            if self._boot_one():
                booted += 1
        if booted:
            logger.debug(
                "VMSandboxPool.prewarm: booted %d instance(s) "
                "(available=%d, total=%d)",
                booted,
                len(self._available),
                self._total_live(),
            )
        return booted

    # ------------------------------------------------------------------
    # Checkout / return
    # ------------------------------------------------------------------

    def checkout(self) -> str:
        """Lease an available instance and return its id.

        Auto-scales (boots a replacement) when the post-checkout available
        count drops below ``min_idle``. Raises :class:`RuntimeError` when no
        instance is available and the pool is at ``max_size``.
        """
        if self._shutdown:
            raise RuntimeError("pool is shut down")
        if not self._available:
            self.auto_scale()
        if not self._available:
            raise RuntimeError(
                f"no available VM instances (total={self._total_live()}, "
                f"max_size={self.config.max_size})"
            )
        iid = self._available.popleft()
        self._checked_out.add(iid)
        self._last_used[iid] = time.monotonic()

        if len(self._available) < self.config.min_idle:
            self.auto_scale()
        return iid

    def return_instance(self, instance_id: str) -> None:
        """Return a checked-out instance to the available pool.

        Idempotent: returning an instance that is already available (or was
        never checked out) is a no-op. Raises :class:`KeyError` only when the
        instance id is not registered with the underlying manager at all.
        """
        if instance_id not in self.manager.instances:
            raise KeyError(
                f"instance {instance_id!r} not registered with manager"
            )
        if instance_id in self._failed:
            return
        if instance_id not in self._checked_out:
            return
        self._checked_out.discard(instance_id)
        self._available.append(instance_id)
        self._last_used[instance_id] = time.monotonic()

    # ------------------------------------------------------------------
    # Auto-scale
    # ------------------------------------------------------------------

    def auto_scale(self) -> int:
        """Top up available instances to ``min_idle`` (capped at ``max_size``).

        Returns the number of instances booted. Safe to call on a timer.
        """
        if self._shutdown:
            return 0
        booted = 0
        while len(self._available) < self.config.min_idle:
            if self._total_live() >= self.config.max_size:
                break
            if not self._boot_one():
                break
            booted += 1
        return booted

    # ------------------------------------------------------------------
    # Idle reaping
    # ------------------------------------------------------------------

    def reap_idle(self) -> int:
        """Release instances idle longer than ``idle_timeout_seconds``.

        Never reaps below ``min_idle``. Does not touch checked-out instances.
        Returns the number of instances released.
        """
        if self._shutdown or not self._available:
            return 0
        now = time.monotonic()
        timeout = self.config.idle_timeout_seconds
        keep_floor = self.config.min_idle

        candidates: list[str] = []
        protected: list[str] = []
        for iid in list(self._available):
            last = self._last_used.get(iid, now)
            age = now - last
            if age >= timeout:
                candidates.append(iid)
            else:
                protected.append(iid)

        # Never drop below min_idle: only reap the surplus beyond the floor
        # that is also stale.
        surplus = max(0, len(self._available) - keep_floor)
        to_reap = candidates[:surplus]

        for iid in to_reap:
            self._release_one(iid)
        if to_reap:
            logger.debug(
                "VMSandboxPool.reap_idle: released %d stale instance(s) "
                "(available=%d)",
                len(to_reap),
                len(self._available),
            )
        return len(to_reap)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Release every live instance (available + checked-out). Idempotent."""
        if self._shutdown:
            return
        self._shutdown = True
        all_ids = list(self._available) + list(self._checked_out)
        self._available.clear()
        self._checked_out.clear()
        for iid in all_ids:
            if iid in self._failed:
                continue
            try:
                self.manager.release(iid)
            except Exception:
                logger.warning(
                    "VMSandboxPool.shutdown: release failed for %s",
                    iid,
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def available_count(self) -> int:
        return len(self._available)

    def checked_out_count(self) -> int:
        return len(self._checked_out)

    def failed_count(self) -> int:
        return len(self._failed)

    def stats(self) -> PoolStats:
        return PoolStats(
            available=len(self._available),
            checked_out=len(self._checked_out),
            failed=len(self._failed),
            total=self._total_live(),
            min_idle=self.config.min_idle,
            max_size=self.config.max_size,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _total_live(self) -> int:
        """Total non-failed, non-stopped instances managed by the pool."""
        return len(self._available) + len(self._checked_out)

    def _boot_one(self) -> bool:
        """Boot a single instance via the manager and add it to available.

        Returns False (and quarantines the id in ``_failed``) when the boot
        results in a FAILED-state instance.
        """
        try:
            inst = self.manager.boot(
                self.backend_name, self.spec, self.target
            )
        except Exception:
            logger.warning(
                "VMSandboxPool._boot_one: manager.boot raised",
                exc_info=True,
            )
            return False
        if inst.state is VMLifecycleState.FAILED:
            self._failed.add(inst.instance_id)
            return False
        self._available.append(inst.instance_id)
        self._last_used[inst.instance_id] = time.monotonic()
        return True

    def _release_one(self, instance_id: str) -> None:
        """Release an available instance and remove it from pool bookkeeping."""
        try:
            self._available.remove(instance_id)
        except ValueError:
            return
        self._last_used.pop(instance_id, None)
        try:
            self.manager.release(instance_id)
        except Exception:
            logger.warning(
                "VMSandboxPool._release_one: release failed for %s",
                instance_id,
                exc_info=True,
            )

    def _mark_failed(self, instance_id: str) -> None:
        """Quarantine an instance id as failed (tested via public surface)."""
        self._available = deque(
            iid for iid in self._available if iid != instance_id
        )
        self._checked_out.discard(instance_id)
        self._failed.add(instance_id)


__all__ = [
    "PoolConfig",
    "PoolStats",
    "VMSandboxPool",
]
