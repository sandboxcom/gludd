"""SQLite-backed multiworker generation and application-outcome state."""

from __future__ import annotations

import os
from pathlib import Path

from general_ludd.decision_codification.durable_feedback import (
    _DurableFeedbackStore,
)
from general_ludd.decision_codification.durable_storage import (
    DurableGenerationStoreError,
)


class DurableGenerationStore(_DurableFeedbackStore):
    """Cross-process SQLite implementation of the generation-store contract.

    Every mutation uses ``BEGIN IMMEDIATE`` and every reader opens a fresh
    connection, so independent daemon workers observe committed pointer, use,
    drift, revocation, rollback, and outcome state without process-local caches.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        busy_timeout_seconds: float = 10.0,
    ) -> None:
        """Open or create one fail-closed, versioned durable state database."""
        if (
            isinstance(busy_timeout_seconds, bool)
            or not isinstance(busy_timeout_seconds, (int, float))
            or not 0 < busy_timeout_seconds <= 60
        ):
            raise ValueError("busy timeout must be between zero and 60 seconds")
        raw_path = Path(path)
        if raw_path.exists() and raw_path.is_symlink():
            raise DurableGenerationStoreError(
                "durable state path must not be a symlink"
            )
        raw_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path = raw_path.absolute()
        self._timeout = float(busy_timeout_seconds)
        self._initialize()

    @property
    def path(self) -> Path:
        """Return the configured shared state path."""
        return self._path


__all__ = ["DurableGenerationStore", "DurableGenerationStoreError"]
