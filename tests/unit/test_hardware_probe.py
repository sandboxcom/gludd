from __future__ import annotations

import os
from unittest.mock import patch

from general_ludd.hardware.probe import HardwareProfile, probe_hardware


class TestHardwareProbe:
    def test_probe_returns_profile(self):
        profile = probe_hardware()
        assert isinstance(profile, HardwareProfile)
        assert profile.cpu_count >= 1
        assert profile.total_memory_gb > 0

    def test_probe_cpu_count_matches_os(self):
        profile = probe_hardware()
        assert profile.cpu_count == (os.cpu_count() or 1)

    def test_recommended_workers_is_quarter_cpu(self):
        profile = probe_hardware()
        expected = max(1, profile.cpu_count // 4)
        assert profile.recommended_workers == expected

    def test_recommended_workers_minimum_one(self):
        with patch("general_ludd.hardware.probe.os.cpu_count", return_value=1):
            profile = probe_hardware()
        assert profile.recommended_workers == 1

    def test_recommended_workers_two_cpu(self):
        with patch("general_ludd.hardware.probe.os.cpu_count", return_value=2):
            profile = probe_hardware()
        assert profile.recommended_workers == 1

    def test_recommended_workers_eight_cpu(self):
        with patch("general_ludd.hardware.probe.os.cpu_count", return_value=8):
            profile = probe_hardware()
        assert profile.recommended_workers == 2

    def test_recommended_workers_sixteen_cpu(self):
        with patch("general_ludd.hardware.probe.os.cpu_count", return_value=16):
            profile = probe_hardware()
        assert profile.recommended_workers == 4

    def test_network_concurrency_defaults(self):
        profile = probe_hardware()
        assert profile.network_concurrency >= 1
        assert profile.network_concurrency <= profile.cpu_count * 4

    def test_local_model_allowed_by_default(self):
        profile = probe_hardware()
        assert isinstance(profile.local_model_allowed, bool)

    def test_local_model_blocked_on_low_memory(self):
        with patch("general_ludd.hardware.probe.os.cpu_count", return_value=4), \
             patch("general_ludd.hardware.probe._total_memory_gb", return_value=1.5):
            profile = probe_hardware()
        assert profile.local_model_allowed is False

    def test_gunicorn_workers_matches_recommended(self):
        profile = probe_hardware()
        assert profile.gunicorn_workers == profile.recommended_workers

    def test_thread_pool_size_based_on_cpu(self):
        profile = probe_hardware()
        assert profile.thread_pool_size >= 1
        assert profile.thread_pool_size <= profile.cpu_count * 2

    def test_to_dict_contains_all_fields(self):
        profile = probe_hardware()
        d = profile.to_dict()
        assert "cpu_count" in d
        assert "total_memory_gb" in d
        assert "recommended_workers" in d
        assert "network_concurrency" in d
        assert "gunicorn_workers" in d
        assert "thread_pool_size" in d
        assert "local_model_allowed" in d
