"""Execute a target project's declared check in a jailed workspace.

Runs the argv resolved by :class:`~general_ludd.project_runner.profile.ProjectProfile`
inside the target repo, with the same hardening gludd uses for its own test
runs (``execution.engine._run_tests``): a new process group (``start_new_session``)
so a hung ``npm``/``terraform`` and all its children are killed on timeout, and
bounded stdout/stderr tail capture so a chatty build can't blow memory.

Two additional guards vs. a naive runner:

- **Sanitized environment** — the child does NOT inherit gludd's full
  ``os.environ`` (which holds secrets like ``ZAI_API_KEY``). It gets a minimal
  base (``PATH``/``HOME``/locale/temp) plus only the names the profile's
  ``env_passthrough`` allowlist opts in (see :func:`_build_env`).
- **Bounded capture** — stdout/stderr are drained by reader threads that keep
  only the last ``_TAIL_CHARS`` of each stream, so a chatty build is bounded in
  RAM instead of buffering the whole log via ``communicate()``.

No ``shell=True`` — argv is a validated list; the workspace is realpath-contained.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from general_ludd.project_runner.profile import ProjectProfile, ProjectProfileError
from general_ludd.system.rlimit import apply_limits

# Keep only the last N chars of each stream so a huge build log can't exhaust
# memory or flood the model context.
_TAIL_CHARS = 8000
_DEFAULT_TIMEOUT_S = 900  # terraform/npm builds routinely exceed the 120s test bound
_READ_CHUNK = 8192  # bytes/chars per pipe read in the bounded reader

# Environment variables always forwarded to a target command so executables
# resolve and locale/temp behave — but NOT gludd's secrets. Everything beyond
# this base must be explicitly opted in via ProjectProfile.env_passthrough.
_BASE_ENV_KEYS = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP",
)
# Fallback so executables still resolve even if the parent env somehow has no PATH.
_FALLBACK_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def _build_env(passthrough: Sequence[str]) -> dict[str, str]:
    """Minimal, sanitized environment for a target-project subprocess.

    Only the sanitized base (``_BASE_ENV_KEYS``) plus the profile's explicit
    ``passthrough`` allowlist cross into the child; gludd's own secrets (API
    keys, tokens) are NOT inherited. ``PATH`` is always present so executables
    resolve.
    """
    parent = os.environ
    env: dict[str, str] = {}
    for key in (*_BASE_ENV_KEYS, *passthrough):
        val = parent.get(key)
        if val is not None:
            env[key] = val
    env.setdefault("PATH", _FALLBACK_PATH)
    return env


class _BoundedReader(threading.Thread):
    """Drain a text pipe in a daemon thread, keeping only the last ``cap`` chars.

    Reading concurrently on its own thread (one per stream) avoids the classic
    two-pipe deadlock without buffering the whole log: memory stays bounded to
    ``cap`` plus one in-flight chunk.
    """

    def __init__(self, stream: IO[str] | None, cap: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._cap = cap
        self._chunks: deque[str] = deque()
        self._size = 0
        # Guards _chunks so .text can snapshot safely even if this reader is
        # still running (e.g. a join() that timed out on an escaped grandchild).
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
        except (ValueError, OSError):  # pipe closed/killed mid-read
            pass

    @property
    def text(self) -> str:
        with self._lock:
            return "".join(self._chunks)[-self._cap:]


@dataclass
class CheckResult:
    """Structured outcome of running one project check."""

    name: str
    exit_code: int | None
    passed: bool
    duration_s: float
    stdout_tail: str = ""
    stderr_tail: str = ""
    timed_out: bool = False
    oom_killed: bool = False
    error: str | None = None
    findings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.error:
            return f"{self.name}: ERROR — {self.error}"
        if self.timed_out:
            return f"{self.name}: TIMED OUT after {self.duration_s:.0f}s"
        if self.oom_killed:
            return f"{self.name}: OOM-KILLED (exit {self.exit_code}, {self.duration_s:.1f}s)"
        state = "PASS" if self.passed else "FAIL"
        return f"{self.name}: {state} (exit {self.exit_code}, {self.duration_s:.1f}s)"


class ProjectCommandRunner:
    """Runs named checks from a :class:`ProjectProfile` in a target workspace."""

    def __init__(
        self,
        workspace: str | Path,
        profile: ProjectProfile,
        *,
        default_timeout_s: int = _DEFAULT_TIMEOUT_S,
        memory_limit_mb: int | None = None,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        if not self._workspace.is_dir():
            raise ProjectProfileError(f"workspace {self._workspace} is not a directory")
        self._profile = profile
        self._default_timeout_s = default_timeout_s
        # Optional RLIMIT_AS cap (MiB) applied in the child before exec so a
        # heavy target test suite is bounded + OOM-killed inside its own process
        # rather than taking down gludd's host. POSIX-only (no-op elsewhere).
        self._memory_limit_mb = memory_limit_mb
        # Sanitized, minimal env — built once from the profile's allowlist so the
        # target's commands never inherit gludd's secrets (ZAI_API_KEY, tokens…).
        self._env = _build_env(profile.env_passthrough)

    @property
    def workspace(self) -> Path:
        return self._workspace

    def run(self, check: str, *, timeout_s: int | None = None) -> CheckResult:
        """Run the ``check`` command; never raises for a check failure/timeout —
        the outcome is always a :class:`CheckResult` (raises only for an
        undeclared/unsafe command, i.e. a configuration error)."""
        argv = self._profile.resolve_argv(check)  # ProjectProfileError on bad config
        timeout = timeout_s if timeout_s is not None else self._default_timeout_s
        start = time.monotonic()
        # RLIMIT_AS cap applied in the child (post-fork, pre-exec) so a heavy
        # target command is bounded inside its own address space rather than
        # taking down gludd's host. POSIX-only (preexec_fn is unavailable on
        # Windows); None ⇒ no cap.
        preexec: Callable[[], None] | None = None
        if self._memory_limit_mb is not None and os.name == "posix":
            _mb = self._memory_limit_mb

            def _apply_mem_limit() -> None:
                apply_limits(_mb, 0)

            preexec = _apply_mem_limit
        try:
            proc = subprocess.Popen(  # argv is validated + allow-listed, no shell
                argv,
                cwd=str(self._workspace),
                env=self._env,  # sanitized minimal env — no gludd secrets leak in
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,  # own process group → killpg reaps children
                preexec_fn=preexec,  # RLIMIT_AS cap before exec (None ⇒ no-op)
            )
        except FileNotFoundError:
            return CheckResult(
                name=check,
                exit_code=None,
                passed=False,
                duration_s=time.monotonic() - start,
                error=f"executable not found: {argv[0]!r} (is it installed in the workspace?)",
            )
        except OSError as exc:
            return CheckResult(
                name=check, exit_code=None, passed=False,
                duration_s=time.monotonic() - start, error=f"spawn failed: {exc}",
            )

        # Drain both pipes on their own threads (bounded to _TAIL_CHARS each) so a
        # chatty build can't buffer its whole log in RAM, and so a killed child
        # holding a pipe open can't wedge us.
        out_reader = _BoundedReader(proc.stdout, _TAIL_CHARS)
        err_reader = _BoundedReader(proc.stderr, _TAIL_CHARS)
        out_reader.start()
        err_reader.start()

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
            # escaped grandchild; readers still drain on EOF/join below
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
        # Readers finish once their pipes hit EOF (on exit or after the kill).
        out_reader.join(timeout=10)
        err_reader.join(timeout=10)
        # Only close a pipe whose reader has finished. If a reader is still
        # blocked in read() (an escaped grandchild is holding the pipe open),
        # close() would contend on the same buffer lock and re-hang us — so we
        # leave it to the daemon reader + eventual process death in that rare
        # case rather than block the caller.
        if not out_reader.is_alive():
            _close(proc.stdout)
        if not err_reader.is_alive():
            _close(proc.stderr)

        exit_code = proc.returncode
        duration = time.monotonic() - start
        # A memory-capped child that busts RLIMIT_AS (or the host OOM-killer) is
        # SIGKILL'd → returncode -9 (Popen) or 137 (128+9). Surface it distinctly
        # from a plain non-zero failure so callers can retry/degrade.
        oom_killed = (not timed_out) and exit_code in (-9, 137)
        passed = (not timed_out) and not oom_killed and exit_code == 0
        return CheckResult(
            name=check,
            exit_code=exit_code,
            passed=passed,
            duration_s=duration,
            stdout_tail=out_reader.text,
            stderr_tail=err_reader.text,
            timed_out=timed_out,
            oom_killed=oom_killed,
        )


def _close(stream: IO[str] | None) -> None:
    """Close a pipe end, ignoring already-closed/errored streams (no FD leak)."""
    if stream is None:
        return
    with contextlib.suppress(ValueError, OSError):
        stream.close()


def _kill_group(proc: subprocess.Popen[str]) -> None:
    """SIGTERM then SIGKILL the child's whole process group (best-effort)."""
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
