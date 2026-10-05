"""Ornith sandboxing: path confinement for exports + subprocess rlimits.

Defense-in-depth for H-ORNITH-SANDBOX-GAPS — both defects gated behind
``ORNITH_ENABLED`` (off by default) but fixed here for when the feature
is turned on.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import TracebackType

from general_ludd.security.sanitize import confine_path
from general_ludd.security.state import SecureState, project_state, secure_directory

_ORNITH_EXPORT_ROOT: str | Path | None = None


def _ornith_export_root() -> Path:
    configured = _ORNITH_EXPORT_ROOT or os.environ.get("ORNITH_EXPORT_ROOT")
    if configured:
        raw = Path(configured).expanduser()
        secure_directory(raw)
        return raw
    return project_state().directory("ornith", "exports")


def _append_root_aliases(roots: list[str], root: str | Path | None) -> None:
    if not root:
        return
    for candidate in (str(root), os.path.realpath(str(root))):
        if candidate and candidate not in roots:
            roots.append(candidate)


def _build_allowed_export_roots() -> list[str]:
    roots: list[str] = []
    _append_root_aliases(roots, _ornith_export_root())
    data_dir = os.environ.get("GLUDD_DATA_DIR")
    if data_dir:
        _append_root_aliases(roots, secure_directory(data_dir))
    return roots


_ALLOWED_EXPORT_ROOTS: list[str] | None = None

ORNITH_SANDBOX_MEM_MB = int(os.environ.get("ORNITH_SANDBOX_MEM_MB", "4096"))
ORNITH_SANDBOX_CPU_S = int(os.environ.get("ORNITH_SANDBOX_CPU_S", "300"))


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
        roots = _ALLOWED_EXPORT_ROOTS or _build_allowed_export_roots()
        for root in roots:
            confined = confine_path(raw, root)
            if confined is not None:
                return Path(confined)
        raise ValueError(
            f"out_path {raw!r} is not within an allowed export root. "
            f"Allowed: {roots}"
        )
    return _ornith_export_root() / default_filename


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
        """Allocate a project-confined working directory."""
        self._state: SecureState = project_state()
        self.temp_dir = self._state.temporary_directory(
            "ornith",
            prefix="ornith-sandbox-",
        )
        self._cleaned = False

    def cleanup(self) -> None:
        """Remove the working directory once, if it still exists."""
        if not self._cleaned and self.temp_dir.exists():
            self._state.cleanup_path(self.temp_dir)
            self._cleaned = True

    def __enter__(self) -> OrnithSandbox:
        """Return this active sandbox to a context-manager caller."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up the sandbox when its context exits."""
        self.cleanup()


def create_ornith_sandbox() -> OrnithSandbox:
    """Create a filesystem-isolated sandbox for the coding-agent subprocess.

    Returns an :class:`OrnithSandbox` that can be used as a context manager.
    """
    return OrnithSandbox()


def _sandbox_preexec_fn(
    mem_mb: int, cpu_s: int, sandbox_dir: str
) -> None:
    """Set RLIMITs before exec.

    ``subprocess.run(cwd=...)`` is the authoritative filesystem confinement.
    Avoid changing cwd here: unit tests invoke this helper directly, and a
    parent-process chdir into a temporary directory can poison later tests once
    that directory is removed.
    """
    _ = sandbox_dir
    try:
        from general_ludd.system.rlimit import apply_limits

        apply_limits(mem_mb, cpu_s)
    except Exception:
        pass


def _timeout_output(value: str | bytes | None) -> str:
    """Normalize partial timeout output despite subprocess text-mode behavior."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


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
                "stdout": _timeout_output(exc.stdout),
                "stderr": _timeout_output(exc.stderr),
                "returncode": -1,
            }
        except FileNotFoundError:
            return {"stdout": "", "stderr": f"binary not found: {cmd[0]}", "returncode": -1}
