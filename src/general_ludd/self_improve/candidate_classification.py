"""Deterministic, content-free task classification for model candidates.

The classifier composes the repository's existing semantic task-type inference
and keyword capability mapper.  Returned artifacts retain only bounded category
labels and a digest of the exact input; task content never crosses the artifact
boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Final

from general_ludd.schemas.benchmark import TaskRole, TaskType
from general_ludd.self_improve.task_diversity import infer_task_type
from general_ludd.small_models.recommender import map_task_to_capabilities

CANDIDATE_CLASSIFICATION_PROTOCOL: Final = "gludd-candidate-classification-v1"
CAPABILITY_PRECEDENCE_VERSION: Final = "gludd-capability-precedence-v1"
_MAX_TASK_TEXT_BYTES: Final = 256_000

# This table is an ordered, versioned policy rather than an accidental reliance
# on the mapper's current list order. Changing it requires a new precedence
# version so prior predictions remain attributable and replayable.
_CAPABILITY_PRECEDENCE: Final[tuple[tuple[str, TaskRole], ...]] = (
    ("context_compaction", TaskRole.COMPACTOR),
    ("documentation_draft", TaskRole.EDITOR),
    ("bounded_enumeration", TaskRole.ENUMERATOR),
    ("failure_classification", TaskRole.REVIEWER),
    ("format_normalization", TaskRole.EDITOR),
    ("schema_extraction", TaskRole.EDITOR),
    ("coding", TaskRole.CODER),
)
_CAPABILITY_ORDER: Final = {
    pair: index for index, pair in enumerate(_CAPABILITY_PRECEDENCE)
}


def _stable_digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_digest(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def task_text_digest(task_text: str) -> str:
    """Return a SHA-256 identity for one bounded, non-empty task description."""
    if not isinstance(task_text, str) or not task_text.strip():
        raise ValueError("task_text must be a non-empty string")
    if "\x00" in task_text:
        raise ValueError("task_text must not contain NUL characters")
    encoded = task_text.encode("utf-8")
    if len(encoded) > _MAX_TASK_TEXT_BYTES:
        raise ValueError(
            f"task_text must encode to no more than {_MAX_TASK_TEXT_BYTES} bytes"
        )
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidateCapability:
    """One bounded task-kind/role pair admitted by precedence policy v1."""

    task_kind: str
    role: TaskRole

    def __post_init__(self) -> None:
        """Reject arbitrary labels and role substitutions."""
        if not isinstance(self.task_kind, str) or not isinstance(self.role, TaskRole):
            raise ValueError("capability must contain a known task kind and TaskRole")
        if (self.task_kind, self.role) not in _CAPABILITY_ORDER:
            raise ValueError("capability is not admitted by the active precedence policy")

    def payload(self) -> dict[str, str]:
        """Return a canonical, content-free representation."""
        return {"role": self.role.value, "task_kind": self.task_kind}


def _normalize_capabilities(
    mapped: object,
) -> tuple[CandidateCapability, ...]:
    if not isinstance(mapped, list) or not mapped:
        raise ValueError("task_text must match at least one mapped capability")
    normalized: list[CandidateCapability] = []
    seen: set[tuple[str, TaskRole]] = set()
    for raw in mapped:
        if not isinstance(raw, tuple) or len(raw) != 2:
            raise ValueError("mapped capability must be a task-kind/role tuple")
        task_kind, role = raw
        if not isinstance(task_kind, str) or not isinstance(role, TaskRole):
            raise ValueError("mapped capability contains an invalid category")
        pair = (task_kind, role)
        if pair in seen:
            raise ValueError("mapped capabilities must not contain duplicates")
        try:
            _CAPABILITY_ORDER[pair]
        except KeyError as exc:
            raise ValueError(
                "mapped capability is not admitted by the active precedence policy"
            ) from exc
        seen.add(pair)
        normalized.append(CandidateCapability(task_kind, role))
    return tuple(
        sorted(
            normalized,
            key=lambda capability: _CAPABILITY_ORDER[
                (capability.task_kind, capability.role)
            ],
        )
    )


@dataclass(frozen=True, slots=True)
class CandidateTaskClassification:
    """Immutable task identity and categories used to select model candidates."""

    task_text_digest: str
    task_type: TaskType
    task_kind: str
    task_role: TaskRole
    matched_capabilities: tuple[CandidateCapability, ...]
    protocol: str = CANDIDATE_CLASSIFICATION_PROTOCOL
    precedence_version: str = CAPABILITY_PRECEDENCE_VERSION

    def __post_init__(self) -> None:
        """Fail closed on stale versions, mutable collections, or category drift."""
        _require_digest(self.task_text_digest, "task_text_digest")
        if not isinstance(self.task_type, TaskType):
            raise ValueError("task_type must be a TaskType")
        if self.protocol != CANDIDATE_CLASSIFICATION_PROTOCOL:
            raise ValueError("protocol is not supported")
        if self.precedence_version != CAPABILITY_PRECEDENCE_VERSION:
            raise ValueError("precedence_version is not supported")
        if not isinstance(self.matched_capabilities, tuple):
            raise ValueError("matched_capabilities must be a tuple")
        if not self.matched_capabilities or any(
            not isinstance(capability, CandidateCapability)
            for capability in self.matched_capabilities
        ):
            raise ValueError(
                "matched_capabilities must contain known CandidateCapability values"
            )
        ordered = tuple(
            sorted(
                self.matched_capabilities,
                key=lambda capability: _CAPABILITY_ORDER[
                    (capability.task_kind, capability.role)
                ],
            )
        )
        if len(set(ordered)) != len(ordered) or ordered != self.matched_capabilities:
            raise ValueError(
                "matched_capabilities must be unique and follow active precedence"
            )
        primary = ordered[0]
        if self.task_kind != primary.task_kind or self.task_role is not primary.role:
            raise ValueError("task_kind and task_role must match the primary capability")

    def payload(self) -> dict[str, object]:
        """Return every immutable field without exposing task content."""
        return {
            "matched_capabilities": [
                capability.payload() for capability in self.matched_capabilities
            ],
            "precedence_version": self.precedence_version,
            "protocol": self.protocol,
            "task_kind": self.task_kind,
            "task_role": self.task_role.value,
            "task_text_digest": self.task_text_digest,
            "task_type": self.task_type.value,
        }

    @property
    def classification_digest(self) -> str:
        """Return the stable identity of the complete classification artifact."""
        return _stable_digest(self.payload())

    def event_payload(self) -> dict[str, str]:
        """Return a safe observability event with no prompt or credential content."""
        return {
            "classification_digest": self.classification_digest,
            "event": "self_improve_candidate_classified",
            "precedence_version": self.precedence_version,
            "protocol": self.protocol,
            "task_kind": self.task_kind,
            "task_role": self.task_role.value,
            "task_text_digest": self.task_text_digest,
            "task_type": self.task_type.value,
        }


def classify_candidate_task(
    task_text: str,
    *,
    expected_task_text_digest: str | None = None,
) -> CandidateTaskClassification:
    """Classify exact task text into deterministic, digest-bound categories."""
    digest = task_text_digest(task_text)
    if expected_task_text_digest is not None:
        expected = _require_digest(
            expected_task_text_digest,
            "expected_task_text_digest",
        )
        if not hmac.compare_digest(digest, expected):
            raise ValueError("task text digest mismatch")
    task_type = infer_task_type(task_text)
    if not isinstance(task_type, TaskType):
        raise ValueError("task type classifier returned an invalid TaskType")
    capabilities = _normalize_capabilities(map_task_to_capabilities(task_text))
    primary = capabilities[0]
    return CandidateTaskClassification(
        task_text_digest=digest,
        task_type=task_type,
        task_kind=primary.task_kind,
        task_role=primary.role,
        matched_capabilities=capabilities,
    )


def verify_candidate_task_classification(
    classification: CandidateTaskClassification,
    task_text: str,
) -> bool:
    """Reclassify exact content and reject substitution or category drift."""
    if not isinstance(classification, CandidateTaskClassification):
        raise ValueError("classification must be a CandidateTaskClassification")
    current = classify_candidate_task(
        task_text,
        expected_task_text_digest=classification.task_text_digest,
    )
    if not hmac.compare_digest(
        current.classification_digest,
        classification.classification_digest,
    ):
        raise ValueError("classification digest mismatch")
    return True


__all__ = (
    "CANDIDATE_CLASSIFICATION_PROTOCOL",
    "CAPABILITY_PRECEDENCE_VERSION",
    "CandidateCapability",
    "CandidateTaskClassification",
    "classify_candidate_task",
    "task_text_digest",
    "verify_candidate_task_classification",
)
