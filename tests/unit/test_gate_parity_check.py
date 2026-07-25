"""Regression coverage for the local release gate's CI phase parity."""

from pathlib import Path

from scripts.check_gate_parity import (
    ci_to_local_phase_names,
    extract_ci_phases,
    extract_local_phases,
)

REPO = Path(__file__).resolve().parents[2]


def test_gate_refresh_covers_every_ci_release_phase() -> None:
    """A release gate must not omit a validation performed by GitHub Actions."""
    ci_phases = extract_ci_phases(REPO / ".github/workflows/build.yml")
    local_phases = set(extract_local_phases(REPO / "Makefile"))

    assert ci_to_local_phase_names(ci_phases) <= local_phases
