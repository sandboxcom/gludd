"""Subprocess-based sandbox backend implementing ``SandboxBackend``."""

from __future__ import annotations

import contextlib
import os
import resource
import signal
import subprocess
import sys
from typing import Any

from general_ludd.sandbox.contracts import SandboxConfig, SandboxResult


def _linux_user_task_count() -> int:
    """Return the current real-UID task count used by Linux ``RLIMIT_NPROC``."""
    if not sys.platform.startswith("linux") or not hasattr(os, "getuid"):
        return 0
    real_uid = os.getuid()
    count = 0
    try:
        with os.scandir("/proc") as processes:
            for process in processes:
                if not process.name.isdecimal():
                    continue
                try:
                    if process.stat(follow_symlinks=False).st_uid != real_uid:
                        continue
                    with os.scandir(f"{process.path}/task") as tasks:
                        count += sum(task.name.isdecimal() for task in tasks)
                except OSError:
                    # Processes can exit while /proc is traversed. Their tasks no
                    # longer count toward the limit, so absence is safe to ignore.
                    continue
    except OSError:
        return 0
    return count


def _nproc_soft_limit(
    *,
    requested_processes: int,
    existing_user_tasks: int,
    hard_limit: int,
) -> int:
    """Translate a sandbox-local process budget to the UID-global rlimit."""
    desired = max(existing_user_tasks, 0) + max(requested_processes, 1)
    if hard_limit >= 0:
        return min(desired, hard_limit)
    return desired


def _verified_child_process_group(proc: subprocess.Popen[str]) -> int | None:
    """Return the child's isolated process group, never the caller's group."""
    pid = proc.pid
    if os.name != "posix" or pid is None or pid <= 0:
        return None
    try:
        child_pgid = os.getpgid(pid)
        child_sid = os.getsid(pid)
        caller_pgid = os.getpgrp()
        caller_sid = os.getsid(0)
    except OSError:
        return None
    if child_pgid != pid or child_sid != pid:
        return None
    if child_pgid == caller_pgid or child_sid == caller_sid:
        return None
    return child_pgid


def _terminate_owned_process(proc: subprocess.Popen[str]) -> None:
    """Idempotently terminate only the process or verified group this owner spawned."""
    if proc.returncode is not None:
        return
    if proc.__dict__.get("_gludd_termination_requested", False) is True:
        return
    proc.__dict__["_gludd_termination_requested"] = True

    child_pgid = _verified_child_process_group(proc)
    if child_pgid is not None:
        try:
            os.killpg(child_pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        return

    try:
        proc.kill()
    except ProcessLookupError:
        return


def _reap_owned_process(proc: subprocess.Popen[str]) -> tuple[str, str]:
    """Bound the post-termination wait while reaping the direct child."""
    try:
        return proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        return "", ""


class ProcessBackend:
    """Execute commands as local subprocesses with resource limits.

    Implements the :class:`SandboxBackend` Protocol using ``subprocess.Popen``
    with optional rlimit-based resource enforcement. This backend is always
    available (no external dependencies) and provides ``IsolationLevel.PROCESS``
    isolation strength.
    """

    name: str = "process"

    def __init__(self, config: SandboxConfig) -> None:
        """Initialize the backend with its sandbox resource policy."""
        self.config = config

    def available(self) -> bool:
        """Return whether local subprocess execution is available."""
        return True

    def execute(
        self,
        command: str,
        *,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> SandboxResult:
        """Execute a command within the configured local resource limits."""
        limits = self.config.to_resource_limits()
        memory_bytes = limits.memory_bytes
        cpu_seconds = self.config.cpu_seconds
        pids_limit = limits.pids_limit
        nproc_soft_limit: int | None = None
        nproc_hard_limit: int | None = None
        if pids_limit is not None and pids_limit > 0:
            _current_soft, nproc_hard_limit = resource.getrlimit(resource.RLIMIT_NPROC)
            nproc_soft_limit = _nproc_soft_limit(
                requested_processes=pids_limit,
                existing_user_tasks=_linux_user_task_count(),
                hard_limit=nproc_hard_limit,
            )

        def _preexec() -> None:
            if memory_bytes is not None and memory_bytes > 0:
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            if cpu_seconds is not None and cpu_seconds > 0:
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            if nproc_soft_limit is not None and nproc_hard_limit is not None:
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(
                        resource.RLIMIT_NPROC,
                        (nproc_soft_limit, nproc_hard_limit),
                    )

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
                start_new_session=os.name == "posix",
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
            _terminate_owned_process(proc)
            stdout, stderr = _reap_owned_process(proc)
            was_killed = True
        except BaseException as cancellation:
            try:
                _terminate_owned_process(proc)
                _reap_owned_process(proc)
            except Exception as cleanup_error:
                cancellation.add_note(
                    f"owned process cleanup failed: {type(cleanup_error).__name__}: "
                    f"{cleanup_error}"
                )
            raise

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
        """Return after confirming ``execute`` completed owned-process cleanup."""
        pass
