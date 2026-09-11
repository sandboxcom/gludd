"""Security contracts for Azure readiness state normalization."""

from __future__ import annotations

from general_ludd.infra.azure_containerapp_runtime_state import (
    _app_provisioning_state,
    _bounded_status_count,
    _environment_ready,
    _event_value,
    _revision_state,
    _with_ready_revision,
)


def test_provider_controlled_state_is_reduced_to_fixed_safe_values() -> None:
    """Unknown provider text never reaches lifecycle progress events."""
    secret = "private-provider-response-never-log"

    assert _app_provisioning_state(
        {
            "properties": {
                "provisioningState": secret,
                "latestReadyRevisionName": 7,
            }
        }
    ) == ("Unknown", False)
    assert _revision_state(
        {
            "properties": {
                "healthState": secret,
                "provisioningState": secret,
                "runningState": secret,
            }
        }
    ).terminal is False


def test_environment_and_count_normalizers_reject_ambiguous_values() -> None:
    """Missing mappings, booleans, negatives, and oversized counts stay inert."""
    assert _environment_ready(None) is False
    assert _environment_ready({"properties": "Succeeded"}) is False
    assert _environment_ready({"properties": {"provisioningState": "Succeeded"}})
    assert _bounded_status_count(True) == 0
    assert _bounded_status_count(-1) == 0
    assert _bounded_status_count(1_000_001) == 0
    assert _bounded_status_count(3) == 3


def test_event_and_document_normalizers_cover_every_bounded_shape() -> None:
    """Event reduction and revision binding preserve no provider-defined schema."""
    assert _event_value(()) == "None"
    assert _event_value(("Running",)) == "Running"
    assert _event_value(("Running", "Waiting")) == "Mixed"
    assert _with_ready_revision(None, "revision") is None
    assert _with_ready_revision({"properties": "invalid"}, "revision") == {
        "properties": "invalid"
    }
