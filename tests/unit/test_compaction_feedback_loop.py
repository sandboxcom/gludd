"""Tests for the compaction adaptive feedback loop wire-up.

Covers: sending AccuracySamples through the controller lowers the level on
failures, holds on insufficient samples, and signals disable after enough
failures at the most-conservative rung (0).
"""

from __future__ import annotations

from general_ludd.controllers.compaction_aggressiveness import (
    AccuracySample,
    CompactionAggressivenessController,
)


def _ctl(floor: float = 0.5, min_samples: int = 3) -> CompactionAggressivenessController:
    """Low-threshold controller for test responsiveness."""
    return CompactionAggressivenessController(floor=floor, min_samples=min_samples)


def test_controller_stays_on_level_3_when_all_samples_pass():
    ctrl = _ctl()
    level = 3
    # 10 straight successes — accuracy holds, controller should climb or hold
    for _ in range(10):
        sample = AccuracySample(passed=10, total=10)
        level = ctrl.compute(level, sample)
    # With perfect accuracy at level 3, should either stay or climb to max
    assert level >= 3


def test_failure_at_level_3_recommends_lower_level():
    """After enough failures at level 3, controller backs off one rung."""
    ctrl = _ctl(min_samples=1)  # single sample is enough to move
    level = 3
    # Feed a sample where all outcomes failed
    sample = AccuracySample(passed=0, total=10)
    level = ctrl.compute(level, sample)
    assert level == 2, f"Expected back-off from 3→2, got level={level}"


def test_insufficient_samples_hold_current_level():
    """Below min_samples, the controller must hold regardless of pass rate."""
    ctrl = _ctl(min_samples=20)
    level = 3
    sample = AccuracySample(passed=0, total=5)  # total < min_samples
    new_level = ctrl.compute(level, sample)
    assert new_level == level, "Must hold when total < min_samples"


def test_zero_total_holds_current_level():
    """A zero-total sample has no evidence — must hold."""
    ctrl = _ctl(min_samples=1)
    level = 3
    sample = AccuracySample(passed=0, total=0)
    new_level = ctrl.compute(level, sample)
    assert new_level == level, "Must hold when total == 0"


def test_disable_signaled_after_repeated_failures_at_level_0():
    """After sustained failure at rung 0 (no more aggression to remove),
    disable_signaled() returns True."""
    ctrl = _ctl(min_samples=1)
    level = 0
    # Fail enough times at the bottom rung
    sample = AccuracySample(passed=0, total=10)
    assert ctrl.disable_signaled(level, sample) is True


def test_disable_not_signaled_when_at_higher_level():
    """disable_signaled is False when not at the bottom rung."""
    ctrl = _ctl(min_samples=1)
    # Level 1 with complete failure — should NOT trigger disable
    sample = AccuracySample(passed=0, total=10)
    assert ctrl.disable_signaled(1, sample) is False


def test_disable_not_signaled_when_accuracy_holds_at_level_0():
    """When accuracy is above floor at rung 0, disable is NOT signaled."""
    ctrl = _ctl(min_samples=1)
    sample = AccuracySample(passed=10, total=10)
    assert ctrl.disable_signaled(0, sample) is False


def test_disable_not_signaled_below_min_samples():
    """Even at level 0, insufficient samples cannot trigger disable."""
    ctrl = _ctl(min_samples=20)
    sample = AccuracySample(passed=0, total=5)
    assert ctrl.disable_signaled(0, sample) is False


def test_full_feedback_loop_simulation():
    """Simulate the entire feedback loop:
    1. Start at level 3 with perfect accuracy — hold or climb
    2. Accuracy drops — back off one step at a time
    3. Eventually reaches level 0 and signals disable
    """
    ctrl = _ctl(min_samples=1)
    level = 3

    # Phase 1: all successes — should hold or climb
    for _ in range(5):
        sample = AccuracySample(passed=10, total=10)
        level = ctrl.compute(level, sample)
    assert level >= 3

    # Phase 2: accuray drops — back off one step per batch
    level = 3
    for _ in range(5):
        sample = AccuracySample(passed=0, total=10)
        level = ctrl.compute(level, sample)
    # After enough failures, should be at level 0
    assert level == 0

    # Phase 3: at level 0 with sustained failures — disable signalled
    sample = AccuracySample(passed=0, total=10)
    assert ctrl.disable_signaled(level, sample) is True


def test_level_never_goes_below_zero():
    """Even with sustained failure, level never goes negative."""
    ctrl = _ctl(min_samples=1)
    level = 0
    for _ in range(10):
        sample = AccuracySample(passed=0, total=10)
        level = ctrl.compute(level, sample)
    assert level == 0, f"Level must clamp at 0, got {level}"


def test_level_never_exceeds_max():
    """Even with perfect accuracy, level never exceeds max_level."""
    ctrl = _ctl(min_samples=1, floor=0.5)
    # Force max_level to 2
    ctrl.max_level = 2
    level = 1
    for _ in range(10):
        sample = AccuracySample(passed=10, total=10)
        level = ctrl.compute(level, sample)
    assert level <= 2, f"Level must never exceed max_level=2, got {level}"


def test_climb_one_rung_at_a_time():
    """With good accuracy, controller climbs one rung per batch, not all at once."""
    ctrl = _ctl(min_samples=1)
    level = 1
    sample = AccuracySample(passed=10, total=10)
    level = ctrl.compute(level, sample)
    assert level == 2, "Must climb exactly one rung per batch"
