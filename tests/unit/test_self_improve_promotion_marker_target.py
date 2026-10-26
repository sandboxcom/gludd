"""Make-contract pins for the development-only promotion marker lookup."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_make_target_contract import _stanzas
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]


def test_marker_target_is_development_only_and_has_no_target_branch_input() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    block = _stanzas(makefile)["self-improve-promotion-marker"]
    log_line = next(line for line in block.splitlines() if "COMMIT=$$(git log " in line)

    assert " refs/heads/development --)" in log_line
    assert log_line.count("refs/heads/development") == 1
    assert "SELF_IMPROVE_PROMOTION_ARTIFACT_DIGEST" in block
    assert "SELF_IMPROVE_PROMOTION_PLAN_DIGEST" in block
    assert "SELF_IMPROVE_PROMOTION_ATTEMPT_DIGEST" in block
    assert "TARGET_BRANCH" not in block


def test_marker_target_contract_exercises_safe_validate_only_mode() -> None:
    contract = json.loads(
        (ROOT / "config/make_target_contract.json").read_text(encoding="utf-8")
    )
    entry = next(
        item
        for item in contract["targets"]
        if item["name"] == "self-improve-promotion-marker"
    )

    assert set(entry["make_variables"]) == {
        "SELF_IMPROVE_PROMOTION_ARTIFACT_DIGEST",
        "SELF_IMPROVE_PROMOTION_PLAN_DIGEST",
        "SELF_IMPROVE_PROMOTION_ATTEMPT_DIGEST",
        "SELF_IMPROVE_PROMOTION_VALIDATE_ONLY",
    }
    assert "SELF_IMPROVE_PROMOTION_VALIDATE_ONLY=1" in entry["behavior"]
