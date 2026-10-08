"""Fail-closed blue/green rollback workflow for FreeLLMAPI bridge artifacts.

The workflow tracks immutable artifact generations, atomic switch-back, and
protected-artifact rules without exposing upstream content or worker state.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite
from threading import Condition, RLock
from time import monotonic
from typing import Literal


class RollbackWorkflowError(ValueError):
    """Fail-closed rollback error exposing no task or model content."""


@dataclass(frozen=True, slots=True)
class RollbackArtifact:
    """One immutable artifact belonging to a single generation."""

    artifact_id: str
    generation_id: int
    kind: str


@dataclass(frozen=True, slots=True)
class RollbackGeneration:
    """Immutable identity of one pinned bridge artifact generation."""

    generation_id: int
    artifacts: tuple[str, ...]
    promoted_at: datetime


@dataclass(frozen=True, slots=True)
class RollbackDrainHeartbeat:
    """Content-free progress emitted while a rolled-back generation drains."""

    active_generation_id: int
    draining_generation_id: int
    remaining_leases: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class RollbackDrainResult:
    """Bounded rehearsal result that can never authorize runtime admission."""

    active_generation_id: int
    draining_generation_id: int
    drained: bool
    remaining_leases: int
    switched: bool
    runtime_admitted: bool = False
    candidate_decision: Literal["HOLD"] = "HOLD"


@dataclass
class FreeLLMAPIRollbackWorkflow:
    """Blue/green generation tracking with atomic switch-back."""

    active_generation: RollbackGeneration | None = None
    previous_generation: RollbackGeneration | None = None
    _generations: list[RollbackGeneration] = field(default_factory=list)
    _next_id: int = 1
    max_active_leases: int = 1024
    _active_leases: dict[str, RollbackGeneration] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _condition: Condition = field(
        default_factory=lambda: Condition(RLock()),
        init=False,
        repr=False,
    )
    _draining_generation: RollbackGeneration | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        """Reject ambiguous or unbounded lease registries."""
        if (
            isinstance(self.max_active_leases, bool)
            or not isinstance(self.max_active_leases, int)
            or self.max_active_leases <= 0
        ):
            raise RollbackWorkflowError("max active leases must be positive")

    def promote(self, artifacts: tuple[str, ...]) -> RollbackGeneration:
        """Promote a new green generation and retain the previous blue one."""
        if not artifacts:
            raise RollbackWorkflowError("artifacts must not be empty")
        if len(set(artifacts)) != len(artifacts):
            raise RollbackWorkflowError("artifact ids must be unique")
        for artifact_id in artifacts:
            if not _is_sha256_uri(artifact_id):
                raise RollbackWorkflowError("artifact id must be a sha256 uri")

        with self._condition:
            generation = RollbackGeneration(
                generation_id=self._next_id,
                artifacts=artifacts,
                promoted_at=datetime.now(UTC),
            )
            self._next_id += 1
            self.previous_generation = self.active_generation
            self.active_generation = generation
            self._generations.append(generation)
            return generation

    def rollback(self) -> RollbackGeneration:
        """Atomically switch new work back to the retained generation."""
        with self._condition:
            if self._draining_generation is not None:
                raise RollbackWorkflowError("rollback drain is still pending")
            return self._swap_generations_locked()

    def _swap_generations_locked(self) -> RollbackGeneration:
        """Switch the generation pointer while the workflow lock is held."""
        if self.previous_generation is None:
            raise RollbackWorkflowError("no previous generation to roll back to")
        self.active_generation, self.previous_generation = (
            self.previous_generation,
            self.active_generation,
        )
        assert self.active_generation is not None
        return self.active_generation

    def rollback_and_drain(
        self,
        timeout_seconds: float,
        *,
        heartbeat: Callable[[RollbackDrainHeartbeat], None] | None = None,
        heartbeat_interval_seconds: float = 30.0,
    ) -> RollbackDrainResult:
        """Atomically switch new work and wait a bounded time for old leases."""
        _validate_interval(
            timeout_seconds,
            maximum=3600.0,
            message=(
                "rollback drain timeout must be greater than zero and at most "
                "3600 seconds"
            ),
        )
        _validate_interval(
            heartbeat_interval_seconds,
            maximum=30.0,
            message=(
                "heartbeat interval must be greater than zero and at most 30 seconds"
            ),
        )
        if heartbeat is not None and not callable(heartbeat):
            raise RollbackWorkflowError("heartbeat must be callable")

        started_at = monotonic()
        deadline = started_at + timeout_seconds
        with self._condition:
            switched = self._draining_generation is None
            if switched:
                draining_generation = self.active_generation
                self._swap_generations_locked()
                assert draining_generation is not None
                self._draining_generation = draining_generation
            else:
                draining_generation = self._draining_generation
                assert draining_generation is not None
                assert self.active_generation is not None

            next_heartbeat_at = started_at
            while True:
                now = monotonic()
                remaining_leases = self._lease_count_locked(draining_generation)
                active_generation = self.active_generation
                assert active_generation is not None
                if remaining_leases == 0:
                    self._draining_generation = None
                    return RollbackDrainResult(
                        active_generation_id=active_generation.generation_id,
                        draining_generation_id=draining_generation.generation_id,
                        drained=True,
                        remaining_leases=0,
                        switched=switched,
                    )

                if heartbeat is not None and now >= next_heartbeat_at:
                    next_heartbeat_at = now + heartbeat_interval_seconds
                    heartbeat(
                        RollbackDrainHeartbeat(
                            active_generation_id=active_generation.generation_id,
                            draining_generation_id=draining_generation.generation_id,
                            remaining_leases=remaining_leases,
                            elapsed_seconds=max(0.0, now - started_at),
                        )
                    )
                    continue

                remaining_seconds = deadline - now
                if remaining_seconds <= 0.0:
                    return RollbackDrainResult(
                        active_generation_id=active_generation.generation_id,
                        draining_generation_id=draining_generation.generation_id,
                        drained=False,
                        remaining_leases=remaining_leases,
                        switched=switched,
                    )
                wait_seconds = remaining_seconds
                if heartbeat is not None:
                    wait_seconds = min(wait_seconds, next_heartbeat_at - now)
                self._condition.wait(timeout=wait_seconds)

    def acquire_lease(self, lease_id: str) -> RollbackGeneration:
        """Pin one content-free work lease to the current generation."""
        if not _is_sha256_uri(lease_id):
            raise RollbackWorkflowError("lease identity must be a sha256 uri")
        with self._condition:
            if self.active_generation is None:
                raise RollbackWorkflowError("no active generation")
            if lease_id in self._active_leases:
                raise RollbackWorkflowError("lease identity already active")
            if len(self._active_leases) >= self.max_active_leases:
                raise RollbackWorkflowError("lease capacity exhausted")
            generation = self.active_generation
            self._active_leases[lease_id] = generation
            return generation

    def generation_for_lease(self, lease_id: str) -> RollbackGeneration:
        """Return the immutable generation pinned to an active lease."""
        if not _is_sha256_uri(lease_id):
            raise RollbackWorkflowError("lease identity must be a sha256 uri")
        with self._condition:
            return self._generation_for_lease_locked(lease_id)

    def _generation_for_lease_locked(self, lease_id: str) -> RollbackGeneration:
        """Resolve exact lease ownership while the workflow lock is held."""
        generation = self._active_leases.get(lease_id)
        if generation is None:
            raise RollbackWorkflowError("lease identity is not active")
        return generation

    def release_lease(self, lease_id: str) -> RollbackGeneration:
        """Release exactly one owned lease and return its pinned generation."""
        if not _is_sha256_uri(lease_id):
            raise RollbackWorkflowError("lease identity must be a sha256 uri")
        with self._condition:
            generation = self._generation_for_lease_locked(lease_id)
            del self._active_leases[lease_id]
            self._condition.notify_all()
            return generation

    @contextmanager
    def lease(self, lease_id: str) -> Iterator[RollbackGeneration]:
        """Own one generation lease and release it on every exit path."""
        generation = self.acquire_lease(lease_id)
        try:
            yield generation
        finally:
            self.release_lease(lease_id)

    @property
    def active_lease_count(self) -> int:
        """Expose bounded content-free ownership for lifecycle telemetry."""
        with self._condition:
            return len(self._active_leases)

    def protected_artifacts(self) -> frozenset[str]:
        """Return artifact ids that must not be garbage collected."""
        with self._condition:
            return self._protected_artifacts_locked()

    def _protected_artifacts_locked(self) -> frozenset[str]:
        """Collect protected artifacts while the workflow lock is held."""
        protected: set[str] = set()
        if self.active_generation is not None:
            protected.update(self.active_generation.artifacts)
        if self.previous_generation is not None:
            protected.update(self.previous_generation.artifacts)
        if self._draining_generation is not None:
            protected.update(self._draining_generation.artifacts)
        for generation in self._active_leases.values():
            protected.update(generation.artifacts)
        return frozenset(protected)

    def is_artifact_protected(self, artifact_id: str) -> bool:
        """Check whether an artifact id is currently protected."""
        return artifact_id in self.protected_artifacts()

    def cleanup_eligible_generations(self) -> tuple[RollbackGeneration, ...]:
        """Return generations that are safe to garbage collect."""
        with self._condition:
            protected = self._protected_artifacts_locked()
            return tuple(
                generation
                for generation in self._generations
                if not any(artifact in protected for artifact in generation.artifacts)
            )

    def _lease_count_locked(self, generation: RollbackGeneration) -> int:
        """Count leases for one immutable generation without exposing identities."""
        return sum(
            leased.generation_id == generation.generation_id
            for leased in self._active_leases.values()
        )


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _is_sha256_uri(value: str) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _validate_interval(value: float, *, maximum: float, message: str) -> None:
    """Reject booleans, non-numeric values, NaN, infinity, and unsafe bounds."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0.0
        or value > maximum
    ):
        raise RollbackWorkflowError(message)


__all__ = [
    "FreeLLMAPIRollbackWorkflow",
    "RollbackArtifact",
    "RollbackDrainHeartbeat",
    "RollbackDrainResult",
    "RollbackGeneration",
    "RollbackWorkflowError",
]
