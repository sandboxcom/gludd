"""Structural contract for the immutable GitHub Actions run summary target."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]


def test_ci_run_summary_target_is_fail_closed_and_id_bound() -> None:
    source = compose_makefile(ROOT / "Makefile")
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
        "CI_RUN_SUMMARY_SHARD_TIMINGS",
    ]
    assert contract["behavior"] == (
        "make ci-run-summary RUN=123 CI_RUN_SUMMARY_REPO=sandboxcom/gludd "
        "CI_RUN_SUMMARY_VALIDATE_ONLY=1 CI_RUN_SUMMARY_SHARD_TIMINGS=1"
    )


def test_ci_run_summary_target_wires_optional_shard_timings() -> None:
    source = compose_makefile(ROOT / "Makefile")
    block = source.split("ci-run-summary:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]

    assert "CI_RUN_SUMMARY_SHARD_TIMINGS ?= 0" in source
    assert "CI_RUN_SUMMARY_SHARD_TIMINGS must be 0 or 1" in block
    assert "--shard-timings" in block


def test_release_policy_requires_producer_owned_current_observations() -> None:
    policy = (ROOT / "AGENTS.md").read_text(encoding="utf-8").split(
        "## Make Target Selection Contract", maxsplit=1,
    )[0]

    assert "Do not manually update generated current handoff or observation JSON" in policy
    assert "make ci-await" in policy and "make pipeline-status" in policy
    assert "CI_RUN_SUMMARY_SHARD_TIMINGS=1" in policy
    assert "diagnostic-only" in policy


@pytest.mark.parametrize("value", ["2", "yes", ""])
def test_ci_run_summary_rejects_invalid_timing_mode_before_lookup(value: str) -> None:
    result = subprocess.run(
        [
            "make",
            "ci-run-summary",
            "RUN=123",
            "CI_RUN_SUMMARY_REPO=sandboxcom/gludd",
            "CI_RUN_SUMMARY_VALIDATE_ONLY=1",
            f"CI_RUN_SUMMARY_SHARD_TIMINGS={value}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode != 0
    assert "CI_RUN_SUMMARY_SHARD_TIMINGS must be 0 or 1" in result.stdout
    assert "CI-RUN-SUMMARY VALIDATED" not in result.stdout
