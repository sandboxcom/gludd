"""Deterministic, evidence-driven model candidates for self-improvement attempts."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass

from general_ludd.hardware.model_fit import can_run_model
from general_ludd.hardware.survey import HardwareInventory
from general_ludd.local_model._local_model_configs import _LOCAL_MODELS, LocalModelConfig
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
from general_ludd.small_models.recommender import recommend_model

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PROMPT_OVERHEAD_TOKENS = 512
_MAX_CANDIDATES = 3


@dataclass(frozen=True)
class PlannedModelCandidate:
    """One immutable model identity in a bounded escalation plan."""

    config: LocalModelConfig
    resolved_revision: str
    evidence_score: float
    escalation_level: int

    def __post_init__(self) -> None:
        """Reject candidates that cannot be safely handed to model acquisition."""
        if self.config.category != "coding":
            raise ValueError("candidate config must identify a coding model")
        if _SHA_RE.fullmatch(self.resolved_revision) is None:
            raise ValueError("resolved_revision must be a 40-character hexadecimal commit")
        if (
            isinstance(self.evidence_score, bool)
            or not isinstance(self.evidence_score, (int, float))
            or not 0.0 <= self.evidence_score <= 1.0
        ):
            raise ValueError("evidence_score must be between 0.0 and 1.0")
        if (
            isinstance(self.escalation_level, bool)
            or not isinstance(self.escalation_level, int)
            or self.escalation_level < 0
        ):
            raise ValueError("escalation_level must be a non-negative integer")


def _estimated_required_context(task_text: str, output_tokens: int) -> int:
    input_tokens = max(1, (len(task_text.encode("utf-8")) + 3) // 4)
    return input_tokens + output_tokens + _PROMPT_OVERHEAD_TOKENS


def _coding_models() -> tuple[LocalModelConfig, ...]:
    return tuple(model for model in _LOCAL_MODELS if model.category == "coding")


def _model_identifiers(model: LocalModelConfig) -> frozenset[str]:
    return frozenset(
        identifier.casefold()
        for identifier in (model.name, model.repo, *model.aliases)
    )


def _failed_names_and_floor(
    failed_ids: Collection[str],
    coding_models: tuple[LocalModelConfig, ...],
) -> tuple[frozenset[str], int]:
    if isinstance(failed_ids, (str, bytes)):
        raise ValueError("prior_failed_model_ids must be a collection of model identifiers")

    lookup = {
        identifier: model
        for model in coding_models
        for identifier in _model_identifiers(model)
    }
    failed_names: set[str] = set()
    failed_size_floor = -1
    for raw_identifier in failed_ids:
        if not isinstance(raw_identifier, str) or not raw_identifier.strip():
            raise ValueError("prior_failed_model_ids must contain non-empty strings")
        model = lookup.get(raw_identifier.strip().casefold())
        if model is None:
            continue
        failed_names.add(model.name)
        failed_size_floor = max(failed_size_floor, model.size_mb)
    return frozenset(failed_names), failed_size_floor


def _evidence_scores(
    task_text: str,
    hardware: HardwareInventory,
    store: CapabilityEvidenceStore,
) -> dict[str, float]:
    recommendations = recommend_model(task_text, hardware, store, urgent=True)
    return {
        recommendation.model_profile_id: recommendation.score
        for recommendation in recommendations
    }


def _ordered_shortlist(
    eligible: list[LocalModelConfig],
    scores: dict[str, float],
    max_candidates: int,
) -> tuple[LocalModelConfig, ...]:
    evidenced = [model for model in eligible if scores.get(model.name, 0.0) > 0.0]
    if not evidenced:
        return tuple(sorted(eligible, key=lambda model: (model.size_mb, model.name))[:max_candidates])

    anchor = min(
        evidenced,
        key=lambda model: (-scores[model.name], model.size_mb, model.name),
    )
    larger = sorted(
        (model for model in eligible if model.size_mb > anchor.size_mb),
        key=lambda model: (model.size_mb, -scores.get(model.name, 0.0), model.name),
    )
    return tuple([anchor, *larger[: max_candidates - 1]])


def plan_model_candidates(
    task_text: str,
    output_tokens: int,
    prior_failed_model_ids: Collection[str],
    hardware: HardwareInventory,
    evidence_store: CapabilityEvidenceStore,
    revision_resolver: Callable[[str], str],
    *,
    max_candidates: int = _MAX_CANDIDATES,
) -> tuple[PlannedModelCandidate, ...]:
    """Plan a deterministic, bounded sequence of immutable coding models.

    Local context capacity, hardware fit, and prior failures are evaluated before
    the resolver performs any remote immutable-revision lookup. Capability
    evidence chooses the first candidate when available; subsequent candidates
    grow monotonically by artifact size. Without matching evidence, the smallest
    fitting catalog models provide a stable fallback.
    """
    if not isinstance(task_text, str) or not task_text.strip():
        raise ValueError("task_text must be a non-empty string")
    if isinstance(output_tokens, bool) or not isinstance(output_tokens, int) or output_tokens <= 0:
        raise ValueError("output_tokens must be a positive integer")
    if (
        isinstance(max_candidates, bool)
        or not isinstance(max_candidates, int)
        or not 1 <= max_candidates <= _MAX_CANDIDATES
    ):
        raise ValueError(f"max_candidates must be between 1 and {_MAX_CANDIDATES}")

    coding_models = _coding_models()
    failed_names, failed_size_floor = _failed_names_and_floor(
        prior_failed_model_ids,
        coding_models,
    )
    required_context = _estimated_required_context(task_text, output_tokens)
    eligible = [
        model
        for model in coding_models
        if model.name not in failed_names
        and model.size_mb > failed_size_floor
        and model.context_size >= required_context
        and can_run_model(hardware, model.name).can_run
    ]
    if not eligible:
        return ()

    scores = _evidence_scores(task_text, hardware, evidence_store)
    shortlist = _ordered_shortlist(eligible, scores, max_candidates)

    planned: list[PlannedModelCandidate] = []
    for level, config in enumerate(shortlist):
        revision = revision_resolver(config.repo)
        normalized_revision = revision.lower() if isinstance(revision, str) else ""
        if _SHA_RE.fullmatch(normalized_revision) is None:
            raise RuntimeError(
                f"revision resolver for {config.repo} did not return a "
                "40-character hexadecimal commit"
            )
        planned.append(
            PlannedModelCandidate(
                config=config,
                resolved_revision=normalized_revision,
                evidence_score=float(scores.get(config.name, 0.0)),
                escalation_level=level,
            )
        )
    return tuple(planned)


__all__ = ["PlannedModelCandidate", "plan_model_candidates"]
