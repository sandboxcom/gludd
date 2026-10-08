"""Shared constants and initialized state for generation-store layers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from general_ludd.decision_codification.artifact_store import (
    DecisionArtifactStore,
)
from general_ludd.decision_codification.rollout import (
    RolloutError,
)

_STATE_SCHEMA: Final[str] = "gludd.decision-shared-generation-state/v1"
_HEAD_NAMESPACE: Final[str] = "decision-shared-generation-v1"
_CANDIDATE_NAMESPACE_PREFIX: Final[str] = "decision-generation-candidate:"
_LEASE_DOMAIN: Final[bytes] = b"general_ludd.decision_generation.lease.v1\x00"
_SAFE_IDENTIFIER: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
)
_SHA256: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_RECORD_BYTES: Final[int] = 64 * 1024
_MAX_HISTORY: Final[int] = 256
_MAX_APPLICATIONS: Final[int] = 100_000
_MAX_RECENT_OUTCOMES: Final[int] = 100


class SharedGenerationStoreError(RolloutError):
    """Refuse a shared-state operation whose identity cannot be trusted."""


class _PostgresGenerationState:
    """Serialize exact generation transitions through existing SQLAlchemy rows.

    PostgreSQL's unique ``bucket_leases.bucket_key`` constraint provides bounded
    cross-host admission. Project-scoped ``variable_values`` rows hold only
    HMAC-authenticated, digest-only state. Every transition, fencing lease, and
    compare-and-swap check is committed or rolled back in one transaction.
    """

    def __init__(
        self,
        engine: Engine,
        artifacts: DecisionArtifactStore,
        *,
        lease_ttl_seconds: int = 30,
        lock_timeout_seconds: float = 10.0,
        clock: Callable[[], datetime] | None = None,
        allow_sqlite_for_tests: bool = False,
    ) -> None:
        """Bind one shared database and the existing artifact trust root."""
        dialect = engine.dialect.name
        if dialect != "postgresql" and not (
            allow_sqlite_for_tests and dialect == "sqlite"
        ):
            raise ValueError("shared generation state requires PostgreSQL")
        if (
            type(lease_ttl_seconds) is not int
            or not 1 <= lease_ttl_seconds <= 60
        ):
            raise ValueError("generation lease TTL must be between 1 and 60 seconds")
        if (
            isinstance(lock_timeout_seconds, bool)
            or not isinstance(lock_timeout_seconds, (int, float))
            or not 0 < lock_timeout_seconds <= 60
        ):
            raise ValueError("generation lock timeout must be between zero and 60 seconds")
        self._engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._artifacts = artifacts
        self._lease_ttl = lease_ttl_seconds
        self._lock_timeout = float(lock_timeout_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def engine(self) -> Engine:
        """Return the SQLAlchemy engine used for shared state."""
        return self._engine

    def coordination_key(self, project_id: str) -> str:
        """Return one opaque project lease key without exposing scope text."""
        if not isinstance(project_id, str) or _SAFE_IDENTIFIER.fullmatch(project_id) is None:
            raise SharedGenerationStoreError(
                "shared generation project scope is invalid"
            )
        digest = hashlib.sha256(
            _LEASE_DOMAIN + project_id.encode("utf-8")
        ).hexdigest()
        return f"decision-generation:{digest}"
