"""Tests for secret-free Azure bootstrap runtime tracing."""

from types import SimpleNamespace

from general_ludd.self_improve.azure_containerapp_bootstrap_runtime import (
    runtime_trace,
)


def test_runtime_trace_allowlists_infrastructure_dimensions() -> None:
    """Runtime traces expose bounded operational facts without resource names."""
    messages: list[str] = []

    runtime_trace(
        messages.append,
        "app_terraform",
        SimpleNamespace(
            phase="apply",
            state="running",
            operation_digest="d" * 64,
            event_source="opentofu_ui",
            resource_type="azapi_resource",
            action="create",
            event_kind="apply_progress",
            provisioning_state="in-progress",
        ),
    )

    assert len(messages) == 1
    assert "event_source=opentofu_ui" in messages[0]
    assert "action=create" in messages[0]
    assert "secret_output=false" in messages[0]


def test_runtime_trace_exposes_only_a_validated_worker_envelope_digest() -> None:
    messages: list[str] = []

    runtime_trace(
        messages.append,
        "backend",
        SimpleNamespace(
            event="azure_containerapp_request_started",
            candidate_digest="c" * 64,
            envelope_digest="e" * 64,
        ),
    )
    runtime_trace(
        messages.append,
        "backend",
        SimpleNamespace(
            event="azure_containerapp_request_started",
            candidate_digest="c" * 64,
            envelope_digest="PRIVATE_ENVELOPE",
        ),
    )

    assert "envelope_digest=" + "e" * 64 in messages[0]
    assert "envelope_digest=none" in messages[1]
    assert "PRIVATE_ENVELOPE" not in messages[1]


def test_runtime_trace_exposes_only_bounded_provider_token_accounting() -> None:
    messages: list[str] = []

    runtime_trace(
        messages.append,
        "backend",
        SimpleNamespace(
            event="azure_containerapp_response_accepted",
            input_tokens=101,
            output_tokens=17,
            total_tokens=118,
        ),
    )
    runtime_trace(
        messages.append,
        "backend",
        SimpleNamespace(
            event="azure_containerapp_response_accepted",
            input_tokens=-1,
            output_tokens="PRIVATE_TOKEN_DETAIL",
            total_tokens=True,
        ),
    )

    assert "input_tokens=101 output_tokens=17 total_tokens=118" in messages[0]
    assert "input_tokens=0 output_tokens=0 total_tokens=0" in messages[1]
    assert "PRIVATE_TOKEN_DETAIL" not in messages[1]
