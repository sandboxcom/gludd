"""Focused contracts for the extracted Azure readiness reader boundary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

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
from general_ludd.self_improve.model_candidates import BackendInfrastructureError


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


def test_terminal_revision_emits_app_and_environment_event_poll_evidence() -> None:
    """A terminal startup visibly reports both bounded diagnostic sources."""
    policy = cast(
        AzureContainerAppLiveProofPolicy,
        SimpleNamespace(min_replicas=1, app_name="gludd-vllm-managed-abc123"),
    )
    progress: list[str] = []
    app_transport = SimpleNamespace(
        get_active_revision_json=lambda _token: {
            "name": f"{policy.app_name}--0000007",
            "properties": {
                "active": True,
                "replicas": 1,
                "healthState": "Unhealthy",
                "provisioningState": "Failed",
                "runningState": "Failed",
                "reasonClasses": ["image_initializing"],
            },
        },
        get_system_event_reason_classes=lambda _token, _revision_name: {
            "reasonClasses": [],
            "eventCount": 0,
            "scopedEventCount": 0,
            "classifiedEventCount": 0,
            "errorEventCount": 0,
            "warningEventCount": 0,
            "unclassifiedErrorCount": 0,
        },
    )
    lifecycle_transport = SimpleNamespace(
        get_environment_system_event_reason_classes=lambda _token, _revision_name: {
            "reasonClasses": ["container_crash"],
            "eventCount": 3,
            "scopedEventCount": 1,
            "classifiedEventCount": 1,
            "errorEventCount": 1,
            "warningEventCount": 0,
            "unclassifiedErrorCount": 0,
        }
    )
    reader = AzureContainerAppRuntimeReaders(
        credential=SimpleNamespace(),
        environment_transport=object(),
        lifecycle_transport=lifecycle_transport,
        app_transport=app_transport,
        policy=policy,
        progress_sink=progress.append,
    )

    with pytest.raises(BackendInfrastructureError):
        reader._revision_readiness(
            "bounded-token",
            {"properties": {}},
            policy,
            replica_diagnostics_available=False,
        )

    assert progress[:2] == [
        "azure_containerapp_system_event_poll phase=readiness source=app "
        "state=available attempt=1 event_count=0 scoped_event_count=0 "
        "classified_event_count=0 error_event_count=0 warning_event_count=0 "
        "unclassified_error_count=0 reason_classes=none",
        "azure_containerapp_system_event_poll phase=readiness source=environment "
        "state=available attempt=1 event_count=3 scoped_event_count=1 "
        "classified_event_count=1 error_event_count=1 warning_event_count=0 "
        "unclassified_error_count=0 reason_classes=container_crash",
    ]
    assert "reason=container_crash" in progress[-1]


def test_terminal_revision_reads_replica_diagnostics_before_failure() -> None:
    """Terminal revision state must not hide its bounded replica evidence."""
    policy = cast(
        AzureContainerAppLiveProofPolicy,
        SimpleNamespace(min_replicas=1, app_name="gludd-vllm-managed-abc123"),
    )
    revision_name = f"{policy.app_name}--0000007"
    progress: list[str] = []
    replica_reads: list[tuple[str, str]] = []

    def replica_status(token: str, observed_revision: str) -> object:
        replica_reads.append((token, observed_revision))
        return {
            "replicaCount": 1,
            "readyContainerCount": 0,
            "startedContainerCount": 0,
            "restartCount": 0,
            "replicaRunningStates": ["NotRunning"],
            "containerRunningStates": [],
            "reasonClasses": ["capacity_exhausted"],
        }

    app_transport = SimpleNamespace(
        get_active_revision_json=lambda _token: {
            "name": revision_name,
            "properties": {
                "active": True,
                "replicas": 1,
                "healthState": "Unhealthy",
                "provisioningState": "Failed",
                "runningState": "Failed",
                "reasonClasses": [],
            },
        },
        get_replica_status_json=replica_status,
    )
    reader = AzureContainerAppRuntimeReaders(
        credential=SimpleNamespace(),
        environment_transport=object(),
        lifecycle_transport=object(),
        app_transport=app_transport,
        policy=policy,
        progress_sink=progress.append,
    )

    with pytest.raises(BackendInfrastructureError):
        reader._revision_readiness(
            "bounded-token",
            {"properties": {}},
            policy,
            replica_diagnostics_available=True,
        )

    assert replica_reads == [("bounded-token", revision_name)]
    assert progress[0] == (
        "azure_containerapp_replica_poll phase=readiness state=terminal "
        "replicas=1 ready_containers=0 started_containers=0 restarts=0 "
        "replica_state=NotRunning container_state=None reason=capacity_exhausted"
    )
    assert "reason=capacity_exhausted" in progress[-1]


def test_terminal_event_reader_waits_for_late_exact_revision_error() -> None:
    """A bounded visible retry captures a terminal event published after state."""
    app_name = "gludd-vllm-managed-abc123"
    revision_name = f"{app_name}--0000007"
    attempts: list[tuple[str, str]] = []
    sleeps: list[float] = []
    progress: list[str] = []

    def app_events(_token: str, observed_revision: str) -> object:
        attempts.append(("app", observed_revision))
        ready = len(attempts) >= 3
        return {
            "reasonClasses": ["startup_timeout"] if ready else [],
            "eventCount": 1 if ready else 0,
            "scopedEventCount": 1 if ready else 0,
            "classifiedEventCount": 1 if ready else 0,
            "errorEventCount": 1 if ready else 0,
            "warningEventCount": 0,
            "unclassifiedErrorCount": 0,
        }

    def environment_events(_token: str, observed_revision: str) -> object:
        attempts.append(("environment", observed_revision))
        return {
            "reasonClasses": [],
            "eventCount": 0,
            "scopedEventCount": 0,
            "classifiedEventCount": 0,
            "errorEventCount": 0,
            "warningEventCount": 0,
            "unclassifiedErrorCount": 0,
        }

    reader = AzureContainerAppRuntimeReaders(
        credential=SimpleNamespace(),
        environment_transport=object(),
        lifecycle_transport=SimpleNamespace(
            get_environment_system_event_reason_classes=environment_events
        ),
        app_transport=SimpleNamespace(
            get_system_event_reason_classes=app_events,
        ),
        policy=cast(
            AzureContainerAppLiveProofPolicy,
            SimpleNamespace(min_replicas=1, app_name=app_name),
        ),
        progress_sink=progress.append,
        sleep=sleeps.append,
    )

    assert reader._terminal_event_reasons("bounded-token", revision_name) == (
        "startup_timeout",
    )
    assert attempts == [
        ("app", revision_name),
        ("environment", revision_name),
        ("app", revision_name),
        ("environment", revision_name),
    ]
    assert sleeps == [5.0]
    assert any("system_event_wait" in message for message in progress)
