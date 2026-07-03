"""Unit tests for the pure adaptive compaction-aggressiveness controller.

Every test is pure and in-memory: an :class:`AccuracySample` plus a current
rung index in, a next rung index out.  No IO, no clocks, no fixtures with
hidden state — mirroring the determinism of ``test_saturation``.
"""

from __future__ import annotations

from general_ludd.compaction.aggressive import LEVELS
from general_ludd.controllers.compaction_aggressiveness import (
    AccuracySample,
    CompactionAggressivenessController,
)

TOP = len(LEVELS) - 1


# --------------------------------------------------------------------------- #
# AccuracySample.rate
# --------------------------------------------------------------------------- #
def test_rate_none_when_no_observations() -> None:
    assert AccuracySample(passed=0, total=0).rate is None


def test_rate_is_ratio() -> None:
    assert AccuracySample(passed=18, total=20).rate == 0.9


def test_rate_clamped_for_malformed_sample() -> None:
    # passed > total can never produce a rate above 1.0.
    assert AccuracySample(passed=30, total=20).rate == 1.0
    # negative passed can never produce a rate below 0.0.
    assert AccuracySample(passed=-5, total=20).rate == 0.0


def test_rate_none_for_negative_total() -> None:
    # A malformed negative total is treated like "no observations" -> hold.
    assert AccuracySample(passed=0, total=-1).rate is None


# --------------------------------------------------------------------------- #
# next_level / compute — core policy
# --------------------------------------------------------------------------- #
def test_holds_accuracy_climbs_one_rung() -> None:
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    # rate = 1.0 >= floor, plenty of samples, room to climb -> +1.
    assert ctl.compute(0, AccuracySample(passed=20, total=20)) == 1


def test_rate_exactly_at_floor_climbs() -> None:
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    # rate == floor counts as "holding" -> climb.
    assert ctl.compute(0, AccuracySample(passed=18, total=20)) == 1


def test_sub_floor_backs_off_one_rung() -> None:
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    # rate = 0.5 < floor -> step back exactly one.
    assert ctl.compute(2, AccuracySample(passed=10, total=20)) == 1


def test_sub_floor_floors_at_zero() -> None:
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    # Already at rung 0 and failing -> cannot go negative.
    assert ctl.compute(0, AccuracySample(passed=1, total=20)) == 0


def test_too_few_samples_holds() -> None:
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    # Only 5 observations even at perfect accuracy -> noise guard holds.
    assert ctl.compute(1, AccuracySample(passed=5, total=5)) == 1


def test_min_samples_boundary_off_by_one() -> None:
    # Locks in the `<` semantics: total == min_samples - 1 HOLDS, but
    # total == min_samples MOVES. Guards against a `<` -> `<=` regression.
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    assert ctl.compute(0, AccuracySample(passed=19, total=19)) == 0  # below -> hold
    assert ctl.compute(0, AccuracySample(passed=20, total=20)) == 1  # at floor -> climb


def test_rate_none_holds() -> None:
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    # Zero observations -> insufficient data -> hold current rung.
    assert ctl.compute(2, AccuracySample(passed=0, total=0)) == 2


def test_at_max_level_with_holding_accuracy_stays() -> None:
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    # At the ceiling with holding accuracy -> no overflow, stays put.
    assert ctl.compute(TOP, AccuracySample(passed=20, total=20)) == TOP


def test_result_clamped_when_current_above_max() -> None:
    # A caller-supplied rung above max_level must clamp back into range.
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20, max_level=2)
    assert ctl.compute(9, AccuracySample(passed=20, total=20)) == 2


def test_above_max_on_backoff_branch_clamps() -> None:
    # Out-of-range current rung on the sub-floor (back-off) branch clamps too:
    # current-1 = 8, clamped down to max_level = 2.
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20, max_level=2)
    assert ctl.compute(9, AccuracySample(passed=5, total=20)) == 2


def test_negative_current_level_clamps_to_zero() -> None:
    # A bad persisted/negative rung clamps to 0 rather than propagating.
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    assert ctl.compute(-5, AccuracySample(passed=5, total=20)) == 0  # back-off floors at 0
    assert ctl.compute(-5, AccuracySample(passed=0, total=0)) == 0   # hold clamps too


def test_single_rung_ladder_holds_at_zero() -> None:
    # max_level == 0: there is nowhere to climb, so holding accuracy stays at 0.
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20, max_level=0)
    assert ctl.compute(0, AccuracySample(passed=20, total=20)) == 0


# --------------------------------------------------------------------------- #
# Trajectory: monotonic climb then a failure batch steps back exactly one.
# --------------------------------------------------------------------------- #
def test_climb_then_single_step_back_no_full_reset() -> None:
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    good = AccuracySample(passed=20, total=20)

    level = 0
    # Climb to the top, one rung per holding measurement.
    for _ in range(TOP + 5):
        level = ctl.compute(level, good)
    assert level == TOP

    # A single failing batch backs off EXACTLY one rung, not to 0.
    bad = AccuracySample(passed=5, total=20)
    stepped = ctl.compute(level, bad)
    assert stepped == TOP - 1


# --------------------------------------------------------------------------- #
# disable_signaled
# --------------------------------------------------------------------------- #
def test_disable_signaled_at_zero_with_sustained_sub_floor() -> None:
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    bad = AccuracySample(passed=5, total=20)
    # At rung 0, still failing, enough samples -> caller may disable compaction.
    assert ctl.disable_signaled(0, bad) is True
    # And compute holds at 0 (already floored).
    assert ctl.compute(0, bad) == 0


def test_disable_not_signaled_above_zero() -> None:
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    bad = AccuracySample(passed=5, total=20)
    assert ctl.disable_signaled(1, bad) is False


def test_disable_not_signaled_when_accuracy_holds() -> None:
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    good = AccuracySample(passed=20, total=20)
    assert ctl.disable_signaled(0, good) is False


def test_disable_not_signaled_with_too_few_samples() -> None:
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    thin = AccuracySample(passed=0, total=3)
    assert ctl.disable_signaled(0, thin) is False


def test_disable_not_signaled_with_no_observations() -> None:
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    empty = AccuracySample(passed=0, total=0)
    assert ctl.disable_signaled(0, empty) is False


def test_disable_signaled_treats_negative_level_like_zero() -> None:
    # A negative rung is <= 0, so a sustained sub-floor reading still signals.
    ctl = CompactionAggressivenessController(floor=0.9, min_samples=20)
    bad = AccuracySample(passed=5, total=20)
    assert ctl.disable_signaled(-1, bad) is True
