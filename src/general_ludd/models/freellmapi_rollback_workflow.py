"""Rollback workflow boundary for FreeLLMAPI admitted artifacts.

The workflow tracks blue/green generations, performs atomic switch-back, and
enforces protected-artifact rules so that a rollback cannot displace an
artifact that the current runtime depends on.  All state is content-free:
only evidence identifiers are retained.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import NoReturn

from general_ludd.models.freellmapi_provenance_bundle import FreeLLMAPIProvenanceBundle

FREELLMAPI_ROLLBACK_SCHEMA_VERSION = 1


class FreeLLMAPIRollbackFault(StrEnum):
    """Stable failure categories that never expose upstream content."""

    SCHEMA = "schema"
    MISSING_GENERATION = "missing_generation"
    ALREADY_ACTIVE = "already_active"
    NO_PRIOR_GENERATION = "no_prior_generation"
    PROTECTED_ARTIFACT = "protected_artifact"


class FreeLLMAPIRollbackError(ValueError):
    """Fail-closed rollback error containing only its stable category."""

    def __init__(self, fault: FreeLLMAPIRollbackFault) -> None:
        """Create an error containing only its stable category."""
        self.fault = fault
        super().__init__(fault.value)


class FreeLLMAPIRollbackGeneration(StrEnum):
    """Stable generation labels for the blue/green rollback pattern."""

    BLUE = "blue"
    GREEN = "green"


@dataclass(frozen=True, slots=True)
class FreeLLMAPIRollbackState:
    """Content-free state of the blue/green rollback workflow.

    Only evidence identifiers are stored; upstream content is never retained.
    """

    active_generation: FreeLLMAPIRollbackGeneration | None
    blue_evidence_id: str | None
    green_evidence_id: str | None
    previous_evidence_id: str | None
    protected_evidence_ids: frozenset[str]

    def __str__(self) -> str:
        """Return a content-free string representation."""
        return (
            f"FreeLLMAPIRollbackState("
            f"active_generation={self.active_generation}, "
            f"blue_evidence_id={self.blue_evidence_id}, "
            f"green_evidence_id={self.green_evidence_id}, "
            f"previous_evidence_id={self.previous_evidence_id}, "
            f"protected={len(self.protected_evidence_ids)}"
            f")"
        )


def _fail(fault: FreeLLMAPIRollbackFault) -> NoReturn:
    raise FreeLLMAPIRollbackError(fault)


def _generation(value: object) -> FreeLLMAPIRollbackGeneration:
    if isinstance(value, FreeLLMAPIRollbackGeneration):
        return value
    if isinstance(value, str):
        try:
            return FreeLLMAPIRollbackGeneration(value)
        except ValueError:
            pass
    _fail(FreeLLMAPIRollbackFault.SCHEMA)


def _evidence_id_for_generation(state: FreeLLMAPIRollbackState, generation: FreeLLMAPIRollbackGeneration) -> str | None:
    if generation == FreeLLMAPIRollbackGeneration.BLUE:
        return state.blue_evidence_id
    return state.green_evidence_id


def create_rollback_state() -> FreeLLMAPIRollbackState:
    """Return a fresh rollback state with no active generation."""
    return FreeLLMAPIRollbackState(
        active_generation=None,
        blue_evidence_id=None,
        green_evidence_id=None,
        previous_evidence_id=None,
        protected_evidence_ids=frozenset(),
    )


def stage_artifact(
    state: FreeLLMAPIRollbackState,
    generation: FreeLLMAPIRollbackGeneration | str,
    bundle: FreeLLMAPIProvenanceBundle,
) -> FreeLLMAPIRollbackState:
    """Assign a validated bundle to the requested generation.

    Args:
        state: Current rollback state.
        generation: Target generation label.
        bundle: Validated provenance bundle to stage.

    Returns:
        A new state with the generation's evidence id updated.
    """
    resolved = _generation(generation)
    if resolved == FreeLLMAPIRollbackGeneration.BLUE:
        return replace(state, blue_evidence_id=bundle.evidence_id)
    return replace(state, green_evidence_id=bundle.evidence_id)


def protect_artifact(state: FreeLLMAPIRollbackState, evidence_id: str) -> FreeLLMAPIRollbackState:
    """Mark an already-staged artifact as protected.

    A protected artifact may not be displaced by a generation switch.  The
    evidence id must correspond to the currently staged blue or green artifact.

    Args:
        state: Current rollback state.
        evidence_id: Evidence id of the artifact to protect.

    Returns:
        A new state with the evidence id in the protected set.

    Raises:
        FreeLLMAPIRollbackError: If the evidence id is not staged in either
            generation.
    """
    staged = {state.blue_evidence_id, state.green_evidence_id}
    if evidence_id not in staged or evidence_id is None:
        _fail(FreeLLMAPIRollbackFault.MISSING_GENERATION)
    return replace(state, protected_evidence_ids=state.protected_evidence_ids | {evidence_id})


def is_rollback_safe(
    state: FreeLLMAPIRollbackState,
    target_generation: FreeLLMAPIRollbackGeneration | str,
) -> bool:
    """Return whether switching to *target_generation* would violate protection.

    A switch is unsafe if the currently active generation's artifact is
    protected and *target_generation* is a different generation.
    """
    target = _generation(target_generation)
    active = state.active_generation
    if active is None or active == target:
        return True
    active_evidence = _evidence_id_for_generation(state, active)
    return active_evidence not in state.protected_evidence_ids


def activate_generation(
    state: FreeLLMAPIRollbackState,
    generation: FreeLLMAPIRollbackGeneration | str,
) -> FreeLLMAPIRollbackState:
    """Atomically switch the active generation.

    The previously active generation's evidence id is preserved so that a
    subsequent :func:`rollback` can switch back.  If the current generation's
    artifact is protected, switching to a different generation is rejected.

    Args:
        state: Current rollback state.
        generation: Generation to activate.

    Returns:
        A new state with the active generation updated.

    Raises:
        FreeLLMAPIRollbackError: If the target generation has no staged
            artifact, or if a protected artifact would be displaced.
    """
    target = _generation(generation)
    target_evidence = _evidence_id_for_generation(state, target)
    if target_evidence is None:
        _fail(FreeLLMAPIRollbackFault.MISSING_GENERATION)
    if state.active_generation == target:
        return state
    if not is_rollback_safe(state, target):
        _fail(FreeLLMAPIRollbackFault.PROTECTED_ARTIFACT)
    previous_evidence: str | None = None
    if state.active_generation is not None:
        previous_evidence = _evidence_id_for_generation(state, state.active_generation)
    return replace(
        state,
        active_generation=target,
        previous_evidence_id=previous_evidence,
    )


def rollback(state: FreeLLMAPIRollbackState) -> FreeLLMAPIRollbackState:
    """Atomically switch back to the previous active generation.

    Args:
        state: Current rollback state.

    Returns:
        A new state with the previous generation re-activated.

    Raises:
        FreeLLMAPIRollbackError: If there is no recorded previous generation.
    """
    if state.previous_evidence_id is None or state.active_generation is None:
        _fail(FreeLLMAPIRollbackFault.NO_PRIOR_GENERATION)
    current = state.active_generation
    previous: FreeLLMAPIRollbackGeneration
    if current == FreeLLMAPIRollbackGeneration.BLUE:
        previous = FreeLLMAPIRollbackGeneration.GREEN
    else:
        previous = FreeLLMAPIRollbackGeneration.BLUE
    return replace(
        state,
        active_generation=previous,
        previous_evidence_id=_evidence_id_for_generation(state, current),
    )


__all__ = [
    "FREELLMAPI_ROLLBACK_SCHEMA_VERSION",
    "FreeLLMAPIRollbackError",
    "FreeLLMAPIRollbackFault",
    "FreeLLMAPIRollbackGeneration",
    "FreeLLMAPIRollbackState",
    "activate_generation",
    "create_rollback_state",
    "is_rollback_safe",
    "protect_artifact",
    "rollback",
    "stage_artifact",
]
