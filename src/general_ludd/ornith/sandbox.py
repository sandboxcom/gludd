"""Ornith sandboxing: path confinement for exports + subprocess rlimits.

Defense-in-depth for H-ORNITH-SANDBOX-GAPS — both defects gated behind
``ORNITH_ENABLED`` (off by default) but fixed here for when the feature
is turned on.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from general_ludd.security.sanitize import confine_path

_ORNITH_EXPORT_ROOT = os.environ.get("ORNITH_EXPORT_ROOT", tempfile.gettempdir())
_GLUDD_DATA_DIR = os.environ.get("GLUDD_DATA_DIR")

_ALLOWED_EXPORT_ROOTS: list[str] = [_ORNITH_EXPORT_ROOT]
if _GLUDD_DATA_DIR:
    _ALLOWED_EXPORT_ROOTS.append(_GLUDD_DATA_DIR)

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
        for root in _ALLOWED_EXPORT_ROOTS:
            confined = confine_path(raw, root)
            if confined is not None:
                return Path(confined)
        raise ValueError(
            f"out_path {raw!r} is not within an allowed export root. "
            f"Allowed: {_ALLOWED_EXPORT_ROOTS}"
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
