"""Unit tests for system load scraping and pressure classification."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_harness.schemas.todo import ResourceProfile


class TestLoadSnapshot:
    def test_load_snapshot_creation(self):
        from agentic_harness.controllers.load_scrape import LoadSnapshot

        snap = LoadSnapshot(
            loadavg_1m=1.0,
            loadavg_5m=1.5,
            loadavg_10m=2.0,
            logical_cpu_count=4,
            cpu_percent=50.0,
            memory_available_percent=60.0,
            disk_free_percent=70.0,
            active_jobs=3,
        )
        assert snap.loadavg_1m == 1.0
        assert snap.loadavg_5m == 1.5
        assert snap.loadavg_10m == 2.0
        assert snap.logical_cpu_count == 4
        assert snap.cpu_percent == 50.0
        assert snap.memory_available_percent == 60.0
        assert snap.disk_free_percent == 70.0
        assert snap.active_jobs == 3


class TestScrapeSystemLoad:
    @patch("agentic_harness.controllers.load_scrape.psutil")
    def test_scrape_returns_snapshot(self, mock_psutil):
        from agentic_harness.controllers.load_scrape import LoadSnapshot, scrape_system_load

        mock_psutil.cpu_count.return_value = 8
        mock_psutil.cpu_percent.return_value = 45.0
        mock_psutil.virtual_memory.return_value = MagicMock(percent=55.0)
        mock_psutil.disk_usage.return_value = MagicMock(percent=30.0)
        mock_psutil.getloadavg.return_value = (1.0, 2.0, 3.0)

        with patch("agentic_harness.controllers.load_scrape._count_active_jobs", return_value=5):
            snap = scrape_system_load()

        assert isinstance(snap, LoadSnapshot)
        assert snap.loadavg_1m == 1.0
        assert snap.loadavg_5m == 2.0
        assert snap.loadavg_10m == 3.0
        assert snap.logical_cpu_count == 8
        assert snap.cpu_percent == 45.0
        assert snap.memory_available_percent == pytest.approx(45.0)
        assert snap.disk_free_percent == pytest.approx(70.0)
        assert snap.active_jobs == 5


class TestClassifyPressure:
    def test_classify_pressure_low(self):
        from agentic_harness.controllers.load_scrape import LoadSnapshot, PressureLevel, classify_pressure

        snap = LoadSnapshot(
            loadavg_1m=0.5, loadavg_5m=0.8, loadavg_10m=1.0,
            logical_cpu_count=8, cpu_percent=20.0,
            memory_available_percent=80.0, disk_free_percent=90.0,
            active_jobs=2,
        )
        result = classify_pressure(snap)
        assert result[ResourceProfile.LOCAL_HEAVY] == PressureLevel.LOW
        assert result[ResourceProfile.AI_HEAVY] == PressureLevel.LOW
        assert result[ResourceProfile.HYBRID] == PressureLevel.LOW
        assert result[ResourceProfile.NETWORK_HEAVY] == PressureLevel.LOW
        assert result[ResourceProfile.LOW_RESOURCE] == PressureLevel.LOW

    def test_classify_pressure_high(self):
        from agentic_harness.controllers.load_scrape import LoadSnapshot, PressureLevel, classify_pressure

        snap = LoadSnapshot(
            loadavg_1m=6.0, loadavg_5m=6.5, loadavg_10m=7.0,
            logical_cpu_count=8, cpu_percent=85.0,
            memory_available_percent=30.0, disk_free_percent=50.0,
            active_jobs=10,
        )
        result = classify_pressure(snap)
        assert result[ResourceProfile.LOCAL_HEAVY] == PressureLevel.HIGH

    def test_classify_pressure_severe(self):
        from agentic_harness.controllers.load_scrape import LoadSnapshot, PressureLevel, classify_pressure

        snap = LoadSnapshot(
            loadavg_1m=12.0, loadavg_5m=11.0, loadavg_10m=10.0,
            logical_cpu_count=4, cpu_percent=98.0,
            memory_available_percent=5.0, disk_free_percent=10.0,
            active_jobs=20,
        )
        result = classify_pressure(snap)
        assert result[ResourceProfile.LOCAL_HEAVY] == PressureLevel.SEVERE
        assert result[ResourceProfile.HYBRID] in (PressureLevel.HIGH, PressureLevel.SEVERE)
