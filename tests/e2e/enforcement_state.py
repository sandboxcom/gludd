"""Confined state paths for enforcement E2E simulators.

The production plugins intentionally use shared ``/tmp/gludd-*`` files.  The
full E2E runner starts independent pytest processes concurrently, so simulator
files are redirected into each file's private resource directory there.  A
direct test invocation retains the production-compatible ``/tmp`` default.
"""

from __future__ import annotations

import os
from pathlib import Path


def state_root() -> Path:
    """Return the configured simulator-state root."""
    return Path(os.environ.get("GLUDD_E2E_STATE_ROOT", "/tmp"))


def state_path(name: str) -> Path:
    """Return a confined state filename and create its private root."""
    candidate = Path(name)
    if not name or name in {".", ".."} or candidate.name != name:
        raise ValueError("enforcement state name must be a plain filename")
    root = state_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / name
