"""Typed, content-free contracts shared by the FreeLLMAPI delta gate."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import NoReturn, TypedDict

from general_ludd.models.freellmapi_upstream_admission import (
    FREELLMAPI_FROZEN_DELTA_GATE as _UPSTREAM_FROZEN_DELTA_GATE,
)

FREELLMAPI_FROZEN_DELTA_GATE = _UPSTREAM_FROZEN_DELTA_GATE
FREELLMAPI_FROZEN_DELTA_SCHEMA_VERSION = 1

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class FreeLLMAPIFrozenDeltaFault(StrEnum):
    """Stable, content-free frozen-delta failure categories."""

    CANDIDATE_INVALID = "candidate_invalid"
    PLAN_INVALID = "plan_invalid"
    ABI_INVALID = "abi_invalid"
    OBSERVATIONS_INVALID = "observations_invalid"


class FreeLLMAPIFrozenDeltaError(ValueError):
    """Fail-closed error that exposes no task, prompt, or model content."""

    def __init__(self, fault: FreeLLMAPIFrozenDeltaFault) -> None:
        """Create an error containing only its stable fault category."""
        self.fault = fault
        super().__init__(fault.value)


class PenaltyWeights(TypedDict):
    """Preregistered per-unit penalties used by one frozen experiment."""

    latency_ms: float
    memory_mib: float
    cost_usd: float
    failure: float


class ValidatedPlan(TypedDict):
    """Validated subset of a plan safe for deterministic evaluation."""

    plan_id: str
    fixtures: tuple[str, ...]
    corpus_sha256: str
    selected_export: str
    capability_id: str
    abi_version: int
    minimum_adjusted_gain: float
    confidence_z: float
    penalties: PenaltyWeights


def fail(fault: FreeLLMAPIFrozenDeltaFault) -> NoReturn:
    """Raise one stable fault without reflecting rejected input content."""
    raise FreeLLMAPIFrozenDeltaError(fault)


def mapping(
    value: object, fault: FreeLLMAPIFrozenDeltaFault
) -> Mapping[str, object]:
    """Return a mapping or fail with the caller's content-free category."""
    if not isinstance(value, Mapping):
        fail(fault)
    return value


def nonempty(value: object, fault: FreeLLMAPIFrozenDeltaFault) -> str:
    """Return bounded-meaning text without exposing it in an error."""
    if not isinstance(value, str) or not value.strip():
        fail(fault)
    return value


def digest(value: object, fault: FreeLLMAPIFrozenDeltaFault) -> str:
    """Validate a lowercase SHA-256 digest."""
    text = nonempty(value, fault)
    if _DIGEST_RE.fullmatch(text) is None:
        fail(fault)
    return text


def candidate_id(value: object, fault: FreeLLMAPIFrozenDeltaFault) -> str:
    """Validate the namespaced SHA-256 identity of an upstream candidate."""
    text = nonempty(value, fault)
    if _CANDIDATE_ID_RE.fullmatch(text) is None:
        fail(fault)
    return text


def number(
    value: object,
    fault: FreeLLMAPIFrozenDeltaFault,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> float:
    """Validate one finite bounded measurement or policy value."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        fail(fault)
    parsed = float(value)
    if not math.isfinite(parsed):
        fail(fault)
    if parsed < minimum or (exclusive_minimum and parsed == minimum):
        fail(fault)
    if maximum is not None and parsed > maximum:
        fail(fault)
    return parsed


def canonical_bytes(value: object, fault: FreeLLMAPIFrozenDeltaFault) -> bytes:
    """Encode evidence deterministically or fail without reflecting content."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        fail(fault)
    return encoded.encode("ascii")


def canonical_digest(value: object, fault: FreeLLMAPIFrozenDeltaFault) -> str:
    """Return the SHA-256 digest of canonical JSON evidence."""
    return hashlib.sha256(canonical_bytes(value, fault)).hexdigest()
