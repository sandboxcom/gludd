"""Deterministic, evidence-driven model candidates for self-improvement attempts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any, cast

from general_ludd.hardware.model_fit import can_run_model
from general_ludd.hardware.survey import HardwareInventory
from general_ludd.local_model._local_model_configs import _LOCAL_MODELS, LocalModelConfig
from general_ludd.schemas.benchmark import TaskRole, TaskType
from general_ludd.self_improve.task_diversity import (
    infer_task_type,
    select_representative_evidence,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
from general_ludd.small_models.recommender import (
    map_task_to_capabilities,
    recommend_model,
)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PROMPT_OVERHEAD_TOKENS = 512
_MAX_CANDIDATES = 3
_OUTCOME_COLLECTION = "general_ludd.self_improve"
_OUTCOME_SUITE_ID = "self_improve_outcome"
_OUTCOME_RECORD_KEYS = frozenset(
    {
        "model_profile_id",
        "model_identity_digest",
        "attempt_identity_digest",
        "task_type",
        "task_kind",
        "role",
        "collection",
        "suite_id",
        "suite_revision",
        "acceptance_contract_digest",
        "passed_cases",
        "total_cases",
        "collection_ok",
        "local_only",
        "evidence_digest",
        "registered_at",
    }
)


def _stable_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


_OUTCOME_ACCEPTANCE_DIGEST = _stable_digest(
    {
        "collection": _OUTCOME_COLLECTION,
        "schema_version": 3,
        "semantics": (
            "one_case_passed_means_prompt_protocol_and_task_shape_scoped_"
            "self_improvement_succeeded"
        ),
        "suite_id": _OUTCOME_SUITE_ID,
    }
)


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


def _estimated_required_context(
    task_text: str,
    output_tokens: int,
    input_tokens: int | None,
) -> int:
    estimated_input = (
        max(1, (len(task_text.encode("utf-8")) + 3) // 4)
        if input_tokens is None
        else input_tokens
    )
    return estimated_input + output_tokens + _PROMPT_OVERHEAD_TOKENS


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


class _TaskShapeEvidenceView:
    """Read-only exact-shape view used by the public recommendation path."""

    def __init__(
        self,
        store: CapabilityEvidenceStore,
        task_type: TaskType,
    ) -> None:
        self._store = store
        self._task_type = task_type

    def query_by_task_kind(self, task_kind: str) -> list[dict[str, Any]]:
        records = self._store.query_by_task_shape(self._task_type, task_kind)
        return list(select_representative_evidence(records))

    def query_by_model(self, model_profile_id: str) -> list[dict[str, Any]]:
        records = [
            record
            for record in self._store.query_by_model(model_profile_id)
            if record.get("task_type") == self._task_type.value
        ]
        return list(select_representative_evidence(records))


def _evidence_scores(
    task_text: str,
    hardware: HardwareInventory,
    store: CapabilityEvidenceStore,
) -> dict[str, float]:
    task_type = infer_task_type(task_text)
    scoped_store = cast(
        CapabilityEvidenceStore,
        _TaskShapeEvidenceView(store, task_type),
    )
    recommendations = recommend_model(
        task_text,
        hardware,
        scoped_store,
        urgent=True,
    )
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
    input_tokens: int | None = None,
    attempt_identity_digest: str | None = None,
    max_candidates: int = _MAX_CANDIDATES,
    on_resolution_failure: Callable[[LocalModelConfig, str], None] | None = None,
) -> tuple[PlannedModelCandidate, ...]:
    """Plan a deterministic, bounded sequence of immutable coding models.

    Local context capacity, hardware fit, and prior failures are evaluated before
    the resolver performs any remote immutable-revision lookup. Capability
    evidence chooses the first candidate when available; subsequent candidates
    grow monotonically by artifact size. When an attempt identity is supplied,
    persisted failures for that exact prompt protocol and task shape become the
    escalation floor. Without matching evidence, the smallest fitting catalog
    models provide a stable fallback.
    """
    if not isinstance(task_text, str) or not task_text.strip():
        raise ValueError("task_text must be a non-empty string")
    if isinstance(output_tokens, bool) or not isinstance(output_tokens, int) or output_tokens <= 0:
        raise ValueError("output_tokens must be a positive integer")
    if (
        input_tokens is not None
        and (
            isinstance(input_tokens, bool)
            or not isinstance(input_tokens, int)
            or input_tokens <= 0
        )
    ):
        raise ValueError("input_tokens must be a positive integer when provided")
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
    if attempt_identity_digest is not None:
        persisted_names, persisted_size_floor = _failed_names_and_floor(
            load_latest_failed_model_ids(
                evidence_store,
                task_text=task_text,
                attempt_identity_digest=attempt_identity_digest,
            ),
            coding_models,
        )
        failed_names = failed_names | persisted_names
        failed_size_floor = max(failed_size_floor, persisted_size_floor)
    required_context = _estimated_required_context(
        task_text,
        output_tokens,
        input_tokens,
    )
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
    ordered = _ordered_shortlist(eligible, scores, len(eligible))

    planned: list[PlannedModelCandidate] = []
    for config in ordered:
        try:
            revision = revision_resolver(config.repo)
        except (OSError, RuntimeError, ValueError) as exc:
            if on_resolution_failure is None:
                raise
            reason = str(exc).strip().replace("\n", " ")[:1000] or type(exc).__name__
            on_resolution_failure(config, reason)
            continue
        normalized_revision = revision.lower() if isinstance(revision, str) else ""
        if _SHA_RE.fullmatch(normalized_revision) is None:
            reason = (
                f"revision resolver for {config.repo} did not return a "
                "40-character hexadecimal commit"
            )
            if on_resolution_failure is None:
                raise RuntimeError(reason)
            on_resolution_failure(config, reason)
            continue
        planned.append(
            PlannedModelCandidate(
                config=config,
                resolved_revision=normalized_revision,
                evidence_score=float(scores.get(config.name, 0.0)),
                escalation_level=len(planned),
            )
        )
        if len(planned) == max_candidates:
            break
    return tuple(planned)


def _primary_task_capability(task_text: str) -> tuple[str, TaskRole]:
    capabilities = map_task_to_capabilities(task_text)
    if not capabilities:
        raise ValueError("task_text must match a mapped capability")
    return capabilities[0]


def _canonical_candidate_model(candidate: PlannedModelCandidate) -> LocalModelConfig:
    if not isinstance(candidate, PlannedModelCandidate):
        raise ValueError("candidate must be a PlannedModelCandidate")
    for model in _coding_models():
        if candidate.config == model:
            return model
    raise ValueError("candidate must contain a configured coding model")


def _validate_attempt_identity_digest(value: object) -> str:
    """Return one canonical prompt-plan/protocol identity or fail closed."""
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(
            "attempt_identity_digest must be a lowercase 64-character hexadecimal digest"
        )
    return value


def _outcome_payload(
    model: LocalModelConfig,
    revision: str,
    task_type: TaskType,
    task_kind: str,
    role: TaskRole,
    attempt_identity_digest: str,
    *,
    succeeded: bool,
) -> dict[str, object]:
    attempt_identity = _validate_attempt_identity_digest(attempt_identity_digest)
    identity_digest = _stable_digest(
        {
            "attempt_identity_digest": attempt_identity,
            "model_profile_id": model.name,
            "resolved_revision": revision,
        }
    )
    return {
        "model_profile_id": model.name,
        "model_identity_digest": identity_digest,
        "attempt_identity_digest": attempt_identity,
        "task_type": task_type.value,
        "task_kind": task_kind,
        "role": role.value,
        "collection": _OUTCOME_COLLECTION,
        "suite_id": _OUTCOME_SUITE_ID,
        "suite_revision": revision,
        "acceptance_contract_digest": _OUTCOME_ACCEPTANCE_DIGEST,
        "passed_cases": int(succeeded),
        "total_cases": 1,
        "collection_ok": True,
        "local_only": True,
    }


def record_self_improve_outcome(
    store: CapabilityEvidenceStore,
    *,
    task_text: str,
    candidate: PlannedModelCandidate,
    succeeded: bool,
    attempt_identity_digest: str,
) -> int:
    """Persist one revision- and prompt-protocol-bound outcome."""
    attempt_identity = _validate_attempt_identity_digest(attempt_identity_digest)
    task_type = infer_task_type(task_text)
    task_kind, role = _primary_task_capability(task_text)
    model = _canonical_candidate_model(candidate)
    if not isinstance(succeeded, bool):
        raise ValueError("succeeded must be a boolean")

    revision = candidate.resolved_revision
    if _SHA_RE.fullmatch(revision) is None:
        raise ValueError("candidate revision must be an immutable commit")
    payload = _outcome_payload(
        model,
        revision,
        task_type,
        task_kind,
        role,
        attempt_identity,
        succeeded=succeeded,
    )
    record = dict(payload)
    record["evidence_digest"] = _stable_digest(payload)
    return store.register_evidence(record)


def _valid_outcome_state(
    record: Mapping[str, object],
    task_type: TaskType,
    task_kind: str,
    role: TaskRole,
    attempt_identity_digest: str,
) -> tuple[str, bool] | None:
    if set(record) != _OUTCOME_RECORD_KEYS:
        return None
    registered_at = record.get("registered_at")
    if (
        isinstance(registered_at, bool)
        or not isinstance(registered_at, (int, float))
        or registered_at < 0
    ):
        return None

    record_attempt_identity = record.get("attempt_identity_digest")
    model_id = record.get("model_profile_id")
    revision = record.get("suite_revision")
    passed_cases = record.get("passed_cases")
    if (
        not isinstance(record_attempt_identity, str)
        or _DIGEST_RE.fullmatch(record_attempt_identity) is None
        or record_attempt_identity != attempt_identity_digest
        or not isinstance(model_id, str)
        or not isinstance(revision, str)
        or _SHA_RE.fullmatch(revision) is None
        or isinstance(passed_cases, bool)
        or not isinstance(passed_cases, int)
        or passed_cases not in (0, 1)
    ):
        return None

    model = next(
        (item for item in _coding_models() if item.name == model_id),
        None,
    )
    if model is None:
        return None
    expected = _outcome_payload(
        model,
        revision,
        task_type,
        task_kind,
        role,
        record_attempt_identity,
        succeeded=passed_cases == 1,
    )
    if any(record.get(key) != value for key, value in expected.items()):
        return None
    evidence_digest = record.get("evidence_digest")
    if (
        not isinstance(evidence_digest, str)
        or evidence_digest != _stable_digest(expected)
    ):
        return None
    return model.name, passed_cases == 1


def load_latest_failed_model_ids(
    store: CapabilityEvidenceStore,
    *,
    task_text: str,
    attempt_identity_digest: str,
) -> tuple[str, ...]:
    """Load latest failures for exactly one prompt-plan/protocol identity."""
    attempt_identity = _validate_attempt_identity_digest(attempt_identity_digest)
    task_type = infer_task_type(task_text)
    task_kind, role = _primary_task_capability(task_text)
    latest: dict[str, bool] = {}
    for record in store.list_all():
        state = _valid_outcome_state(
            record,
            task_type,
            task_kind,
            role,
            attempt_identity,
        )
        if state is None:
            continue
        model_id, succeeded = state
        latest[model_id] = succeeded
    return tuple(sorted(model_id for model_id, succeeded in latest.items() if not succeeded))


__all__ = [
    "PlannedModelCandidate",
    "load_latest_failed_model_ids",
    "plan_model_candidates",
    "record_self_improve_outcome",
]
