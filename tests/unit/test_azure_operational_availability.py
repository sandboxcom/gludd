"""Versioned, content-free Azure operational-availability learning tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.self_improve.azure_infrastructure_evidence import (
    AzureInfrastructurePhase,
)
from general_ludd.self_improve.azure_operational_availability import (
    AzureAvailabilityTerminal,
    AzureOperationalEvidenceError,
    build_azure_availability_scope,
    load_azure_availability_index,
    record_azure_availability_terminal,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

IMAGE = "registry.example/vllm@sha256:" + ("9" * 64)
DEPLOYMENT = "a" * 64


def _store(tmp_path: Path) -> CapabilityEvidenceStore:
    return CapabilityEvidenceStore(str(tmp_path / "evidence.json"))


def _requirement(*, parameters: int = 3_000_000_000) -> ModelServingRequirement:
    return ModelServingRequirement(
        model_id="vendor/private-name-must-not-persist",
        revision="b" * 40,
        parameter_count=parameters,
        weight_bits=16,
        kv_cache_mib=2_048,
        runtime_overhead_mib=3_072,
    )


def _scope(*, location: str = "westus3", sku: str = "provider/accelerator-small"):
    return build_azure_availability_scope(
        location=location,
        resource_sku=sku,
        container_image=IMAGE,
        requirement=_requirement(),
    )


def _record(
    store: CapabilityEvidenceStore,
    *,
    observed_at: float,
    outcome: AzureAvailabilityTerminal,
    scope=None,
) -> None:
    record_azure_availability_terminal(
        store,
        scope=_scope() if scope is None else scope,
        deployment_identity_digest=DEPLOYMENT,
        phase=AzureInfrastructurePhase.CANDIDATE_STARTUP,
        outcome=outcome,
        clock=lambda: observed_at,
    )


def test_scope_binds_region_sku_runtime_and_content_free_topology() -> None:
    baseline = _scope()

    assert baseline.scope_version == 1
    assert baseline.location == "westus3"
    assert baseline.resource_sku == "provider/accelerator-small"
    assert baseline.runtime_version_digest == "sha256:" + ("9" * 64)
    assert len(baseline.topology_digest) == 64
    assert len(baseline.scope_digest) == 64
    assert baseline != _scope(location="eastus")
    assert baseline != _scope(sku="provider/accelerator-large")
    assert baseline != build_azure_availability_scope(
        location="westus3",
        resource_sku="provider/accelerator-small",
        container_image=IMAGE,
        requirement=_requirement(parameters=7_000_000_000),
    )
    assert "private-name" not in json.dumps(baseline.payload())


def test_recent_terminals_score_exact_scope_and_success_heals_failure_streak(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _record(store, observed_at=1_000.0, outcome=AzureAvailabilityTerminal.UNAVAILABLE)
    _record(store, observed_at=1_001.0, outcome=AzureAvailabilityTerminal.TIMEOUT)
    _record(store, observed_at=1_002.0, outcome=AzureAvailabilityTerminal.AVAILABLE)

    index = load_azure_availability_index(
        store,
        max_age_seconds=300,
        minimum_failures=2,
        now_epoch=1_003.0,
    )
    assessment = index.assess(_scope())

    assert assessment.observed_outcomes == 3
    assert assessment.successful_outcomes == 1
    assert assessment.failed_outcomes == 2
    assert assessment.consecutive_failures == 0
    assert assessment.feasible is True
    assert assessment.availability_score == pytest.approx(0.4)
    assert index.assess(_scope(location="eastus")).observed_outcomes == 0
    assert index.assess(_scope(location="eastus")).availability_score == 0.5


def test_repeated_failures_make_only_exact_versioned_scope_infeasible(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _record(store, observed_at=1_000.0, outcome=AzureAvailabilityTerminal.UNAVAILABLE)
    _record(store, observed_at=1_001.0, outcome=AzureAvailabilityTerminal.RATE_LIMITED)

    index = load_azure_availability_index(
        store,
        max_age_seconds=300,
        minimum_failures=2,
        now_epoch=1_002.0,
    )

    assert index.assess(_scope()).feasible is False
    assert index.assess(_scope(sku="provider/accelerator-large")).feasible is True


def test_stale_terminals_expire_without_mutating_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _record(store, observed_at=100.0, outcome=AzureAvailabilityTerminal.UNAVAILABLE)
    before = store.list_all()

    index = load_azure_availability_index(
        store,
        max_age_seconds=60,
        minimum_failures=1,
        now_epoch=1_000.0,
    )

    assert index.assess(_scope()).observed_outcomes == 0
    assert store.list_all() == before


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda record: record.update(schema_version=99), "schema"),
        (lambda record: record.update(scope_version=99), "scope"),
        (lambda record: record.update(evidence_digest="f" * 64), "digest"),
        (lambda record: record.update(resource_sku="drifted sku"), "resource_sku"),
        (lambda record: record.update(registered_at=1_001.0), "future"),
    ],
)
def test_malformed_or_drifted_collection_fails_closed(
    tmp_path: Path,
    mutate,
    match: str,
) -> None:
    store = _store(tmp_path)
    _record(store, observed_at=1_000.0, outcome=AzureAvailabilityTerminal.AVAILABLE)
    record = store.list_all()[0]
    mutate(record)
    store.register_evidence(record)

    with pytest.raises(AzureOperationalEvidenceError, match=match):
        load_azure_availability_index(
            store,
            max_age_seconds=300,
            minimum_failures=2,
            now_epoch=1_000.0,
        )


def test_record_and_trace_exclude_model_repository_and_provider_text(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    traces: list[dict[str, object]] = []

    record_azure_availability_terminal(
        store,
        scope=_scope(),
        deployment_identity_digest=DEPLOYMENT,
        phase=AzureInfrastructurePhase.PREFLIGHT,
        outcome=AzureAvailabilityTerminal.UNAVAILABLE,
        clock=lambda: 1_000.0,
        trace_sink=traces.append,
    )

    serialized = json.dumps(store.list_all()) + json.dumps(traces)
    assert "private-name-must-not-persist" not in serialized
    assert "registry.example" not in serialized
    assert "provider message" not in serialized
    assert store.list_all()[0]["collection"] == "infra.azure_operational_availability"
    assert traces[0]["event"] == "GLUDD_AZURE_AVAILABILITY_RECORDED"
    assert traces[0]["scope_version"] == 1


@pytest.mark.parametrize(
    "outcome",
    [
        "available",
        "authentication",
        "authorization",
        "transport",
        "invalid_response",
    ],
)
def test_untyped_or_nonplacement_terminal_is_rejected_before_mutation(
    tmp_path: Path,
    outcome: str,
) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="outcome"):
        record_azure_availability_terminal(
            store,
            scope=_scope(),
            deployment_identity_digest=DEPLOYMENT,
            phase=AzureInfrastructurePhase.CANDIDATE_STARTUP,
            outcome=outcome,  # type: ignore[arg-type]
        )

    assert store.list_all() == []


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"location": "West US"}, "location"),
        ({"resource_sku": "bad sku"}, "resource_sku"),
        ({"runtime_version_digest": "latest"}, "runtime_version_digest"),
        ({"topology_digest": "short"}, "topology_digest"),
        ({"scope_version": 2}, "scope_version"),
        ({"scope_version": True}, "scope_version"),
    ],
)
def test_scope_rejects_malformed_or_drifted_coordinates(
    overrides: dict[str, object],
    match: str,
) -> None:
    arguments: dict[str, object] = {
        "location": "westus3",
        "resource_sku": "provider/accelerator-small",
        "runtime_version_digest": "sha256:" + ("9" * 64),
        "topology_digest": "c" * 64,
        "scope_version": 1,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=match):
        type(_scope())(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize("container_image", [7, "mutable:latest"])
def test_scope_builder_rejects_mutable_or_untyped_runtime(
    container_image: object,
) -> None:
    with pytest.raises(ValueError, match="container_image"):
        build_azure_availability_scope(
            location="westus3",
            resource_sku="provider/accelerator-small",
            container_image=container_image,  # type: ignore[arg-type]
            requirement=_requirement(),
        )

    with pytest.raises(ValueError, match="requirement"):
        build_azure_availability_scope(
            location="westus3",
            resource_sku="provider/accelerator-small",
            container_image=IMAGE,
            requirement=object(),  # type: ignore[arg-type]
        )


def test_index_and_recorder_reject_wrong_contract_types(tmp_path: Path) -> None:
    store = _store(tmp_path)
    index = load_azure_availability_index(
        store,
        max_age_seconds=60,
        minimum_failures=1,
        now_epoch=1_000.0,
    )
    with pytest.raises(ValueError, match="scope"):
        index.assess(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="store"):
        record_azure_availability_terminal(
            object(),  # type: ignore[arg-type]
            scope=_scope(),
            deployment_identity_digest=DEPLOYMENT,
            phase=AzureInfrastructurePhase.PREFLIGHT,
            outcome=AzureAvailabilityTerminal.AVAILABLE,
        )
    with pytest.raises(ValueError, match="scope"):
        record_azure_availability_terminal(
            store,
            scope=object(),  # type: ignore[arg-type]
            deployment_identity_digest=DEPLOYMENT,
            phase=AzureInfrastructurePhase.PREFLIGHT,
            outcome=AzureAvailabilityTerminal.AVAILABLE,
        )


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"deployment_identity_digest": "short"}, "deployment_identity_digest"),
        ({"phase": "request"}, "phase"),
        ({"clock": object()}, "clock"),
        ({"clock": lambda: float("nan")}, "clock"),
        (
            {"clock": lambda: (_ for _ in ()).throw(RuntimeError("secret"))},
            "clock",
        ),
        ({"trace_sink": object()}, "trace_sink"),
    ],
)
def test_recorder_rejects_invalid_boundaries_before_mutation(
    tmp_path: Path,
    overrides: dict[str, object],
    match: str,
) -> None:
    store = _store(tmp_path)
    arguments: dict[str, object] = {
        "scope": _scope(),
        "deployment_identity_digest": DEPLOYMENT,
        "phase": AzureInfrastructurePhase.PREFLIGHT,
        "outcome": AzureAvailabilityTerminal.AVAILABLE,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=match):
        record_azure_availability_terminal(store, **arguments)  # type: ignore[arg-type]

    assert store.list_all() == []


def test_trace_failure_is_censored_after_durable_terminal(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(RuntimeError, match="trace publication"):
        record_azure_availability_terminal(
            store,
            scope=_scope(),
            deployment_identity_digest=DEPLOYMENT,
            phase=AzureInfrastructurePhase.PREFLIGHT,
            outcome=AzureAvailabilityTerminal.AVAILABLE,
            clock=lambda: 1_000.0,
            trace_sink=lambda _event: (_ for _ in ()).throw(
                RuntimeError("private sink detail")
            ),
        )

    assert len(store.list_all()) == 1


@pytest.mark.parametrize(
    ("max_age_seconds", "minimum_failures", "now_epoch"),
    [
        (0, 1, 1_000.0),
        (86_401, 1, 1_000.0),
        (True, 1, 1_000.0),
        (60, 0, 1_000.0),
        (60, 101, 1_000.0),
        (60, True, 1_000.0),
        (60, 1, float("nan")),
    ],
)
def test_loader_rejects_unbounded_policy_or_clock(
    tmp_path: Path,
    max_age_seconds: object,
    minimum_failures: object,
    now_epoch: object,
) -> None:
    with pytest.raises(ValueError):
        load_azure_availability_index(
            _store(tmp_path),
            max_age_seconds=max_age_seconds,  # type: ignore[arg-type]
            minimum_failures=minimum_failures,  # type: ignore[arg-type]
            now_epoch=now_epoch,  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="store"):
        load_azure_availability_index(
            object(),  # type: ignore[arg-type]
            max_age_seconds=60,
            minimum_failures=1,
        )


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda record: record.update(registered_at=float("nan")), "registered_at"),
        (lambda record: record.update(scope_digest="e" * 64), "scope digest"),
        (lambda record: record.update(deployment_identity_digest="short"), "deployment"),
        (lambda record: record.update(phase="request"), "phase"),
        (lambda record: record.update(outcome="transport"), "transport"),
        (lambda record: record.update(extra=True), "schema"),
    ],
)
def test_owned_collection_rejects_every_invalid_record_shape(
    tmp_path: Path,
    mutate,
    match: str,
) -> None:
    store = _store(tmp_path)
    _record(store, observed_at=1_000.0, outcome=AzureAvailabilityTerminal.AVAILABLE)
    record = store.list_all()[0]
    mutate(record)
    store.register_evidence(record)

    with pytest.raises(AzureOperationalEvidenceError, match=match):
        load_azure_availability_index(
            store,
            max_age_seconds=60,
            minimum_failures=1,
            now_epoch=1_000.0,
        )


def test_foreign_collection_is_ignored(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_evidence(
        {
            "collection": "model.quality",
            "provider_private_text": "must remain foreign",
            "registered_at": 1_000.0,
        }
    )

    index = load_azure_availability_index(
        store,
        max_age_seconds=60,
        minimum_failures=1,
        now_epoch=1_000.0,
    )

    assert index.observed_scope_count == 0


def test_digest_valid_legacy_collection_remains_readable(tmp_path: Path) -> None:
    current = CapabilityEvidenceStore(str(tmp_path / "current.json"))
    _record(
        current,
        observed_at=1_000.0,
        outcome=AzureAvailabilityTerminal.AVAILABLE,
    )
    record = current.list_all()[0]
    record["collection"] = "self_improve.azure_operational_availability"
    payload = {key: value for key, value in record.items() if key != "evidence_digest"}
    record["evidence_digest"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
    legacy = CapabilityEvidenceStore(str(tmp_path / "legacy.json"))
    legacy.register_evidence(record)

    index = load_azure_availability_index(
        legacy,
        max_age_seconds=60,
        minimum_failures=1,
        now_epoch=1_000.0,
    )

    assert index.assess(_scope()).observed_outcomes == 1
    assert index.assess(_scope()).successful_outcomes == 1
