"""Validation and hysteresis branch coverage for Azure deploy strategy."""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from general_ludd.infra.deploy_strategy import (
    DeployStrategist,
    DeployUrgency,
    ElasticTierController,
    ElasticWorkload,
    ResourceTier,
)


def _workload(
    *,
    score_items: int,
    spot: bool,
    urgency: DeployUrgency = DeployUrgency.NORMAL,
    latency: float = 900.0,
) -> ElasticWorkload:
    """Build a workload whose queue count controls its demand score."""
    return ElasticWorkload(
        urgency=urgency,
        queued_items=score_items,
        concurrent_items=0,
        estimated_runtime_minutes=1.0,
        latency_budget_seconds=latency,
        spot_eligible=spot,
    )


def _spot_controller() -> ElasticTierController:
    """Return a controller advanced through its public API to the spot tier."""
    controller = ElasticTierController()
    decision = controller.select(_workload(score_items=3, spot=True))
    assert decision.tier is ResourceTier.SPOT_VM
    return controller


def test_resource_tier_representation_and_controller_state_are_observable() -> None:
    """Tier identity and current hysteresis state have stable diagnostics."""
    controller = ElasticTierController()

    assert ResourceTier.CONTAINER_APP.deploy_type == "containerapp"
    assert "startup=600s" in repr(ResourceTier.CONTAINER_APP)
    assert controller.current_tier is ResourceTier.CONTAINER_APP


@pytest.mark.parametrize(
    ("field", "factory"),
    [
        (
            "queued_items",
            lambda: ElasticWorkload(DeployUrgency.NORMAL, True, 0, 30.0, 900.0, True),
        ),
        (
            "queued_items",
            lambda: ElasticWorkload(DeployUrgency.NORMAL, -1, 0, 30.0, 900.0, True),
        ),
        (
            "estimated_runtime_minutes",
            lambda: ElasticWorkload(DeployUrgency.NORMAL, 0, 0, math.inf, 900.0, True),
        ),
        (
            "latency_budget_seconds",
            lambda: ElasticWorkload(DeployUrgency.NORMAL, 0, 0, 30.0, 0.0, True),
        ),
    ],
)
def test_elastic_workload_rejects_nonphysical_inputs(
    field: str,
    factory: Callable[[], ElasticWorkload],
) -> None:
    """Counts and elapsed-time inputs reject booleans, negatives, and non-finite values."""
    with pytest.raises(ValueError, match=field):
        factory()


def test_dedicated_tier_scales_down_to_spot_only_when_eligible() -> None:
    """Dedicated capacity retains or releases according to the spot eligibility boundary."""
    eligible = ElasticTierController()
    eligible.select(_workload(score_items=10, spot=False))
    assert eligible.select(_workload(score_items=3, spot=True)).tier is ResourceTier.SPOT_VM

    ineligible = ElasticTierController()
    ineligible.select(_workload(score_items=10, spot=False))
    assert ineligible.select(_workload(score_items=3, spot=False)).tier is ResourceTier.DEDICATED_VM


def test_spot_tier_covers_hold_scale_up_and_scale_down_boundaries() -> None:
    """Spot capacity responds deterministically to each hysteresis branch."""
    assert _spot_controller().select(_workload(score_items=2, spot=True)).tier is ResourceTier.SPOT_VM
    assert _spot_controller().select(_workload(score_items=3, spot=False)).tier is ResourceTier.DEDICATED_VM
    assert _spot_controller().select(_workload(score_items=0, spot=False)).tier is ResourceTier.CONTAINER_APP
    assert _spot_controller().select(_workload(score_items=10, spot=True)).tier is ResourceTier.DEDICATED_VM


@pytest.mark.parametrize(
    ("action", "message"),
    [
        (
            lambda: DeployStrategist().plan(
                DeployUrgency.NORMAL,
                "t4",
                "model",
                estimated_runtime_minutes=True,
            ),
            "estimated_runtime_minutes",
        ),
        (
            lambda: DeployStrategist().plan(
                DeployUrgency.NORMAL,
                "t4",
                "model",
                max_cost_usd=math.inf,
            ),
            "max_cost_usd",
        ),
        (
            lambda: DeployStrategist().plan(
                DeployUrgency.NORMAL,
                "t4",
                "model",
                vm_shutdown_seconds=-1.0,
            ),
            "vm_shutdown_seconds",
        ),
    ],
)
def test_deploy_plan_rejects_invalid_budget_and_time_inputs(
    action: Callable[[], object],
    message: str,
) -> None:
    """Invalid planning inputs fail before any pricing or infrastructure call."""
    with pytest.raises(ValueError, match=message):
        action()


def test_deploy_plan_rejects_workload_with_different_urgency() -> None:
    """A caller cannot reuse demand evidence for a different urgency policy."""
    workload = _workload(score_items=0, spot=False, urgency=DeployUrgency.BACKGROUND)

    with pytest.raises(ValueError, match="workload urgency"):
        DeployStrategist().plan(
            DeployUrgency.NORMAL,
            "t4",
            "model",
            workload=workload,
        )
