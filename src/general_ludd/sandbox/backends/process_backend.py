"""Subprocess-based sandbox backend implementing ``SandboxBackend``."""

from __future__ import annotations

import contextlib
import os
import resource
import subprocess
import sys
from typing import Any

from general_ludd.sandbox.contracts import SandboxConfig, SandboxResult


class ProcessBackend:
    """Execute commands as local subprocesses with resource limits.

    Implements the :class:`SandboxBackend` Protocol using ``subprocess.Popen``
    with optional rlimit-based resource enforcement. This backend is always
    available (no external dependencies) and provides ``IsolationLevel.PROCESS``
    isolation strength.
    """

    name: str = "process"

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config

    def available(self) -> bool:
        return True

    def execute(
        self,
        command: str,
        *,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> SandboxResult:
        limits = self.config.to_resource_limits()
        memory_bytes = limits.memory_bytes
        cpu_seconds = self.config.cpu_seconds
        pids_limit = limits.pids_limit

        def _preexec() -> None:
            if memory_bytes is not None and memory_bytes > 0:
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            if cpu_seconds is not None and cpu_seconds > 0:
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            if pids_limit is not None and pids_limit > 0:
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(resource.RLIMIT_NPROC, (pids_limit, pids_limit))

        merged_env = {**os.environ, **(env or {})}

        try:
            proc = subprocess.Popen(
                command,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=True,
                preexec_fn=_preexec,
                env=merged_env,
            )
        except FileNotFoundError:
            return SandboxResult(
                returncode=127,
                stdout="",
                stderr=f"command not found: {command.split()[0] if command else ''}",
                was_killed=False,
            )

        try:
            stdout, stderr = proc.communicate(timeout=self.config.timeout)
            was_killed = False
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                stdout = ""
                stderr = ""
            was_killed = True

        stdout = stdout or ""
        stderr = stderr or ""

        max_bytes = self.config.max_output_bytes
        if max_bytes > 0:
            if len(stdout) > max_bytes:
                stdout = stdout[:max_bytes]
            if len(stderr) > max_bytes:
                stderr = stderr[:max_bytes]
            total = len(stdout) + len(stderr)
            if total > max_bytes:
                excess = total - max_bytes
                if len(stdout) > len(stderr):
                    stdout = stdout[: max(len(stdout) - excess, 0)]
                else:
                    stderr = stderr[: max(len(stderr) - excess, 0)]

        returncode = proc.returncode if proc.returncode is not None else -1

        try:
            usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu_time_ms = int((usage.ru_utime + usage.ru_stime) * 1000)
            memory_used_bytes = usage.ru_maxrss
            if sys.platform == "darwin":
                memory_used_bytes = memory_used_bytes
            else:
                memory_used_bytes = memory_used_bytes * 1024
        except Exception:
            cpu_time_ms = 0
            memory_used_bytes = 0

        return SandboxResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            pid=proc.pid or 0,
            was_killed=was_killed,
            cpu_time_ms=cpu_time_ms,
            memory_used_bytes=memory_used_bytes,
        )

    def cleanup(self) -> None:
        pass
