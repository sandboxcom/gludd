"""Unit tests for system monitor (load average / CPU capacity)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from general_ludd.system.monitor import (
    can_start_process,
    get_cpu_count,
    get_load_average,
    wait_for_capacity,
)


class TestGetLoadAverage:
    def test_returns_three_values(self):
        load = get_load_average()
        assert isinstance(load, tuple)
        assert len(load) == 3
        assert all(isinstance(x, float) for x in load)
        assert all(x >= 0 for x in load)

    def test_values_are_reasonable(self):
        # On a typical system, load averages should be non-negative
        load1, load5, load15 = get_load_average()
        assert load1 >= 0
        assert load5 >= 0
        assert load15 >= 0


class TestGetCpuCount:
    def test_returns_positive_int(self):
        count = get_cpu_count()
        assert isinstance(count, int)
        assert count > 0

    def test_matches_os_cpu_count(self):
        import os
        expected = os.cpu_count() or 1
        assert get_cpu_count() == expected


class TestCanStartProcess:
    def test_default_threshold_allows_on_idle_system(self):
        # On a typical idle system, load should be well below thresholds
        # This test assumes the test runner has reasonable load
        result = can_start_process()
        assert isinstance(result, bool)

    def test_high_threshold_multiplier_allows_more_load(self):
        # With a very high threshold, should always allow
        result = can_start_process(threshold_multiplier=100.0, max_load_5min=1000.0)
        assert result is True

    def test_zero_threshold_blocks(self):
        # With zero threshold, should block unless load is exactly 0
        # Mock load average to return non-zero values
        with patch("general_ludd.system.monitor.get_load_average", return_value=(1.0, 2.0, 3.0)):
            result = can_start_process(threshold_multiplier=0.0, max_load_5min=0.0)
            assert result is False

    def test_max_load_5min_enforced(self):
        # Even if load is below threshold_multiplier * cores, max_load_5min caps it
        with patch(
            "general_ludd.system.monitor.get_load_average",
            return_value=(0.5, 15.0, 10.0),
        ), patch("general_ludd.system.monitor.get_cpu_count", return_value=8):
                # 15.0 > max_load_5min (10) -> should block
                result = can_start_process(threshold_multiplier=2.5, max_load_5min=10.0)
                assert result is False

    def test_threshold_multiplier_enforced(self):
        # Load above 2.5x cores should block
        with patch(
            "general_ludd.system.monitor.get_load_average",
            return_value=(10.0, 25.0, 20.0),
        ), patch("general_ludd.system.monitor.get_cpu_count", return_value=8):
                # 25.0 > 2.5 * 8 = 20.0 -> should block
                result = can_start_process(threshold_multiplier=2.5, max_load_5min=100.0)
                assert result is False

    def test_both_thresholds_must_pass(self):
        # Both threshold_multiplier and max_load_5min must be satisfied
        with patch(
            "general_ludd.system.monitor.get_load_average",
            return_value=(5.0, 8.0, 6.0),
        ), patch("general_ludd.system.monitor.get_cpu_count", return_value=8):
                # 8.0 <= 2.5 * 8 = 20.0 (passes multiplier)
                # 8.0 <= 10.0 (passes max_load_5min)
                result = can_start_process(threshold_multiplier=2.5, max_load_5min=10.0)
                assert result is True


class TestWaitForCapacity:
    def test_returns_immediately_when_capacity_available(self):
        # Should return quickly if capacity is available
        start = time.time()
        wait_for_capacity(threshold_multiplier=100.0, max_load_5min=1000.0, check_interval=0.01)
        elapsed = time.time() - start
        assert elapsed < 1.0  # Should return almost immediately

    def test_respects_timeout(self):
        # Should raise TimeoutError when timeout expires
        with patch(
            "general_ludd.system.monitor.can_start_process",
            return_value=False,
        ), pytest.raises(TimeoutError):
                wait_for_capacity(
                    threshold_multiplier=2.5,
                    max_load_5min=10.0,
                    check_interval=0.01,
                    timeout=0.1,
                )

    def test_returns_when_capacity_becomes_available(self):
        # Mock can_start_process to return False twice then True
        call_count = [0]

        def mock_can_start(*args, **kwargs):
            call_count[0] += 1
            return call_count[0] >= 3

        with patch("general_ludd.system.monitor.can_start_process", side_effect=mock_can_start):
            start = time.time()
            wait_for_capacity(check_interval=0.01, timeout=5.0)
            elapsed = time.time() - start
            # Should have waited ~0.02s (2 intervals)
            assert elapsed < 1.0


class TestSystemMonitorIntegration:
    def test_load_average_and_cpu_count_consistent(self):
        # Basic integration: load and cpu count should work together
        load1, _load5, _load15 = get_load_average()
        cores = get_cpu_count()
        # On a healthy system, 1-min load shouldn't wildly exceed cores * 10
        assert load1 < cores * 10

    def test_can_start_process_uses_load_and_cpu(self):
        # Verify the function actually uses both load average and cpu count
        with patch(
            "general_ludd.system.monitor.get_load_average",
            return_value=(20.0, 25.0, 30.0),
        ), patch("general_ludd.system.monitor.get_cpu_count", return_value=4):
                # 25.0 > 2.5 * 4 = 10.0 -> should block
                assert can_start_process(threshold_multiplier=2.5, max_load_5min=100) is False
                # But with higher threshold it should pass
                assert can_start_process(threshold_multiplier=10.0, max_load_5min=100) is True
