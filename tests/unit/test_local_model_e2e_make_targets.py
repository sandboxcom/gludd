"""Contracts for usable, isolated local-model E2E Make targets."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
CONTRACT = ROOT / "config" / "make_target_contract.json"


def _target_body(name: str) -> str:
    content = MAKEFILE.read_text(encoding="utf-8")
    start = content.index(f"{name}:")
    return content[start:].split("\n\n", 1)[0]


def _contract_entry(name: str) -> dict[str, object]:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return next(entry for entry in document["targets"] if entry["name"] == name)


def test_game_target_defaults_to_owned_hermetic_lifecycle() -> None:
    content = MAKEFILE.read_text(encoding="utf-8")
    body = _target_body("test-e2e-games-local-model")

    assert "LOCAL_MODEL_E2E_MODE ?= hermetic" in content
    assert "LOCAL_MODEL_GAME ?= snake" in content
    assert "-m scripts.run_local_model_game_e2e" in body
    assert "http://localhost:11434" not in body


def test_game_target_contract_requires_explicit_external_endpoint() -> None:
    entry = _contract_entry("test-e2e-games-local-model")

    assert entry["make_variables"] == [
        "LOCAL_MODEL_E2E_MODE",
        "LOCAL_MODEL_BASE_URL",
        "LOCAL_MODEL_NAME",
        "LOCAL_MODEL_KEY",
        "LOCAL_MODEL_GAME",
        "PYTEST_ARGS",
    ]
    assert "LOCAL_MODEL_E2E_MODE=hermetic" in str(entry["behavior"])
    assert "LOCAL_MODEL_GAME=snake" in str(entry["behavior"])


def test_inference_target_uses_locked_extra_and_explicit_artifact() -> None:
    content = MAKEFILE.read_text(encoding="utf-8")
    body = _target_body("test-local-model-inference")

    assert "LOCAL_MODEL_INFERENCE_MODEL_PATH ?=" in content
    assert "LOCAL_MODEL_INFERENCE_VALIDATE_ONLY ?= 0" in content
    assert "--extra local-inference" in body
    assert "scripts/local_model_inference_smoke.py" in body
    assert "glob.glob" not in body


def test_inference_target_contract_has_safe_behavioral_example() -> None:
    entry = _contract_entry("test-local-model-inference")

    assert entry["make_variables"] == [
        "LOCAL_MODEL_INFERENCE_MODEL_PATH",
        "LOCAL_MODEL_INFERENCE_VALIDATE_ONLY",
    ]
    assert "LOCAL_MODEL_INFERENCE_VALIDATE_ONLY=1" in str(entry["behavior"])


def test_game_pipeline_target_uses_locked_local_inference_extra() -> None:
    body = _target_body("test-e2e-game-pipeline")

    assert "--extra local-inference" in body
    assert "GLUDD_LIVE_MODEL_E2E=\"1\"" in body


def test_small_model_cleanup_is_exact_and_recoverable() -> None:
    body = _target_body("clean-e2e-small-model")

    assert "rm -rf -- /tmp/gludd-qwen-e2e-model" in body
    assert "e2e-download-small-model" in body
    assert "E2E_SMALL_MODEL_CLEAN_VALIDATE_ONLY" in body


def test_small_model_cleanup_contract_defaults_to_validation() -> None:
    entry = _contract_entry("clean-e2e-small-model")

    assert entry["make_variables"] == ["E2E_SMALL_MODEL_CLEAN_VALIDATE_ONLY"]
    assert "E2E_SMALL_MODEL_CLEAN_VALIDATE_ONLY=1" in str(entry["behavior"])
