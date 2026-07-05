"""E2E integration tests for FloorController auto_tune convergence.

Simulates multi-tick scenarios with varying system load to prove the floor
auto-tunes toward appropriate values: rising under high success + deep queue,
falling under low success, staying bounded [1, 20], and recording history
correctly across ticks.
"""

from __future__ import annotations

from general_ludd.controllers.floor import FloorController


def _tick(
    fc: FloorController,
    cpu: float,
    mem: float,
    success_rate: float,
    queue_depth: int,
) -> int:
    return fc.auto_tune(
        cpu_pct=cpu,
        memory_pct=mem,
        dispatch_success_rate=success_rate,
        queue_depth=queue_depth,
    )


class TestFloorConvergesUpward:
    """High success + deep queue → floor rises toward 20 across ticks."""

    def test_floor_rises_from_one_to_twenty_across_ten_ticks(self) -> None:
        fc = FloorController(floor=1)
        expected = [3, 5, 7, 9, 11, 13, 15, 17, 19, 20]

        for i, exp in enumerate(expected):
            result = _tick(fc, cpu=30.0, mem=40.0, success_rate=98.0, queue_depth=30)
            assert result == exp, f"tick {i}: expected {exp}, got {result}"
            assert fc.floor == exp

        assert len(fc.floor_history) == 10

    def test_floor_stays_at_twenty_once_converged(self) -> None:
        fc = FloorController(floor=20)
        for _ in range(5):
            result = _tick(fc, cpu=30.0, mem=40.0, success_rate=99.0, queue_depth=30)
            assert result == 20
        assert all(e["reason"] == "no_change" for e in fc.floor_history)
        assert all(e["floor"] == 20 for e in fc.floor_history)

    def test_queue_depth_at_twenty_one_no_raise(self) -> None:
        fc = FloorController(floor=10)
        result = _tick(fc, cpu=30.0, mem=40.0, success_rate=99.0, queue_depth=21)
        assert result == 12
        result = _tick(fc, cpu=30.0, mem=40.0, success_rate=99.0, queue_depth=18)
        assert result == 12


class TestFloorConvergesDownward:
    """Low success rate → floor falls toward 1 across ticks."""

    def test_floor_falls_from_twenty_to_one_across_ten_ticks(self) -> None:
        fc = FloorController(floor=20)
        expected = [18, 16, 14, 12, 10, 8, 6, 4, 2, 1]

        for i, exp in enumerate(expected):
            result = _tick(fc, cpu=50.0, mem=60.0, success_rate=50.0, queue_depth=5)
            assert result == exp, f"tick {i}: expected {exp}, got {result}"
            assert fc.floor == exp

        assert len(fc.floor_history) == 10

    def test_floor_stays_at_one_once_converged(self) -> None:
        fc = FloorController(floor=1)
        for _ in range(5):
            result = _tick(fc, cpu=50.0, mem=60.0, success_rate=50.0, queue_depth=5)
            assert result == 1
        assert all(e["reason"] == "no_change" for e in fc.floor_history)
        assert all(e["floor"] == 1 for e in fc.floor_history)


class TestFloorOverloadConvergesDownward:
    """Low success rate takes priority over deep queue — floor falls
    even when the queue is deep, because dispatch is failing."""

    def test_overload_lowers_floor_despite_deep_queue(self) -> None:
        fc = FloorController(floor=10)
        result = _tick(fc, cpu=50.0, mem=70.0, success_rate=80.0, queue_depth=30)
        assert result == 8
        assert fc.floor_history[0]["reason"] == "low_success_rate"

    def test_overload_converges_to_one(self) -> None:
        fc = FloorController(floor=10)
        expected = [8, 6, 4, 2, 1]
        for i, exp in enumerate(expected):
            result = _tick(fc, cpu=70.0, mem=80.0, success_rate=60.0, queue_depth=30)
            assert result == exp, f"tick {i}: expected {exp}, got {result}"
        result = _tick(fc, cpu=70.0, mem=80.0, success_rate=60.0, queue_depth=30)
        assert result == 1


