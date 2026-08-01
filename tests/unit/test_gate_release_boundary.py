"""Regression tests for the local gate versus live release verification boundary."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
LIVE_RELEASE_CHECKS = {
    "check-multiplatform-consistency",
    "check-provenance-attestation",
    "check-changelog-accuracy",
}


def _target_block(target: str) -> str:
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(f"{target}:"))
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index] and not lines[index][0].isspace()),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_local_gate_does_not_require_a_published_release() -> None:
    gate_declaration = _target_block("gate").splitlines()[0]
    for check in LIVE_RELEASE_CHECKS:
        assert check not in gate_declaration


def test_live_checks_remain_available_as_explicit_release_targets() -> None:
    for check in LIVE_RELEASE_CHECKS:
        script_name = check.replace("-", "_")
        assert script_name in _target_block(check)
