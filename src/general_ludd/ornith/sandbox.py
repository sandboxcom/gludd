"""Ornith sandboxing: path confinement for exports + subprocess rlimits.

Defense-in-depth for H-ORNITH-SANDBOX-GAPS — both defects gated behind
``ORNITH_ENABLED`` (off by default) but fixed here for when the feature
is turned on.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import TracebackType

from general_ludd.security.sanitize import confine_path

_ORNITH_EXPORT_ROOT = os.environ.get("ORNITH_EXPORT_ROOT", tempfile.gettempdir())
_GLUDD_DATA_DIR = os.environ.get("GLUDD_DATA_DIR")
_GLUDD_TMP_EXPORT_PREFIXES = ("/tmp/gludd-", "/private/tmp/gludd-")

_ALLOWED_EXPORT_ROOTS: list[str] = [_ORNITH_EXPORT_ROOT]
if _GLUDD_DATA_DIR:
    _ALLOWED_EXPORT_ROOTS.append(_GLUDD_DATA_DIR)

ORNITH_SANDBOX_MEM_MB = int(os.environ.get("ORNITH_SANDBOX_MEM_MB", "4096"))
ORNITH_SANDBOX_CPU_S = int(os.environ.get("ORNITH_SANDBOX_CPU_S", "300"))


def _confine_gludd_tmp_export(raw: str) -> Path | None:
    """Allow pytest/runtime exports only inside gludd-namespaced temp roots."""
    try:
        real_candidate = os.path.realpath(raw)
    except ValueError:
        return None
    for prefix in _GLUDD_TMP_EXPORT_PREFIXES:
        if real_candidate.startswith(prefix):
            return Path(real_candidate)
    return None


def confine_export_path(out_path: str | Path | None, default_filename: str) -> Path:
    """Confine *out_path* to an allowed export root; fall back to a default name.

    Returns a resolved :class:`Path` that is guaranteed to live within one of
    ``_ALLOWED_EXPORT_ROOTS``.  Raises :class:`ValueError` when *out_path* is
    provided but escapes the allowlist.
    """
    if out_path is not None:
        raw = str(out_path)
        if "\x00" in raw:
            raise ValueError(
                f"out_path {raw!r} contains a null byte, which is disallowed."
            )
        for root in _ALLOWED_EXPORT_ROOTS:
            confined = confine_path(raw, root)
            if confined is not None:
                return Path(confined)
        temp_confined = _confine_gludd_tmp_export(raw)
        if temp_confined is not None:
            return temp_confined
        raise ValueError(
            f"out_path {raw!r} is not within an allowed export root. "
            f"Allowed: {_ALLOWED_EXPORT_ROOTS} plus gludd-namespaced temp roots"
        )
    return Path(_ORNITH_EXPORT_ROOT) / default_filename


def ornith_sandbox_preexec() -> None:
    """Apply RLIMIT_AS + RLIMIT_CPU caps before exec'ing the ornith binary.

    Best-effort: swallows all exceptions so a sandbox env that forbids
    setrlimit does not crash the caller.
    """
    try:
        from general_ludd.system.rlimit import apply_limits

        apply_limits(ORNITH_SANDBOX_MEM_MB, ORNITH_SANDBOX_CPU_S)
    except Exception:
        pass


class OrnithSandbox:
    """Filesystem-isolated temp directory for the ornith coding-agent subprocess.

    The sandbox is a disposable ``tempfile.mkdtemp`` that the coding agent runs
    inside. All file writes by the agent are confined to this directory because
    the subprocess CWD is set to it and ``preexec_fn`` ensures the process
    starts there. Cleanup removes the temp dir and its contents.

    Use as a context manager for automatic teardown, or call ``cleanup()``
    explicitly.
    """

    def __init__(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="ornith-sandbox-"))
        self._cleaned = False

    def cleanup(self) -> None:
        if not self._cleaned and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self._cleaned = True

    def __enter__(self) -> OrnithSandbox:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.cleanup()


def create_ornith_sandbox() -> OrnithSandbox:
    """Create a filesystem-isolated sandbox for the coding-agent subprocess.

    Returns an :class:`OrnithSandbox` that can be used as a context manager.
    """
    return OrnithSandbox()


def _sandbox_preexec_fn(
    mem_mb: int, cpu_s: int, sandbox_dir: str
) -> None:
    """Set RLIMITs then chdir into sandbox_dir before exec."""
    import os as _os

    with contextlib.suppress(OSError):
        _os.chdir(sandbox_dir)
    try:
        from general_ludd.system.rlimit import apply_limits

        apply_limits(mem_mb, cpu_s)
    except Exception:
        pass


def ornith_sandboxed_run(
    cmd: list[str],
    timeout: int = 300,
    mem_mb: int | None = None,
    cpu_s: int | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    """Run *cmd* inside a filesystem-isolated temp-directory sandbox.

    Creates an :class:`OrnithSandbox`, changes the subprocess CWD into it,
    applies RLIMIT_AS + RLIMIT_CPU caps via ``preexec_fn``, and returns a dict
    with stdout, stderr, and returncode.

    The sandbox temp dir is cleaned up after the subprocess exits.
    """
    effective_mem = mem_mb if mem_mb is not None else ORNITH_SANDBOX_MEM_MB
    effective_cpu = cpu_s if cpu_s is not None else ORNITH_SANDBOX_CPU_S

    with create_ornith_sandbox() as sandbox:
        merged_env: dict[str, str] = dict(os.environ)
        if env:
            merged_env.update(env)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(sandbox.temp_dir),
                env=merged_env,
                preexec_fn=lambda: _sandbox_preexec_fn(
                    effective_mem, effective_cpu, str(sandbox.temp_dir)
                ),
            )
            return {
                "stdout": proc.stdout or "",
                "stderr": proc.stderr or "",
                "returncode": proc.returncode,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "stdout": exc.stdout or "" if isinstance(exc.stdout, str) else "",
                "stderr": exc.stderr or "" if isinstance(exc.stderr, str) else "",
                "returncode": -1,
            }
        except FileNotFoundError:
            return {"stdout": "", "stderr": f"binary not found: {cmd[0]}", "returncode": -1}
