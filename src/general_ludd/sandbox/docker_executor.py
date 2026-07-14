"""Execute commands inside Docker containers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field


@dataclass
class DockerContainerConfig:
    image: str
    command: str
    workdir: str = "/workspace"
    environment: dict[str, str] = field(default_factory=dict)
    volumes: dict[str, str] = field(default_factory=dict)
    memory_bytes: int | None = None
    cpu_shares: int | None = None
    network_mode: str = "none"


@dataclass
class DockerResult:
    returncode: int
    stdout: str
    stderr: str
    container_id: str = ""


class DockerExecutor:
    def __init__(self, timeout: int = 300, max_output_bytes: int = 1_000_000, auto_remove: bool = True) -> None:
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self.auto_remove = auto_remove

    def pull_image(self, image: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["docker", "pull", image], capture_output=True, text=True, timeout=self.timeout)

    def execute(self, config: DockerContainerConfig) -> DockerResult:
        cmd = self._build_command(config)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        container_id = result.stdout.strip().split("\n")[-1] if result.stdout else ""
        return DockerResult(
            returncode=result.returncode, stdout=result.stdout,
            stderr=result.stderr, container_id=container_id,
        )

    def execute_in_container(self, container_id: str, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", "exec", container_id, *command.split()],
            capture_output=True, text=True, timeout=self.timeout,
        )

    def stop_container(self, container_id: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["docker", "stop", container_id], capture_output=True, text=True, timeout=30)

    def remove_container(self, container_id: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, text=True, timeout=30)

    @staticmethod
    def parse_container_id(run_output: str) -> str:
        return run_output.strip().split("\n")[-1].strip()

    def _build_command(self, config: DockerContainerConfig) -> list[str]:
        cmd = ["docker", "run"]
        if self.auto_remove:
            cmd.append("--rm")
        cmd.extend(["--workdir", config.workdir])
        cmd.extend(["--network", config.network_mode])
        for host_path, container_path in config.volumes.items():
            cmd.extend(["-v", f"{host_path}:{container_path}"])
        for key, value in config.environment.items():
            cmd.extend(["-e", f"{key}={value}"])
        if config.memory_bytes is not None:
            cmd.extend(["--memory", str(config.memory_bytes)])
        if config.cpu_shares is not None:
            cmd.extend(["--cpu-shares", str(config.cpu_shares)])
        cmd.append(config.image)
        cmd.append(config.command)
        return cmd
