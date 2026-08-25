"""Structural contract for the immutable GitHub Actions run summary target."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ci_run_summary_target_is_fail_closed_and_id_bound() -> None:
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    marker = "ci-run-summary:"
    assert marker in source
    block = source.split(marker, maxsplit=1)[1].split("\n\n", maxsplit=1)[0]

    assert '$(RUN)' in block
    assert "scripts/ci_run_summary.py" in block
    assert "--run" in block
    assert "CI_RUN_SUMMARY_REPO" in block
    assert "CI_RUN_SUMMARY_VALIDATE_ONLY" in block
    assert "|| true" not in block
    assert "2>/dev/null" not in block


def test_ci_run_summary_has_a_safe_behavioral_contract() -> None:
    payload = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    contract = next(
        item for item in payload["targets"] if item["name"] == "ci-run-summary"
    )

    assert contract["make_variables"] == [
        "RUN",
        "CI_RUN_SUMMARY_REPO",
        "CI_RUN_SUMMARY_VALIDATE_ONLY",
    ]
    assert contract["behavior"] == (
        "make ci-run-summary RUN=123 CI_RUN_SUMMARY_REPO=sandboxcom/gludd "
        "CI_RUN_SUMMARY_VALIDATE_ONLY=1"
    )
