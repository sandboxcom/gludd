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
