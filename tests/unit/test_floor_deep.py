"""Deep tests for FloorController — comprehensive coverage of init, health gating,
env var handling, properties, and boundary edge cases."""

from __future__ import annotations

import pytest

from general_ludd.controllers.floor import FloorController

# ── __init__: floor resolution priority ──────────────────────────────────


def test_init_default_floor_is_5(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOOR", raising=False)
    fc = FloorController()
    assert fc.floor == 5


def test_init_explicit_floor_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOOR", raising=False)
    fc = FloorController(floor=12)
    assert fc.floor == 12


def test_init_env_var_floor_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOOR", "8")
    fc = FloorController()
    assert fc.floor == 8


def test_init_explicit_floor_wins_over_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOOR", "4")
    fc = FloorController(floor=7)
    assert fc.floor == 7


def test_init_floor_zero_is_allowed() -> None:
    fc = FloorController(floor=0)
    assert fc.floor == 0


def test_init_large_floor() -> None:
    fc = FloorController(floor=1000)
    assert fc.floor == 1000


# ── health property and update_health ─────────────────────────────────────


def test_initial_health_is_100() -> None:
    fc = FloorController()
    assert fc.health == 100.0


def test_update_health_sets_value() -> None:
    fc = FloorController()
    fc.update_health(75.0)
    assert fc.health == 75.0


def test_update_health_clamps_below_zero_to_zero() -> None:
    fc = FloorController()
    fc.update_health(-10.0)
    assert fc.health == 0.0


def test_update_health_clamps_above_100() -> None:
    fc = FloorController()
    fc.update_health(150.0)
    assert fc.health == 100.0


def test_update_health_at_zero() -> None:
    fc = FloorController()
    fc.update_health(0.0)
    assert fc.health == 0.0


def test_update_health_at_100() -> None:
    fc = FloorController()
    fc.update_health(100.0)
    assert fc.health == 100.0


def test_update_health_float_precision() -> None:
    fc = FloorController()
    fc.update_health(49.999999)
    assert fc.health == pytest.approx(49.999999)


# ── get_max_active — health gating ────────────────────────────────────────


def test_get_max_active_full_health_returns_floor() -> None:
    fc = FloorController(floor=10)
    assert fc.get_max_active() == 10


def test_get_max_active_full_health_floor_7() -> None:
    fc = FloorController(floor=7)
    assert fc.get_max_active() == 7


def test_get_max_active_health_at_50_still_full() -> None:
    fc = FloorController(floor=10)
    fc.update_health(50.0)
    assert fc.get_max_active() == 10


def test_get_max_active_health_just_below_50_half() -> None:
    fc = FloorController(floor=10)
    fc.update_health(49.9)
    assert fc.get_max_active() == 5


def test_get_max_active_health_at_25_still_half() -> None:
    fc = FloorController(floor=10)
    fc.update_health(25.0)
    assert fc.get_max_active() == 5


def test_get_max_active_health_just_below_25_zero() -> None:
    fc = FloorController(floor=10)
    fc.update_health(24.9)
    assert fc.get_max_active() == 0


def test_get_max_active_health_zero() -> None:
    fc = FloorController(floor=10)
    fc.update_health(0.0)
    assert fc.get_max_active() == 0


def test_get_max_active_half_floor_odd_rounds_down() -> None:
    fc = FloorController(floor=11)
    fc.update_health(30.0)
    assert fc.get_max_active() == 5


def test_get_max_active_half_floor_one_floors_at_one() -> None:
    fc = FloorController(floor=1)
    fc.update_health(30.0)
    assert fc.get_max_active() == 1


def test_get_max_active_floor_zero_always_zero_or_one() -> None:
    fc = FloorController(floor=0)
    # health >= 50: returns floor (0)
    assert fc.get_max_active() == 0
    # health < 50 but >= 25: max(1, floor//2) = max(1, 0) = 1
    fc.update_health(30.0)
    assert fc.get_max_active() == 1
    # health < 25: returns 0
    fc.update_health(20.0)
    assert fc.get_max_active() == 0


def test_get_max_active_returns_int() -> None:
    fc = FloorController(floor=10)
    fc.update_health(49.5)
    assert isinstance(fc.get_max_active(), int)


# ── auto_tune — additional boundary edges ─────────────────────────────────


def test_auto_tune_both_success_low_and_queue_high_raises_by_two() -> None:
    """Success > 95 and queue > 20 raises floor by 2."""
    fc = FloorController(floor=10)
    result = fc.auto_tune(
        cpu_pct=50.0,
        memory_pct=60.0,
        dispatch_success_rate=97.0,
        queue_depth=25,
    )
    assert result == 12
    assert fc.floor == 12


def test_auto_tune_queue_20_or_less_no_raise() -> None:
    fc = FloorController(floor=10)
    result = fc.auto_tune(
        cpu_pct=50.0,
        memory_pct=60.0,
        dispatch_success_rate=96.0,
        queue_depth=20,
    )
    assert result == 10


def test_auto_tune_success_at_95_no_raise() -> None:
    fc = FloorController(floor=10)
    result = fc.auto_tune(
        cpu_pct=50.0,
        memory_pct=60.0,
        dispatch_success_rate=95.0,
        queue_depth=25,
    )
    assert result == 10


def test_auto_tune_success_between_90_and_95_no_change() -> None:
    fc = FloorController(floor=10)
    result = fc.auto_tune(
        cpu_pct=50.0,
        memory_pct=60.0,
        dispatch_success_rate=93.0,
        queue_depth=15,
    )
    assert result == 10


def test_auto_tune_returns_int_always() -> None:
    fc = FloorController(floor=10)
    result = fc.auto_tune(
        cpu_pct=50.0,
        memory_pct=60.0,
        dispatch_success_rate=99.0,
        queue_depth=30,
    )
    assert isinstance(result, int)


# ── floor_history property ────────────────────────────────────────────────


def test_floor_history_starts_empty() -> None:
    fc = FloorController()
    assert fc.floor_history == []


def test_floor_history_returns_copy_not_reference() -> None:
    fc = FloorController(floor=10)
    fc.auto_tune(
        cpu_pct=50.0,
        memory_pct=60.0,
        dispatch_success_rate=80.0,
        queue_depth=5,
    )
    hist = fc.floor_history
    hist.append({"spurious": True})
    assert len(fc.floor_history) == 1


def test_floor_history_timestamp_is_isoformat() -> None:
    fc = FloorController(floor=10)
    fc.auto_tune(
        cpu_pct=50.0,
        memory_pct=60.0,
        dispatch_success_rate=80.0,
        queue_depth=5,
    )
    entry = fc.floor_history[0]
    assert "timestamp" in entry
    timestamp = entry["timestamp"]
    assert isinstance(timestamp, str)
    assert "T" in timestamp
    assert timestamp.endswith("+00:00") or timestamp.endswith("Z")


def test_floor_history_has_all_metric_keys() -> None:
    fc = FloorController(floor=10)
    fc.auto_tune(
        cpu_pct=42.5,
        memory_pct=78.0,
        dispatch_success_rate=85.0,
        queue_depth=15,
    )
    entry = fc.floor_history[0]
    for key in (
        "floor",
        "previous_floor",
        "cpu_pct",
        "memory_pct",
        "dispatch_success_rate",
        "queue_depth",
        "timestamp",
        "reason",
    ):
        assert key in entry, f"missing key {key}"


# ── stress: rapid toggle of auto_tune ─────────────────────────────────────


def test_auto_tune_stress_rapid_toggle_low_then_high() -> None:
    fc = FloorController(floor=10)
    # Low success drops by 2 repeatedly
    for _ in range(4):
        fc.auto_tune(
            cpu_pct=50.0,
            memory_pct=60.0,
            dispatch_success_rate=80.0,
            queue_depth=5,
        )
    assert fc.floor == 2  # 10 -> 8 -> 6 -> 4 -> 2
    # High queue raises back
    for _ in range(3):
        fc.auto_tune(
            cpu_pct=50.0,
            memory_pct=60.0,
            dispatch_success_rate=98.0,
            queue_depth=25,
        )
    assert fc.floor == 8  # 2 -> 4 -> 6 -> 8


def test_auto_tune_history_length_matches_call_count() -> None:
    fc = FloorController(floor=10)
    for _ in range(5):
        fc.auto_tune(
            cpu_pct=50.0,
            memory_pct=60.0,
            dispatch_success_rate=92.0,
            queue_depth=10,
        )
    assert len(fc.floor_history) == 5


def test_get_max_active_after_auto_tune_reflects_new_floor() -> None:
    fc = FloorController(floor=10)
    fc.auto_tune(
        cpu_pct=50.0,
        memory_pct=60.0,
        dispatch_success_rate=80.0,
        queue_depth=5,
    )
    assert fc.floor == 8
    assert fc.get_max_active() == 8
