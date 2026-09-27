"""Shared, content-free validation primitives for FreeLLMAPI release proof."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import NoReturn

FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION = 1
FREELLMAPI_FROZEN_CORPUS_GATE = "freellmapi-live-corpus-proof-v1"
FREELLMAPI_LIVE_PROVIDER_GATE = "freellmapi-live-provider-proof-v1"
FREELLMAPI_ROLLBACK_GATE = "freellmapi-rollback-proof-v1"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PROVIDER_FAILURES = frozenset(
    {
        "authentication",
        "authorization",
        "not_found",
        "rate_limited",
        "timeout",
        "transport",
        "unavailable",
        "invalid_response",
        "internal",
    }
)


class FreeLLMAPIReleaseProofFault(StrEnum):
    """Stable content-free failure categories for release-proof handling."""

    CANDIDATE_INVALID = "candidate_invalid"
    CORPUS_INVALID = "corpus_invalid"
    LIVE_PROVIDER_INVALID = "live_provider_invalid"
    ROLLBACK_INVALID = "rollback_invalid"
    RECEIPT_INVALID = "receipt_invalid"


class FreeLLMAPIReleaseProofError(ValueError):
    """Fail-closed proof error that never reflects provider or task content."""

    def __init__(self, fault: FreeLLMAPIReleaseProofFault) -> None:
        """Create an error containing only its stable fault category."""
        self.fault = fault
        super().__init__(fault.value)


def fail(fault: FreeLLMAPIReleaseProofFault) -> NoReturn:
    """Raise one stable release-proof fault."""
    raise FreeLLMAPIReleaseProofError(fault)


def as_mapping(
    value: object, fault: FreeLLMAPIReleaseProofFault
) -> Mapping[str, object]:
    """Return a mapping or fail with the caller-owned category."""
    if not isinstance(value, Mapping):
        fail(fault)
    return value


def sha256_digest(value: object, fault: FreeLLMAPIReleaseProofFault) -> str:
    """Validate a raw SHA-256 digest."""
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        fail(fault)
    return value


def evidence_id(value: object, fault: FreeLLMAPIReleaseProofFault) -> str:
    """Validate a digest-addressed evidence identifier."""
    if not isinstance(value, str) or EVIDENCE_ID_RE.fullmatch(value) is None:
        fail(fault)
    return value


def canonical_digest(value: object, fault: FreeLLMAPIReleaseProofFault) -> str:
    """Digest canonical ASCII JSON while converting serialization failures."""
    try:
        payload = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        fail(fault)
    return hashlib.sha256(payload).hexdigest()


def stable_evidence_id(value: Mapping[str, object]) -> str:
    """Return the content-addressed identifier for an unsigned receipt."""
    return "sha256:" + canonical_digest(
        value, FreeLLMAPIReleaseProofFault.RECEIPT_INVALID
    )


def verify_receipt_identity(
    receipt: Mapping[str, object], fault: FreeLLMAPIReleaseProofFault
) -> None:
    """Verify that a receipt is exactly bound to its stored content identity."""
    stored = evidence_id(receipt.get("evidence_id"), fault)
    unsigned = dict(receipt)
    unsigned.pop("evidence_id", None)
    if stored != stable_evidence_id(unsigned):
        fail(fault)


def nonnegative_number(
    value: object,
    fault: FreeLLMAPIReleaseProofFault,
    *,
    maximum: float | None = None,
) -> float:
    """Validate a finite, nonnegative number and optional upper bound."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        fail(fault)
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0:
        fail(fault)
    if maximum is not None and parsed > maximum:
        fail(fault)
    return parsed


def strict_timestamp(value: object, fault: FreeLLMAPIReleaseProofFault) -> str:
    """Validate an offset-aware ISO-8601 timestamp without normalizing it."""
    if not isinstance(value, str):
        fail(fault)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail(fault)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail(fault)
    return value


__all__ = [
    "EVIDENCE_ID_RE",
    "FREELLMAPI_FROZEN_CORPUS_GATE",
    "FREELLMAPI_LIVE_PROVIDER_GATE",
    "FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION",
    "FREELLMAPI_ROLLBACK_GATE",
    "PROVIDER_FAILURES",
    "PROVIDER_RE",
    "FreeLLMAPIReleaseProofError",
    "FreeLLMAPIReleaseProofFault",
    "as_mapping",
    "canonical_digest",
    "evidence_id",
    "fail",
    "nonnegative_number",
    "sha256_digest",
    "stable_evidence_id",
    "strict_timestamp",
    "verify_receipt_identity",
]
