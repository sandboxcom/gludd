"""Unit tests for sandbox resource limits."""

from __future__ import annotations

from general_ludd.sandbox.resource_limits import ResourceLimits


class TestResourceLimits:
    def test_default_values(self) -> None:
        limits = ResourceLimits()
        assert limits.cpu_shares is None
        assert limits.cpu_quota is None
        assert limits.cpu_period == 100_000
        assert limits.memory_bytes is None
        assert limits.memory_swap_bytes is None
        assert limits.disk_bytes is None
        assert limits.pids_limit is None
        assert limits.timeout_seconds == 300

    def test_default_light(self) -> None:
        limits = ResourceLimits.default_light()
        assert limits.cpu_shares == 1024
        assert limits.memory_bytes == 256 * 1024 * 1024
        assert limits.timeout_seconds == 120

    def test_default_medium(self) -> None:
        limits = ResourceLimits.default_medium()
        assert limits.cpu_shares == 2048
        assert limits.memory_bytes == 512 * 1024 * 1024
        assert limits.timeout_seconds == 300

    def test_default_heavy(self) -> None:
        limits = ResourceLimits.default_heavy()
        assert limits.cpu_shares == 4096
        assert limits.memory_bytes == 1024 * 1024 * 1024
        assert limits.timeout_seconds == 600

    def test_to_docker_args_memory(self) -> None:
        limits = ResourceLimits(memory_bytes=500_000_000)
        args = limits.to_docker_args()
        assert "--memory" in args
        assert "500000000" in args

    def test_to_docker_args_cpu(self) -> None:
        limits = ResourceLimits(cpu_shares=2048, cpu_quota=50000)
        args = limits.to_docker_args()
        assert "--cpu-shares" in args
        assert "2048" in args
        assert "--cpu-quota" in args
        assert "50000" in args

    def test_to_docker_args_swap(self) -> None:
        limits = ResourceLimits(memory_swap_bytes=1_000_000_000)
        args = limits.to_docker_args()
        assert "--memory-swap" in args
        assert "1000000000" in args

    def test_to_docker_args_pids(self) -> None:
        limits = ResourceLimits(pids_limit=100)
        args = limits.to_docker_args()
        assert "--pids-limit" in args
        assert "100" in args

    def test_to_docker_args_disk(self) -> None:
        limits = ResourceLimits(disk_bytes=5_000_000_000)
        args = limits.to_docker_args()
        assert "--storage-opt" in args
        assert "size=5000000000" in args

    def test_to_docker_args_cpu_period_default(self) -> None:
        limits = ResourceLimits(cpu_period=100_000)
        args = limits.to_docker_args()
        assert "--cpu-period" not in args

    def test_to_docker_args_cpu_period_custom(self) -> None:
        limits = ResourceLimits(cpu_period=50_000)
        args = limits.to_docker_args()
        assert "--cpu-period" in args
        assert "50000" in args

    def test_to_kubernetes_resources(self) -> None:
        limits = ResourceLimits(memory_bytes=512_000_000, cpu_shares=2048)
        res = limits.to_kubernetes_resources()
        assert "limits" in res
        assert "requests" in res
        assert res["limits"]["memory"] == "512000000"
        assert res["requests"]["memory"] == "512000000"

    def test_to_kubernetes_resources_empty(self) -> None:
        limits = ResourceLimits()
        res = limits.to_kubernetes_resources()
        assert res["limits"] == {}
        assert res["requests"] == {}

    def test_to_kubernetes_resources_disk(self) -> None:
        limits = ResourceLimits(disk_bytes=1_000_000_000)
        res = limits.to_kubernetes_resources()
        assert "ephemeral-storage" in res["limits"]

    def test_to_process_limits(self) -> None:
        limits = ResourceLimits(memory_bytes=256_000_000, cpu_shares=2048)
        proc = limits.to_process_limits()
        assert proc["memory_mb"] == 244
        assert proc["cpu_seconds"] == 2

    def test_to_process_limits_empty(self) -> None:
        limits = ResourceLimits()
        proc = limits.to_process_limits()
        assert proc == {}

    def test_exceed_memory(self) -> None:
        limits = ResourceLimits(memory_bytes=1_000_000)
        assert limits.exceed_memory(2_000_000) is True
        assert limits.exceed_memory(500_000) is False
        assert limits.exceed_memory(1_000_000) is False

    def test_exceed_memory_unlimited(self) -> None:
        limits = ResourceLimits()
        assert limits.exceed_memory(999_999_999_999) is False

    def test_exceed_timeout(self) -> None:
        limits = ResourceLimits(timeout_seconds=60)
        assert limits.exceed_timeout(120) is True
        assert limits.exceed_timeout(30) is False
        assert limits.exceed_timeout(60) is False

    def test_all_limits_set(self) -> None:
        limits = ResourceLimits(cpu_shares=4096, cpu_quota=100_000, cpu_period=100_000, memory_bytes=1_000_000_000, memory_swap_bytes=2_000_000_000, disk_bytes=10_000_000_000, pids_limit=256, timeout_seconds=600)
        assert limits.memory_bytes == 1_000_000_000
        assert limits.disk_bytes == 10_000_000_000
        assert limits.pids_limit == 256
        assert limits.timeout_seconds == 600
