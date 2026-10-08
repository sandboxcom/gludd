"""Pure identity and existing-evidence helpers for decision capture."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from typing import Final, TypeVar

from general_ludd.decision_codification.schema import VerifiedOutcome
from general_ludd.replay.store import VerifiedBundle

_CORRELATION_DOMAIN: Final[bytes] = (
    b"general_ludd.decision_capture.correlation.v1\x00"
)
_ReceiptT = TypeVar("_ReceiptT")


def bounded_private_identifier(
    value: object,
    *,
    max_bytes: int,
    error_type: type[RuntimeError],
) -> str:
    """Validate a private identifier without placing its content in errors."""
    if not isinstance(value, str) or not value:
        raise error_type("decision capture identity is invalid")
    if len(value.encode("utf-8")) > max_bytes:
        raise error_type("decision capture identity exceeds its bound")
    return value


def correlation_digest(
    *,
    correlation_key: bytes,
    project_id: str,
    policy_digest: str,
    label: str,
    values: tuple[str, ...],
) -> str:
    """Derive one domain-separated opaque identity for private inputs."""
    message = b"\x00".join(
        (
            label.encode("ascii"),
            project_id.encode("utf-8"),
            policy_digest.encode("ascii"),
            *(value.encode("utf-8") for value in values),
        )
    )
    return hmac.new(
        correlation_key,
        _CORRELATION_DOMAIN + message,
        hashlib.sha256,
    ).hexdigest()


def receipt_for_existing_bundle(
    bundle: VerifiedBundle,
    *,
    project_id: str,
    event_type: str,
    correlation: dict[str, object],
    decision_payload: dict[str, object],
    outcome: VerifiedOutcome,
    terminal_event_id: str,
    status_digest: str,
    error_type: type[RuntimeError],
    receipt_factory: Callable[[str, str, str, str], _ReceiptT],
) -> _ReceiptT:
    """Verify that immutable evidence exactly matches an idempotent request."""
    if len(bundle.events) != 2:
        raise error_type("decision capture identity conflicts with evidence")
    decision_event, outcome_event = bundle.events
    expected_outcome = {
        "decision_event_digest": decision_event.digest,
        "verified_outcome": outcome.value,
        "terminal_event_ids": [terminal_event_id],
        "gate_digests": [],
        "status_digests": [status_digest],
    }
    evidence_matches = (
        bundle.manifest.project_id == project_id
        and decision_event.type == event_type
        and outcome_event.type == "decision.outcome"
        and decision_event.project_id == project_id
        and outcome_event.project_id == project_id
        and decision_event.correlation.model_dump(mode="json") == correlation
        and outcome_event.correlation.model_dump(mode="json") == correlation
        and decision_event.payload == decision_payload
        and outcome_event.payload == expected_outcome
    )
    if not evidence_matches:
        raise error_type("decision capture identity conflicts with evidence")
    return receipt_factory(
        bundle.manifest.run_id,
        decision_event.digest,
        outcome_event.digest,
        bundle.manifest.events_sha256,
    )


__all__ = [
    "bounded_private_identifier",
    "correlation_digest",
    "receipt_for_existing_bundle",
]
