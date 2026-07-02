"""Execute a target project's declared check in a jailed workspace.

Runs the argv resolved by :class:`~general_ludd.project_runner.profile.ProjectProfile`
inside the target repo, with the same hardening gludd uses for its own test
runs (``execution.engine._run_tests``): a new process group (``start_new_session``)
so a hung ``npm``/``terraform`` and all its children are killed on timeout, and
bounded stdout/stderr tail capture so a chatty build can't blow memory.

No ``shell=True`` — argv is a validated list; the workspace is realpath-contained.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from general_ludd.project_runner.profile import ProjectProfile, ProjectProfileError

# Keep only the last N chars of each stream so a huge build log can't exhaust
# memory or flood the model context.
_TAIL_CHARS = 8000
_DEFAULT_TIMEOUT_S = 900  # terraform/npm builds routinely exceed the 120s test bound


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
    error: str | None = None
    findings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.error:
            return f"{self.name}: ERROR — {self.error}"
        if self.timed_out:
            return f"{self.name}: TIMED OUT after {self.duration_s:.0f}s"
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
    ) -> None:
        self._workspace = Path(workspace).resolve()
        if not self._workspace.is_dir():
            raise ProjectProfileError(f"workspace {self._workspace} is not a directory")
        self._profile = profile
        self._default_timeout_s = default_timeout_s

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
        try:
            proc = subprocess.Popen(  # argv is validated + allow-listed, no shell
                argv,
                cwd=str(self._workspace),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,  # own process group → killpg reaps children
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

        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
            try:
                out, err = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                out, err = "", ""
        exit_code = proc.returncode
        duration = time.monotonic() - start
        passed = (not timed_out) and exit_code == 0
        return CheckResult(
            name=check,
            exit_code=exit_code,
            passed=passed,
            duration_s=duration,
            stdout_tail=(out or "")[-_TAIL_CHARS:],
            stderr_tail=(err or "")[-_TAIL_CHARS:],
            timed_out=timed_out,
        )


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
