"""Make contract for managed self-improvement model acquisition."""

from __future__ import annotations

import json
from pathlib import Path


def test_self_improve_target_defaults_to_managed_model_acquisition() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    target = makefile.split("\ntest-self-improve:", 1)[1].split(
        "\n# Compatibility alias", 1
    )[0]

    assert '--local-model-path "$(SELF_IMPROVE_MODEL_PATH)"' in target
    assert "SELF_IMPROVE_MODEL_PATH is required" not in target
    usage = makefile.split(
        "# Local self-improvement benchmark", 1
    )[1].split("\ntest-self-improve:", 1)[0]
    assert "optional override" in usage
    assert "/tmp/gludd-qwen" not in usage
    assert "SELF_IMPROVE_MODEL_PATH ?=\n" in makefile


def test_documented_behavior_exercises_automatic_validate_only_path() -> None:
    contract = json.loads(
        Path("config/make_target_contract.json").read_text(encoding="utf-8")
    )
    entry = next(
        item for item in contract["targets"] if item["name"] == "test-self-improve"
    )

    assert "SELF_IMPROVE_MODEL_PATH" in entry["make_variables"]
    assert "SELF_IMPROVE_MODEL_PATH=" in entry["behavior"]
    assert "/tmp/gludd-qwen" not in entry["behavior"]
    assert "SELF_IMPROVE_VALIDATE_ONLY=1" in entry["behavior"]
