"""Durable, content-free Azure accelerator operational evidence tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from general_ludd.self_improve.azure_infrastructure_evidence import (
    AzureInfrastructurePhase,
    load_recent_unavailable_profiles,
    record_azure_infrastructure_failure,
)
from general_ludd.self_improve.model_candidates import BackendFailure
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

IMAGE = "registry.example/vllm@sha256:" + ("9" * 64)
LOCATION = "westus3"
PROFILE = "provider/accelerator-small"
DEPLOYMENT = "a" * 64


def _store(tmp_path: Path) -> CapabilityEvidenceStore:
    return CapabilityEvidenceStore(str(tmp_path / "evidence.json"))


def _record(
    store: CapabilityEvidenceStore,
    *,
    observed_at: float,
    failure: BackendFailure = BackendFailure.UNAVAILABLE,
    location: str = LOCATION,
    profile: str = PROFILE,
    image: str = IMAGE,
) -> None:
    record_azure_infrastructure_failure(
        store,
        location=location,
        workload_profile_type=profile,
        container_image=image,
        deployment_identity_digest=DEPLOYMENT,
        phase=AzureInfrastructurePhase.CANDIDATE_STARTUP,
        failure=failure,
        clock=lambda: observed_at,
    )


def test_repeated_recent_startup_failures_suppress_only_exact_profile_scope(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _record(store, observed_at=1_000.0)
    _record(store, observed_at=1_001.0)
    _record(
        store,
        observed_at=1_001.0,
        location="eastus",
        profile="provider/accelerator-other",
    )

    unavailable = load_recent_unavailable_profiles(
        store,
        location=LOCATION,
        container_image=IMAGE,
        max_age_seconds=300,
        minimum_failures=2,
        now_epoch=1_002.0,
    )

    assert unavailable == frozenset({PROFILE})


def test_stale_tampered_authentication_and_foreign_image_records_are_ignored(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _record(store, observed_at=100.0)
    _record(store, observed_at=995.0, failure=BackendFailure.AUTHENTICATION)
    _record(
        store,
        observed_at=996.0,
        image="registry.example/other@sha256:" + ("8" * 64),
    )
    raw = store.list_all()[0]
    raw["failure"] = BackendFailure.UNAVAILABLE.value
    raw["evidence_digest"] = "f" * 64
    raw["registered_at"] = 997.0
    store.register_evidence(raw)

    assert load_recent_unavailable_profiles(
        store,
        location=LOCATION,
        container_image=IMAGE,
        max_age_seconds=100,
        minimum_failures=1,
        now_epoch=1_000.0,
    ) == frozenset()


def test_record_and_trace_expose_only_bounded_operational_facts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    traces: list[dict[str, object]] = []

    count = record_azure_infrastructure_failure(
        store,
        location=LOCATION,
        workload_profile_type=PROFILE,
        container_image=IMAGE,
        deployment_identity_digest=DEPLOYMENT,
        phase=AzureInfrastructurePhase.CANDIDATE_STARTUP,
        failure=BackendFailure.TIMEOUT,
        clock=lambda: 1_000.0,
        trace_sink=traces.append,
    )

    assert count == 1
    records = store.list_all()
    assert len(records) == 1
    assert records[0]["collection"] == "self_improve.azure_infrastructure"
    assert records[0]["container_image_digest"] == "sha256:" + ("9" * 64)
    assert "registry.example" not in json.dumps(records)
    assert "model" not in json.dumps(records).casefold()
    assert traces == [
        {
            "deployment_identity_digest": DEPLOYMENT,
            "event": "SELF_IMPROVE_AZURE_INFRASTRUCTURE_RECORDED",
            "failure": "timeout",
            "location": LOCATION,
            "phase": "candidate_startup",
            "schema_version": 1,
            "workload_profile_type": PROFILE,
        }
    ]


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"location": "West US 3"}, "location"),
        ({"workload_profile_type": "bad profile"}, "workload_profile_type"),
        ({"container_image": "mutable:latest"}, "container_image"),
        ({"deployment_identity_digest": "short"}, "deployment_identity_digest"),
        ({"phase": "candidate_startup"}, "phase"),
        ({"failure": "provider-private"}, "failure"),
        ({"clock": lambda: float("nan")}, "clock"),
    ],
)
def test_invalid_observations_fail_before_store_mutation(
    tmp_path: Path,
    override: dict[str, object],
    match: str,
) -> None:
    store = _store(tmp_path)
    arguments: dict[str, object] = {
        "location": LOCATION,
        "workload_profile_type": PROFILE,
        "container_image": IMAGE,
        "deployment_identity_digest": DEPLOYMENT,
        "phase": AzureInfrastructurePhase.CANDIDATE_STARTUP,
        "failure": BackendFailure.UNAVAILABLE,
        "clock": lambda: 1_000.0,
    }
    arguments.update(override)

    with pytest.raises(ValueError, match=match):
        record_azure_infrastructure_failure(store, **arguments)  # type: ignore[arg-type]

    assert store.list_all() == []


@pytest.mark.parametrize(
    ("max_age_seconds", "minimum_failures"),
    [(0, 1), (86_401, 1), (60, 0), (60, 101), (True, 1)],
)
def test_loader_rejects_unbounded_policy_values(
    tmp_path: Path,
    max_age_seconds: object,
    minimum_failures: object,
) -> None:
    with pytest.raises(ValueError):
        load_recent_unavailable_profiles(
            _store(tmp_path),
            location=LOCATION,
            container_image=IMAGE,
            max_age_seconds=max_age_seconds,  # type: ignore[arg-type]
            minimum_failures=minimum_failures,  # type: ignore[arg-type]
            now_epoch=1_000.0,
        )
