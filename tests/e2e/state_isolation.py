"""Typed primitives for hermetic E2E state and process ownership."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Mapping
from pathlib import Path


def build_state_environment(
    state_dir: Path,
    filenames: Mapping[str, str],
    *,
    base: Mapping[str, str] | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an environment whose declared mutable files stay under one root."""
    root = state_dir.resolve()
    environment = dict(os.environ if base is None else base)
    for key, filename in filenames.items():
        candidate = (root / filename).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"state path for {key} escapes isolation root: {filename}")
        environment[key] = str(candidate)
    if extra is not None:
        environment.update(extra)
    return environment


def signal_process_group(proc: subprocess.Popen[str], sig: signal.Signals) -> None:
    """Signal only a live process group created with ``start_new_session=True``."""
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, sig)


__all__ = ["build_state_environment", "signal_process_group"]
