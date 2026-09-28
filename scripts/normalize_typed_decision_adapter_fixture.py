#!/usr/bin/env python3
"""Normalize pinned OpenJev/Laya fixtures without admitting or invoking them.

This module is a strict offline boundary.  It accepts only the documented wire
schema and a disabled, immutable profile, then emits provider-neutral evidence.
It intentionally has no transport, model-loading, runtime, or admission code.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, Never, TypeAlias

from jsonschema import Draft202012Validator
from scripts.check_typed_decision_adapter_update import (
    canonical_digest as canonical_digest,
)
from scripts.check_typed_decision_adapter_update import (
    canonical_json_bytes,
    check_candidate_pins,
    validate_profile,
)

NORMALIZATION_VERSION = "gludd.typed-decision-normalization/v1"
RECORD_VERSION = "typed-decision-adapter-normalization-record/v1"
TASK_KIND = "failure_classification"
CONTRACT_SCHEMA_VERSION = "gludd.typed-decision-adapter-contract/v1"
CONTRACT_SCHEMA_DIGEST = (
    "sha256:1082d5b5a024f62514fd0bf6c23f2cbe2deaed2f26900010082c98dabdb60468"
)
SUM_TOLERANCE = Decimal("0.0001")
MAX_TOKEN_COUNT = (1 << 63) - 1
MAX_STATE_DEPTH = 32
MAX_STATE_NODES = 65_536
MAX_RECORD_DEPTH = 8
MAX_RECORD_NODES = 4_096
MAX_RECORD_CHARACTERS = 256 * 1024

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")

OutcomeValue: TypeAlias = str | bool | float
ReasonCode: TypeAlias = Literal[
    "contract_invalid",
    "profile_unpinned",
    "schema_version_mismatch",
    "identity_mismatch",
    "capability_mismatch",
    "request_malformed",
    "response_malformed",
    "response_semantic_mismatch",
    "replay_mismatch",
]


class NormalizationError(RuntimeError):
    """Bounded deterministic rejection that never renders untrusted input."""

    reason_code: ReasonCode
    detail_code: str
    error_reference: str

    def __init__(self, reason_code: ReasonCode, detail_code: str) -> None:
        self.reason_code = reason_code
        self.detail_code = detail_code
        self.error_reference = canonical_digest(
            {"detail_code": detail_code, "reason_code": reason_code},
        )
        super().__init__(
            f"normalization rejected: {reason_code}/{detail_code} "
            f"[{self.error_reference}]",
        )


def _reject(reason: ReasonCode, detail: str) -> Never:
    raise NormalizationError(reason, detail)


def _is_mapping(value: object) -> bool:
    return isinstance(value, dict)


def _mapping(
    value: object,
    *,
    reason: ReasonCode,
    detail: str,
) -> Mapping[str, object]:
    if not _is_mapping(value):
        _reject(reason, detail)
    assert isinstance(value, dict)
    return value


def _strict_keys(
    value: Mapping[str, object],
    expected: set[str],
    *,
    reason: ReasonCode,
    detail: str,
) -> None:
    if set(value) != expected:
        _reject(reason, detail)


def _string(
    value: object,
    *,
    reason: ReasonCode,
    detail: str,
    maximum: int = 160,
) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        _reject(reason, detail)
    return value


def _identifier(
    value: object,
    *,
    reason: ReasonCode,
    detail: str,
    maximum: int = 160,
) -> str:
    """Accept one unambiguous NFC identifier without control code points."""

    identifier = _string(value, reason=reason, detail=detail, maximum=maximum)
    if unicodedata.normalize("NFC", identifier) != identifier or any(
        unicodedata.category(character).startswith("C") for character in identifier
    ):
        _reject(reason, detail)
    return identifier


def _validate_bounded_tree(
    value: object,
    *,
    max_depth: int,
    max_nodes: int,
    max_characters: int,
    reason: ReasonCode,
    detail: str,
) -> None:
    """Bound nested JSON work iteratively before schema traversal or hashing."""

    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    characters = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if depth > max_depth or nodes > max_nodes:
            _reject(reason, detail)
        if isinstance(current, dict):
            for key, child in current.items():
                if not isinstance(key, str):
                    _reject(reason, detail)
                characters += len(key)
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
        elif isinstance(current, str):
            characters += len(current)
        elif isinstance(current, float):
            if not math.isfinite(current):
                _reject(reason, detail)
        elif current is not None and not isinstance(current, (bool, int)):
            _reject(reason, detail)
        if characters > max_characters:
            _reject(reason, detail)


def _integer(
    value: object,
    *,
    reason: ReasonCode,
    detail: str,
    minimum: int = 0,
    maximum: int = MAX_TOKEN_COUNT,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        _reject(reason, detail)
    return value


def _decimal(
    value: object,
    *,
    reason: ReasonCode,
    detail: str,
    minimum: Decimal = Decimal(0),
    maximum: Decimal = Decimal(1),
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject(reason, detail)
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, OverflowError, ValueError):
        _reject(reason, detail)
    if not converted.is_finite() or not minimum <= converted <= maximum:
        _reject(reason, detail)
    return converted


def _canonical_float(value: Decimal) -> float:
    return 0.0 if value == 0 else float(value)


def _canonical_digest(value: object, *, reason: ReasonCode, detail: str) -> str:
    try:
        return canonical_digest(value)
    except (TypeError, ValueError, RuntimeError):
        _reject(reason, detail)


@dataclass(frozen=True, slots=True)
class CanonicalProbability:
    """One probability in request-defined canonical label order."""

    label: str
    probability: float

    def to_dict(self) -> dict[str, object]:
        return {"label": self.label, "probability": self.probability}

    @classmethod
    def from_dict(cls, value: object) -> CanonicalProbability:
        item = _mapping(value, reason="replay_mismatch", detail="probability_shape")
        _strict_keys(
            item,
            {"label", "probability"},
            reason="replay_mismatch",
            detail="probability_fields",
        )
        label = _identifier(
            item["label"],
            reason="replay_mismatch",
            detail="probability_label",
        )
        probability = _decimal(
            item["probability"],
            reason="replay_mismatch",
            detail="probability_value",
        )
        return cls(label, _canonical_float(probability))


@dataclass(frozen=True, slots=True)
class NormalizedOutcome:
    """Provider-neutral result for one request-bound question."""

    normalization_version: str
    request_id: str
    identity_fingerprint: str
    task_kind: str
    question_id: str
    kind: str
    outcome: OutcomeValue
    probabilities: tuple[CanonicalProbability, ...]
    upstream_confidence: float | None
    disposition: str = "valid"
    reason_code: None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "disposition": self.disposition,
            "identity_fingerprint": self.identity_fingerprint,
            "kind": self.kind,
            "normalization_version": self.normalization_version,
            "outcome": self.outcome,
            "probabilities": [item.to_dict() for item in self.probabilities],
            "question_id": self.question_id,
            "reason_code": self.reason_code,
            "request_id": self.request_id,
            "task_kind": self.task_kind,
            "upstream_confidence": self.upstream_confidence,
        }

    @classmethod
    def from_dict(cls, value: object) -> NormalizedOutcome:
        item = _mapping(value, reason="replay_mismatch", detail="outcome_shape")
        expected = {
            "disposition",
            "identity_fingerprint",
            "kind",
            "normalization_version",
            "outcome",
            "probabilities",
            "question_id",
            "reason_code",
            "request_id",
            "task_kind",
            "upstream_confidence",
        }
        _strict_keys(item, expected, reason="replay_mismatch", detail="outcome_fields")
        probabilities = item["probabilities"]
        if not isinstance(probabilities, list) or not probabilities:
            _reject("replay_mismatch", "outcome_probabilities")
        confidence_value = item["upstream_confidence"]
        confidence = (
            None
            if confidence_value is None
            else _canonical_float(
                _decimal(
                    confidence_value,
                    reason="replay_mismatch",
                    detail="outcome_confidence",
                ),
            )
        )
        outcome = item["outcome"]
        if isinstance(outcome, (bool, str)):
            parsed_outcome: OutcomeValue = outcome
        elif isinstance(outcome, (int, float)) and not isinstance(outcome, bool):
            parsed_outcome = _canonical_float(
                _decimal(
                    outcome,
                    reason="replay_mismatch",
                    detail="outcome_value",
                    maximum=Decimal(9),
                ),
            )
        else:
            _reject("replay_mismatch", "outcome_value")
        if item["disposition"] != "valid" or item["reason_code"] is not None:
            _reject("replay_mismatch", "outcome_disposition")
        return cls(
            normalization_version=_string(
                item["normalization_version"],
                reason="replay_mismatch",
                detail="outcome_normalization_version",
            ),
            request_id=_string(
                item["request_id"],
                reason="replay_mismatch",
                detail="outcome_request_id",
                maximum=128,
            ),
            identity_fingerprint=_string(
                item["identity_fingerprint"],
                reason="replay_mismatch",
                detail="outcome_identity",
            ),
            task_kind=_string(
                item["task_kind"],
                reason="replay_mismatch",
                detail="outcome_task",
            ),
            question_id=_identifier(
                item["question_id"],
                reason="replay_mismatch",
                detail="outcome_question",
            ),
            kind=_string(
                item["kind"],
                reason="replay_mismatch",
                detail="outcome_kind",
            ),
            outcome=parsed_outcome,
            probabilities=tuple(
                CanonicalProbability.from_dict(probability)
                for probability in probabilities
            ),
            upstream_confidence=confidence,
        )


@dataclass(frozen=True, slots=True)
class NormalizationBinding:
    """Exact schema, identity, capability, and payload binding."""

    contract_schema_version: str
    contract_schema_digest: str
    normalization_version: str
    request_schema_version: str
    response_schema_version: str
    profile_id: str
    profile_digest: str
    identity_fingerprint: str
    capability_digest: str
    request_digest: str
    response_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "capability_digest": self.capability_digest,
            "contract_schema_digest": self.contract_schema_digest,
            "contract_schema_version": self.contract_schema_version,
            "identity_fingerprint": self.identity_fingerprint,
            "normalization_version": self.normalization_version,
            "profile_digest": self.profile_digest,
            "profile_id": self.profile_id,
            "request_digest": self.request_digest,
            "request_schema_version": self.request_schema_version,
            "response_digest": self.response_digest,
            "response_schema_version": self.response_schema_version,
        }

    @classmethod
    def from_dict(cls, value: object) -> NormalizationBinding:
        item = _mapping(value, reason="replay_mismatch", detail="binding_shape")
        expected = {
            "capability_digest",
            "contract_schema_digest",
            "contract_schema_version",
            "identity_fingerprint",
            "normalization_version",
            "profile_digest",
            "profile_id",
            "request_digest",
            "request_schema_version",
            "response_digest",
            "response_schema_version",
        }
        _strict_keys(item, expected, reason="replay_mismatch", detail="binding_fields")
        values: dict[str, str] = {}
        for key in expected:
            values[key] = _string(
                item[key],
                reason="replay_mismatch",
                detail="binding_value",
            )
        for key in (
            "capability_digest",
            "contract_schema_digest",
            "identity_fingerprint",
            "profile_digest",
            "request_digest",
            "response_digest",
        ):
            if _DIGEST.fullmatch(values[key]) is None:
                _reject("replay_mismatch", "binding_digest")
        return cls(**values)


@dataclass(frozen=True, slots=True)
class NormalizationRecord:
    """Immutable all-or-nothing normalization result and offline evidence."""

    binding: NormalizationBinding
    outcomes: tuple[NormalizedOutcome, ...]
    request_id: str
    task_kind: str
    ready: bool
    network_accessed: bool
    runtime_invoked: bool
    model_invoked: bool
    admission_performed: bool
    record_digest: str
    version: str = RECORD_VERSION

    def body_dict(self) -> dict[str, object]:
        return {
            "admission_performed": self.admission_performed,
            "binding": self.binding.to_dict(),
            "model_invoked": self.model_invoked,
            "network_accessed": self.network_accessed,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "ready": self.ready,
            "request_id": self.request_id,
            "runtime_invoked": self.runtime_invoked,
            "task_kind": self.task_kind,
            "version": self.version,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.body_dict(), "record_digest": self.record_digest}

    @classmethod
    def from_dict(cls, value: object) -> NormalizationRecord:
        item = _mapping(value, reason="replay_mismatch", detail="record_shape")
        expected = {
            "admission_performed",
            "binding",
            "model_invoked",
            "network_accessed",
            "outcomes",
            "ready",
            "record_digest",
            "request_id",
            "runtime_invoked",
            "task_kind",
            "version",
        }
        _strict_keys(item, expected, reason="replay_mismatch", detail="record_fields")
        outcomes = item["outcomes"]
        if not isinstance(outcomes, list) or not 1 <= len(outcomes) <= 32:
            _reject("replay_mismatch", "record_outcomes")
        _validate_bounded_tree(
            item,
            max_depth=MAX_RECORD_DEPTH,
            max_nodes=MAX_RECORD_NODES,
            max_characters=MAX_RECORD_CHARACTERS,
            reason="replay_mismatch",
            detail="record_bounds",
        )
        digest = item["record_digest"]
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            _reject("replay_mismatch", "record_digest")
        body = {key: item[key] for key in expected if key != "record_digest"}
        if _canonical_digest(
            body,
            reason="replay_mismatch",
            detail="record_canonical_json",
        ) != digest:
            _reject("replay_mismatch", "record_digest")
        false_flags = (
            item["admission_performed"],
            item["model_invoked"],
            item["network_accessed"],
            item["runtime_invoked"],
        )
        if any(value is not False for value in false_flags) or item["ready"] is not True:
            _reject("replay_mismatch", "record_side_effect_flags")
        if item["version"] != RECORD_VERSION:
            _reject("replay_mismatch", "record_version")
        binding = NormalizationBinding.from_dict(item["binding"])
        parsed_outcomes = tuple(
            NormalizedOutcome.from_dict(outcome) for outcome in outcomes
        )
        request_id = _string(
                item["request_id"],
                reason="replay_mismatch",
                detail="record_request_id",
                maximum=128,
            )
        if _REQUEST_ID.fullmatch(request_id) is None:
            _reject("replay_mismatch", "record_request_id")
        task_kind = _string(
                item["task_kind"],
                reason="replay_mismatch",
                detail="record_task_kind",
            )
        record = cls(
            binding=binding,
            outcomes=parsed_outcomes,
            request_id=request_id,
            task_kind=task_kind,
            ready=True,
            network_accessed=False,
            runtime_invoked=False,
            model_invoked=False,
            admission_performed=False,
            record_digest=digest,
        )
        _validate_record_consistency(record)
        return record


def _validate_record_consistency(record: NormalizationRecord) -> None:
    """Reject a correctly hashed record whose typed identities disagree."""

    binding = record.binding
    if (
        binding.contract_schema_version != CONTRACT_SCHEMA_VERSION
        or binding.contract_schema_digest != CONTRACT_SCHEMA_DIGEST
        or binding.normalization_version != NORMALIZATION_VERSION
        or record.task_kind != TASK_KIND
    ):
        _reject("replay_mismatch", "record_binding")
    question_ids = tuple(outcome.question_id for outcome in record.outcomes)
    if question_ids != tuple(sorted(set(question_ids))):
        _reject("replay_mismatch", "record_question_identity")
    for outcome in record.outcomes:
        if (
            outcome.normalization_version != NORMALIZATION_VERSION
            or outcome.request_id != record.request_id
            or outcome.identity_fingerprint != binding.identity_fingerprint
            or outcome.task_kind != record.task_kind
            or outcome.kind not in {"choice", "noul", "score"}
        ):
            _reject("replay_mismatch", "outcome_binding")
        labels = tuple(item.label for item in outcome.probabilities)
        if not 2 <= len(labels) <= 20 or len(labels) != len(set(labels)):
            _reject("replay_mismatch", "outcome_probability_identity")
        decimals = tuple(Decimal(str(item.probability)) for item in outcome.probabilities)
        if abs(sum(decimals, Decimal(0)) - Decimal(1)) > SUM_TOLERANCE:
            _reject("replay_mismatch", "outcome_probability_sum")
        if outcome.kind == "choice":
            if (
                labels != tuple(sorted(labels))
                or not isinstance(outcome.outcome, str)
                or outcome.outcome not in labels
                or outcome.upstream_confidence is None
            ):
                _reject("replay_mismatch", "choice_outcome")
            maximum = max(decimals)
            winners = tuple(
                label
                for label, probability in zip(labels, decimals, strict=True)
                if probability == maximum
            )
            if winners != (outcome.outcome,):
                _reject("replay_mismatch", "choice_outcome")
        elif outcome.kind == "noul":
            if (
                labels != ("false", "true")
                or not isinstance(outcome.outcome, bool)
                or outcome.upstream_confidence is not None
                or decimals[1] == Decimal("0.5")
                or outcome.outcome != (decimals[1] > Decimal("0.5"))
            ):
                _reject("replay_mismatch", "noul_outcome")
        else:
            expected_labels = tuple(str(index) for index in range(len(labels)))
            expected_score = sum(
                (
                    Decimal(index) * probability
                    for index, probability in enumerate(decimals)
                ),
                Decimal(0),
            )
            if (
                labels != expected_labels
                or isinstance(outcome.outcome, bool)
                or not isinstance(outcome.outcome, (int, float))
                or outcome.upstream_confidence is None
                or abs(Decimal(str(outcome.outcome)) - expected_score) > SUM_TOLERANCE
            ):
                _reject("replay_mismatch", "score_outcome")


def _profile_sections(
    profile: Mapping[str, object],
) -> tuple[
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
]:
    identity = _mapping(
        profile.get("identity"),
        reason="profile_unpinned",
        detail="profile_identity",
    )
    transport = _mapping(
        profile.get("transport"),
        reason="profile_unpinned",
        detail="profile_transport",
    )
    capabilities = _mapping(
        profile.get("capabilities"),
        reason="profile_unpinned",
        detail="profile_capabilities",
    )
    limits = _mapping(
        profile.get("limits"),
        reason="profile_unpinned",
        detail="profile_limits",
    )
    compatibility = _mapping(
        profile.get("compatibility"),
        reason="profile_unpinned",
        detail="profile_compatibility",
    )
    return identity, transport, capabilities, limits, compatibility


def _schema_version(schema: Mapping[str, object]) -> str:
    properties = _mapping(
        schema.get("properties"),
        reason="contract_invalid",
        detail="schema_properties",
    )
    definition = _mapping(
        properties.get("schema_version"),
        reason="contract_invalid",
        detail="schema_version_definition",
    )
    return _string(
        definition.get("const"),
        reason="contract_invalid",
        detail="schema_version_const",
    )


def _version_membership(
    compatibility: Mapping[str, object],
    key: str,
    selected: str,
) -> None:
    versions = compatibility.get(key)
    if (
        not isinstance(versions, list)
        or any(not isinstance(item, str) for item in versions)
        or selected not in versions
    ):
        _reject("schema_version_mismatch", f"{key}_binding")


def _validate_wire_schema(
    schema: Mapping[str, object],
    value: Mapping[str, object],
    definition: str,
    reason: ReasonCode,
) -> None:
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict) or definition not in definitions:
        _reject("contract_invalid", "wire_schema_definition")
    validator_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": definitions,
        "$ref": f"#/$defs/{definition}",
    }
    try:
        invalid = next(Draft202012Validator(validator_schema).iter_errors(value), None)
    except (TypeError, ValueError, RuntimeError):
        _reject("contract_invalid", "wire_schema_validation")
    if invalid is not None:
        _reject(reason, "wire_schema_rejected")


def _state_shape(value: object) -> str:
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    _reject("request_malformed", "state_shape")


def _string_sequence(
    value: object,
    *,
    reason: ReasonCode,
    detail: str,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) for item in value)
    ):
        _reject(reason, detail)
    return tuple(value)


def _validate_profile_and_envelope(
    schema: Mapping[str, object],
    profile: Mapping[str, object],
    *,
    request_id: str,
    task_kind: str,
    request_schema_version: str,
    response_schema_version: str,
) -> tuple[
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    str,
]:
    if _REQUEST_ID.fullmatch(request_id) is None:
        _reject("request_malformed", "request_id")
    if task_kind != TASK_KIND:
        _reject("schema_version_mismatch", "task_kind")
    identity, transport, capabilities, limits, compatibility = _profile_sections(profile)
    if compatibility.get("normalization_version") != NORMALIZATION_VERSION:
        _reject("schema_version_mismatch", "normalization_version")
    _version_membership(compatibility, "request_schema_versions", request_schema_version)
    _version_membership(compatibility, "response_schema_versions", response_schema_version)
    if validate_profile(schema, profile) or check_candidate_pins(profile):
        _reject("profile_unpinned", "profile_contract")
    fingerprint = _string(
        identity.get("fingerprint"),
        reason="profile_unpinned",
        detail="identity_fingerprint",
    )
    if _DIGEST.fullmatch(fingerprint) is None:
        _reject("profile_unpinned", "identity_fingerprint")
    return identity, transport, capabilities, limits, fingerprint


def _validate_capability_bounds(
    capabilities: Mapping[str, object],
    limits: Mapping[str, object],
    request: Mapping[str, object],
    response: Mapping[str, object],
) -> None:
    questions = _mapping(
        request.get("questions"),
        reason="request_malformed",
        detail="questions",
    )
    question_types = set(
        _string_sequence(
            capabilities.get("question_types"),
            reason="profile_unpinned",
            detail="capability_question_types",
        ),
    )
    state_shapes = set(
        _string_sequence(
            capabilities.get("state_shapes"),
            reason="profile_unpinned",
            detail="capability_state_shapes",
        ),
    )
    if _state_shape(request.get("state")) not in state_shapes:
        _reject("capability_mismatch", "state_shape")
    max_questions = _integer(
        capabilities.get("max_questions"),
        reason="profile_unpinned",
        detail="capability_max_questions",
        minimum=1,
        maximum=32,
    )
    if not 1 <= len(questions) <= max_questions:
        _reject("capability_mismatch", "question_count")
    max_choice = _integer(
        capabilities.get("max_choice_options"),
        reason="profile_unpinned",
        detail="capability_max_choice_options",
        minimum=2,
        maximum=20,
    )
    max_score = _integer(
        capabilities.get("max_score_levels"),
        reason="profile_unpinned",
        detail="capability_max_score_levels",
        minimum=2,
        maximum=10,
    )
    for question in questions.values():
        item = _mapping(
            question,
            reason="request_malformed",
            detail="question_shape",
        )
        kind = item.get("type")
        if not isinstance(kind, str) or kind not in question_types:
            _reject("capability_mismatch", "question_type")
        criteria = item.get("criteria")
        if kind == "choice" and (
            not isinstance(criteria, dict) or len(criteria) > max_choice
        ):
            _reject("capability_mismatch", "choice_options")
        if kind == "score" and (
            not isinstance(criteria, list) or len(criteria) > max_score
        ):
            _reject("capability_mismatch", "score_levels")
    state_limit = _integer(
        capabilities.get("max_state_bytes"),
        reason="profile_unpinned",
        detail="capability_max_state_bytes",
        minimum=1,
    )
    request_limit = _integer(
        limits.get("request_bytes"),
        reason="profile_unpinned",
        detail="limit_request_bytes",
        minimum=1,
    )
    response_limit = _integer(
        limits.get("response_bytes"),
        reason="profile_unpinned",
        detail="limit_response_bytes",
        minimum=1,
    )
    try:
        state_bytes = len(canonical_json_bytes(request["state"]))
        request_bytes = len(canonical_json_bytes(request))
    except (TypeError, ValueError, RuntimeError):
        _reject("request_malformed", "request_canonical_json")
    try:
        response_bytes = len(canonical_json_bytes(response))
    except (TypeError, ValueError, RuntimeError):
        _reject("response_malformed", "response_canonical_json")
    if state_bytes > state_limit:
        _reject("capability_mismatch", "state_bytes")
    if request_bytes > request_limit:
        _reject("capability_mismatch", "request_bytes")
    if response_bytes > response_limit:
        _reject("capability_mismatch", "response_bytes")


def _probabilities(
    values: object,
    labels: Sequence[str],
    *,
    detail: str,
) -> tuple[tuple[CanonicalProbability, ...], dict[str, Decimal]]:
    probabilities = _mapping(
        values,
        reason="response_semantic_mismatch",
        detail=f"{detail}_probability_shape",
    )
    if set(probabilities) != set(labels):
        _reject("response_semantic_mismatch", f"{detail}_probability_labels")
    decimals = {
        label: _decimal(
            probabilities[label],
            reason="response_semantic_mismatch",
            detail=f"{detail}_probability_value",
        )
        for label in labels
    }
    if abs(sum(decimals.values(), Decimal(0)) - Decimal(1)) > SUM_TOLERANCE:
        _reject("response_semantic_mismatch", f"{detail}_probability_sum")
    canonical = tuple(
        CanonicalProbability(label, _canonical_float(decimals[label]))
        for label in labels
    )
    return canonical, decimals


def _confidence(answer: Mapping[str, object], *, detail: str) -> float:
    return _canonical_float(
        _decimal(
            answer.get("confidence"),
            reason="response_semantic_mismatch",
            detail=f"{detail}_confidence",
        ),
    )


def _choice(
    question: Mapping[str, object],
    answer: Mapping[str, object],
) -> tuple[OutcomeValue, tuple[CanonicalProbability, ...], float | None]:
    _strict_keys(
        answer,
        {"choice", "probabilities", "confidence"},
        reason="response_malformed",
        detail="choice_fields",
    )
    criteria = _mapping(
        question.get("criteria"),
        reason="request_malformed",
        detail="choice_criteria",
    )
    labels = tuple(
        sorted(
            _identifier(
                label,
                reason="request_malformed",
                detail="choice_label",
            )
            for label in criteria
        ),
    )
    selected = _string(
        answer.get("choice"),
        reason="response_semantic_mismatch",
        detail="choice_selected",
    )
    canonical, decimals = _probabilities(
        answer.get("probabilities"),
        labels,
        detail="choice",
    )
    maximum = max(decimals.values())
    winners = tuple(label for label in labels if decimals[label] == maximum)
    if len(winners) != 1 or selected != winners[0]:
        _reject("response_semantic_mismatch", "choice_selection")
    return selected, canonical, _confidence(answer, detail="choice")


def _noul(
    answer: Mapping[str, object],
) -> tuple[OutcomeValue, tuple[CanonicalProbability, ...], float | None]:
    _strict_keys(
        answer,
        {"noul"},
        reason="response_malformed",
        detail="noul_fields",
    )
    positive = _decimal(
        answer.get("noul"),
        reason="response_semantic_mismatch",
        detail="noul_probability",
    )
    if positive == Decimal("0.5"):
        _reject("response_semantic_mismatch", "noul_ambiguous")
    negative = Decimal(1) - positive
    return (
        positive > Decimal("0.5"),
        (
            CanonicalProbability("false", _canonical_float(negative)),
            CanonicalProbability("true", _canonical_float(positive)),
        ),
        None,
    )


def _score(
    question: Mapping[str, object],
    answer: Mapping[str, object],
) -> tuple[OutcomeValue, tuple[CanonicalProbability, ...], float | None]:
    _strict_keys(
        answer,
        {"score", "legend", "probabilities", "confidence"},
        reason="response_malformed",
        detail="score_fields",
    )
    criteria = question.get("criteria")
    if not isinstance(criteria, list):
        _reject("request_malformed", "score_criteria")
    canonical_criteria = tuple(
        _identifier(
            item,
            reason="request_malformed",
            detail="score_criteria",
            maximum=2048,
        )
        for item in criteria
    )
    if len(canonical_criteria) != len(set(canonical_criteria)):
        _reject("request_malformed", "score_criteria")
    labels = tuple(str(index) for index in range(len(canonical_criteria)))
    legend = _mapping(
        answer.get("legend"),
        reason="response_semantic_mismatch",
        detail="score_legend_shape",
    )
    if set(legend) != set(labels) or any(
        legend[label] != canonical_criteria[int(label)] for label in labels
    ):
        _reject("response_semantic_mismatch", "score_legend")
    canonical, decimals = _probabilities(
        answer.get("probabilities"),
        labels,
        detail="score",
    )
    expected = sum(
        (Decimal(index) * decimals[str(index)] for index in range(len(labels))),
        Decimal(0),
    )
    supplied = _decimal(
        answer.get("score"),
        reason="response_semantic_mismatch",
        detail="score_value",
        maximum=Decimal(len(labels) - 1),
    )
    if abs(expected - supplied) > SUM_TOLERANCE:
        _reject("response_semantic_mismatch", "score_expectation")
    return _canonical_float(expected), canonical, _confidence(answer, detail="score")


def _normalize_outcomes(
    request: Mapping[str, object],
    response: Mapping[str, object],
    *,
    request_id: str,
    task_kind: str,
    fingerprint: str,
) -> tuple[NormalizedOutcome, ...]:
    questions = _mapping(
        request.get("questions"),
        reason="request_malformed",
        detail="questions",
    )
    answers = _mapping(
        response.get("answers"),
        reason="response_malformed",
        detail="answers",
    )
    question_ids = tuple(
        _identifier(
            question_id,
            reason="request_malformed",
            detail="question_id",
        )
        for question_id in questions
    )
    if set(question_ids) != set(answers):
        _reject("response_malformed", "answer_identity_set")
    outcomes: list[NormalizedOutcome] = []
    for question_id in sorted(question_ids):
        question = _mapping(
            questions[question_id],
            reason="request_malformed",
            detail="question_shape",
        )
        answer = _mapping(
            answers[question_id],
            reason="response_malformed",
            detail="answer_shape",
        )
        kind = _string(
            question.get("type"),
            reason="request_malformed",
            detail="question_kind",
        )
        if kind == "choice":
            outcome, probabilities, confidence = _choice(question, answer)
        elif kind == "noul":
            outcome, probabilities, confidence = _noul(answer)
        elif kind == "score":
            outcome, probabilities, confidence = _score(question, answer)
        else:
            _reject("request_malformed", "question_kind")
        outcomes.append(
            NormalizedOutcome(
                normalization_version=NORMALIZATION_VERSION,
                request_id=request_id,
                identity_fingerprint=fingerprint,
                task_kind=task_kind,
                question_id=question_id,
                kind=kind,
                outcome=outcome,
                probabilities=probabilities,
                upstream_confidence=confidence,
            ),
        )
    return tuple(outcomes)


def normalize_fixture(
    schema: Mapping[str, object],
    profile: Mapping[str, object],
    *,
    request_id: str,
    task_kind: str,
    request_schema_version: str,
    response_schema_version: str,
    upstream_request: Mapping[str, object],
    upstream_response: Mapping[str, object],
) -> NormalizationRecord:
    """Normalize one exact local fixture or reject it before producing outcomes."""

    if not isinstance(schema, dict) or not isinstance(profile, dict):
        _reject("contract_invalid", "contract_shape")
    if not isinstance(upstream_request, dict):
        _reject("request_malformed", "request_shape")
    if not isinstance(upstream_response, dict):
        _reject("response_malformed", "response_shape")
    schema_digest = _canonical_digest(
        schema,
        reason="contract_invalid",
        detail="schema_canonical_json",
    )
    if schema_digest != CONTRACT_SCHEMA_DIGEST:
        _reject("contract_invalid", "schema_digest")
    _identity, transport, capabilities, limits, fingerprint = (
        _validate_profile_and_envelope(
            schema,
            profile,
            request_id=request_id,
            task_kind=task_kind,
            request_schema_version=request_schema_version,
            response_schema_version=response_schema_version,
        )
    )
    max_state_characters = _integer(
        capabilities.get("max_state_bytes"),
        reason="profile_unpinned",
        detail="capability_max_state_bytes",
        minimum=1,
        maximum=1_048_576,
    )
    _validate_bounded_tree(
        upstream_request.get("state"),
        max_depth=MAX_STATE_DEPTH,
        max_nodes=MAX_STATE_NODES,
        max_characters=max_state_characters,
        reason="capability_mismatch",
        detail="state_complexity",
    )
    _validate_wire_schema(schema, upstream_request, "wireRequest", "request_malformed")
    _validate_wire_schema(schema, upstream_response, "wireResponse", "response_malformed")
    model_id = transport.get("model_id")
    if (
        not isinstance(model_id, str)
        or upstream_request.get("model") != model_id
        or upstream_response.get("model") != model_id
    ):
        _reject("identity_mismatch", "model_id")
    _validate_capability_bounds(capabilities, limits, upstream_request, upstream_response)
    usage = _mapping(
        upstream_response.get("usage"),
        reason="response_malformed",
        detail="usage",
    )
    _integer(
        usage.get("input_tokens"),
        reason="response_malformed",
        detail="input_tokens",
    )
    _integer(
        usage.get("output_tokens"),
        reason="response_malformed",
        detail="output_tokens",
    )
    outcomes = _normalize_outcomes(
        upstream_request,
        upstream_response,
        request_id=request_id,
        task_kind=task_kind,
        fingerprint=fingerprint,
    )
    binding = NormalizationBinding(
        contract_schema_version=_schema_version(schema),
        contract_schema_digest=schema_digest,
        normalization_version=NORMALIZATION_VERSION,
        request_schema_version=request_schema_version,
        response_schema_version=response_schema_version,
        profile_id=_string(
            profile.get("profile_id"),
            reason="profile_unpinned",
            detail="profile_id",
            maximum=80,
        ),
        profile_digest=_canonical_digest(
            profile,
            reason="profile_unpinned",
            detail="profile_canonical_json",
        ),
        identity_fingerprint=fingerprint,
        capability_digest=_canonical_digest(
            capabilities,
            reason="profile_unpinned",
            detail="capability_canonical_json",
        ),
        request_digest=_canonical_digest(
            upstream_request,
            reason="request_malformed",
            detail="request_canonical_json",
        ),
        response_digest=_canonical_digest(
            upstream_response,
            reason="response_malformed",
            detail="response_canonical_json",
        ),
    )
    without_digest = NormalizationRecord(
        binding=binding,
        outcomes=outcomes,
        request_id=request_id,
        task_kind=task_kind,
        ready=True,
        network_accessed=False,
        runtime_invoked=False,
        model_invoked=False,
        admission_performed=False,
        record_digest="",
    )
    return NormalizationRecord(
        binding=binding,
        outcomes=outcomes,
        request_id=request_id,
        task_kind=task_kind,
        ready=True,
        network_accessed=False,
        runtime_invoked=False,
        model_invoked=False,
        admission_performed=False,
        record_digest=canonical_digest(without_digest.body_dict()),
    )


def replay_normalization(
    record: NormalizationRecord,
    schema: Mapping[str, object],
    profile: Mapping[str, object],
    *,
    request_id: str,
    task_kind: str,
    request_schema_version: str,
    response_schema_version: str,
    upstream_request: Mapping[str, object],
    upstream_response: Mapping[str, object],
) -> NormalizationRecord:
    """Rebuild exact evidence and reject changed inputs or tampered records."""

    if not isinstance(record, NormalizationRecord):
        _reject("replay_mismatch", "record_type")
    try:
        rebuilt = normalize_fixture(
            schema,
            profile,
            request_id=request_id,
            task_kind=task_kind,
            request_schema_version=request_schema_version,
            response_schema_version=response_schema_version,
            upstream_request=upstream_request,
            upstream_response=upstream_response,
        )
    except NormalizationError:
        _reject("replay_mismatch", "rebuild_rejected")
    if rebuilt != record:
        _reject("replay_mismatch", "record_changed")
    return rebuilt
