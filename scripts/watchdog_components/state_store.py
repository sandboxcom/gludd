"""Typed, fail-closed JSON scalar normalization for the watchdog."""

from __future__ import annotations

import json
from pathlib import Path


def _as_int(value: object, default: int = 0) -> int:
    """Convert a JSON scalar to int without accepting containers."""
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _as_record(value: object) -> dict[str, object] | None:
    """Return a string-keyed view of a decoded JSON object."""
    if not isinstance(value, dict):
        return None
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _as_float(value: object, default: float = 0.0) -> float:
    """Convert a JSON scalar to float without accepting containers."""
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _as_text(value: object, default: str = "") -> str:
    """Return text for a decoded scalar while rejecting containers."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return default


def _read_json_record(path: Path) -> dict[str, object]:
    """Read one JSON object, returning an empty record on invalid input."""
    try:
        value: object = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return _as_record(value) or {}
