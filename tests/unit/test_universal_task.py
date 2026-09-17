"""Unit contract for the provider-neutral universal task core."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from general_ludd.execution.universal_task import ExecutionTarget, UniversalTaskRequest


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


def _target(
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
