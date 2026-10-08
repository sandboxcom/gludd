"""Fail-closed blue/green rollback workflow for FreeLLMAPI bridge artifacts.

The workflow tracks immutable artifact generations, atomic switch-back, and
protected-artifact rules without exposing upstream content or worker state.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime


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
        if self.previous_generation is None:
            raise RollbackWorkflowError("no previous generation to roll back to")
        self.active_generation, self.previous_generation = (
            self.previous_generation,
            self.active_generation,
        )
        assert self.active_generation is not None
        return self.active_generation

    def acquire_lease(self, lease_id: str) -> RollbackGeneration:
        """Pin one content-free work lease to the current generation."""
        if not _is_sha256_uri(lease_id):
            raise RollbackWorkflowError("lease identity must be a sha256 uri")
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
        generation = self._active_leases.get(lease_id)
        if generation is None:
            raise RollbackWorkflowError("lease identity is not active")
        return generation

    def release_lease(self, lease_id: str) -> RollbackGeneration:
        """Release exactly one owned lease and return its pinned generation."""
        generation = self.generation_for_lease(lease_id)
        del self._active_leases[lease_id]
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
        return len(self._active_leases)

    def protected_artifacts(self) -> frozenset[str]:
        """Return artifact ids that must not be garbage collected."""
        protected: set[str] = set()
        if self.active_generation is not None:
            protected.update(self.active_generation.artifacts)
        if self.previous_generation is not None:
            protected.update(self.previous_generation.artifacts)
        for generation in self._active_leases.values():
            protected.update(generation.artifacts)
        return frozenset(protected)

    def is_artifact_protected(self, artifact_id: str) -> bool:
        """Check whether an artifact id is currently protected."""
        return artifact_id in self.protected_artifacts()

    def cleanup_eligible_generations(self) -> tuple[RollbackGeneration, ...]:
        """Return generations that are safe to garbage collect."""
        protected = self.protected_artifacts()
        return tuple(
            generation
            for generation in self._generations
            if not any(artifact in protected for artifact in generation.artifacts)
        )


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _is_sha256_uri(value: str) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


__all__ = [
    "FreeLLMAPIRollbackWorkflow",
    "RollbackArtifact",
    "RollbackGeneration",
    "RollbackWorkflowError",
]
