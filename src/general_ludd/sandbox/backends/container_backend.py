"""Container-based sandbox backend (podman / docker) implementing ``SandboxBackend``."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from general_ludd.sandbox.contracts import SandboxConfig, SandboxResult


class ContainerBackend:
    """Execute commands inside a container runtime (podman or docker).

    Implements the :class:`SandboxBackend` Protocol. Prefers podman when
    available, falls back to docker. Provides ``IsolationLevel.CONTAINER``
    isolation strength.
    """

    name: str

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        self._container_ids: list[str] = []
        self._runtime: str = self._detect_runtime()
        self.name = self._runtime

    def _detect_runtime(self) -> str:
        if shutil.which("podman") is not None:
            return "podman"
        if shutil.which("docker") is not None:
            return "docker"
        return "docker"

    def available(self) -> bool:
        return shutil.which(self._runtime) is not None

    def pull_image(self, image: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self._runtime, "pull", image],
            capture_output=True,
            text=True,
            timeout=self.config.timeout,
        )

    def execute(
        self,
        command: str,
        *,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> SandboxResult:
        limits = self.config.to_resource_limits()

        try:
            result = self._run_container(
                command=command,
                image=self.config.image_path,
                workdir=workdir or "/workspace",
                env=env,
                memory_bytes=limits.memory_bytes,
                cpu_shares=(min(limits.cpu_shares or 1024, 1024) if limits.cpu_shares is not None else None),
                pids_limit=limits.pids_limit,
                allow_network=self.config.allow_network,
                max_output_bytes=self.config.max_output_bytes,
            )
            return result
        except subprocess.TimeoutExpired:
            return SandboxResult(
                returncode=-1,
                stdout="",
                stderr="container execution timed out",
                was_killed=True,
            )
        except FileNotFoundError:
            return SandboxResult(
                returncode=127,
                stdout="",
                stderr=f"{self._runtime} runtime not found",
                was_killed=False,
            )

    def _run_container(
        self,
        *,
        command: str,
        image: str,
        workdir: str,
        env: dict[str, str] | None,
        memory_bytes: int | None,
        cpu_shares: int | None,
        pids_limit: int | None,
        allow_network: bool,
        max_output_bytes: int,
    ) -> SandboxResult:
        cmd: list[str] = [self._runtime, "run", "--rm"]

        cmd.extend(["--workdir", workdir])

        if not allow_network:
            cmd.extend(["--network", "none"])

        if memory_bytes is not None and memory_bytes > 0:
            cmd.extend(["--memory", str(memory_bytes)])

        if cpu_shares is not None:
            cmd.extend(["--cpu-shares", str(cpu_shares)])

        if pids_limit is not None and pids_limit > 0:
            cmd.extend(["--pids-limit", str(pids_limit)])

        if env:
            for key, value in env.items():
                cmd.extend(["-e", f"{key}={value}"])

        cmd.append(image)
        cmd.extend(["sh", "-c", command])

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.config.timeout,
        )

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        if max_output_bytes > 0:
            if len(stdout) > max_output_bytes:
                stdout = stdout[:max_output_bytes]
            if len(stderr) > max_output_bytes:
                stderr = stderr[:max_output_bytes]

        return SandboxResult(
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            was_killed=False,
        )

    def cleanup(self) -> None:
        import contextlib

        for cid in self._container_ids:
            with contextlib.suppress(Exception):
                subprocess.run(
                    [self._runtime, "rm", "-f", cid],
                    capture_output=True,
                    timeout=30,
                )
        self._container_ids.clear()
