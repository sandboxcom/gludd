"""Unit tests for FloorController.auto_tune()."""

from __future__ import annotations

from general_ludd.controllers.floor import FloorController


class TestAutoTuneLowSuccessRate:
    def test_low_success_rate_lowers_floor_by_two(self) -> None:
        fc = FloorController(floor=10)
        result = fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=80.0, queue_depth=5,
        )
        assert result == 8
        assert fc.floor == 8

    def test_low_success_rate_does_not_go_below_one(self) -> None:
        fc = FloorController(floor=2)
        result = fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=80.0, queue_depth=5,
        )
        assert result == 1
        assert fc.floor == 1

    def test_floor_at_one_stays_at_one(self) -> None:
        fc = FloorController(floor=1)
        result = fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=50.0, queue_depth=5,
        )
        assert result == 1
        assert fc.floor == 1

    def test_exactly_90_pct_success_is_not_low(self) -> None:
        fc = FloorController(floor=10)
        result = fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=90.0, queue_depth=5,
        )
        assert result == 10
        assert fc.floor == 10


class TestAutoTuneHighQueueDepth:
    def test_high_queue_depth_and_success_raises_floor(self) -> None:
        fc = FloorController(floor=10)
        result = fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=98.0, queue_depth=25,
        )
        assert result == 12
        assert fc.floor == 12

    def test_high_queue_depth_does_not_exceed_20(self) -> None:
        fc = FloorController(floor=19)
        result = fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=98.0, queue_depth=30,
        )
        assert result == 20
        assert fc.floor == 20

    def test_floor_at_max_stays_at_max(self) -> None:
        fc = FloorController(floor=20)
        result = fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=99.0, queue_depth=30,
        )
        assert result == 20
        assert fc.floor == 20

    def test_queue_depth_20_or_below_no_raise(self) -> None:
        fc = FloorController(floor=10)
        result = fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=99.0, queue_depth=20,
        )
        assert result == 10
        assert fc.floor == 10

    def test_exactly_95_pct_success_no_raise(self) -> None:
        fc = FloorController(floor=10)
        result = fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=95.0, queue_depth=30,
        )
        assert result == 10
        assert fc.floor == 10


class TestAutoTuneNoChange:
    def test_normal_metrics_no_change(self) -> None:
        fc = FloorController(floor=10)
        result = fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=92.0, queue_depth=10,
        )
        assert result == 10
        assert fc.floor == 10

    def test_returns_int(self) -> None:
        fc = FloorController(floor=10)
        result = fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=80.0, queue_depth=5,
        )
        assert isinstance(result, int)


class TestFloorHistory:
    def test_no_change_records_history_entry(self) -> None:
        fc = FloorController(floor=10)
        fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=92.0, queue_depth=5,
        )
        assert len(fc.floor_history) == 1
        entry = fc.floor_history[0]
        assert entry["reason"] == "no_change"
        assert entry["floor"] == 10
        assert entry["previous_floor"] == 10

    def test_low_success_rate_records_history_with_reason(self) -> None:
        fc = FloorController(floor=10)
        fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=80.0, queue_depth=5,
        )
        entry = fc.floor_history[0]
        assert entry["reason"] == "low_success_rate"
        assert entry["floor"] == 8
        assert entry["previous_floor"] == 10

    def test_high_queue_depth_records_history_with_reason(self) -> None:
        fc = FloorController(floor=10)
        fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=98.0, queue_depth=30,
        )
        entry = fc.floor_history[0]
        assert entry["reason"] == "high_queue_depth"
        assert entry["floor"] == 12
        assert entry["previous_floor"] == 10

    def test_history_accumulates_multiple_entries(self) -> None:
        fc = FloorController(floor=10)
        fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=80.0, queue_depth=5,
        )
        fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=98.0, queue_depth=30,
        )
        fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=92.0, queue_depth=5,
        )
        assert len(fc.floor_history) == 3
        assert fc.floor_history[0]["reason"] == "low_success_rate"
        assert fc.floor_history[1]["reason"] == "high_queue_depth"
        assert fc.floor_history[2]["reason"] == "no_change"

    def test_history_entry_has_all_required_keys(self) -> None:
        fc = FloorController(floor=10)
        fc.auto_tune(
            cpu_pct=42.5, memory_pct=78.0,
            dispatch_success_rate=85.0, queue_depth=15,
        )
        entry = fc.floor_history[0]
        required_keys = {
            "floor", "previous_floor", "cpu_pct", "memory_pct",
            "dispatch_success_rate", "queue_depth", "timestamp", "reason",
        }
        assert set(entry) == required_keys

    def test_history_preserves_metric_values(self) -> None:
        fc = FloorController(floor=10)
        fc.auto_tune(
            cpu_pct=42.5, memory_pct=78.0,
            dispatch_success_rate=85.0, queue_depth=15,
        )
        entry = fc.floor_history[0]
        assert entry["cpu_pct"] == 42.5
        assert entry["memory_pct"] == 78.0
        assert entry["dispatch_success_rate"] == 85.0
        assert entry["queue_depth"] == 15

    def test_history_is_a_copy_not_a_reference(self) -> None:
        fc = FloorController(floor=10)
        fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=80.0, queue_depth=5,
        )
        hist = fc.floor_history
        hist.append({"spurious": True})
        assert len(fc.floor_history) == 1

    def test_low_success_takes_priority_over_high_queue(self) -> None:
        fc = FloorController(floor=10)
        result = fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=80.0, queue_depth=30,
        )
        assert result == 8
        assert fc.floor == 8
        assert fc.floor_history[0]["reason"] == "low_success_rate"


class TestAutoTuneIdempotency:
    def test_repeated_calls_at_floor_one_no_further_decrease(self) -> None:
        fc = FloorController(floor=3)
        fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=50.0, queue_depth=5,
        )
        assert fc.floor == 1
        fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=50.0, queue_depth=5,
        )
        assert fc.floor == 1

    def test_repeated_calls_at_floor_20_no_further_increase(self) -> None:
        fc = FloorController(floor=19)
        fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=99.0, queue_depth=30,
        )
        assert fc.floor == 20
        fc.auto_tune(
            cpu_pct=50.0, memory_pct=60.0,
            dispatch_success_rate=99.0, queue_depth=30,
        )
        assert fc.floor == 20
