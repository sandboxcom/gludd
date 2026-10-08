"""Fail-first contracts for terminal release predecessor admission."""

from __future__ import annotations

from pathlib import Path

from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _target_block(name: str) -> str:
    """Return one target from the repository's composed Makefile."""
    content = compose_makefile(MAKEFILE)
    marker = f"\n{name}:"
    start = content.index(marker)
    end = content.find("\n\n", start)
    return content[start : len(content) if end == -1 else end]


def test_terminal_promotion_disables_predecessor_bypass_in_both_modes() -> None:
    """Validation and live promotion must enforce the same predecessor policy."""
    readiness_calls = [
        line
        for line in _target_block("release-promote").splitlines()
        if "release-readiness" in line
    ]

    assert len(readiness_calls) == 2
    for call in readiness_calls:
        assert "RELEASE_ALLOW_INCOMPLETE_TASKS=0" in call
        assert "RELEASE_ALLOW_INVALID_RECEIPT=0" in call
