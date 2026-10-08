"""Bounded monotonic worker-liveness policy shared by broadcast operations."""

from __future__ import annotations

import os

MAX_WORKERS = 64
NANOSECONDS_PER_SECOND = 1_000_000_000
LIVENESS_PROBE_TIMEOUT_SECONDS = 1.0
LIVENESS_TOTAL_TIMEOUT_SECONDS = 10.0


def enforcement_enabled() -> bool:
    """Return false only for the explicit emergency rollback value ``0``."""
    return os.environ.get("GLUDD_WORKER_LIVENESS_ENFORCE", "1").strip() != "0"


def probe_timeout_seconds(remaining_ns: int) -> float:
    """Clamp one health probe to its per-request and shared-wave budgets."""
    return min(
        LIVENESS_PROBE_TIMEOUT_SECONDS,
        remaining_ns / NANOSECONDS_PER_SECOND,
    )


def probe_deadline_ns(now_ns: int) -> int:
    """Return the shared monotonic deadline for one bounded probe wave."""
    return now_ns + int(LIVENESS_TOTAL_TIMEOUT_SECONDS * NANOSECONDS_PER_SECOND)
