"""Ownership contract for shared FreeLLMAPI proof types."""

import math

import pytest

from general_ludd.models.freellmapi_release_common import (
    FreeLLMAPIReleaseProofFault,
    as_mapping,
    canonical_digest,
    evidence_id,
    nonnegative_number,
    sha256_digest,
    stable_evidence_id,
    strict_timestamp,
    verify_receipt_identity,
)
from general_ludd.models.freellmapi_release_proof import (
    FreeLLMAPIReleaseProofError,
)

_FAULT = FreeLLMAPIReleaseProofFault.RECEIPT_INVALID


def test_release_error_is_owned_by_the_common_module() -> None:
    assert FreeLLMAPIReleaseProofError.__module__ == (
        "general_ludd.models.freellmapi_release_common"
    )


def test_release_common_validators_fail_closed_on_wrong_shapes() -> None:
    invalid_calls = (
        lambda: as_mapping(object(), _FAULT),
        lambda: sha256_digest(7, _FAULT),
        lambda: sha256_digest("bad", _FAULT),
        lambda: evidence_id(7, _FAULT),
        lambda: evidence_id("sha256:bad", _FAULT),
        lambda: canonical_digest(object(), _FAULT),
        lambda: nonnegative_number(True, _FAULT),
        lambda: nonnegative_number("1", _FAULT),
        lambda: nonnegative_number(math.nan, _FAULT),
        lambda: nonnegative_number(-1, _FAULT),
        lambda: nonnegative_number(2, _FAULT, maximum=1),
        lambda: strict_timestamp(7, _FAULT),
        lambda: strict_timestamp("not-a-time", _FAULT),
        lambda: strict_timestamp("2026-09-27T12:00:00", _FAULT),
    )

    for call in invalid_calls:
        with pytest.raises(FreeLLMAPIReleaseProofError) as caught:
            call()
        assert caught.value.fault is _FAULT


def test_release_common_validators_accept_canonical_values_and_detect_rehash() -> None:
    digest = "a" * 64
    assert as_mapping({"value": 1}, _FAULT) == {"value": 1}
    assert sha256_digest(digest, _FAULT) == digest
    assert evidence_id(f"sha256:{digest}", _FAULT) == f"sha256:{digest}"
    assert nonnegative_number(1, _FAULT) == 1.0
    assert nonnegative_number(1, _FAULT, maximum=1) == 1.0
    assert strict_timestamp("2026-09-27T12:00:00Z", _FAULT).endswith("Z")
    receipt: dict[str, object] = {"value": 1}
    receipt["evidence_id"] = stable_evidence_id(receipt)
    verify_receipt_identity(receipt, _FAULT)
    receipt["value"] = 2
    with pytest.raises(FreeLLMAPIReleaseProofError):
        verify_receipt_identity(receipt, _FAULT)
