"""Execute commands as local subprocesses with resource limits."""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass


@dataclass
class ProcessLimits:
    memory_mb: int | None = None
    cpu_seconds: int | None = None
    max_file_size: int | None = None
    max_open_files: int | None = None
    max_processes: int | None = None


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    pid: int = 0
    was_killed: bool = False


class ProcessExecutor:
    def __init__(self, timeout: int = 300, max_output_bytes: int = 1_000_000) -> None:
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes

    def execute(
        self, command: str, workdir: str | None = None,
        limits: ProcessLimits | None = None, env: dict[str, str] | None = None,
    ) -> ProcessResult:
        preexec_fn = None
        if limits is not None:
            def preexec_fn() -> None:
                self._apply_limits(limits)
        proc = subprocess.Popen(
            shlex.split(command), cwd=workdir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            preexec_fn=preexec_fn, env={**os.environ, **(env or {})},
        )
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout)
            was_killed = False
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                return ProcessResult(returncode=-1, stdout="", stderr="", pid=proc.pid or 0, was_killed=True)
            was_killed = True
        return ProcessResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout or "", stderr=stderr or "",
            pid=proc.pid or 0, was_killed=was_killed,
        )

    @staticmethod
    def _apply_limits(limits: ProcessLimits) -> None:
        import resource

        def _set_limit(resource_name: str, value: tuple[int, int]) -> None:
            resource_id = getattr(resource, resource_name, None)
            if resource_id is None:
                return
            try:
                resource.setrlimit(resource_id, value)
            except (OSError, ValueError):
                return

        if limits.memory_mb is not None:
            memory_bytes = limits.memory_mb * 1024 * 1024
            _set_limit("RLIMIT_AS", (memory_bytes, memory_bytes))
        if limits.cpu_seconds is not None:
            _set_limit("RLIMIT_CPU", (limits.cpu_seconds, limits.cpu_seconds))
        if limits.max_file_size is not None:
            size = limits.max_file_size
            _set_limit("RLIMIT_FSIZE", (size, size))
        if limits.max_open_files is not None:
            open_files = limits.max_open_files
            _set_limit("RLIMIT_NOFILE", (open_files, open_files))
        if limits.max_processes is not None:
            processes = limits.max_processes
            _set_limit("RLIMIT_NPROC", (processes, processes))
