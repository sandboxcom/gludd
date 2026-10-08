"""Focused contracts for the extracted replay run-ID boundary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.replay.run_ids import validate_run_id


def test_run_id_accepts_portable_bounded_identity() -> None:
    """Portable ASCII identifiers pass through without normalization."""
    assert validate_run_id("run-2026.10_08") == "run-2026.10_08"


@pytest.mark.parametrize("value", ["../escape", "CON", "nul.txt", "r\u0430n"])
def test_run_id_rejects_ambiguous_or_nonportable_identity(value: str) -> None:
    """Traversal, device names, and Unicode lookalikes fail closed."""
    with pytest.raises(ValidationError):
        validate_run_id(value)
