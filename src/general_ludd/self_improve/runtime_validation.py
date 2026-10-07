"""Pure validation and evidence helpers for the self-improvement runtime."""

from __future__ import annotations

import re
from typing import Final

_SHA_RE: Final = re.compile(r"^[0-9a-f]{40}$")


def validate_target_and_variables(target: str, variables: dict[str, str]) -> None:
    """Reject unsafe Make targets and variable shapes before process creation."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", target):
        raise ValueError(f"unsafe Make target: {target}")
    for key, value in variables.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"unsafe Make variable: {key}")
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError(f"unsafe value for Make variable: {key}")


def validate_sha(label: str, value: str) -> None:
    """Require one exact lowercase Git object identifier."""
    if not _SHA_RE.fullmatch(value):
        raise ValueError(f"{label} must be exactly 40 lowercase hex characters")


def line_count_from_patch(patch: str) -> int:
    """Count changed content lines while excluding unified-diff headers."""
    return sum(
        1
        for line in patch.splitlines()
        if line.startswith(("+", "-"))
        and not line.startswith(("+++", "---"))
    )


def warning_count(output: str) -> int:
    """Count nonzero warning summaries in bounded command output."""
    return sum(
        1
        for line in output.splitlines()
        if re.search(r"\bwarning(?:s)?\b", line, flags=re.IGNORECASE)
        and not re.search(r"\b0\s+warnings?\b", line, flags=re.IGNORECASE)
    )
