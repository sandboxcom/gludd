"""Bounded request parsing for git-release service operations."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .helper_ranker import TaskRequirements

_MAX_FILE_BYTES = 32 * 1024 * 1024


class GitReleaseRequestError(ValueError):
    """Signal invalid git-release operation input at the service boundary."""


def request_text(
    request: Mapping[str, Any],
    key: str,
    *,
    default: str | None = None,
    maximum: int = 4096,
) -> str:
    """Return one bounded, single-line request string."""
    value = request.get(key, default)
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > maximum
        or any(char in value for char in "\r\n\x00")
    ):
        raise GitReleaseRequestError(f"{key} must be a non-empty string")
    return value.strip()


def request_integer(
    request: Mapping[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Return one bounded integer request field."""
    value = request.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise GitReleaseRequestError(f"{key} must be between {minimum} and {maximum}")
    return value


def request_number(
    request: Mapping[str, Any],
    key: str,
    *,
    default: float | None = None,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    """Return one finite, bounded numeric request field."""
    value = request.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GitReleaseRequestError(f"{key} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < minimum or (maximum is not None and parsed > maximum):
        raise GitReleaseRequestError(f"{key} must be within the supported range")
    return parsed


def request_mapping(request: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """Return one mapping-valued request field."""
    value = request.get(key)
    if not isinstance(value, Mapping):
        raise GitReleaseRequestError(f"{key} must be an object")
    return value


def request_file(repository: Path, request: Mapping[str, Any], key: str) -> Path:
    """Resolve one bounded regular file strictly within the repository."""
    relative = request_text(request, key)
    root = repository.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise GitReleaseRequestError(f"{key} must resolve inside the repository")
    if not candidate.is_file():
        raise GitReleaseRequestError(f"{key} is not a regular file")
    try:
        size = candidate.stat().st_size
    except OSError as exc:
        raise GitReleaseRequestError(f"{key} cannot be inspected") from exc
    if size < 1 or size > _MAX_FILE_BYTES:
        raise GitReleaseRequestError(f"{key} must be between 1 and {_MAX_FILE_BYTES} bytes")
    return candidate


def read_bounded(path: Path, key: str) -> bytes:
    """Read one previously validated request file."""
    try:
        return path.read_bytes()
    except OSError as exc:
        raise GitReleaseRequestError(f"{key} cannot be read") from exc


def task_requirements(request: Mapping[str, Any]) -> TaskRequirements:
    """Build bounded helper-selection requirements from a request."""
    platforms_raw = request.get("platforms", [])
    if not isinstance(platforms_raw, list) or len(platforms_raw) > 32:
        raise GitReleaseRequestError("platforms must be a list with at most 32 entries")
    if any(not isinstance(value, str) or not value.strip() for value in platforms_raw):
        raise GitReleaseRequestError("platforms entries must be non-empty strings")
    return TaskRequirements(
        kind=request_text(request, "kind", default="build", maximum=64),
        needs_dry_run=request.get("needs_dry_run", False) is True,
        needs_rollback=request.get("needs_rollback", False) is True,
        min_score=request_integer(request, "min_score", default=50, minimum=0, maximum=100),
        platforms=tuple(value.strip() for value in platforms_raw),
    )
