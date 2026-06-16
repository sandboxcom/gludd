"""Unit tests for the saturation controller.

Deterministic, no sleeps, no live state.  Every assertion is computed purely
from inputs passed into the controller.
"""

from __future__ import annotations

import math

import pytest

from general_ludd.controllers.saturation import (
    SaturationController,
    SourceCapacity,
)
from general_ludd.scheduling.scheduler import WorkItem


def _backlog(n: int) -> list[WorkItem]:
    """Ordered backlog of n items with ids item-0 .. item-(n-1)."""
    return [WorkItem(id=f"item-{i}") for i in range(n)]


@pytest.fixture
def controller() -> SaturationController:
    return SaturationController()


# --------------------------------------------------------------------------- #
# Backfill: exact-to-target                                                    #
# --------------------------------------------------------------------------- #


def test_backfills_exactly_to_target(controller: SaturationController) -> None:
    # target 5, 2 running => exactly 3 backfilled.
    plan = controller.plan_backfill(target=5, running=2, backlog=_backlog(10))
    assert len(plan) == 3
    assert [w.id for w in plan] == ["item-0", "item-1", "item-2"]


def test_zero_idle_when_backlog_available(
    controller: SaturationController,
) -> None:
    # No slots running at all => fill every one of the 4 slots.
    plan = controller.plan_backfill(target=4, running=0, backlog=_backlog(8))
    assert len(plan) == 4


# --------------------------------------------------------------------------- #
# Backfill: pull 0 cases                                                       #
# --------------------------------------------------------------------------- #


def test_pulls_zero_when_running_equals_target(
    controller: SaturationController,
) -> None:
    plan = controller.plan_backfill(target=4, running=4, backlog=_backlog(10))
    assert plan == []


def test_pulls_zero_when_running_exceeds_target(
    controller: SaturationController,
) -> None:
    # Over-subscribed (e.g. target lowered mid-flight): never go negative.
    plan = controller.plan_backfill(target=3, running=7, backlog=_backlog(10))
    assert plan == []


def test_empty_backlog_is_noop(controller: SaturationController) -> None:
    plan = controller.plan_backfill(target=5, running=0, backlog=[])
    assert plan == []


def test_zero_target_pulls_nothing(controller: SaturationController) -> None:
    plan = controller.plan_backfill(target=0, running=0, backlog=_backlog(5))
    assert plan == []


def test_backfill_bounded_by_backlog_length(
    controller: SaturationController,
) -> None:
    # 5 free slots but only 2 items available => pull 2, not 5.
    plan = controller.plan_backfill(target=5, running=0, backlog=_backlog(2))
    assert len(plan) == 2


# --------------------------------------------------------------------------- #
# Backfill: ordering                                                           #
# --------------------------------------------------------------------------- #


def test_drains_backlog_in_order(controller: SaturationController) -> None:
    backlog = _backlog(10)
    plan = controller.plan_backfill(target=4, running=1, backlog=backlog)
    # Drained from the FRONT, preserving caller ordering.
    assert [w.id for w in plan] == ["item-0", "item-1", "item-2"]


def test_backlog_not_mutated(controller: SaturationController) -> None:
    backlog = _backlog(6)
    snapshot = list(backlog)
    controller.plan_backfill(target=3, running=0, backlog=backlog)
    assert backlog == snapshot


# --------------------------------------------------------------------------- #
# Per-source capacity                                                          #
# --------------------------------------------------------------------------- #


def test_never_exceeds_single_source_cap(
    controller: SaturationController,
) -> None:
    # target wants 10 but the one source can only hold 3.
    caps = [SourceCapacity(source_id="gpu0", capacity=3, running=0)]
    plan = controller.plan_backfill(
        target=10, running=0, backlog=_backlog(10), per_source_caps=caps
    )
    assert len(plan) == 3


def test_respects_per_source_running_headroom(
    controller: SaturationController,
) -> None:
    # gpu0 cap 4 with 3 already running => only 1 headroom.
    caps = [SourceCapacity(source_id="gpu0", capacity=4, running=3)]
    plan = controller.plan_backfill(
        target=10, running=3, backlog=_backlog(10), per_source_caps=caps
    )
    assert len(plan) == 1


def test_multiple_sources_with_differing_caps(
    controller: SaturationController,
) -> None:
    # gpu0 headroom 2, gpu1 headroom 3 => total 5 backfilled.
    caps = [
        SourceCapacity(source_id="gpu0", capacity=2, running=0),
        SourceCapacity(source_id="gpu1", capacity=5, running=2),
    ]
    assignment = controller.plan_backfill_by_source(
        target=20, running=2, backlog=_backlog(10), per_source_caps=caps
    )
    assert len(assignment.items) == 5
    # Each source got no more than its headroom.
    assert len(assignment.by_source["gpu0"]) == 2
    assert len(assignment.by_source["gpu1"]) == 3
    # And no item is assigned to two sources.
    all_assigned = [
        w.id for items in assignment.by_source.values() for w in items
    ]
    assert len(all_assigned) == len(set(all_assigned))


def test_per_source_total_caps_global_target(
    controller: SaturationController,
) -> None:
    # global headroom (target-running)=2 is SMALLER than source headroom (6).
    # The tighter bound (global) wins.
    caps = [
        SourceCapacity(source_id="gpu0", capacity=3, running=0),
        SourceCapacity(source_id="gpu1", capacity=3, running=0),
    ]
    plan = controller.plan_backfill(
        target=5, running=3, backlog=_backlog(10), per_source_caps=caps
    )
    assert len(plan) == 2


def test_all_sources_full_pulls_nothing(
    controller: SaturationController,
) -> None:
    caps = [
        SourceCapacity(source_id="gpu0", capacity=2, running=2),
        SourceCapacity(source_id="gpu1", capacity=3, running=3),
    ]
    plan = controller.plan_backfill(
        target=10, running=5, backlog=_backlog(10), per_source_caps=caps
    )
    assert plan == []


def test_source_capacity_headroom_property() -> None:
    assert SourceCapacity("g", capacity=4, running=1).headroom == 3
    # Over-subscribed source never reports negative headroom.
    assert SourceCapacity("g", capacity=4, running=9).headroom == 0
    assert SourceCapacity("g", capacity=0, running=0).headroom == 0


# --------------------------------------------------------------------------- #
# Utilization math                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("running", "target", "expected"),
    [
        (0, 4, 0.0),
        (1, 4, 0.25),
        (2, 4, 0.5),
        (4, 4, 1.0),
        (6, 4, 1.0),  # over-subscribed clamps to 1.0
        (3, 0, 0.0),  # zero target => 0.0, no div-by-zero
        (-1, 4, 0.0),  # negative running clamps to 0.0
    ],
)
def test_utilization_math(
    controller: SaturationController,
    running: int,
    target: int,
    expected: float,
) -> None:
    assert math.isclose(controller.utilization(running, target), expected)


def test_full_saturation_then_backfill_zero(
    controller: SaturationController,
) -> None:
    # When utilization is 1.0, there is nothing to backfill.
    target, running = 4, 4
    assert controller.utilization(running, target) == 1.0
    assert controller.plan_backfill(target, running, _backlog(5)) == []