class TestFloorBounds:
    """Floor never goes below 1 or above 20, regardless of ticks."""

    def test_floor_never_below_one(self) -> None:
        fc = FloorController(floor=3)
        for _ in range(10):
            _tick(fc, cpu=50.0, mem=60.0, success_rate=0.0, queue_depth=0)
        assert fc.floor == 1

    def test_floor_never_above_twenty(self) -> None:
        fc = FloorController(floor=10)
        for _ in range(20):
            _tick(fc, cpu=10.0, mem=20.0, success_rate=100.0, queue_depth=100)
        assert fc.floor == 20

    def test_floor_always_in_bounds_during_mixed_ticks(self) -> None:
        fc = FloorController(floor=10)
        scenarios = [
            (50.0, 60.0, 50.0, 5),
            (50.0, 60.0, 99.0, 30),
            (50.0, 60.0, 50.0, 5),
            (50.0, 60.0, 99.0, 30),
            (50.0, 60.0, 99.0, 30),
            (50.0, 60.0, 99.0, 30),
            (50.0, 60.0, 99.0, 30),
            (50.0, 60.0, 99.0, 30),
            (50.0, 60.0, 99.0, 30),
            (50.0, 60.0, 99.0, 30),
            (50.0, 60.0, 99.0, 30),
        ]
        for cpu, mem, sr, qd in scenarios:
            result = _tick(fc, cpu, mem, sr, qd)
            assert 1 <= result <= 20, f"floor {result} out of bounds"
            assert 1 <= fc.floor <= 20


class TestFloorHistoryAcrossTicks:
    """History accumulates correctly across ticks and preserves metric values."""

    def test_history_accumulates_per_tick(self) -> None:
        fc = FloorController(floor=10)
        for _ in range(7):
            _tick(fc, cpu=30.0, mem=40.0, success_rate=98.0, queue_depth=30)
        assert len(fc.floor_history) == 7

    def test_history_entries_have_monotonic_timestamps(self) -> None:
        fc = FloorController(floor=10)
        _tick(fc, cpu=30.0, mem=40.0, success_rate=98.0, queue_depth=30)
        _tick(fc, cpu=30.0, mem=40.0, success_rate=98.0, queue_depth=30)
        t0 = fc.floor_history[0]["timestamp"]
        t1 = fc.floor_history[1]["timestamp"]
        assert t1 >= t0

    def test_history_captures_full_tick_metrics(self) -> None:
        fc = FloorController(floor=10)
        _tick(fc, cpu=33.3, mem=66.6, success_rate=80.0, queue_depth=25)
        entry = fc.floor_history[0]
        assert entry["cpu_pct"] == 33.3
        assert entry["memory_pct"] == 66.6
        assert entry["dispatch_success_rate"] == 80.0
        assert entry["queue_depth"] == 25
        assert entry["floor"] == 8
        assert entry["previous_floor"] == 10
        assert entry["reason"] == "low_success_rate"

    def test_history_tracks_full_convergence_trace(self) -> None:
        fc = FloorController(floor=1)
        for _ in range(10):
            _tick(fc, cpu=30.0, mem=40.0, success_rate=98.0, queue_depth=30)
        reasons = [e["reason"] for e in fc.floor_history]
        assert reasons == ["high_queue_depth"] * 10
        floors = [e["floor"] for e in fc.floor_history]
        assert floors == [3, 5, 7, 9, 11, 13, 15, 17, 19, 20]
        prev_floors = [e["previous_floor"] for e in fc.floor_history]
        assert prev_floors == [1, 3, 5, 7, 9, 11, 13, 15, 17, 19]


