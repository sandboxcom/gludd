"""Unit contract for the provider-neutral universal task core."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from general_ludd.execution.universal_task import (
    ExecutionTarget,
    ModelProfileOrigin,
    UniversalTaskRequest,
)


def test_execution_target_requires_auditable_routing_evidence() -> None:
    with pytest.raises(ValueError, match="health_evidence"):
        ExecutionTarget(
            profile_id="local",
            provider="local",
            accelerator_sku="cpu",
            capabilities=frozenset({"example"}),
            allowed_data_classifications=frozenset({"public"}),
            estimated_cost_usd=0.0,
            healthy=True,
            health_evidence="",
            capability_evidence="benchmark",
            cost_evidence="catalog",
            privacy_evidence="public-only",
            offline=True,
        )


def test_execution_target_rejects_an_unbound_profile_origin() -> None:
    """A target cannot erase provenance to bypass discovered-origin verification."""
    with pytest.raises(ValueError, match="profile_origin_required"):
        _unbound_target()


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: UniversalTaskRequest("", "example", "bounded work", 1.0),
            "task_id",
        ),
        (
            lambda: UniversalTaskRequest(
                "task-1", "example", "bounded work", float("nan")
            ),
            "budget_usd",
        ),
    ],
)
def test_universal_request_rejects_missing_identity_or_invalid_budget(
    factory: Callable[[], object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: _target(capabilities=frozenset()),
            "capabilities",
        ),
        (
            lambda: _target(allowed_data_classifications=frozenset()),
            "allowed_data_classifications",
        ),
        (
            lambda: _target(estimated_cost_usd=-1.0),
            "estimated_cost_usd",
        ),
    ],
)
def test_execution_target_rejects_missing_scope_or_invalid_cost(
    factory: Callable[[], object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def test_execution_target_binds_profile_origin_without_conflating_provider() -> None:
    """Discovery provenance and the native invocation provider are distinct facts."""
    target = ExecutionTarget.bind_origin(
        profile_id="freellmapi-groq-0123456789abcdef0123",
        provider="groq",
        accelerator_sku="groq-managed",
        capabilities=frozenset({"example"}),
        allowed_data_classifications=frozenset({"public"}),
        estimated_cost_usd=0.0,
        healthy=True,
        health_evidence="heartbeat",
        capability_evidence="benchmark",
        cost_evidence="catalog",
        privacy_evidence="public-only",
        offline=False,
        origin_source="freellmapi",
        origin_protocol="gludd-freellmapi-probe-profile-v1",
        origin_evidence_sha256="a" * 64,
    )
    origin = target.profile_origin

    assert target.provider == "groq"
    assert target.profile_origin == origin
    assert origin is not None
    assert origin.receipt_sha256 != origin.evidence_sha256


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (("", "profile-v1", "a" * 64), "source"),
        (("catalog", "", "a" * 64), "protocol"),
        (("catalog", "profile-v1", "not-a-digest"), "evidence_sha256"),
    ],
)
def test_profile_origin_requires_complete_digest_bound_evidence(
    values: tuple[str, str, str],
    message: str,
) -> None:
    """An advisory discovery label alone is not auditable route provenance."""
    target = _target()

    with pytest.raises(ValueError, match=message):
        ModelProfileOrigin.bind(
            source=values[0],
            protocol=values[1],
            evidence_sha256=values[2],
            profile_id=target.profile_id,
            provider=target.provider,
            accelerator_sku=target.accelerator_sku,
            capabilities=target.capabilities,
            allowed_data_classifications=target.allowed_data_classifications,
            offline=target.offline,
            model_runner_id=target.model_runner_id,
        )


def test_profile_origin_receipt_rejects_forgery_and_cross_boundary_replay() -> None:
    """One trusted receipt cannot be rewritten or attached to another route scope."""
    target = _target()
    origin = target.profile_origin
    assert origin is not None

    with pytest.raises(ValueError, match="receipt_sha256"):
        replace(origin, receipt_sha256="b" * 64)

    for changes in (
        {"provider": "azure"},
        {"profile_id": "different-profile"},
        {"accelerator_sku": "different-sku"},
        {"capabilities": frozenset({"other"})},
        {"allowed_data_classifications": frozenset({"internal"})},
        {"offline": False},
        {"model_runner_id": "different-runner"},
    ):
        with pytest.raises(ValueError, match="profile_origin_target_mismatch"):
            replace(target, **changes)


def _target(
    *,
    capabilities: frozenset[str] = frozenset({"example"}),
    allowed_data_classifications: frozenset[str] = frozenset({"public"}),
    estimated_cost_usd: float = 0.0,
) -> ExecutionTarget:
    return ExecutionTarget.bind_origin(
        profile_id="local",
        provider="local",
        accelerator_sku="cpu",
        capabilities=capabilities,
        allowed_data_classifications=allowed_data_classifications,
        estimated_cost_usd=estimated_cost_usd,
        healthy=True,
        health_evidence="heartbeat",
        capability_evidence="benchmark",
        cost_evidence="catalog",
        privacy_evidence="public-only",
        offline=True,
        origin_source="operator-configured",
        origin_protocol="gludd-native-profile-v1",
        origin_evidence_sha256="f" * 64,
    )


def _unbound_target(
    *,
    capabilities: frozenset[str] = frozenset({"example"}),
    allowed_data_classifications: frozenset[str] = frozenset({"public"}),
    estimated_cost_usd: float = 0.0,
) -> ExecutionTarget:
    return ExecutionTarget(
        profile_id="local",
        provider="local",
        accelerator_sku="cpu",
        capabilities=capabilities,
        allowed_data_classifications=allowed_data_classifications,
        estimated_cost_usd=estimated_cost_usd,
        healthy=True,
        health_evidence="heartbeat",
        capability_evidence="benchmark",
        cost_evidence="catalog",
        privacy_evidence="public-only",
        offline=True,
    )
