"""Resource limit definitions for sandbox environments."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResourceLimits:
    """Resource ceilings shared by container and process sandboxes."""

    cpu_shares: int | None = None
    cpu_quota: int | None = None
    cpu_period: int = 100_000
    memory_bytes: int | None = None
    memory_swap_bytes: int | None = None
    disk_bytes: int | None = None
    pids_limit: int | None = None
    timeout_seconds: int = 300

    def to_docker_args(self) -> list[str]:
        """Render configured limits as Docker command arguments."""
        args: list[str] = []
        if self.memory_bytes is not None:
            args.extend(["--memory", str(self.memory_bytes)])
        if self.memory_swap_bytes is not None:
            args.extend(["--memory-swap", str(self.memory_swap_bytes)])
        if self.cpu_shares is not None:
            args.extend(["--cpu-shares", str(self.cpu_shares)])
        if self.cpu_quota is not None:
            args.extend(["--cpu-quota", str(self.cpu_quota)])
        if self.cpu_period != 100_000:
            args.extend(["--cpu-period", str(self.cpu_period)])
        if self.pids_limit not in (None, 0):
            args.extend(["--pids-limit", str(self.pids_limit)])
        if self.disk_bytes is not None:
            args.extend(["--storage-opt", f"size={self.disk_bytes}"])
        return args

    def to_kubernetes_resources(self) -> dict[str, dict[str, str]]:
        """Render configured limits as Kubernetes requests and limits."""
        limits: dict[str, str] = {}
        requests: dict[str, str] = {}
        if self.memory_bytes is not None:
            mem = str(self.memory_bytes)
            limits["memory"] = mem
            requests["memory"] = mem
        if self.cpu_shares is not None:
            cpu = f"{max(1, self.cpu_shares // 1024)}m"
            limits["cpu"] = cpu
            requests["cpu"] = cpu
        if self.disk_bytes is not None:
            limits["ephemeral-storage"] = str(self.disk_bytes)
            requests["ephemeral-storage"] = str(self.disk_bytes)
        return {"limits": limits, "requests": requests}

    def to_process_limits(self) -> dict[str, int]:
        """Render the limits supported by the local process sandbox."""
        result: dict[str, int] = {}
        if self.memory_bytes is not None:
            result["memory_mb"] = max(1, self.memory_bytes // (1024 * 1024))
        if self.cpu_shares is not None:
            result["cpu_seconds"] = max(1, self.cpu_shares // 1024)
        return result

    def exceed_memory(self, used_bytes: int) -> bool:
        """Return whether *used_bytes* exceeds the configured memory ceiling."""
        if self.memory_bytes is None:
            return False
        return used_bytes > self.memory_bytes

    def exceed_timeout(self, elapsed_seconds: float) -> bool:
        """Return whether elapsed time exceeds the configured timeout."""
        return elapsed_seconds > self.timeout_seconds

    @classmethod
    def default_light(cls) -> ResourceLimits:
        """Return limits for a lightweight sandbox workload."""
        return cls(cpu_shares=1024, memory_bytes=256 * 1024 * 1024, timeout_seconds=120)

    @classmethod
    def default_medium(cls) -> ResourceLimits:
        """Return limits for a medium sandbox workload."""
        return cls(cpu_shares=2048, memory_bytes=512 * 1024 * 1024, timeout_seconds=300)

    @classmethod
    def default_heavy(cls) -> ResourceLimits:
        """Return limits for a heavyweight sandbox workload."""
        return cls(cpu_shares=4096, memory_bytes=1024 * 1024 * 1024, timeout_seconds=600)
