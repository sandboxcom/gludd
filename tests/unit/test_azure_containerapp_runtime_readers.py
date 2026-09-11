"""Focused contracts for the extracted Azure readiness reader boundary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.infra.azure_containerapp_runtime_readers import (
    AzureContainerAppRuntimeReaders,
)
from general_ludd.infra.azure_containerapp_runtime_state import (
    _observed_revision_name,
    _safe_status_values,
)


def test_reader_returns_immediate_absence_from_the_bound_app_transport() -> None:
    """The extracted reader owns the credential-to-app read boundary."""
    policy = cast(
        AzureContainerAppLiveProofPolicy,
        SimpleNamespace(min_replicas=1, app_name="gludd-vllm-managed-abc123"),
    )
    credential = SimpleNamespace(
        get_token=lambda *_scopes: SimpleNamespace(token="unit-token")
    )
    app_transport = SimpleNamespace(get_json=lambda token: None)
    reader = AzureContainerAppRuntimeReaders(
        credential=credential,
        environment_transport=object(),
        lifecycle_transport=object(),
        app_transport=app_transport,
        policy=policy,
    )

    assert reader.read_app(policy, expect_absent=True) is None


def test_observed_revision_name_accepts_only_the_bound_app_namespace() -> None:
    """Provider-controlled names cannot redirect a readiness lookup."""
    app_name = "gludd-vllm-managed-abc123"

    assert (
        _observed_revision_name(
            {"name": f"{app_name}--0000007"},
            app_name,
        )
        == f"{app_name}--0000007"
    )
    assert _observed_revision_name({"name": "other--0000007"}, app_name) is None
    assert _observed_revision_name({"name": f"{app_name}--UPPER"}, app_name) is None
    assert _observed_revision_name({"name": f"{app_name}--"}, app_name) is None


def test_safe_status_values_are_bounded_filtered_and_deterministic() -> None:
    """Only fixed vocabulary may cross the provider observability boundary."""
    allowed = frozenset({"Running", "Waiting"})

    assert _safe_status_values(
        ["Waiting", "private-provider-text", "Running", "Running"],
        allowed,
    ) == ("Running", "Waiting")
    assert _safe_status_values("Running", allowed) == ()
    assert _safe_status_values(["Running"] * 33, allowed) == ()
