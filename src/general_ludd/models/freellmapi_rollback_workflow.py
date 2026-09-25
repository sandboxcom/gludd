"""Rollback workflow boundary for FreeLLMAPI admitted artifacts.

The workflow tracks blue/green generations, performs atomic switch-back, and
enforces protected-artifact rules so that a rollback cannot displace an
artifact that the current runtime depends on.  All state is content-free:
only evidence identifiers are retained.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import NoReturn

FREELLMAPI_ROLLBACK_SCHEMA_VERSION = 1


class FreeLLMAPIRollbackFault(StrEnum):
    """Stable failure categories that never expose upstream content."""

    NO_PRIOR_GENERATION = "no_prior_generation"
    EMPTY_ARTIFACT_SET = "empty_artifact_set"
    ARTIFACT_CONFLICT = "artifact_conflict"


class FreeLLMAPIRollbackError(ValueError):
    """Fail-closed rollback error containing only its stable category."""

    def __init__(self, fault: FreeLLMAPIRollbackFault) -> None:
        """Create an error containing only its stable category."""
        self.fault = fault
        super().__init__(fault.value)


# Backward-compatible alias used by the class-based workflow boundary.
RollbackWorkflowError = FreeLLMAPIRollbackError


@dataclass(frozen=True, slots=True)
class RollbackArtifact:
    """One artifact tracked by the rollback workflow boundary."""

    artifact_id: str
    generation_id: int
    kind: str


@dataclass(frozen=True, slots=True)
class RollbackGeneration:
    """One immutable generation in the blue/green rollback sequence."""

    generation_id: int
    artifacts: tuple[str, ...]
    promoted_at: datetime


@dataclass
class FreeLLMAPIRollbackWorkflow:
    """Blue/green rollback boundary for FreeLLMAPI deployments.

    Promotions create monotonically numbered generations.  The most recent
    generation is *active* (green); the one before it is *previous* (blue).
    :meth:`rollback` atomically swaps them.  Artifacts belonging to the active
    and previous generations are protected from cleanup.
    """

    _generations: list[RollbackGeneration] = field(default_factory=list)
    _active_index: int = -1
    _previous_index: int = -1

    @property
    def active_generation(self) -> RollbackGeneration | None:
        """Return the generation currently serving new work, if any."""
        if self._active_index < 0:
            return None
        return self._generations[self._active_index]

    @property
    def previous_generation(self) -> RollbackGeneration | None:
        """Return the immediately previous accepted generation, if any."""
        if self._previous_index < 0:
            return None
        return self._generations[self._previous_index]

    def promote(self, artifacts: tuple[str, ...]) -> RollbackGeneration:
        """Stage a new generation and make it active.

        Args:
            artifacts: Non-empty tuple of unique artifact identifiers for the
                new generation.

        Returns:
            The newly created active generation.

        Raises:
            FreeLLMAPIRollbackError: If *artifacts* is empty or contains
                duplicate identifiers.
        """
        if not artifacts:
            _fail(FreeLLMAPIRollbackFault.EMPTY_ARTIFACT_SET)
        if len(set(artifacts)) != len(artifacts):
            _fail(FreeLLMAPIRollbackFault.ARTIFACT_CONFLICT)

        generation_id = (self._generations[-1].generation_id + 1) if self._generations else 1
        generation = RollbackGeneration(
            generation_id=generation_id,
            artifacts=artifacts,
            promoted_at=datetime.now(UTC),
        )
        self._generations.append(generation)
        if self._active_index >= 0:
            self._previous_index = self._active_index
        self._active_index = len(self._generations) - 1
        return generation

    def rollback(self) -> RollbackGeneration:
        """Atomically switch back to the previous generation.

        Returns:
            The generation that becomes active after the switch.

        Raises:
            FreeLLMAPIRollbackError: If there is no previous generation.
        """
        if self._previous_index < 0:
            _fail(FreeLLMAPIRollbackFault.NO_PRIOR_GENERATION)
        current_active = self._active_index
        self._active_index = self._previous_index
        self._previous_index = current_active
        return self.active_generation  # type: ignore[return-value]

    def protected_artifacts(self) -> frozenset[str]:
        """Return all artifact ids that may not be cleaned up."""
        protected: set[str] = set()
        for index in (self._active_index, self._previous_index):
            if index >= 0:
                protected.update(self._generations[index].artifacts)
        return frozenset(protected)

    def is_artifact_protected(self, artifact_id: str) -> bool:
        """Return True if *artifact_id* belongs to the active or previous generation."""
        return artifact_id in self.protected_artifacts()

    def cleanup_eligible_generations(self) -> tuple[RollbackGeneration, ...]:
        """Return generations older than the active and previous ones.

        These generations are safe to remove because they are no longer needed
        for serving or rollback.
        """
        kept = {self._active_index, self._previous_index}
        return tuple(generation for index, generation in enumerate(self._generations) if index not in kept)


def _fail(fault: FreeLLMAPIRollbackFault) -> NoReturn:
    raise FreeLLMAPIRollbackError(fault)


__all__ = [
    "FREELLMAPI_ROLLBACK_SCHEMA_VERSION",
    "FreeLLMAPIRollbackError",
    "FreeLLMAPIRollbackFault",
    "FreeLLMAPIRollbackWorkflow",
    "RollbackArtifact",
    "RollbackGeneration",
    "RollbackWorkflowError",
]
