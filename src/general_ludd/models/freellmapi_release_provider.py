"""Sanitized native-provider evidence for the FreeLLMAPI release proof."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import cast

from general_ludd.models.freellmapi_release_common import (
    EVIDENCE_ID_RE,
    FREELLMAPI_LIVE_PROVIDER_GATE,
    FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION,
    PROVIDER_FAILURES,
    PROVIDER_RE,
    FreeLLMAPIReleaseProofFault,
    evidence_id,
    fail,
    sha256_digest,
    stable_evidence_id,
    strict_timestamp,
    verify_receipt_identity,
)

_LIVE_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "gate",
        "candidate_id",
        "corpus_evidence_id",
        "provider",
        "provider_model_sha256",
        "candidate_identity_digest",
        "observed_at",
        "external_opt_in",
        "transport_owner",
        "request_count",
        "accepted_count",
        "provider_input_tokens",
        "provider_output_tokens",
        "provider_total_tokens",
        "provider_failure",
        "queue_empty_after",
        "provisioned_compute_remaining",
        "decision",
        "runtime_admitted",
        "evidence_id",
    }
)


def _accounting(accounting: object) -> tuple[int, int, int, int, int, int, int]:
    fault = FreeLLMAPIReleaseProofFault.LIVE_PROVIDER_INVALID
    names = (
        "requests_started",
        "responses_received",
        "responses_accepted",
        "requests_failed",
        "provider_input_tokens",
        "provider_output_tokens",
        "provider_total_tokens",
    )
    values: list[int] = []
    for name in names:
        value = getattr(accounting, name, None)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            fail(fault)
        values.append(value)
    return cast("tuple[int, int, int, int, int, int, int]", tuple(values))


def _receipt_count(receipt: Mapping[str, object], name: str) -> int:
    fault = FreeLLMAPIReleaseProofFault.LIVE_PROVIDER_INVALID
    value = receipt.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        fail(fault)
    return value


def build_live_provider_receipt(
    *,
    candidate_id: str,
    corpus_evidence_id: str,
    provider: str,
    model: str,
    traces: Sequence[object],
    accounting: object,
    observed_at: str,
    external_opt_in: bool,
    queue_empty_after: bool,
    provisioned_compute_remaining: int,
) -> dict[str, object]:
    """Build sanitized evidence from one real native-gateway provider call."""
    fault = FreeLLMAPIReleaseProofFault.LIVE_PROVIDER_INVALID
    if EVIDENCE_ID_RE.fullmatch(candidate_id) is None:
        fail(fault)
    evidence_id(corpus_evidence_id, fault)
    if PROVIDER_RE.fullmatch(provider) is None:
        fail(fault)
    if not isinstance(model, str) or not model or len(model.encode("utf-8")) > 256:
        fail(fault)
    if external_opt_in is not True or queue_empty_after is not True:
        fail(fault)
    if (
        isinstance(provisioned_compute_remaining, bool)
        or provisioned_compute_remaining != 0
    ):
        fail(fault)
    if isinstance(traces, (str, bytes)) or len(traces) != 2:
        fail(fault)
    first, second = traces
    first_event = str(getattr(first, "event", ""))
    second_event = str(getattr(second, "event", ""))
    accepted_response = second_event == "freellmapi_response_accepted"
    rejected_response = second_event == "freellmapi_request_failed"
    if first_event != "freellmapi_request_started" or not (
        accepted_response or rejected_response
    ):
        fail(fault)
    first_digest = sha256_digest(getattr(first, "candidate_digest", None), fault)
    second_digest = sha256_digest(getattr(second, "candidate_digest", None), fault)
    request_number = getattr(first, "request_number", None)
    if (
        first_digest != second_digest
        or request_number != 1
        or getattr(second, "request_number", None) != 1
    ):
        fail(fault)
    values = _accounting(accounting)
    started, received, accepted, failed, input_tokens, output_tokens, total_tokens = values
    provider_failure: str | None
    if accepted_response:
        provider_failure = None
        valid_accounting = (
            (started, received, accepted, failed) == (1, 1, 1, 0)
            and input_tokens + output_tokens == total_tokens
            and getattr(second, "input_tokens", None) == input_tokens
            and getattr(second, "output_tokens", None) == output_tokens
            and getattr(second, "total_tokens", None) == total_tokens
        )
    else:
        provider_failure = str(getattr(second, "failure", ""))
        valid_accounting = (
            provider_failure in PROVIDER_FAILURES
            and (started, received, accepted, failed) == (1, 0, 0, 1)
            and (input_tokens, output_tokens, total_tokens) == (0, 0, 0)
        )
    if not valid_accounting:
        fail(fault)
    receipt: dict[str, object] = {
        "schema_version": FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION,
        "gate": FREELLMAPI_LIVE_PROVIDER_GATE,
        "candidate_id": candidate_id,
        "corpus_evidence_id": corpus_evidence_id,
        "provider": provider,
        "provider_model_sha256": hashlib.sha256(
            f"{provider}\0{model}".encode()
        ).hexdigest(),
        "candidate_identity_digest": first_digest,
        "observed_at": strict_timestamp(observed_at, fault),
        "external_opt_in": True,
        "transport_owner": "general_ludd.models.gateway",
        "request_count": started,
        "accepted_count": accepted,
        "provider_input_tokens": input_tokens,
        "provider_output_tokens": output_tokens,
        "provider_total_tokens": total_tokens,
        "provider_failure": provider_failure,
        "queue_empty_after": True,
        "provisioned_compute_remaining": 0,
        "decision": (
            "live_provider_verified"
            if accepted_response
            else "live_provider_rejected"
        ),
        "runtime_admitted": False,
    }
    receipt["evidence_id"] = stable_evidence_id(receipt)
    return receipt


def validate_live_receipt(
    receipt: Mapping[str, object], *, candidate_id: object, corpus_id: object
) -> None:
    """Validate a tracked live receipt without replaying the external call."""
    fault = FreeLLMAPIReleaseProofFault.LIVE_PROVIDER_INVALID
    verify_receipt_identity(receipt, fault)
    expected_candidate = evidence_id(candidate_id, fault)
    expected_corpus = evidence_id(corpus_id, fault)
    provider = receipt.get("provider")
    provisioned = receipt.get("provisioned_compute_remaining")
    if (
        set(receipt) != _LIVE_RECEIPT_KEYS
        or receipt.get("schema_version") != FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION
        or receipt.get("gate") != FREELLMAPI_LIVE_PROVIDER_GATE
        or receipt.get("candidate_id") != expected_candidate
        or receipt.get("corpus_evidence_id") != expected_corpus
        or not isinstance(provider, str)
        or PROVIDER_RE.fullmatch(provider) is None
        or receipt.get("decision")
        not in {"live_provider_verified", "live_provider_rejected"}
        or receipt.get("runtime_admitted") is not False
        or receipt.get("external_opt_in") is not True
        or receipt.get("queue_empty_after") is not True
        or isinstance(provisioned, bool)
        or provisioned != 0
        or receipt.get("transport_owner") != "general_ludd.models.gateway"
    ):
        fail(fault)
    strict_timestamp(receipt.get("observed_at"), fault)
    sha256_digest(receipt.get("provider_model_sha256"), fault)
    sha256_digest(receipt.get("candidate_identity_digest"), fault)
    request_count = _receipt_count(receipt, "request_count")
    accepted_count = _receipt_count(receipt, "accepted_count")
    input_tokens = _receipt_count(receipt, "provider_input_tokens")
    output_tokens = _receipt_count(receipt, "provider_output_tokens")
    total_tokens = _receipt_count(receipt, "provider_total_tokens")
    decision = receipt.get("decision")
    failure = receipt.get("provider_failure")
    accepted_valid = (
        decision == "live_provider_verified"
        and failure is None
        and request_count == 1
        and accepted_count == 1
        and input_tokens + output_tokens == total_tokens
    )
    rejected_valid = (
        decision == "live_provider_rejected"
        and isinstance(failure, str)
        and failure in PROVIDER_FAILURES
        and request_count == 1
        and accepted_count == 0
        and (input_tokens, output_tokens, total_tokens) == (0, 0, 0)
    )
    if not (accepted_valid or rejected_valid):
        fail(fault)


__all__ = [
    "build_live_provider_receipt",
    "validate_live_receipt",
    "verify_receipt_identity",
]
