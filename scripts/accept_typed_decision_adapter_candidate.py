#!/usr/bin/env python3
"""Run the hermetic OpenJev/Laya adapter acceptance chain.

The chain is deliberately evidence-only. It prepares and durably replays a
disabled update, validates an exact hypothetical shadow projection, plans
batching/residency, and normalizes pinned fixtures. It never admits a profile,
downloads a model, contacts a network, or invokes a runtime.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Never

from scripts import check_typed_decision_adapter_update as update
from scripts import normalize_typed_decision_adapter_fixture as normalizer
from scripts import plan_typed_decision_adapter_batch as planner

ACCEPTANCE_VERSION = "typed-decision-adapter-offline-acceptance/v1"
LANE_EVIDENCE_VERSION = "typed-decision-adapter-offline-lane-evidence/v1"
TASK_KIND = "failure_classification"
MAX_LANES = 16
MAX_FIXTURES = 64

INERT_SIDE_EFFECT_FLAGS = (
    "admission_enabled",
    "admission_performed",
    "model_invoked",
    "models_downloaded",
    "network_accessed",
    "runtime_changed",
    "runtime_invoked",
)
REDACTED_FIELDS = (
    "actor_id",
    "credentials",
    "request.request_id",
    "request.state",
    "response.answers",
    "transport.endpoint",
)

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class AcceptanceError(RuntimeError):
    """Bounded rejection which never renders candidate or fixture content."""


def _reject(stage: str) -> Never:
    raise AcceptanceError(f"offline acceptance rejected: {stage}")


@dataclass(frozen=True, slots=True)
class NormalizationFixture:
    """One exact local wire fixture bound to a redacted request reference."""

    fixture_id: str
    request_id: str
    request_schema_version: str
    response_schema_version: str
    upstream_request: Mapping[str, object]
    upstream_response: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AdapterAcceptanceLane:
    """Inputs for one disabled candidate and its hypothetical shadow plan."""

    lane_id: str
    baseline_profile: Mapping[str, object]
    candidate_profile: Mapping[str, object]
    shadow_profile: Mapping[str, object]
    event_id: str
    recorded_at: str
    actor_id: str
    prepared_update_path: Path
    requests: tuple[planner.BatchRequest, ...]
    bounds: planner.PlannerBounds
    snapshot: planner.ResidencySnapshot
    measurements: tuple[planner.IdentityMeasurement, ...]
    normalization_fixtures: tuple[NormalizationFixture, ...]


@dataclass(frozen=True, slots=True)
class LaneAcceptanceEvidence:
    """Digest-only evidence linking one candidate through every offline stage."""

    lane_id: str
    identity_fingerprint: str
    candidate_profile_digest: str
    shadow_profile_digest: str
    shadow_projection_digest: str
    compatibility_evidence_digest: str
    update_plan_digest: str
    update_receipt_digest: str
    rollback_digest: str
    batch_plan_digest: str
    fixture_manifest_digest: str
    normalization_record_digests: tuple[str, ...]
    normalization_kinds: tuple[str, ...]
    persistence_idempotent: bool
    rollback_verified: bool
    replay_verified: bool

    def to_dict(self) -> dict[str, object]:
        """Return the canonical redacted lane evidence."""

        return {
            "batch_plan_digest": self.batch_plan_digest,
            "candidate_profile_digest": self.candidate_profile_digest,
            "compatibility_evidence_digest": self.compatibility_evidence_digest,
            "fixture_manifest_digest": self.fixture_manifest_digest,
            "identity_fingerprint": self.identity_fingerprint,
            "lane_id": self.lane_id,
            "normalization_kinds": list(self.normalization_kinds),
            "normalization_record_digests": list(
                self.normalization_record_digests,
            ),
            "persistence_idempotent": self.persistence_idempotent,
            "replay_verified": self.replay_verified,
            "rollback_digest": self.rollback_digest,
            "rollback_verified": self.rollback_verified,
            "shadow_profile_digest": self.shadow_profile_digest,
            "shadow_projection_digest": self.shadow_projection_digest,
            "update_plan_digest": self.update_plan_digest,
            "update_receipt_digest": self.update_receipt_digest,
            "version": LANE_EVIDENCE_VERSION,
        }


@dataclass(frozen=True, slots=True)
class OfflineAcceptanceReceipt:
    """One immutable redacted receipt for the complete hermetic chain."""

    schema_digest: str
    lanes: tuple[LaneAcceptanceEvidence, ...]
    redacted_fields: tuple[str, ...]
    admission_enabled: bool
    admission_performed: bool
    model_invoked: bool
    models_downloaded: bool
    network_accessed: bool
    runtime_changed: bool
    runtime_invoked: bool
    receipt_digest: str

    def body_dict(self) -> dict[str, object]:
        """Return every digest-covered receipt field."""

        return {
            "admission_enabled": self.admission_enabled,
            "admission_performed": self.admission_performed,
            "lanes": [lane.to_dict() for lane in self.lanes],
            "model_invoked": self.model_invoked,
            "models_downloaded": self.models_downloaded,
            "network_accessed": self.network_accessed,
            "redacted_fields": list(self.redacted_fields),
            "runtime_changed": self.runtime_changed,
            "runtime_invoked": self.runtime_invoked,
            "schema_digest": self.schema_digest,
            "version": ACCEPTANCE_VERSION,
        }

    def to_dict(self) -> dict[str, object]:
        """Return the canonical receipt including its digest."""

        return {**self.body_dict(), "receipt_digest": self.receipt_digest}


def _reference(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _mapping(value: object, *, stage: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        _reject(stage)
    return value


def _string(parent: Mapping[str, object], key: str, *, stage: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value:
        _reject(stage)
    return value


def _validate_identifier(value: object, *, stage: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _reject(stage)
    return value


def _validate_shadow_projection(
    candidate: Mapping[str, object],
    shadow: Mapping[str, object],
) -> str:
    """Allow exactly disabled-to-shadow state projection and no other drift."""

    if candidate.get("profile_state") != "disabled":
        _reject("shadow_projection")
    if shadow.get("profile_state") != "shadow":
        _reject("shadow_projection")
    expected = copy.deepcopy(dict(candidate))
    expected["profile_state"] = "shadow"
    try:
        matches = update.canonical_json_bytes(expected) == update.canonical_json_bytes(
            dict(shadow),
        )
    except (TypeError, ValueError, RuntimeError, RecursionError):
        _reject("shadow_projection")
    if not matches:
        _reject("shadow_projection")
    return update.canonical_digest(
        {
            "candidate_profile_digest": update.canonical_digest(dict(candidate)),
            "shadow_profile_digest": update.canonical_digest(dict(shadow)),
            "transition": "disabled_to_shadow_projection_only",
        },
    )


def _question_facts(
    request: Mapping[str, object],
) -> tuple[tuple[str, ...], int, int, int]:
    questions = _mapping(request.get("questions"), stage="batch_normalization_link")
    if not questions or len(questions) > 32:
        _reject("batch_normalization_link")
    kinds: list[str] = []
    max_choice_options = 0
    max_score_levels = 0
    for question in questions.values():
        item = _mapping(question, stage="batch_normalization_link")
        kind = item.get("type")
        if kind not in {"choice", "noul", "score"}:
            _reject("batch_normalization_link")
        assert isinstance(kind, str)
        kinds.append(kind)
        criteria = item.get("criteria")
        if kind == "choice":
            max_choice_options = max(
                max_choice_options,
                len(_mapping(criteria, stage="batch_normalization_link")),
            )
        elif kind == "score":
            if not isinstance(criteria, list):
                _reject("batch_normalization_link")
            max_score_levels = max(max_score_levels, len(criteria))
    return (
        tuple(sorted(set(kinds))),
        len(questions),
        max_choice_options,
        max_score_levels,
    )


def _state_shape(value: object) -> str:
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    _reject("batch_normalization_link")


def _validate_batch_normalization_link(
    candidate: Mapping[str, object],
    requests: Sequence[planner.BatchRequest],
    fixtures: Sequence[NormalizationFixture],
) -> tuple[tuple[planner.BatchRequest, NormalizationFixture], ...]:
    profile_id = _string(candidate, "profile_id", stage="batch_normalization_link")
    if not requests or len(requests) != len(fixtures) or len(requests) > MAX_FIXTURES:
        _reject("batch_normalization_link")
    request_by_id: dict[str, planner.BatchRequest] = {}
    for request in requests:
        request_id = _validate_identifier(
            getattr(request, "request_id", None),
            stage="batch_normalization_link",
        )
        if request_id in request_by_id:
            _reject("batch_normalization_link")
        request_by_id[request_id] = request
    fixture_by_id: dict[str, NormalizationFixture] = {}
    fixture_ids: set[str] = set()
    for fixture in fixtures:
        request_id = _validate_identifier(
            fixture.request_id,
            stage="batch_normalization_link",
        )
        fixture_id = _validate_identifier(
            fixture.fixture_id,
            stage="batch_normalization_link",
        )
        if request_id in fixture_by_id or fixture_id in fixture_ids:
            _reject("batch_normalization_link")
        fixture_by_id[request_id] = fixture
        fixture_ids.add(fixture_id)
    if set(request_by_id) != set(fixture_by_id):
        _reject("batch_normalization_link")

    linked: list[tuple[planner.BatchRequest, NormalizationFixture]] = []
    for request_id in sorted(request_by_id):
        request = request_by_id[request_id]
        fixture = fixture_by_id[request_id]
        upstream_request = fixture.upstream_request
        if not isinstance(upstream_request, dict):
            _reject("batch_normalization_link")
        kinds, question_count, choice_options, score_levels = _question_facts(
            upstream_request,
        )
        try:
            question_digest = update.canonical_digest(upstream_request["questions"])
            state_bytes = len(update.canonical_json_bytes(upstream_request.get("state")))
            request_bytes = len(update.canonical_json_bytes(upstream_request))
        except (KeyError, TypeError, ValueError, RuntimeError, RecursionError):
            _reject("batch_normalization_link")
        if (
            request.profile_id != profile_id
            or request.question_schema_digest != question_digest
            or request.question_types != kinds
            or request.state_shape != _state_shape(upstream_request.get("state"))
            or request.state_bytes != state_bytes
            or request.question_count != question_count
            or request.max_choice_options != choice_options
            or request.max_score_levels != score_levels
            or request.request_bytes != request_bytes
        ):
            _reject("batch_normalization_link")
        linked.append((request, fixture))
    return tuple(linked)


def _prepare_update(
    schema: Mapping[str, object],
    lane: AdapterAcceptanceLane,
) -> update.PreparedUpdate:
    try:
        prepared = update.build_prepared_update(
            schema,
            lane.baseline_profile,
            lane.candidate_profile,
            event_id=lane.event_id,
            recorded_at=lane.recorded_at,
            actor_id=lane.actor_id,
        )
        replayed = update.verify_prepared_update(
            prepared.to_dict(),
            schema,
            lane.baseline_profile,
            lane.candidate_profile,
        )
    except (update.ContractInputError, update.UpdatePlanError):
        _reject("update")
    if replayed != prepared:
        _reject("update")
    return prepared


def _persist_update(
    schema: Mapping[str, object],
    lane: AdapterAcceptanceLane,
    prepared: update.PreparedUpdate,
) -> None:
    try:
        first = update.persist_prepared_update(lane.prepared_update_path, prepared)
        second = update.persist_prepared_update(lane.prepared_update_path, prepared)
        stored = update.load_json_object(lane.prepared_update_path)
        replayed = update.verify_prepared_update(
            stored,
            schema,
            lane.baseline_profile,
            lane.candidate_profile,
        )
    except (update.ContractInputError, update.UpdatePlanError, OSError):
        _reject("persistence")
    if (
        first not in {update.PersistenceStatus.CREATED, update.PersistenceStatus.UNCHANGED}
        or second != update.PersistenceStatus.UNCHANGED
        or replayed != prepared
    ):
        _reject("persistence")


def _plan_batch(
    schema: Mapping[str, object],
    lane: AdapterAcceptanceLane,
) -> planner.BatchResidencyPlan:
    requests = tuple(sorted(lane.requests, key=lambda item: item.request_id))
    try:
        plan = planner.build_batch_residency_plan(
            schema,
            [lane.shadow_profile],
            requests,
            bounds=lane.bounds,
            snapshot=lane.snapshot,
            measurements=lane.measurements,
        )
        replayed = planner.verify_batch_residency_plan(
            plan.to_dict(),
            schema,
            [lane.shadow_profile],
            requests,
            bounds=lane.bounds,
            snapshot=lane.snapshot,
            measurements=lane.measurements,
        )
    except planner.PlannerInputError:
        _reject("batch")
    if replayed != plan or not plan.ready:
        _reject("batch")
    return plan


def _normalize_linked_fixtures(
    schema: Mapping[str, object],
    candidate: Mapping[str, object],
    linked: Sequence[tuple[planner.BatchRequest, NormalizationFixture]],
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    record_digests: list[str] = []
    kinds: set[str] = set()
    manifest: list[dict[str, object]] = []
    for request, fixture in linked:
        try:
            record = normalizer.normalize_fixture(
                schema,
                candidate,
                request_id=fixture.request_id,
                task_kind=TASK_KIND,
                request_schema_version=fixture.request_schema_version,
                response_schema_version=fixture.response_schema_version,
                upstream_request=fixture.upstream_request,
                upstream_response=fixture.upstream_response,
            )
            parsed = normalizer.NormalizationRecord.from_dict(record.to_dict())
            replayed = normalizer.replay_normalization(
                record,
                schema,
                candidate,
                request_id=fixture.request_id,
                task_kind=TASK_KIND,
                request_schema_version=fixture.request_schema_version,
                response_schema_version=fixture.response_schema_version,
                upstream_request=fixture.upstream_request,
                upstream_response=fixture.upstream_response,
            )
        except normalizer.NormalizationError:
            _reject("normalization")
        record_kinds = tuple(sorted({outcome.kind for outcome in record.outcomes}))
        if parsed != record or replayed != record or record_kinds != request.question_types:
            _reject("normalization")
        if (
            record.network_accessed
            or record.runtime_invoked
            or record.model_invoked
            or record.admission_performed
            or not record.ready
        ):
            _reject("normalization")
        record_digests.append(record.record_digest)
        kinds.update(record_kinds)
        manifest.append(
            {
                "fixture_reference": _reference(fixture.fixture_id),
                "record_digest": record.record_digest,
                "request_digest": record.binding.request_digest,
                "request_reference": _reference(fixture.request_id),
                "response_digest": record.binding.response_digest,
            },
        )
    return (
        tuple(record_digests),
        tuple(sorted(kinds)),
        update.canonical_digest(manifest),
    )


def _accept_lane(
    schema: Mapping[str, object],
    lane: AdapterAcceptanceLane,
) -> LaneAcceptanceEvidence:
    lane_id = _validate_identifier(lane.lane_id, stage="lane")
    prepared = _prepare_update(schema, lane)
    _persist_update(schema, lane, prepared)
    projection_digest = _validate_shadow_projection(
        lane.candidate_profile,
        lane.shadow_profile,
    )
    batch = _plan_batch(schema, lane)
    linked = _validate_batch_normalization_link(
        lane.candidate_profile,
        lane.requests,
        lane.normalization_fixtures,
    )
    record_digests, kinds, fixture_manifest_digest = _normalize_linked_fixtures(
        schema,
        lane.candidate_profile,
        linked,
    )

    plan = prepared.plan
    receipt = prepared.receipt
    rollback = plan.rollback
    candidate_digest = update.canonical_digest(dict(lane.candidate_profile))
    shadow_digest = update.canonical_digest(dict(lane.shadow_profile))
    candidate_identity = _mapping(
        lane.candidate_profile.get("identity"),
        stage="stage_binding",
    )
    shadow_identity = _mapping(
        lane.shadow_profile.get("identity"),
        stage="stage_binding",
    )
    fingerprint = _string(candidate_identity, "fingerprint", stage="stage_binding")
    if (
        shadow_identity.get("fingerprint") != fingerprint
        or plan.candidate.profile_digest != candidate_digest
        or plan.compatibility_evidence.candidate_profile_digest != candidate_digest
        or batch.evidence.profile_digests != (shadow_digest,)
        or plan.admission_enabled
        or plan.network_accessed
        or plan.runtime_changed
        or receipt.admission_enabled
        or receipt.network_accessed
        or receipt.runtime_changed
        or batch.admission_performed
        or batch.network_accessed
        or batch.runtime_invoked
        or batch.models_downloaded
    ):
        _reject("stage_binding")
    rollback_verified = (
        rollback.required
        and rollback.target_state == "disabled"
        and rollback.baseline_profile_digest
        == update.canonical_digest(dict(lane.baseline_profile))
        and receipt.rollback_digest == rollback.rollback_digest
    )
    if not rollback_verified:
        _reject("rollback")

    return LaneAcceptanceEvidence(
        lane_id=lane_id,
        identity_fingerprint=fingerprint,
        candidate_profile_digest=candidate_digest,
        shadow_profile_digest=shadow_digest,
        shadow_projection_digest=projection_digest,
        compatibility_evidence_digest=plan.compatibility_evidence.evidence_digest,
        update_plan_digest=plan.plan_digest,
        update_receipt_digest=receipt.receipt_digest,
        rollback_digest=rollback.rollback_digest,
        batch_plan_digest=batch.plan_digest,
        fixture_manifest_digest=fixture_manifest_digest,
        normalization_record_digests=record_digests,
        normalization_kinds=kinds,
        persistence_idempotent=True,
        rollback_verified=True,
        replay_verified=True,
    )


def run_offline_acceptance(
    schema: Mapping[str, object],
    lanes: Sequence[AdapterAcceptanceLane],
) -> OfflineAcceptanceReceipt:
    """Run every offline stage and return one deterministic digest-only receipt."""

    if not isinstance(schema, dict) or not 1 <= len(lanes) <= MAX_LANES:
        _reject("inputs")
    lane_ids = [lane.lane_id for lane in lanes]
    if len(lane_ids) != len(set(lane_ids)):
        _reject("inputs")
    evidence = tuple(
        _accept_lane(schema, lane)
        for lane in sorted(lanes, key=lambda item: item.lane_id)
    )
    if {kind for lane in evidence for kind in lane.normalization_kinds} != {
        "choice",
        "noul",
        "score",
    }:
        _reject("fixture_coverage")
    without_digest = OfflineAcceptanceReceipt(
        schema_digest=update.canonical_digest(dict(schema)),
        lanes=evidence,
        redacted_fields=REDACTED_FIELDS,
        admission_enabled=False,
        admission_performed=False,
        model_invoked=False,
        models_downloaded=False,
        network_accessed=False,
        runtime_changed=False,
        runtime_invoked=False,
        receipt_digest="",
    )
    return OfflineAcceptanceReceipt(
        schema_digest=without_digest.schema_digest,
        lanes=without_digest.lanes,
        redacted_fields=without_digest.redacted_fields,
        admission_enabled=False,
        admission_performed=False,
        model_invoked=False,
        models_downloaded=False,
        network_accessed=False,
        runtime_changed=False,
        runtime_invoked=False,
        receipt_digest=update.canonical_digest(without_digest.body_dict()),
    )


_RECEIPT_FIELDS = {
    *INERT_SIDE_EFFECT_FLAGS,
    "lanes",
    "receipt_digest",
    "redacted_fields",
    "schema_digest",
    "version",
}
_LANE_FIELDS = {
    "batch_plan_digest",
    "candidate_profile_digest",
    "compatibility_evidence_digest",
    "fixture_manifest_digest",
    "identity_fingerprint",
    "lane_id",
    "normalization_kinds",
    "normalization_record_digests",
    "persistence_idempotent",
    "replay_verified",
    "rollback_digest",
    "rollback_verified",
    "shadow_profile_digest",
    "shadow_projection_digest",
    "update_plan_digest",
    "update_receipt_digest",
    "version",
}
_LANE_DIGEST_FIELDS = {
    "batch_plan_digest",
    "candidate_profile_digest",
    "compatibility_evidence_digest",
    "fixture_manifest_digest",
    "identity_fingerprint",
    "rollback_digest",
    "shadow_profile_digest",
    "shadow_projection_digest",
    "update_plan_digest",
    "update_receipt_digest",
}


def _validate_receipt_shape(document: Mapping[str, object]) -> None:
    if (
        set(document) != _RECEIPT_FIELDS
        or document.get("version") != ACCEPTANCE_VERSION
        or document.get("redacted_fields") != list(REDACTED_FIELDS)
        or not isinstance(document.get("receipt_digest"), str)
        or _DIGEST.fullmatch(str(document.get("receipt_digest"))) is None
        or not isinstance(document.get("schema_digest"), str)
        or _DIGEST.fullmatch(str(document.get("schema_digest"))) is None
    ):
        _reject("receipt")
    if any(document.get(flag) is not False for flag in INERT_SIDE_EFFECT_FLAGS):
        _reject("receipt")
    lanes = document.get("lanes")
    if not isinstance(lanes, list) or not 1 <= len(lanes) <= MAX_LANES:
        _reject("receipt")
    for lane in lanes:
        if not isinstance(lane, dict) or set(lane) != _LANE_FIELDS:
            _reject("receipt")
        if lane.get("version") != LANE_EVIDENCE_VERSION:
            _reject("receipt")
        if any(
            lane.get(field) is not True
            for field in (
                "persistence_idempotent",
                "replay_verified",
                "rollback_verified",
            )
        ):
            _reject("receipt")
        if any(
            not isinstance(lane.get(field), str)
            or _DIGEST.fullmatch(str(lane.get(field))) is None
            for field in _LANE_DIGEST_FIELDS
        ):
            _reject("receipt")
        try:
            _validate_identifier(lane.get("lane_id"), stage="receipt")
        except AcceptanceError:
            _reject("receipt")
        kinds = lane.get("normalization_kinds")
        digests = lane.get("normalization_record_digests")
        if (
            not isinstance(kinds, list)
            or not kinds
            or len(kinds) > 3
            or not all(kind in {"choice", "noul", "score"} for kind in kinds)
            or kinds != sorted(set(kinds))
            or not isinstance(digests, list)
            or not 1 <= len(digests) <= MAX_FIXTURES
            or not all(isinstance(item, str) and _DIGEST.fullmatch(item) for item in digests)
        ):
            _reject("receipt")


def verify_offline_acceptance(
    document: Mapping[str, object],
    schema: Mapping[str, object],
    lanes: Sequence[AdapterAcceptanceLane],
) -> OfflineAcceptanceReceipt:
    """Rebuild every stage and reject changed inputs, evidence, or inert flags."""

    if not isinstance(document, dict):
        _reject("receipt")
    _validate_receipt_shape(document)
    try:
        expected = run_offline_acceptance(schema, lanes)
        matches = update.canonical_json_bytes(document) == update.canonical_json_bytes(
            expected.to_dict(),
        )
    except AcceptanceError:
        raise
    except (TypeError, ValueError, RuntimeError, RecursionError):
        _reject("receipt")
    if not matches:
        _reject("receipt")
    return expected
