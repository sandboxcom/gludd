"""Structured subprocess runner for ``make`` targets.

Provides a single abstraction over raw ``["make", ...]`` calls found in
``background_test_runner``, ``execution.engine``, and ``git_automation.repo``.
Every invocation goes through ``subprocess.Popen`` with a minimal sanitized
environment, bounded output capture, and a per-target timeout.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

_TAIL_CHARS = 16000
_DEFAULT_TIMEOUT_S = 300
_READ_CHUNK = 8192
_FORBIDDEN_METACHARS: frozenset[str] = frozenset(
    {";", "|", "&", "$", "`", "(", ")", "{", "}", "<", ">", "!", "\\", "'", '"', "\t", "\n", "\r"}
)
_BASE_ENV_KEYS = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP",
)
_FALLBACK_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

_PHASE_RE = re.compile(r"=== (\w[\w.\- ]+\w) ===")


class _BoundedReader(threading.Thread):
    def __init__(self, stream: IO[str] | None, cap: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._cap = cap
        self._chunks: deque[str] = deque()
        self._size = 0
        self._lock = threading.Lock()

    def run(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            for chunk in iter(lambda: stream.read(_READ_CHUNK), ""):
                if not chunk:
                    break
                with self._lock:
                    self._chunks.append(chunk)
                    self._size += len(chunk)
                    while self._size > self._cap and len(self._chunks) > 1:
                        self._size -= len(self._chunks.popleft())
        except (ValueError, OSError):
            pass

    @property
    def text(self) -> str:
        with self._lock:
            return "".join(self._chunks)[-self._cap:]


@dataclass
class MakeResult:
    """Outcome of one bounded ``make`` subprocess invocation."""

    target: str
    exit_code: int | None
    success: bool
    duration_s: float
    stdout_tail: str = ""
    stderr_tail: str = ""
    timed_out: bool = False
    oom_killed: bool = False
    error: str | None = None
    phases: list[str] = field(default_factory=list)


class MakeRunner:
    """Execute ``make`` targets with bounded output, time, and environment."""

    def __init__(
        self,
        cwd: str | Path | None = None,
        default_timeout_s: int = _DEFAULT_TIMEOUT_S,
    ) -> None:
        """Initialize a runner rooted at ``cwd`` with a bounded default timeout."""
        self._cwd = Path(cwd).resolve() if cwd else Path.cwd()
        self._default_timeout_s = default_timeout_s

    def _build_env(self) -> dict[str, str]:
        parent = os.environ
        env: dict[str, str] = {}
        for key in _BASE_ENV_KEYS:
            val = parent.get(key)
            if val is not None:
                env[key] = val
        env.setdefault("PATH", _FALLBACK_PATH)
        return env

    def _sanitize_args(self, args: list[str]) -> list[str]:
        for arg in args:
            if set(arg) & _FORBIDDEN_METACHARS:
                raise ValueError(f"Argument contains shell metacharacters: {arg!r}")
        return args

    def spawn(
        self,
        target: str,
        *,
        extra_args: list[str] | None = None,
        log_file: str | Path | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> tuple[subprocess.Popen[str], Path | None]:
        """Spawn a make subprocess and return immediately (non-blocking).

        Returns the Popen handle and the log file path (if log_file was
        provided). Used by BackgroundTestRunner to launch background tests.
        """
        extra_args = self._sanitize_args(extra_args or [])
        cmd = ["make", target, *extra_args]
        env = self._build_env()
        if env_extra:
            env.update(env_extra)

        log_path = Path(log_file) if log_file else None

        try:
            if log_path:
                with open(log_path, "w", encoding="utf-8") as fh:
                    proc = subprocess.Popen(
                        cmd,
                        cwd=str(self._cwd),
                        env=env,
                        stdout=fh,
                        stderr=subprocess.STDOUT,
                        text=True,
                        close_fds=True,
                    )
            else:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(self._cwd),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
        except FileNotFoundError:
            raise RuntimeError("make executable not found") from None
        except OSError as exc:
            raise RuntimeError(f"spawn failed: {exc}") from exc

        return proc, log_path

    def run(
        self,
        target: str,
        *,
        extra_args: list[str] | None = None,
        timeout_s: int | None = None,
        env_extra: dict[str, str] | None = None,
        stream: bool = False,
        stream_callback: Callable[[str], None] | None = None,
    ) -> MakeResult:
        """Run one target synchronously and return its bounded structured result."""
        extra_args = self._sanitize_args(extra_args or [])
        cmd = ["make", target, *extra_args]
        timeout = timeout_s if timeout_s is not None else self._default_timeout_s
        start = time.monotonic()
        env = self._build_env()
        if env_extra:
            env.update(env_extra)

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self._cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError:
            return MakeResult(
                target=target,
                exit_code=None,
                success=False,
                duration_s=time.monotonic() - start,
                error="make executable not found",
            )
        except OSError as exc:
            return MakeResult(
                target=target,
                exit_code=None,
                success=False,
                duration_s=time.monotonic() - start,
                error=f"spawn failed: {exc}",
            )

        try:
            if stream and stream_callback:
                return self._run_stream(
                    proc,
                    cmd,
                    target,
                    start,
                    timeout,
                    stream_callback,
                )

            out_reader = _BoundedReader(proc.stdout, _TAIL_CHARS)
            err_reader = _BoundedReader(proc.stderr, _TAIL_CHARS)
            out_reader.start()
            err_reader.start()

            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill_group(proc)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=10)

            out_reader.join(timeout=10)
            err_reader.join(timeout=10)

            exit_code = proc.returncode
            duration = time.monotonic() - start
            oom_killed = (not timed_out) and exit_code in (-9, 137)
            success = (not timed_out) and not oom_killed and exit_code == 0
            stdout_tail = out_reader.text
            stderr_tail = err_reader.text
            phases = self._extract_phases(stdout_tail)

            return MakeResult(
                target=target,
                exit_code=exit_code,
                success=success,
                duration_s=duration,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
                timed_out=timed_out,
                oom_killed=oom_killed,
                phases=phases,
            )
        finally:
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    with contextlib.suppress(OSError):
                        pipe.close()

    def _run_stream(
        self,
        proc: subprocess.Popen[str],
        cmd: list[str],
        target: str,
        start: float,
        timeout: int,
        callback: Callable[[str], None],
    ) -> MakeResult:
        logger.debug("Running %s (streaming, timeout=%ds)", " ".join(cmd), timeout)
        phase_text = _BoundedReader(proc.stdout, _TAIL_CHARS)
        phase_text.start()
        phases: list[str] = []
        deadline = start + timeout

        while proc.poll() is None:
            if time.monotonic() > deadline:
                self._kill_group(proc)
                phase_text.join(timeout=10)
                duration = time.monotonic() - start
                return MakeResult(
                    target=target,
                    exit_code=proc.returncode,
                    success=False,
                    duration_s=duration,
                    stdout_tail=phase_text.text,
                    timed_out=True,
                    phases=phases,
                )
            new_phases = self._extract_phases_from_latest(phase_text.text)
            for phase in new_phases:
                if phase not in phases:
                    phases.append(phase)
                    callback(phase)
            time.sleep(0.25)

        phase_text.join(timeout=10)
        exit_code = proc.returncode
        duration = time.monotonic() - start
        success = exit_code == 0
        stdout_tail = phase_text.text
        phases_all = self._extract_phases(stdout_tail)
        callback("=== COMPLETE ===")
        return MakeResult(
            target=target,
            exit_code=exit_code,
            success=success,
            duration_s=duration,
            stdout_tail=stdout_tail,
            phases=phases_all,
        )

    def run_test(
        self,
        testfile: str | None = None,
        *,
        timeout_s: int | None = None,
    ) -> MakeResult:
        """Run the test target, optionally restricted to one test file."""
        if testfile:
            return self.run("test", extra_args=[f"TESTFILE={testfile}"], timeout_s=timeout_s)
        return self.run("test", timeout_s=timeout_s)

    def run_gate(self, *, timeout_s: int | None = None) -> MakeResult:
        """Run the project gate with its release-safe default timeout."""
        return self.run("gate", timeout_s=timeout_s or 3600)

    def run_lint(self, *, timeout_s: int | None = None) -> MakeResult:
        """Run the project lint target."""
        return self.run("lint", timeout_s=timeout_s or 180)

    def run_typecheck(self, *, timeout_s: int | None = None) -> MakeResult:
        """Run the project type-check target."""
        return self.run("typecheck", timeout_s=timeout_s or 300)

    def run_specific(self, testfile: str, *, timeout_s: int | None = None) -> MakeResult:
        """Run one explicit test node through the project test wrapper."""
        return self.run("test-specific", extra_args=[f"TESTFILE={testfile}"], timeout_s=timeout_s)

    @staticmethod
    def _extract_phases(text: str) -> list[str]:
        return _PHASE_RE.findall(text)

    @staticmethod
    def _extract_phases_from_latest(text: str) -> list[str]:
        return _PHASE_RE.findall(text)

    @staticmethod
    def _kill_group(proc: subprocess.Popen[str]) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except (ProcessLookupError, PermissionError, OSError):
                return
            if sig is signal.SIGTERM:
                try:
                    proc.wait(timeout=5)
                    return
                except subprocess.TimeoutExpired:
                    continue