class TestFloorOscillation:
    """Mixed load causes floor to oscillate within bounds."""

    def test_alternating_pass_fail_oscillates(self) -> None:
        fc = FloorController(floor=10)
        floors: list[int] = []

        for _ in range(4):
            floors.append(_tick(fc, cpu=50.0, mem=60.0, success_rate=80.0, queue_depth=5))
            floors.append(_tick(fc, cpu=50.0, mem=60.0, success_rate=99.0, queue_depth=30))

        assert floors == [8, 10, 8, 10, 8, 10, 8, 10]
        assert all(1 <= f <= 20 for f in floors)

    def test_oscillating_history_reasons(self) -> None:
        fc = FloorController(floor=10)
        _tick(fc, cpu=50.0, mem=60.0, success_rate=80.0, queue_depth=5)
        _tick(fc, cpu=50.0, mem=60.0, success_rate=99.0, queue_depth=30)
        _tick(fc, cpu=50.0, mem=60.0, success_rate=80.0, queue_depth=5)
        _tick(fc, cpu=50.0, mem=60.0, success_rate=99.0, queue_depth=30)

        reasons = [e["reason"] for e in fc.floor_history]
        assert reasons == ["low_success_rate", "high_queue_depth", "low_success_rate", "high_queue_depth"]


class TestFloorRecovery:
    """After a low-success episode, the floor recovers when success improves."""

    def test_recovery_from_low_success_episode(self) -> None:
        fc = FloorController(floor=10)

        for _ in range(3):
            _tick(fc, cpu=50.0, mem=60.0, success_rate=50.0, queue_depth=5)
        assert fc.floor == 4

        for _ in range(5):
            _tick(fc, cpu=30.0, mem=40.0, success_rate=99.0, queue_depth=30)
        assert fc.floor == 14

        assert 1 <= fc.floor <= 20

    def test_full_cycle_up_down_up(self) -> None:
        fc = FloorController(floor=10)

        for _ in range(5):
            _tick(fc, cpu=30.0, mem=40.0, success_rate=99.0, queue_depth=30)
        assert fc.floor == 20

        for _ in range(7):
            _tick(fc, cpu=80.0, mem=90.0, success_rate=30.0, queue_depth=5)
        assert fc.floor == 6

        for _ in range(3):
            _tick(fc, cpu=30.0, mem=40.0, success_rate=99.0, queue_depth=30)
        assert fc.floor == 12

    def test_stable_mid_range_convergence(self) -> None:
        fc = FloorController(floor=5)

        _tick(fc, cpu=50.0, mem=60.0, success_rate=94.0, queue_depth=10)
        assert fc.floor == 5

        _tick(fc, cpu=50.0, mem=60.0, success_rate=94.0, queue_depth=10)
        assert fc.floor == 5

        _tick(fc, cpu=50.0, mem=60.0, success_rate=80.0, queue_depth=10)
        assert fc.floor == 3

        _tick(fc, cpu=50.0, mem=60.0, success_rate=80.0, queue_depth=10)
        assert fc.floor == 1

        _tick(fc, cpu=30.0, mem=40.0, success_rate=99.0, queue_depth=30)
        assert fc.floor == 3

        _tick(fc, cpu=30.0, mem=40.0, success_rate=99.0, queue_depth=30)
        assert fc.floor == 5

        assert fc.floor == 5


class TestFloorHistorySnapshot:
    """floor_history property returns an independent snapshot."""

    def test_snapshot_mutation_does_not_affect_internal_state(self) -> None:
        fc = FloorController(floor=10)
        _tick(fc, cpu=50.0, mem=60.0, success_rate=80.0, queue_depth=5)

        snapshot = fc.floor_history
        snapshot.clear()

        assert len(fc.floor_history) == 1

    def test_two_snapshots_are_independent(self) -> None:
        fc = FloorController(floor=10)
        _tick(fc, cpu=50.0, mem=60.0, success_rate=80.0, queue_depth=5)
        _tick(fc, cpu=30.0, mem=40.0, success_rate=99.0, queue_depth=30)

        s1 = fc.floor_history
        s2 = fc.floor_history

        s1.pop()
        assert len(s2) == 2
