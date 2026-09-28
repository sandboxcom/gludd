#!/usr/bin/env python3
"""Build offline batching and residency evidence for admitted adapter profiles.

The planner is deliberately side-effect free. It consumes already-local pinned
profiles and bounded metadata, then emits an immutable plan. It never fetches,
admits, downloads, preloads, evicts, or invokes an adapter or model runtime.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from scripts.check_typed_decision_adapter_update import (
    UpdatePlanError,
    canonical_digest,
    canonical_json_bytes,
    validate_profile,
)

PLAN_VERSION = "typed-decision-adapter-batch-residency-plan/v1"
EVIDENCE_VERSION = "typed-decision-adapter-batch-residency-evidence/v1"

MAX_PLANNER_REQUESTS = 1024
MAX_PLANNER_PROFILES = 64
MAX_RESIDENT_IDENTITIES = 64
MAX_RESIDENT_BYTES = 1 << 50
MAX_TOTAL_REQUEST_BYTES = 1 << 30

ADMITTED_PROFILE_STATES = ("shadow", "canary_1", "canary_5", "canary_25")
REQUIRED_GROUPING_KEYS = (
    "identity_fingerprint",
    "schema_version",
    "question_schema_digest",
    "token_budget",
    "calibration_digest",
    "trust_sensitivity_class",
    "deadline_bucket",
)
REDACTED_BATCH_FIELDS = (
    "answers",
    "credentials",
    "questions",
    "request_id",
    "state",
    "transport.endpoint",
)

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_MOVING_ALIAS = re.compile(
    r"(?:^|[-_.@/])(latest|preview|main|master|example|unresolved)(?:$|[-_.@/])",
    re.IGNORECASE,
)
_IDENTITY_DIGEST_FIELDS = (
    "fingerprint",
    "server_digest",
    "model_digest",
    "tokenizer_digest",
    "renderer_digest",
    "calibration_digest",
    "runtime_digest",
)


class PlannerInputError(RuntimeError):
    """Raised when an offline plan input or replay evidence cannot be trusted."""


@dataclass(frozen=True, slots=True)
class PlannerBounds:
    """Explicit hard bounds for one planning operation."""

    max_requests: int
    max_profiles: int
    max_loaded_identities: int
    resident_byte_budget: int
    max_total_request_bytes: int

    def __post_init__(self) -> None:
        checks = (
            ("max_requests", self.max_requests, 1, MAX_PLANNER_REQUESTS),
            ("max_profiles", self.max_profiles, 1, MAX_PLANNER_PROFILES),
            (
                "max_loaded_identities",
                self.max_loaded_identities,
                0,
                MAX_RESIDENT_IDENTITIES,
            ),
            (
                "resident_byte_budget",
                self.resident_byte_budget,
                0,
                MAX_RESIDENT_BYTES,
            ),
            (
                "max_total_request_bytes",
                self.max_total_request_bytes,
                1,
                MAX_TOTAL_REQUEST_BYTES,
            ),
        )
        for name, value, minimum, maximum in checks:
            if isinstance(value, bool) or not minimum <= value <= maximum:
                raise PlannerInputError(
                    f"{name} must be between {minimum} and {maximum}",
                )

    def to_dict(self) -> dict[str, object]:
        """Return canonical bound evidence."""

        return {
            "max_loaded_identities": self.max_loaded_identities,
            "max_profiles": self.max_profiles,
            "max_requests": self.max_requests,
            "max_total_request_bytes": self.max_total_request_bytes,
            "resident_byte_budget": self.resident_byte_budget,
        }


@dataclass(frozen=True, slots=True)
class BatchRequest:
    """Bounded request metadata; payload, state, credentials, and answers are absent."""

    request_id: str
    profile_id: str
    tenant_digest: str
    schema_version: str
    question_schema_digest: str
    token_budget: int
    trust_sensitivity_class: str
    deadline_bucket_ms: int
    question_types: tuple[str, ...]
    state_shape: str
    state_bytes: int
    question_count: int
    max_choice_options: int
    max_score_levels: int
    request_bytes: int

    def to_dict(self) -> dict[str, object]:
        """Return input fields for test/build reconstruction, including request ID."""

        return {
            "deadline_bucket_ms": self.deadline_bucket_ms,
            "max_choice_options": self.max_choice_options,
            "max_score_levels": self.max_score_levels,
            "profile_id": self.profile_id,
            "question_count": self.question_count,
            "question_schema_digest": self.question_schema_digest,
            "question_types": self.question_types,
            "request_bytes": self.request_bytes,
            "request_id": self.request_id,
            "schema_version": self.schema_version,
            "state_bytes": self.state_bytes,
            "state_shape": self.state_shape,
            "tenant_digest": self.tenant_digest,
            "token_budget": self.token_budget,
            "trust_sensitivity_class": self.trust_sensitivity_class,
        }


@dataclass(frozen=True, slots=True)
class ResidentIdentity:
    """Measured local residency state; no model path or runtime handle is accepted."""

    fingerprint: str
    measured_bytes: int
    lease_count: int
    inflight_count: int
    last_used_sequence: int

    def to_dict(self) -> dict[str, object]:
        """Return canonical snapshot evidence."""

        return {
            "fingerprint": self.fingerprint,
            "inflight_count": self.inflight_count,
            "last_used_sequence": self.last_used_sequence,
            "lease_count": self.lease_count,
            "measured_bytes": self.measured_bytes,
        }


@dataclass(frozen=True, slots=True)
class ResidencySnapshot:
    """Immutable measured identities present before planning."""

    loaded: tuple[ResidentIdentity, ...]

    def to_dict(self) -> dict[str, object]:
        """Return canonical snapshot evidence independent of input ordering."""

        return {
            "loaded": [
                item.to_dict()
                for item in sorted(self.loaded, key=lambda resident: resident.fingerprint)
            ],
        }


@dataclass(frozen=True, slots=True)
class IdentityMeasurement:
    """Measured bytes required by one not-yet-resident exact identity."""

    fingerprint: str
    measured_bytes: int

    def to_dict(self) -> dict[str, object]:
        """Return canonical measurement evidence."""

        return {
            "fingerprint": self.fingerprint,
            "measured_bytes": self.measured_bytes,
        }


@dataclass(frozen=True, slots=True)
class GroupingEvidence:
    """Exact capability and isolation key for one batch group."""

    profile_id: str
    tenant_digest: str
    identity_fingerprint: str
    schema_version: str
    question_schema_digest: str
    token_budget: int
    calibration_digest: str
    trust_sensitivity_class: str
    deadline_bucket_ms: int

    def to_dict(self) -> dict[str, object]:
        """Return canonical grouping evidence."""

        return {
            "calibration_digest": self.calibration_digest,
            "deadline_bucket_ms": self.deadline_bucket_ms,
            "identity_fingerprint": self.identity_fingerprint,
            "profile_id": self.profile_id,
            "question_schema_digest": self.question_schema_digest,
            "schema_version": self.schema_version,
            "tenant_digest": self.tenant_digest,
            "token_budget": self.token_budget,
            "trust_sensitivity_class": self.trust_sensitivity_class,
        }


@dataclass(frozen=True, slots=True)
class BatchMember:
    """Redacted request reference retaining original order evidence."""

    ordinal: int
    request_digest: str

    def to_dict(self) -> dict[str, object]:
        """Return canonical member evidence."""

        return {"ordinal": self.ordinal, "request_digest": self.request_digest}


@dataclass(frozen=True, slots=True)
class BatchGroup:
    """One bounded batch or singleton decision."""

    group_id: str
    group_key_digest: str
    key: GroupingEvidence
    members: tuple[BatchMember, ...]
    member_count: int
    max_batch_size: int
    max_wait_ms: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        """Return canonical group evidence."""

        return {
            "group_id": self.group_id,
            "group_key_digest": self.group_key_digest,
            "key": self.key.to_dict(),
            "max_batch_size": self.max_batch_size,
            "max_wait_ms": self.max_wait_ms,
            "member_count": self.member_count,
            "members": [member.to_dict() for member in self.members],
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ResidencyDecision:
    """Non-executing residency intent or rejection for one requested identity."""

    fingerprint: str
    ownership: str
    decision: str
    reason: str
    measured_bytes: int

    def to_dict(self) -> dict[str, object]:
        """Return canonical residency evidence."""

        return {
            "decision": self.decision,
            "fingerprint": self.fingerprint,
            "measured_bytes": self.measured_bytes,
            "ownership": self.ownership,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class EvictionDecision:
    """Non-executing retain/eviction intent for one resident identity."""

    fingerprint: str
    decision: str
    reason: str
    measured_bytes: int

    def to_dict(self) -> dict[str, object]:
        """Return canonical eviction evidence."""

        return {
            "decision": self.decision,
            "fingerprint": self.fingerprint,
            "measured_bytes": self.measured_bytes,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RedactedPlanEvidence:
    """Content-addressed evidence with payload-bearing fields explicitly absent."""

    profile_digests: tuple[str, ...]
    request_evidence_digest: str
    snapshot_digest: str
    measurement_digest: str
    redacted_fields: tuple[str, ...]
    evidence_digest: str

    def body_dict(self) -> dict[str, object]:
        """Return the digest-covered evidence body."""

        return {
            "measurement_digest": self.measurement_digest,
            "profile_digests": list(self.profile_digests),
            "redacted_fields": list(self.redacted_fields),
            "request_evidence_digest": self.request_evidence_digest,
            "snapshot_digest": self.snapshot_digest,
            "version": EVIDENCE_VERSION,
        }

    def to_dict(self) -> dict[str, object]:
        """Return canonical evidence."""

        return {**self.body_dict(), "evidence_digest": self.evidence_digest}


@dataclass(frozen=True, slots=True)
class BatchResidencyPlan:
    """Immutable offline plan with explicit proof of absent runtime side effects."""

    schema_digest: str
    bounds: PlannerBounds
    groups: tuple[BatchGroup, ...]
    residency: tuple[ResidencyDecision, ...]
    evictions: tuple[EvictionDecision, ...]
    ready: bool
    reasons: tuple[str, ...]
    evidence: RedactedPlanEvidence
    admission_performed: bool
    network_accessed: bool
    runtime_invoked: bool
    models_downloaded: bool
    plan_digest: str

    def body_dict(self) -> dict[str, object]:
        """Return the digest-covered plan body."""

        return {
            "admission_performed": self.admission_performed,
            "bounds": self.bounds.to_dict(),
            "evidence": self.evidence.to_dict(),
            "evictions": [item.to_dict() for item in self.evictions],
            "groups": [group.to_dict() for group in self.groups],
            "models_downloaded": self.models_downloaded,
            "network_accessed": self.network_accessed,
            "ready": self.ready,
            "reasons": list(self.reasons),
            "residency": [item.to_dict() for item in self.residency],
            "runtime_invoked": self.runtime_invoked,
            "schema_digest": self.schema_digest,
            "version": PLAN_VERSION,
        }

    def to_dict(self) -> dict[str, object]:
        """Return canonical plan evidence."""

        return {**self.body_dict(), "plan_digest": self.plan_digest}


@dataclass(frozen=True, slots=True)
class _ProfileInfo:
    profile_id: str
    profile_digest: str
    fingerprint: str
    contract_version: str
    calibration_digest: str
    question_types: tuple[str, ...]
    state_shapes: tuple[str, ...]
    max_state_bytes: int
    max_state_tokens: int
    max_questions: int
    max_choice_options: int
    max_score_levels: int
    request_bytes: int
    concurrency: int
    queue_depth: int
    deadline_ms: int
    batching_enabled: bool
    max_batch_size: int
    max_wait_ms: int
    ownership: str
    max_loaded_identities: int
    resident_byte_budget: int


@dataclass(frozen=True, slots=True)
class _RequestFact:
    profile: _ProfileInfo
    key: GroupingEvidence
    member: BatchMember
    request_evidence: Mapping[str, object]


def _mapping(parent: Mapping[str, object], key: str, *, context: str) -> Mapping[str, object]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise PlannerInputError(f"{context}.{key} must be an object")
    return value


def _string(parent: Mapping[str, object], key: str, *, context: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value:
        raise PlannerInputError(f"{context}.{key} must be a non-empty string")
    return value


def _integer(parent: Mapping[str, object], key: str, *, context: str) -> int:
    value = parent.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlannerInputError(f"{context}.{key} must be an integer")
    return value


def _strings(parent: Mapping[str, object], key: str, *, context: str) -> tuple[str, ...]:
    value = parent.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PlannerInputError(f"{context}.{key} must be a string array")
    return tuple(value)


def _is_repeated(value: str, *, prefix: str = "") -> bool:
    payload = value.removeprefix(prefix)
    if not payload:
        return True
    for width in (1, 2, 4, 5, 8, 10, 16, 20):
        if len(payload) % width == 0 and payload == payload[:width] * (
            len(payload) // width
        ):
            return True
    return False


def _is_digest(value: object, *, reject_sentinel: bool = True) -> bool:
    return (
        isinstance(value, str)
        and _DIGEST.fullmatch(value) is not None
        and (not reject_sentinel or not _is_repeated(value, prefix="sha256:"))
    )


def _profile_info(
    schema: Mapping[str, object],
    profile: Mapping[str, object],
) -> _ProfileInfo:
    profile_id = profile.get("profile_id", "<unknown>")
    findings = validate_profile(schema, profile)
    if findings:
        raise PlannerInputError(
            f"profile {profile_id} schema or grouping contract invalid: {findings[0]}",
        )
    if profile.get("example_only") is not False:
        raise PlannerInputError(f"profile {profile_id} is an example fixture")
    if profile.get("profile_state") not in ADMITTED_PROFILE_STATES:
        raise PlannerInputError(f"profile {profile_id} is not admitted")

    source = _mapping(profile, "source_pin", context="profile")
    identity = _mapping(profile, "identity", context="profile")
    capabilities = _mapping(profile, "capabilities", context="profile")
    limits = _mapping(profile, "limits", context="profile")
    batching = _mapping(profile, "batching", context="profile")
    residency = _mapping(profile, "residency", context="profile")
    transport = _mapping(profile, "transport", context="profile")

    repository = source.get("repository")
    if not isinstance(repository, str) or not repository.startswith("https://"):
        raise PlannerInputError(f"profile {profile_id} source repository is not pinned HTTPS")
    release = source.get("release")
    if not isinstance(release, str) or _MOVING_ALIAS.search(release) is not None:
        raise PlannerInputError(f"profile {profile_id} source release has a moving alias")
    commit = source.get("commit")
    if (
        not isinstance(commit, str)
        or _COMMIT.fullmatch(commit) is None
        or _is_repeated(commit)
    ):
        raise PlannerInputError(f"profile {profile_id} source commit is not immutable")
    if not _is_digest(source.get("source_archive_digest")):
        raise PlannerInputError(f"profile {profile_id} source archive digest is not immutable")
    for field in _IDENTITY_DIGEST_FIELDS:
        if not _is_digest(identity.get(field)):
            raise PlannerInputError(f"profile {profile_id} identity.{field} is not immutable")
    model_revision = identity.get("model_revision")
    if not isinstance(model_revision, str) or _MOVING_ALIAS.search(model_revision):
        raise PlannerInputError(f"profile {profile_id} model revision has a moving alias")

    grouping_keys = _strings(batching, "grouping_keys", context="profile.batching")
    if set(grouping_keys) != set(REQUIRED_GROUPING_KEYS):
        raise PlannerInputError(f"profile {profile_id} grouping keys are incomplete")
    fingerprint = _string(identity, "fingerprint", context="profile.identity")
    ownership = _string(residency, "ownership", context="profile.residency")
    preloads = _strings(
        residency,
        "preload_identity_fingerprints",
        context="profile.residency",
    )
    max_loaded = _integer(
        residency,
        "max_loaded_identities",
        context="profile.residency",
    )
    resident_budget = _integer(
        residency,
        "resident_byte_budget",
        context="profile.residency",
    )
    transport_kind = _string(transport, "kind", context="profile.transport")
    if ownership == "external_endpoint":
        if max_loaded != 0 or resident_budget != 0 or preloads:
            raise PlannerInputError(
                f"profile {profile_id} external residency must make no resource claim",
            )
        if transport_kind != "jev_http_external":
            raise PlannerInputError(f"profile {profile_id} external ownership mismatch")
    elif (
        max_loaded < 1
        or resident_budget < 1
        or preloads != (fingerprint,)
    ):
        raise PlannerInputError(
            f"profile {profile_id} preload identity must bind its exact fingerprint",
        )
    elif transport_kind != "model_worker_jev_http":
        raise PlannerInputError(f"profile {profile_id} worker ownership mismatch")

    return _ProfileInfo(
        profile_id=_string(profile, "profile_id", context="profile"),
        profile_digest=canonical_digest(dict(profile)),
        fingerprint=fingerprint,
        contract_version=_string(
            identity,
            "contract_version",
            context="profile.identity",
        ),
        calibration_digest=_string(
            identity,
            "calibration_digest",
            context="profile.identity",
        ),
        question_types=_strings(
            capabilities,
            "question_types",
            context="profile.capabilities",
        ),
        state_shapes=_strings(
            capabilities,
            "state_shapes",
            context="profile.capabilities",
        ),
        max_state_bytes=_integer(
            capabilities,
            "max_state_bytes",
            context="profile.capabilities",
        ),
        max_state_tokens=_integer(
            capabilities,
            "max_state_tokens",
            context="profile.capabilities",
        ),
        max_questions=_integer(
            capabilities,
            "max_questions",
            context="profile.capabilities",
        ),
        max_choice_options=_integer(
            capabilities,
            "max_choice_options",
            context="profile.capabilities",
        ),
        max_score_levels=_integer(
            capabilities,
            "max_score_levels",
            context="profile.capabilities",
        ),
        request_bytes=_integer(limits, "request_bytes", context="profile.limits"),
        concurrency=_integer(limits, "concurrency", context="profile.limits"),
        queue_depth=_integer(limits, "queue_depth", context="profile.limits"),
        deadline_ms=_integer(limits, "deadline_ms", context="profile.limits"),
        batching_enabled=batching.get("enabled") is True,
        max_batch_size=_integer(
            batching,
            "max_batch_size",
            context="profile.batching",
        ),
        max_wait_ms=_integer(batching, "max_wait_ms", context="profile.batching"),
        ownership=ownership,
        max_loaded_identities=max_loaded,
        resident_byte_budget=resident_budget,
    )


def _validate_request_value(
    condition: bool,
    *,
    request_id: str,
    message: str,
) -> None:
    if not condition:
        raise PlannerInputError(f"request {request_id} {message}")


def _request_fact(
    request: BatchRequest,
    profile: _ProfileInfo,
    *,
    ordinal: int,
) -> _RequestFact:
    request_id = request.request_id
    _validate_request_value(
        isinstance(request_id, str) and _IDENTIFIER.fullmatch(request_id) is not None,
        request_id=str(request_id),
        message="request_id is invalid",
    )
    _validate_request_value(
        _is_digest(request.tenant_digest, reject_sentinel=False),
        request_id=request_id,
        message="tenant digest is invalid",
    )
    _validate_request_value(
        _is_digest(request.question_schema_digest, reject_sentinel=False),
        request_id=request_id,
        message="question schema digest is invalid",
    )
    _validate_request_value(
        request.schema_version == profile.contract_version,
        request_id=request_id,
        message="schema version is not admitted",
    )
    _validate_request_value(
        request.trust_sensitivity_class in {"public", "confidential", "restricted"},
        request_id=request_id,
        message="trust sensitivity class is invalid",
    )
    _validate_request_value(
        isinstance(request.question_types, tuple)
        and bool(request.question_types)
        and len(set(request.question_types)) == len(request.question_types)
        and set(request.question_types).issubset(profile.question_types),
        request_id=request_id,
        message="question type exceeds capability",
    )
    _validate_request_value(
        request.state_shape in profile.state_shapes,
        request_id=request_id,
        message="state shape exceeds capability",
    )
    integer_checks = (
        ("token budget", request.token_budget, 1, profile.max_state_tokens),
        ("state bytes", request.state_bytes, 0, profile.max_state_bytes),
        ("question count", request.question_count, 1, profile.max_questions),
        ("choice options", request.max_choice_options, 0, profile.max_choice_options),
        ("score levels", request.max_score_levels, 0, profile.max_score_levels),
        ("request bytes", request.request_bytes, 1, profile.request_bytes),
        ("deadline", request.deadline_bucket_ms, 1, profile.deadline_ms),
    )
    for name, value, minimum, maximum in integer_checks:
        _validate_request_value(
            isinstance(value, int)
            and not isinstance(value, bool)
            and minimum <= value <= maximum,
            request_id=request_id,
            message=f"{name} exceeds admitted bounds",
        )
    if "choice" in request.question_types:
        _validate_request_value(
            request.max_choice_options >= 2,
            request_id=request_id,
            message="choice options are incomplete",
        )
    if "score" in request.question_types:
        _validate_request_value(
            request.max_score_levels >= 2,
            request_id=request_id,
            message="score levels are incomplete",
        )

    request_digest = "sha256:" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    key = GroupingEvidence(
        profile_id=profile.profile_id,
        tenant_digest=request.tenant_digest,
        identity_fingerprint=profile.fingerprint,
        schema_version=request.schema_version,
        question_schema_digest=request.question_schema_digest,
        token_budget=request.token_budget,
        calibration_digest=profile.calibration_digest,
        trust_sensitivity_class=request.trust_sensitivity_class,
        deadline_bucket_ms=request.deadline_bucket_ms,
    )
    evidence = {
        "deadline_bucket_ms": request.deadline_bucket_ms,
        "max_choice_options": request.max_choice_options,
        "max_score_levels": request.max_score_levels,
        "ordinal": ordinal,
        "profile_id": request.profile_id,
        "question_count": request.question_count,
        "question_schema_digest": request.question_schema_digest,
        "question_types": list(request.question_types),
        "request_bytes": request.request_bytes,
        "request_digest": request_digest,
        "schema_version": request.schema_version,
        "state_bytes": request.state_bytes,
        "state_shape": request.state_shape,
        "tenant_digest": request.tenant_digest,
        "token_budget": request.token_budget,
        "trust_sensitivity_class": request.trust_sensitivity_class,
    }
    return _RequestFact(
        profile=profile,
        key=key,
        member=BatchMember(ordinal=ordinal, request_digest=request_digest),
        request_evidence=evidence,
    )


def _build_groups(facts: Sequence[_RequestFact]) -> tuple[BatchGroup, ...]:
    buckets: dict[bytes, list[_RequestFact]] = {}
    for fact in facts:
        serialized_key = canonical_json_bytes(fact.key.to_dict())
        buckets.setdefault(serialized_key, []).append(fact)

    groups: list[BatchGroup] = []
    for serialized_key in sorted(buckets):
        bucket = sorted(buckets[serialized_key], key=lambda fact: fact.member.ordinal)
        profile = bucket[0].profile
        group_key_digest = "sha256:" + hashlib.sha256(serialized_key).hexdigest()
        batch_size = profile.max_batch_size if profile.batching_enabled else 1
        reason = (
            "capability_and_security_key"
            if profile.batching_enabled
            else "profile_batching_disabled"
        )
        for offset in range(0, len(bucket), batch_size):
            members = tuple(fact.member for fact in bucket[offset : offset + batch_size])
            group_id = canonical_digest(
                {
                    "chunk_index": offset // batch_size,
                    "group_key_digest": group_key_digest,
                    "members": [member.to_dict() for member in members],
                },
            )
            groups.append(
                BatchGroup(
                    group_id=group_id,
                    group_key_digest=group_key_digest,
                    key=bucket[0].key,
                    members=members,
                    member_count=len(members),
                    max_batch_size=batch_size,
                    max_wait_ms=profile.max_wait_ms if profile.batching_enabled else 0,
                    reason=reason,
                ),
            )
    return tuple(sorted(groups, key=lambda group: (group.key.profile_id, group.group_id)))


def _validate_snapshot(snapshot: ResidencySnapshot) -> dict[str, ResidentIdentity]:
    if len(snapshot.loaded) > MAX_RESIDENT_IDENTITIES:
        raise PlannerInputError("residency snapshot exceeds identity bound")
    loaded: dict[str, ResidentIdentity] = {}
    for resident in snapshot.loaded:
        if not _is_digest(resident.fingerprint, reject_sentinel=False):
            raise PlannerInputError("resident fingerprint is invalid")
        if resident.fingerprint in loaded:
            raise PlannerInputError("duplicate resident identity")
        numeric = (
            resident.measured_bytes,
            resident.lease_count,
            resident.inflight_count,
            resident.last_used_sequence,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in numeric):
            raise PlannerInputError("resident counters must be integers")
        if (
            not 1 <= resident.measured_bytes <= MAX_RESIDENT_BYTES
            or resident.lease_count < 0
            or resident.inflight_count < 0
            or resident.last_used_sequence < 0
        ):
            raise PlannerInputError("resident counters exceed bounds")
        loaded[resident.fingerprint] = resident
    return loaded


def _validate_measurements(
    measurements: Sequence[IdentityMeasurement],
    *,
    requested_workers: set[str],
) -> dict[str, int]:
    measured: dict[str, int] = {}
    for measurement in measurements:
        if not _is_digest(measurement.fingerprint, reject_sentinel=False):
            raise PlannerInputError("measurement fingerprint is invalid")
        if measurement.fingerprint in measured:
            raise PlannerInputError("duplicate identity measurement")
        if measurement.fingerprint not in requested_workers:
            raise PlannerInputError("measurement does not match a requested worker identity")
        if (
            isinstance(measurement.measured_bytes, bool)
            or not isinstance(measurement.measured_bytes, int)
            or not 1 <= measurement.measured_bytes <= MAX_RESIDENT_BYTES
        ):
            raise PlannerInputError("measured resident bytes exceed bounds")
        measured[measurement.fingerprint] = measurement.measured_bytes
    return measured


def _plan_residency(
    requested: Sequence[_ProfileInfo],
    bounds: PlannerBounds,
    snapshot: ResidencySnapshot,
    measurements: Sequence[IdentityMeasurement],
) -> tuple[
    tuple[ResidencyDecision, ...],
    tuple[EvictionDecision, ...],
    tuple[str, ...],
]:
    loaded = _validate_snapshot(snapshot)
    requested_by_fingerprint = {profile.fingerprint: profile for profile in requested}
    workers = {
        fingerprint: profile
        for fingerprint, profile in requested_by_fingerprint.items()
        if profile.ownership == "universal_model_worker"
    }
    measured = _validate_measurements(
        measurements,
        requested_workers=set(workers),
    )
    decisions: list[ResidencyDecision] = []
    for fingerprint, profile in sorted(requested_by_fingerprint.items()):
        if profile.ownership == "external_endpoint":
            decisions.append(
                ResidencyDecision(
                    fingerprint=fingerprint,
                    ownership=profile.ownership,
                    decision="external_unmanaged",
                    reason="external_endpoint_no_residency_claim",
                    measured_bytes=0,
                ),
            )

    if not workers:
        return tuple(decisions), (), ()

    effective_loaded_limit = min(
        bounds.max_loaded_identities,
        *(profile.max_loaded_identities for profile in workers.values()),
    )
    effective_byte_budget = min(
        bounds.resident_byte_budget,
        *(profile.resident_byte_budget for profile in workers.values()),
    )
    requested_fingerprints = set(requested_by_fingerprint)
    new_valid: dict[str, int] = {}
    new_rejections: dict[str, str] = {}
    for fingerprint, profile in workers.items():
        if fingerprint in loaded:
            continue
        required_bytes = measured.get(fingerprint)
        if required_bytes is None:
            new_rejections[fingerprint] = "missing_measured_resident_bytes"
        elif required_bytes > profile.resident_byte_budget:
            new_rejections[fingerprint] = "profile_resident_byte_budget_exceeded"
        else:
            new_valid[fingerprint] = required_bytes

    total_count = len(loaded) + len(new_valid)
    total_bytes = sum(item.measured_bytes for item in loaded.values()) + sum(
        new_valid.values(),
    )
    eligible = sorted(
        (
            resident
            for resident in loaded.values()
            if resident.fingerprint not in requested_fingerprints
            and resident.lease_count == 0
            and resident.inflight_count == 0
        ),
        key=lambda resident: (resident.last_used_sequence, resident.fingerprint),
    )
    selected: set[str] = set()
    for resident in eligible:
        if (
            total_count <= effective_loaded_limit
            and total_bytes <= effective_byte_budget
        ):
            break
        selected.add(resident.fingerprint)
        total_count -= 1
        total_bytes -= resident.measured_bytes

    capacity_fits = (
        total_count <= effective_loaded_limit
        and total_bytes <= effective_byte_budget
    )
    active_capacity = any(
        resident.fingerprint not in requested_fingerprints
        and (resident.lease_count > 0 or resident.inflight_count > 0)
        for resident in loaded.values()
    )
    if not capacity_fits:
        if active_capacity:
            capacity_reason = "leased_or_inflight_capacity"
        elif total_bytes > effective_byte_budget:
            capacity_reason = "resident_byte_budget_blocked"
        else:
            capacity_reason = "loaded_identity_limit_blocked"
    else:
        capacity_reason = ""

    current_over_bound = (
        len(loaded) - len(selected) > effective_loaded_limit
        or sum(
            resident.measured_bytes
            for resident in loaded.values()
            if resident.fingerprint not in selected
        )
        > effective_byte_budget
    )
    for fingerprint, profile in sorted(workers.items()):
        if fingerprint in loaded:
            resident = loaded[fingerprint]
            if current_over_bound:
                decisions.append(
                    ResidencyDecision(
                        fingerprint=fingerprint,
                        ownership=profile.ownership,
                        decision="reject",
                        reason="snapshot_exceeds_resource_bounds",
                        measured_bytes=resident.measured_bytes,
                    ),
                )
            else:
                decisions.append(
                    ResidencyDecision(
                        fingerprint=fingerprint,
                        ownership=profile.ownership,
                        decision="reuse",
                        reason="identity_already_resident",
                        measured_bytes=resident.measured_bytes,
                    ),
                )
        elif fingerprint in new_rejections:
            decisions.append(
                ResidencyDecision(
                    fingerprint=fingerprint,
                    ownership=profile.ownership,
                    decision="reject",
                    reason=new_rejections[fingerprint],
                    measured_bytes=measured.get(fingerprint, 0),
                ),
            )
        elif not capacity_fits:
            decisions.append(
                ResidencyDecision(
                    fingerprint=fingerprint,
                    ownership=profile.ownership,
                    decision="reject",
                    reason=capacity_reason,
                    measured_bytes=new_valid[fingerprint],
                ),
            )
        else:
            decisions.append(
                ResidencyDecision(
                    fingerprint=fingerprint,
                    ownership=profile.ownership,
                    decision="plan_preload",
                    reason="evict_then_preload" if selected else "capacity_available",
                    measured_bytes=new_valid[fingerprint],
                ),
            )

    evictions: list[EvictionDecision] = []
    for fingerprint, resident in sorted(loaded.items()):
        if fingerprint in requested_fingerprints:
            decision, reason = "retain", "requested_identity"
        elif resident.lease_count > 0:
            decision, reason = "retain", "active_lease"
        elif resident.inflight_count > 0:
            decision, reason = "retain", "inflight_request"
        elif fingerprint in selected:
            decision, reason = "plan_evict", "oldest_eligible_for_capacity"
        else:
            decision, reason = "retain", "capacity_sufficient"
        evictions.append(
            EvictionDecision(
                fingerprint=fingerprint,
                decision=decision,
                reason=reason,
                measured_bytes=resident.measured_bytes,
            ),
        )
    reasons = tuple(
        sorted({decision.reason for decision in decisions if decision.decision == "reject"}),
    )
    return tuple(decisions), tuple(evictions), reasons


def build_batch_residency_plan(
    schema: Mapping[str, object],
    profiles: Sequence[Mapping[str, object]],
    requests: Sequence[BatchRequest],
    *,
    bounds: PlannerBounds,
    snapshot: ResidencySnapshot,
    measurements: Sequence[IdentityMeasurement],
) -> BatchResidencyPlan:
    """Build one deterministic plan without admitting or invoking any runtime."""

    if not profiles or len(profiles) > bounds.max_profiles:
        raise PlannerInputError("profile count exceeds max_profiles")
    if not requests or len(requests) > bounds.max_requests:
        raise PlannerInputError("request count exceeds max_requests")

    profile_by_id: dict[str, _ProfileInfo] = {}
    fingerprint_owner: dict[str, str] = {}
    for profile_mapping in profiles:
        info = _profile_info(schema, profile_mapping)
        if info.profile_id in profile_by_id:
            raise PlannerInputError("duplicate profile_id")
        if info.fingerprint in fingerprint_owner:
            raise PlannerInputError("duplicate admitted identity fingerprint")
        profile_by_id[info.profile_id] = info
        fingerprint_owner[info.fingerprint] = info.profile_id

    seen_request_ids: set[str] = set()
    per_profile_counts: dict[str, int] = {}
    facts: list[_RequestFact] = []
    total_request_bytes = 0
    for ordinal, request in enumerate(requests):
        if request.request_id in seen_request_ids:
            raise PlannerInputError("duplicate request_id")
        seen_request_ids.add(request.request_id)
        profile_info = profile_by_id.get(request.profile_id)
        if profile_info is None:
            raise PlannerInputError(f"request {request.request_id} references unknown profile")
        fact = _request_fact(request, profile_info, ordinal=ordinal)
        facts.append(fact)
        total_request_bytes += request.request_bytes
        per_profile_counts[profile_info.profile_id] = (
            per_profile_counts.get(profile_info.profile_id, 0) + 1
        )
    if total_request_bytes > bounds.max_total_request_bytes:
        raise PlannerInputError("total request bytes exceed planner bound")
    for profile_id, count in per_profile_counts.items():
        profile_info = profile_by_id[profile_id]
        if count > profile_info.concurrency + profile_info.queue_depth:
            raise PlannerInputError(
                f"profile {profile_id} queue and concurrency capacity exceeded",
            )

    requested_profiles = tuple(
        profile_by_id[profile_id] for profile_id in sorted(per_profile_counts)
    )
    groups = _build_groups(facts)
    residency, evictions, reasons = _plan_residency(
        requested_profiles,
        bounds,
        snapshot,
        measurements,
    )
    evidence_without_digest = RedactedPlanEvidence(
        profile_digests=tuple(
            sorted(info.profile_digest for info in profile_by_id.values()),
        ),
        request_evidence_digest=canonical_digest(
            [fact.request_evidence for fact in facts],
        ),
        snapshot_digest=canonical_digest(snapshot.to_dict()),
        measurement_digest=canonical_digest(
            [
                measurement.to_dict()
                for measurement in sorted(
                    measurements,
                    key=lambda item: item.fingerprint,
                )
            ],
        ),
        redacted_fields=REDACTED_BATCH_FIELDS,
        evidence_digest="",
    )
    evidence = RedactedPlanEvidence(
        profile_digests=evidence_without_digest.profile_digests,
        request_evidence_digest=evidence_without_digest.request_evidence_digest,
        snapshot_digest=evidence_without_digest.snapshot_digest,
        measurement_digest=evidence_without_digest.measurement_digest,
        redacted_fields=evidence_without_digest.redacted_fields,
        evidence_digest=canonical_digest(evidence_without_digest.body_dict()),
    )
    plan_without_digest = BatchResidencyPlan(
        schema_digest=canonical_digest(dict(schema)),
        bounds=bounds,
        groups=groups,
        residency=residency,
        evictions=evictions,
        ready=not reasons,
        reasons=reasons,
        evidence=evidence,
        admission_performed=False,
        network_accessed=False,
        runtime_invoked=False,
        models_downloaded=False,
        plan_digest="",
    )
    return BatchResidencyPlan(
        schema_digest=plan_without_digest.schema_digest,
        bounds=plan_without_digest.bounds,
        groups=plan_without_digest.groups,
        residency=plan_without_digest.residency,
        evictions=plan_without_digest.evictions,
        ready=plan_without_digest.ready,
        reasons=plan_without_digest.reasons,
        evidence=plan_without_digest.evidence,
        admission_performed=plan_without_digest.admission_performed,
        network_accessed=plan_without_digest.network_accessed,
        runtime_invoked=plan_without_digest.runtime_invoked,
        models_downloaded=plan_without_digest.models_downloaded,
        plan_digest=canonical_digest(plan_without_digest.body_dict()),
    )


def verify_batch_residency_plan(
    document: Mapping[str, object],
    schema: Mapping[str, object],
    profiles: Sequence[Mapping[str, object]],
    requests: Sequence[BatchRequest],
    *,
    bounds: PlannerBounds,
    snapshot: ResidencySnapshot,
    measurements: Sequence[IdentityMeasurement],
) -> BatchResidencyPlan:
    """Rebuild an offline plan and reject any mutation or unsafe side-effect flag."""

    if document.get("version") != PLAN_VERSION:
        raise PlannerInputError("batch residency plan version mismatch")
    for flag in (
        "admission_performed",
        "network_accessed",
        "runtime_invoked",
        "models_downloaded",
    ):
        if document.get(flag) is not False:
            raise PlannerInputError(f"fail-closed {flag} must be false")
    expected = build_batch_residency_plan(
        schema,
        profiles,
        requests,
        bounds=bounds,
        snapshot=snapshot,
        measurements=measurements,
    )
    try:
        matches = canonical_json_bytes(document) == canonical_json_bytes(expected.to_dict())
    except UpdatePlanError as exc:
        raise PlannerInputError("batch residency plan is not canonical JSON") from exc
    if not matches:
        raise PlannerInputError("batch residency plan content or digest mismatch")
    return expected
