"""Exact censored vocabulary tests for Azure lifecycle failure evidence."""

from general_ludd.infra.azure_containerapp_failure_details import (
    LIVE_PROOF_FAILURE_DETAILS,
)
from general_ludd.infra.azure_containerapp_make_types import (
    LIVE_PROOF_RUNTIME_FAILURE_DETAILS,
)


def test_failure_details_are_fixed_and_include_every_runtime_phase() -> None:
    assert isinstance(LIVE_PROOF_FAILURE_DETAILS, frozenset)
    assert LIVE_PROOF_RUNTIME_FAILURE_DETAILS <= LIVE_PROOF_FAILURE_DETAILS
    assert {
        "action",
        "configuration",
        "environment_binding",
        "resource_identity",
        "resource_scope",
    } <= LIVE_PROOF_FAILURE_DETAILS


def test_failure_details_never_admit_provider_or_secret_text() -> None:
    assert "provider_error_text" not in LIVE_PROOF_FAILURE_DETAILS
    assert "clientSecret" not in LIVE_PROOF_FAILURE_DETAILS
    assert all(
        detail == detail.casefold() and detail.replace("_", "").isalnum()
        for detail in LIVE_PROOF_FAILURE_DETAILS
    )
