"""Tests for SlurmJobMonitor — the Slurm job watcher that validates job IDs
via ``_require_job_id`` before passing them to sacct/scancel/squeue.

An AWS instance ID like ``i-12345`` is rejected because _JOB_ID_RE requires
numeric-first job IDs (defence against flag injection into argv)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.infra.slurm import SlurmAdapter, SlurmJobConfig, SlurmJobMonitor


@pytest.fixture
def adapter() -> SlurmAdapter:
    return SlurmAdapter()


@pytest.fixture
def config() -> SlurmJobConfig:
    return SlurmJobConfig()


class TestMonitorValidJobIds:
    def test_simple_numeric(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        monitor = SlurmJobMonitor(adapter, "12345", config)
        assert monitor._job_id == "12345"

    def test_array_suffix(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        monitor = SlurmJobMonitor(adapter, "123_4", config)
        assert monitor._job_id == "123_4"

    def test_dot_suffix(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        monitor = SlurmJobMonitor(adapter, "123.456", config)
        assert monitor._job_id == "123.456"

    def test_array_with_plus(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        monitor = SlurmJobMonitor(adapter, "123+5", config)
        assert monitor._job_id == "123+5"

    def test_single_digit(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        monitor = SlurmJobMonitor(adapter, "1", config)
        assert monitor._job_id == "1"

    def test_long_numeric(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        monitor = SlurmJobMonitor(adapter, "12345678901234", config)
        assert monitor._job_id == "12345678901234"


class TestMonitorInvalidJobIds:
    def test_aws_instance_id_rejected(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        with pytest.raises(ValueError, match="invalid Slurm job id"):
            SlurmJobMonitor(adapter, "i-12345", config)

    def test_flag_injection_rejected(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        with pytest.raises(ValueError, match="invalid Slurm job id"):
            SlurmJobMonitor(adapter, "-A evil", config)

    def test_empty_string_rejected(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        with pytest.raises(ValueError, match="invalid Slurm job id"):
            SlurmJobMonitor(adapter, "", config)

    def test_leading_space_rejected(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        with pytest.raises(ValueError, match="invalid Slurm job id"):
            SlurmJobMonitor(adapter, " 123", config)

    def test_non_numeric_rejected(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        with pytest.raises(ValueError, match="invalid Slurm job id"):
            SlurmJobMonitor(adapter, "abc", config)

    def test_contains_space_rejected(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        with pytest.raises(ValueError, match="invalid Slurm job id"):
            SlurmJobMonitor(adapter, "123 456", config)

    def test_leading_dash_rejected(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        with pytest.raises(ValueError, match="invalid Slurm job id"):
            SlurmJobMonitor(adapter, "--help", config)

    def test_dot_only_rejected(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        with pytest.raises(ValueError, match="invalid Slurm job id"):
            SlurmJobMonitor(adapter, ".", config)


class TestMonitorActivityCheckerOptional:
    def test_monitor_accepts_activity_checker(self, adapter: SlurmAdapter, config: SlurmJobConfig) -> None:
        monitor = SlurmJobMonitor(adapter, "42", config, activity_checker=lambda: True)
        assert monitor._job_id == "42"
