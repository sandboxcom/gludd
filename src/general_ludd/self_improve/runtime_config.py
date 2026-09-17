"""Bounded JSON configuration input for live self-improvement candidates."""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

_MAX_RUNTIME_CONFIG_BYTES: Final = 65_536


def load_self_improve_runtime_config(
    configured_path: str,
) -> Mapping[str, object] | None:
    """Load one explicit regular JSON object or keep live providers disabled."""
    if not configured_path:
        return None
    path = Path(configured_path).expanduser()
    try:
        metadata = path.lstat()
    except OSError:
        raise ValueError("self-improvement runtime configuration is unavailable") from None
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(
            "self-improvement runtime configuration must be a regular non-symlink file"
        )
    if metadata.st_size > _MAX_RUNTIME_CONFIG_BYTES:
        raise ValueError("self-improvement runtime configuration exceeds its size limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("self-improvement runtime configuration is invalid") from None
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise ValueError("self-improvement runtime configuration must be a JSON object")
    return cast(Mapping[str, object], value)


__all__ = ("load_self_improve_runtime_config",)
