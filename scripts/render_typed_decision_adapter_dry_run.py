#!/usr/bin/env python3
"""Render a deterministic, inert OpenJev/Laya adapter update dry run.

This module deliberately stops before persistence.  It derives operator evidence
from the same compatibility, rollback, projection, batching, and normalization
contracts as the offline acceptance chain, but never writes a prepared update or
admits, downloads, invokes, or applies a candidate.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Never

from scripts import accept_typed_decision_adapter_candidate as acceptance
from scripts import check_typed_decision_adapter_update as update

DRY_RUN_REPORT_VERSION = "typed-decision-adapter-dry-run-report/v1"
DRY_RUN_LANE_VERSION = "typed-decision-adapter-dry-run-lane/v1"
DRY_RUN_COMPATIBILITY_VERSION = "typed-decision-adapter-dry-run-compatibility/v1"
DRY_RUN_PROVENANCE_VERSION = "typed-decision-adapter-dry-run-provenance/v1"
DRY_RUN_ROLLBACK_VERSION = "typed-decision-adapter-dry-run-rollback/v1"
DRY_RUN_RECOVERY_MODE_VERSION = "typed-decision-adapter-dry-run-recovery-mode/v1"
DRY_RUN_STAGE_VERSION = "typed-decision-adapter-dry-run-stage/v1"

DRY_RUN_STAGE_ORDER = acceptance.ACCEPTANCE_STAGE_ORDER
DRY_RUN_STAGE_STATUS = {
    "compatibility_update": "verified_offline",
    "atomic_persistence": "planned_not_persisted",
    "rollback_ready": "verified_not_durable",
    "shadow_projection": "validated_not_applied",
    "batch_residency": "planned_not_applied",
    "normalization_replay": "verified_offline",
}
DRY_RUN_INERT_FLAGS = (
    "admission_enabled",
    "admission_performed",
    "apply_performed",
    "external_mutation_performed",
    "model_invoked",
    "models_downloaded",
    "network_accessed",
    "persistence_performed",
    "recovery_performed",
    "runtime_changed",
    "runtime_invoked",
)
STAGE_INERT_FLAGS = (
    "admission_performed",
    "apply_performed",
    "external_mutation_performed",
    "model_invoked",
    "models_downloaded",
    "network_accessed",
    "persistence_performed",
    "runtime_changed",
    "runtime_invoked",
)
REDACTED_FIELDS = (
    "actor_id",
    "credentials",
    "fixture.fixture_id",
    "lane_id",
    "profile_id",
    "request.request_id",
    "request.state",
    "response.answers",
    "source_pin.commit",
    "source_pin.release",
    "source_pin.repository",
    "transport.endpoint",
)
RECOVERY_MODES = (
    ("prepared_update_replay", "dry_run_not_persisted"),
    ("projection_resume", "dry_run_projection_not_applied"),
    ("runtime_rollback", "dry_run_runtime_unchanged"),
)

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class DryRunReportError(RuntimeError):
    """Bounded fail-closed error which never renders operator input."""


def _reject(stage: str) -> Never:
    raise DryRunReportError(f"adapter dry-run rejected: {stage}")


def _reference(domain: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        _reject("compatibility")
    return update.canonical_digest(
        {
            "domain": f"typed-decision-adapter-dry-run/{domain}",
            "value": value,
        },
    )


def _object(parent: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = parent.get(key)
    if not isinstance(value, dict):
        _reject("compatibility")
    return value


def _text(parent: Mapping[str, object], key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value:
        _reject("compatibility")
    return value


@dataclass(frozen=True, slots=True)
class DryRunCompatibility:
    """Exact compatibility outcome for an update which remains unapplied."""

    status: str
    baseline_profile_digest: str
    candidate_profile_digest: str
    evidence_digest: str
    plan_digest: str
    receipt_digest: str
    prepared_update_digest: str
    changed_pin_paths: tuple[str, ...]
    protected_sections_verified: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "baseline_profile_digest": self.baseline_profile_digest,
            "candidate_profile_digest": self.candidate_profile_digest,
            "changed_pin_paths": list(self.changed_pin_paths),
            "evidence_digest": self.evidence_digest,
            "plan_digest": self.plan_digest,
            "prepared_update_digest": self.prepared_update_digest,
            "protected_sections_verified": list(self.protected_sections_verified),
            "receipt_digest": self.receipt_digest,
            "status": self.status,
            "version": DRY_RUN_COMPATIBILITY_VERSION,
        }


@dataclass(frozen=True, slots=True)
class DryRunProvenance:
    """Secret-safe references to the exact source and identity pins."""

    schema_digest: str
    repository_reference: str
    baseline_source_reference: str
    candidate_source_reference: str
    baseline_identity_digest: str
    candidate_identity_digest: str
    baseline_commit_reference: str
    candidate_commit_reference: str
    baseline_release_reference: str
    candidate_release_reference: str
    baseline_archive_digest: str
    candidate_archive_digest: str
    candidate_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "baseline_archive_digest": self.baseline_archive_digest,
            "baseline_commit_reference": self.baseline_commit_reference,
            "baseline_identity_digest": self.baseline_identity_digest,
            "baseline_release_reference": self.baseline_release_reference,
            "baseline_source_reference": self.baseline_source_reference,
            "candidate_archive_digest": self.candidate_archive_digest,
            "candidate_commit_reference": self.candidate_commit_reference,
            "candidate_fingerprint": self.candidate_fingerprint,
            "candidate_identity_digest": self.candidate_identity_digest,
            "candidate_release_reference": self.candidate_release_reference,
            "candidate_source_reference": self.candidate_source_reference,
            "repository_reference": self.repository_reference,
            "schema_digest": self.schema_digest,
            "version": DRY_RUN_PROVENANCE_VERSION,
        }


@dataclass(frozen=True, slots=True)
class DryRunRollbackBoundary:
    """Rollback proof at the dry-run boundary before any durable mutation."""

    boundary: str
    baseline_profile_digest: str
    rollback_digest: str
    target_state: str
    required: bool
    durable: bool
    candidate_applied: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "baseline_profile_digest": self.baseline_profile_digest,
            "boundary": self.boundary,
            "candidate_applied": self.candidate_applied,
            "durable": self.durable,
            "required": self.required,
            "rollback_digest": self.rollback_digest,
            "target_state": self.target_state,
            "version": DRY_RUN_ROLLBACK_VERSION,
        }


@dataclass(frozen=True, slots=True)
class DryRunRecoveryMode:
    """One explicitly disabled recovery path and its stable reason."""

    mode: str
    enabled: bool
    reason_code: str

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "reason_code": self.reason_code,
            "version": DRY_RUN_RECOVERY_MODE_VERSION,
        }


@dataclass(frozen=True, slots=True)
class DryRunStage:
    """One ordered, predecessor-bound, side-effect-free dry-run stage."""

    ordinal: int
    stage: str
    status: str
    predecessor_digest: str
    evidence_digest: str
    admission_performed: bool
    apply_performed: bool
    external_mutation_performed: bool
    model_invoked: bool
    models_downloaded: bool
    network_accessed: bool
    persistence_performed: bool
    runtime_changed: bool
    runtime_invoked: bool
    stage_digest: str

    def body_dict(self) -> dict[str, object]:
        return {
            "admission_performed": self.admission_performed,
            "apply_performed": self.apply_performed,
            "evidence_digest": self.evidence_digest,
            "external_mutation_performed": self.external_mutation_performed,
            "model_invoked": self.model_invoked,
            "models_downloaded": self.models_downloaded,
            "network_accessed": self.network_accessed,
            "ordinal": self.ordinal,
            "persistence_performed": self.persistence_performed,
            "predecessor_digest": self.predecessor_digest,
            "runtime_changed": self.runtime_changed,
            "runtime_invoked": self.runtime_invoked,
            "stage": self.stage,
            "status": self.status,
            "version": DRY_RUN_STAGE_VERSION,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.body_dict(), "stage_digest": self.stage_digest}


@dataclass(frozen=True, slots=True)
class DryRunLaneReport:
    """Operator-visible evidence for one provider-neutral candidate lane."""

    lane_reference: str
    profile_reference: str
    compatibility: DryRunCompatibility
    provenance: DryRunProvenance
    rollback: DryRunRollbackBoundary
    recovery_modes: tuple[DryRunRecoveryMode, ...]
    stages: tuple[DryRunStage, ...]
    batch_plan_digest: str
    fixture_manifest_digest: str
    normalization_record_digests: tuple[str, ...]
    normalization_kinds: tuple[str, ...]
    lane_digest: str

    def body_dict(self) -> dict[str, object]:
        return {
            "batch_plan_digest": self.batch_plan_digest,
            "compatibility": self.compatibility.to_dict(),
            "fixture_manifest_digest": self.fixture_manifest_digest,
            "lane_reference": self.lane_reference,
            "normalization_kinds": list(self.normalization_kinds),
            "normalization_record_digests": list(self.normalization_record_digests),
            "profile_reference": self.profile_reference,
            "provenance": self.provenance.to_dict(),
            "recovery_modes": [mode.to_dict() for mode in self.recovery_modes],
            "rollback": self.rollback.to_dict(),
            "stages": [stage.to_dict() for stage in self.stages],
            "version": DRY_RUN_LANE_VERSION,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.body_dict(), "lane_digest": self.lane_digest}


@dataclass(frozen=True, slots=True)
class TypedDecisionAdapterDryRunReport:
    """Schema-versioned, deterministic JSON report for operator review."""

    schema_digest: str
    lanes: tuple[DryRunLaneReport, ...]
    redacted_fields: tuple[str, ...]
    admission_enabled: bool
    admission_performed: bool
    apply_performed: bool
    external_mutation_performed: bool
    model_invoked: bool
    models_downloaded: bool
    network_accessed: bool
    persistence_performed: bool
    recovery_performed: bool
    runtime_changed: bool
    runtime_invoked: bool
    report_digest: str

    def body_dict(self) -> dict[str, object]:
        return {
            "admission_enabled": self.admission_enabled,
            "admission_performed": self.admission_performed,
            "apply_performed": self.apply_performed,
            "external_mutation_performed": self.external_mutation_performed,
            "lanes": [lane.to_dict() for lane in self.lanes],
            "model_invoked": self.model_invoked,
            "models_downloaded": self.models_downloaded,
            "network_accessed": self.network_accessed,
            "persistence_performed": self.persistence_performed,
            "recovery_performed": self.recovery_performed,
            "redacted_fields": list(self.redacted_fields),
            "runtime_changed": self.runtime_changed,
            "runtime_invoked": self.runtime_invoked,
            "schema_digest": self.schema_digest,
            "version": DRY_RUN_REPORT_VERSION,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.body_dict(), "report_digest": self.report_digest}


def _build_provenance(
    schema_digest: str,
    lane: acceptance.AdapterAcceptanceLane,
    prepared: update.PreparedUpdate,
) -> DryRunProvenance:
    baseline_source = _object(lane.baseline_profile, "source_pin")
    candidate_source = _object(lane.candidate_profile, "source_pin")
    baseline_identity = _object(lane.baseline_profile, "identity")
    candidate_identity = _object(lane.candidate_profile, "identity")
    repository = _text(candidate_source, "repository")
    if _text(baseline_source, "repository") != repository:
        _reject("compatibility")
    return DryRunProvenance(
        schema_digest=schema_digest,
        repository_reference=_reference("source_repository", repository),
        baseline_source_reference=update.canonical_digest(dict(baseline_source)),
        candidate_source_reference=update.canonical_digest(dict(candidate_source)),
        baseline_identity_digest=update.canonical_digest(dict(baseline_identity)),
        candidate_identity_digest=update.canonical_digest(dict(candidate_identity)),
        baseline_commit_reference=_reference(
            "source_commit",
            prepared.plan.baseline.source_commit,
        ),
        candidate_commit_reference=_reference(
            "source_commit",
            prepared.plan.candidate.source_commit,
        ),
        baseline_release_reference=_reference(
            "source_release",
            prepared.plan.baseline.source_release,
        ),
        candidate_release_reference=_reference(
            "source_release",
            prepared.plan.candidate.source_release,
        ),
        baseline_archive_digest=prepared.plan.baseline.source_archive_digest,
        candidate_archive_digest=prepared.plan.candidate.source_archive_digest,
        candidate_fingerprint=prepared.plan.candidate.fingerprint,
    )


def _build_stages(
    root_digest: str,
    evidence_by_stage: Mapping[str, str],
) -> tuple[DryRunStage, ...]:
    if _DIGEST.fullmatch(root_digest) is None or set(evidence_by_stage) != set(
        DRY_RUN_STAGE_ORDER,
    ):
        _reject("report")
    predecessor = root_digest
    stages: list[DryRunStage] = []
    for ordinal, stage_name in enumerate(DRY_RUN_STAGE_ORDER, start=1):
        evidence_digest = evidence_by_stage[stage_name]
        if _DIGEST.fullmatch(evidence_digest) is None:
            _reject("report")
        without_digest = DryRunStage(
            ordinal=ordinal,
            stage=stage_name,
            status=DRY_RUN_STAGE_STATUS[stage_name],
            predecessor_digest=predecessor,
            evidence_digest=evidence_digest,
            admission_performed=False,
            apply_performed=False,
            external_mutation_performed=False,
            model_invoked=False,
            models_downloaded=False,
            network_accessed=False,
            persistence_performed=False,
            runtime_changed=False,
            runtime_invoked=False,
            stage_digest="",
        )
        current = DryRunStage(
            ordinal=without_digest.ordinal,
            stage=without_digest.stage,
            status=without_digest.status,
            predecessor_digest=without_digest.predecessor_digest,
            evidence_digest=without_digest.evidence_digest,
            admission_performed=False,
            apply_performed=False,
            external_mutation_performed=False,
            model_invoked=False,
            models_downloaded=False,
            network_accessed=False,
            persistence_performed=False,
            runtime_changed=False,
            runtime_invoked=False,
            stage_digest=update.canonical_digest(without_digest.body_dict()),
        )
        stages.append(current)
        predecessor = current.stage_digest
    return tuple(stages)


def _build_lane(
    schema: Mapping[str, object],
    schema_digest: str,
    lane: acceptance.AdapterAcceptanceLane,
) -> DryRunLaneReport:
    try:
        lane_id = acceptance._validate_identifier(lane.lane_id, stage="lane")
        prepared = acceptance._prepare_update(schema, lane)
    except (acceptance.AcceptanceError, update.ContractInputError, update.UpdatePlanError):
        _reject("compatibility")

    try:
        projection_digest = acceptance._validate_shadow_projection(
            lane.candidate_profile,
            lane.shadow_profile,
        )
    except acceptance.AcceptanceError:
        _reject("shadow_projection")
    try:
        batch = acceptance._plan_batch(schema, lane)
    except acceptance.AcceptanceError:
        _reject("batch_residency")
    try:
        linked = acceptance._validate_batch_normalization_link(
            lane.candidate_profile,
            lane.requests,
            lane.normalization_fixtures,
        )
        record_digests, kinds, fixture_manifest_digest = (
            acceptance._normalize_linked_fixtures(
                schema,
                lane.candidate_profile,
                linked,
            )
        )
    except acceptance.AcceptanceError:
        _reject("normalization_replay")

    plan = prepared.plan
    receipt = prepared.receipt
    rollback = plan.rollback
    if (
        not acceptance._rollback_is_verified(prepared, lane)
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
        _reject("rollback")

    lane_reference = acceptance._reference("lane_id", lane_id)
    profile_reference = _reference("profile_id", plan.candidate.profile_id)
    prepared_digest = update.canonical_digest(prepared.to_dict())
    normalization_digest = update.canonical_digest(
        {
            "fixture_manifest_digest": fixture_manifest_digest,
            "normalization_record_digests": list(record_digests),
            "version": "typed-decision-adapter-dry-run-normalization/v1",
        },
    )
    stage_root = update.canonical_digest(
        {
            "candidate_profile_digest": plan.candidate.profile_digest,
            "lane_reference": lane_reference,
            "profile_reference": profile_reference,
            "schema_digest": schema_digest,
            "version": "typed-decision-adapter-dry-run-stage-root/v1",
        },
    )
    stages = _build_stages(
        stage_root,
        {
            "atomic_persistence": prepared_digest,
            "batch_residency": batch.plan_digest,
            "compatibility_update": plan.compatibility_evidence.evidence_digest,
            "normalization_replay": normalization_digest,
            "rollback_ready": rollback.rollback_digest,
            "shadow_projection": projection_digest,
        },
    )
    compatibility = DryRunCompatibility(
        status=plan.compatibility_evidence.checker_status,
        baseline_profile_digest=plan.baseline.profile_digest,
        candidate_profile_digest=plan.candidate.profile_digest,
        evidence_digest=plan.compatibility_evidence.evidence_digest,
        plan_digest=plan.plan_digest,
        receipt_digest=receipt.receipt_digest,
        prepared_update_digest=prepared_digest,
        changed_pin_paths=plan.compatibility_evidence.changed_pin_paths,
        protected_sections_verified=plan.compatibility_evidence.protected_sections_verified,
    )
    provenance = _build_provenance(schema_digest, lane, prepared)
    rollback_boundary = DryRunRollbackBoundary(
        boundary="before_persistence_and_projection",
        baseline_profile_digest=rollback.baseline_profile_digest,
        rollback_digest=rollback.rollback_digest,
        target_state="disabled",
        required=True,
        durable=False,
        candidate_applied=False,
    )
    modes = tuple(
        DryRunRecoveryMode(mode=mode, enabled=False, reason_code=reason)
        for mode, reason in RECOVERY_MODES
    )
    without_digest = DryRunLaneReport(
        lane_reference=lane_reference,
        profile_reference=profile_reference,
        compatibility=compatibility,
        provenance=provenance,
        rollback=rollback_boundary,
        recovery_modes=modes,
        stages=stages,
        batch_plan_digest=batch.plan_digest,
        fixture_manifest_digest=fixture_manifest_digest,
        normalization_record_digests=record_digests,
        normalization_kinds=kinds,
        lane_digest="",
    )
    return DryRunLaneReport(
        lane_reference=without_digest.lane_reference,
        profile_reference=without_digest.profile_reference,
        compatibility=without_digest.compatibility,
        provenance=without_digest.provenance,
        rollback=without_digest.rollback,
        recovery_modes=without_digest.recovery_modes,
        stages=without_digest.stages,
        batch_plan_digest=without_digest.batch_plan_digest,
        fixture_manifest_digest=without_digest.fixture_manifest_digest,
        normalization_record_digests=without_digest.normalization_record_digests,
        normalization_kinds=without_digest.normalization_kinds,
        lane_digest=update.canonical_digest(without_digest.body_dict()),
    )


def build_dry_run_report(
    schema: Mapping[str, object],
    lanes: Sequence[acceptance.AdapterAcceptanceLane],
) -> TypedDecisionAdapterDryRunReport:
    """Build a complete report without persistence, network, or runtime effects."""

    if not isinstance(schema, dict) or not 1 <= len(lanes) <= acceptance.MAX_LANES:
        _reject("inputs")
    try:
        lane_ids = [acceptance._validate_identifier(lane.lane_id, stage="lane") for lane in lanes]
        schema_digest = update.canonical_digest(dict(schema))
    except (acceptance.AcceptanceError, update.UpdatePlanError):
        _reject("inputs")
    if len(lane_ids) != len(set(lane_ids)):
        _reject("inputs")
    lane_reports = tuple(
        _build_lane(schema, schema_digest, lane)
        for lane in sorted(lanes, key=lambda item: item.lane_id)
    )
    if {kind for lane in lane_reports for kind in lane.normalization_kinds} != {
        "choice",
        "noul",
        "score",
    }:
        _reject("fixture_coverage")
    without_digest = TypedDecisionAdapterDryRunReport(
        schema_digest=schema_digest,
        lanes=lane_reports,
        redacted_fields=REDACTED_FIELDS,
        admission_enabled=False,
        admission_performed=False,
        apply_performed=False,
        external_mutation_performed=False,
        model_invoked=False,
        models_downloaded=False,
        network_accessed=False,
        persistence_performed=False,
        recovery_performed=False,
        runtime_changed=False,
        runtime_invoked=False,
        report_digest="",
    )
    return TypedDecisionAdapterDryRunReport(
        schema_digest=without_digest.schema_digest,
        lanes=without_digest.lanes,
        redacted_fields=without_digest.redacted_fields,
        admission_enabled=False,
        admission_performed=False,
        apply_performed=False,
        external_mutation_performed=False,
        model_invoked=False,
        models_downloaded=False,
        network_accessed=False,
        persistence_performed=False,
        recovery_performed=False,
        runtime_changed=False,
        runtime_invoked=False,
        report_digest=update.canonical_digest(without_digest.body_dict()),
    )


_REPORT_FIELDS = {
    *DRY_RUN_INERT_FLAGS,
    "lanes",
    "redacted_fields",
    "report_digest",
    "schema_digest",
    "version",
}
_LANE_FIELDS = {
    "batch_plan_digest",
    "compatibility",
    "fixture_manifest_digest",
    "lane_digest",
    "lane_reference",
    "normalization_kinds",
    "normalization_record_digests",
    "profile_reference",
    "provenance",
    "recovery_modes",
    "rollback",
    "stages",
    "version",
}
_COMPATIBILITY_FIELDS = {
    "baseline_profile_digest",
    "candidate_profile_digest",
    "changed_pin_paths",
    "evidence_digest",
    "plan_digest",
    "prepared_update_digest",
    "protected_sections_verified",
    "receipt_digest",
    "status",
    "version",
}
_PROVENANCE_FIELDS = {
    "baseline_archive_digest",
    "baseline_commit_reference",
    "baseline_identity_digest",
    "baseline_release_reference",
    "baseline_source_reference",
    "candidate_archive_digest",
    "candidate_commit_reference",
    "candidate_fingerprint",
    "candidate_identity_digest",
    "candidate_release_reference",
    "candidate_source_reference",
    "repository_reference",
    "schema_digest",
    "version",
}
_ROLLBACK_FIELDS = {
    "baseline_profile_digest",
    "boundary",
    "candidate_applied",
    "durable",
    "required",
    "rollback_digest",
    "target_state",
    "version",
}
_RECOVERY_FIELDS = {"enabled", "mode", "reason_code", "version"}
_STAGE_FIELDS = {
    *STAGE_INERT_FLAGS,
    "evidence_digest",
    "ordinal",
    "predecessor_digest",
    "stage",
    "stage_digest",
    "status",
    "version",
}


def _has_digests(document: Mapping[str, object], fields: Sequence[str]) -> bool:
    return all(
        isinstance(document.get(field), str)
        and _DIGEST.fullmatch(str(document.get(field))) is not None
        for field in fields
    )


def _digest_without(document: Mapping[str, object], excluded: str) -> str:
    return update.canonical_digest(
        {key: value for key, value in document.items() if key != excluded},
    )


def _validate_lane_shape(lane: Mapping[str, object], schema_digest: str) -> None:
    if (
        set(lane) != _LANE_FIELDS
        or lane.get("version") != DRY_RUN_LANE_VERSION
        or not _has_digests(
            lane,
            (
                "batch_plan_digest",
                "fixture_manifest_digest",
                "lane_digest",
                "lane_reference",
                "profile_reference",
            ),
        )
        or _digest_without(lane, "lane_digest") != lane.get("lane_digest")
    ):
        _reject("report")

    compatibility = lane.get("compatibility")
    provenance = lane.get("provenance")
    rollback = lane.get("rollback")
    recovery_modes = lane.get("recovery_modes")
    stages = lane.get("stages")
    if not isinstance(compatibility, dict) or set(compatibility) != _COMPATIBILITY_FIELDS:
        _reject("report")
    if (
        compatibility.get("version") != DRY_RUN_COMPATIBILITY_VERSION
        or compatibility.get("status") != update.CheckStatus.COMPATIBLE_PINNED_UPDATE.value
        or not _has_digests(
            compatibility,
            (
                "baseline_profile_digest",
                "candidate_profile_digest",
                "evidence_digest",
                "plan_digest",
                "prepared_update_digest",
                "receipt_digest",
            ),
        )
        or not isinstance(compatibility.get("changed_pin_paths"), list)
        or not isinstance(compatibility.get("protected_sections_verified"), list)
    ):
        _reject("report")
    if not isinstance(provenance, dict) or set(provenance) != _PROVENANCE_FIELDS:
        _reject("report")
    if (
        provenance.get("version") != DRY_RUN_PROVENANCE_VERSION
        or provenance.get("schema_digest") != schema_digest
        or not _has_digests(
            provenance,
            tuple(_PROVENANCE_FIELDS - {"version"}),
        )
    ):
        _reject("report")
    if not isinstance(rollback, dict) or set(rollback) != _ROLLBACK_FIELDS:
        _reject("report")
    if (
        rollback.get("version") != DRY_RUN_ROLLBACK_VERSION
        or rollback.get("boundary") != "before_persistence_and_projection"
        or rollback.get("target_state") != "disabled"
        or rollback.get("required") is not True
        or rollback.get("durable") is not False
        or rollback.get("candidate_applied") is not False
        or not _has_digests(
            rollback,
            ("baseline_profile_digest", "rollback_digest"),
        )
    ):
        _reject("report")
    if not isinstance(recovery_modes, list) or len(recovery_modes) != len(RECOVERY_MODES):
        _reject("report")
    for document, (mode, reason) in zip(recovery_modes, RECOVERY_MODES, strict=True):
        if (
            not isinstance(document, dict)
            or set(document) != _RECOVERY_FIELDS
            or document.get("version") != DRY_RUN_RECOVERY_MODE_VERSION
            or document.get("mode") != mode
            or document.get("reason_code") != reason
            or document.get("enabled") is not False
        ):
            _reject("report")
    if not isinstance(stages, list) or len(stages) != len(DRY_RUN_STAGE_ORDER):
        _reject("report")
    predecessor: object | None = None
    for ordinal, (stage_name, stage) in enumerate(
        zip(DRY_RUN_STAGE_ORDER, stages, strict=True),
        start=1,
    ):
        if not isinstance(stage, dict) or set(stage) != _STAGE_FIELDS:
            _reject("report")
        if ordinal == 1:
            predecessor = stage.get("predecessor_digest")
            if not isinstance(predecessor, str) or _DIGEST.fullmatch(predecessor) is None:
                _reject("report")
        if (
            stage.get("version") != DRY_RUN_STAGE_VERSION
            or stage.get("stage") != stage_name
            or stage.get("status") != DRY_RUN_STAGE_STATUS[stage_name]
            or isinstance(stage.get("ordinal"), bool)
            or stage.get("ordinal") != ordinal
            or stage.get("predecessor_digest") != predecessor
            or any(stage.get(flag) is not False for flag in STAGE_INERT_FLAGS)
            or not _has_digests(
                stage,
                ("evidence_digest", "predecessor_digest", "stage_digest"),
            )
            or _digest_without(stage, "stage_digest") != stage.get("stage_digest")
        ):
            _reject("report")
        predecessor = stage.get("stage_digest")
    kinds = lane.get("normalization_kinds")
    records = lane.get("normalization_record_digests")
    if (
        not isinstance(kinds, list)
        or kinds != sorted(set(kinds))
        or not kinds
        or any(kind not in {"choice", "noul", "score"} for kind in kinds)
        or not isinstance(records, list)
        or not records
        or len(records) > acceptance.MAX_FIXTURES
        or any(not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in records)
    ):
        _reject("report")


def _validate_report_shape(document: Mapping[str, object]) -> None:
    if (
        set(document) != _REPORT_FIELDS
        or document.get("version") != DRY_RUN_REPORT_VERSION
        or document.get("redacted_fields") != list(REDACTED_FIELDS)
        or any(document.get(flag) is not False for flag in DRY_RUN_INERT_FLAGS)
        or not _has_digests(document, ("report_digest", "schema_digest"))
        or _digest_without(document, "report_digest") != document.get("report_digest")
    ):
        _reject("report")
    lanes = document.get("lanes")
    if not isinstance(lanes, list) or not 1 <= len(lanes) <= acceptance.MAX_LANES:
        _reject("report")
    schema_digest = document.get("schema_digest")
    assert isinstance(schema_digest, str)
    for lane in lanes:
        if not isinstance(lane, dict):
            _reject("report")
        _validate_lane_shape(lane, schema_digest)


def verify_dry_run_report(
    document: Mapping[str, object],
    schema: Mapping[str, object],
    lanes: Sequence[acceptance.AdapterAcceptanceLane],
) -> TypedDecisionAdapterDryRunReport:
    """Replay a report and reject tamper, input reuse, or side-effect drift."""

    if not isinstance(document, dict):
        _reject("report")
    try:
        _validate_report_shape(document)
        expected = build_dry_run_report(schema, lanes)
        matches = update.canonical_json_bytes(document) == update.canonical_json_bytes(
            expected.to_dict(),
        )
    except DryRunReportError:
        raise
    except (TypeError, ValueError, RuntimeError, RecursionError):
        _reject("report")
    if not matches:
        _reject("report")
    return expected


def render_dry_run_report(report: TypedDecisionAdapterDryRunReport) -> bytes:
    """Render canonical, newline-terminated operator JSON after digest replay."""

    if update.canonical_digest(report.body_dict()) != report.report_digest:
        _reject("report")
    return update.canonical_json_bytes(report.to_dict()) + b"\n"
