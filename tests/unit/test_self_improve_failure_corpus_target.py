"""Structural contract for the offline self-improvement failure replay target."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = "test-self-improve-failure-corpus"


def _target_body(makefile: str, target: str) -> str:
    marker = f"\n{target}:"
    start = makefile.index(marker) + 1
    following = makefile.find("\n\n", start)
    return makefile[start:] if following < 0 else makefile[start:following]


def test_make_target_is_offline_and_uses_explicit_corpus_path() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    body = _target_body(makefile, TARGET)

    assert (
        "$(UV) run python -m scripts.replay_self_improve_failure_corpus "
        '--corpus "$(SELF_IMPROVE_FAILURE_CORPUS_FILE)"'
    ) in body
    assert "SELF_IMPROVE_MODEL_PATH" not in body
    assert "llama" not in body.lower()


def test_make_help_and_machine_contract_include_safe_behavior() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    contract = json.loads(
        (ROOT / "config/make_target_contract.json").read_text(encoding="utf-8")
    )
    entries = {item["name"]: item for item in contract["targets"]}

    assert "test-self-improve-failure-corpus" in makefile
    assert entries[TARGET] == {
        "name": TARGET,
        "make_variables": ["SELF_IMPROVE_FAILURE_CORPUS_FILE"],
        "behavior": (
            "make test-self-improve-failure-corpus "
            "SELF_IMPROVE_FAILURE_CORPUS_FILE="
            "config/self-improve/failure-corpus.json"
        ),
    }


def test_feature_document_records_official_and_long_lived_practitioner_evidence() -> None:
    document = (
        ROOT / "docs/SELF_IMPROVEMENT_FAILURE_CORPUS.md"
    ).read_text(encoding="utf-8")

    assert "https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md" in document
    assert "https://github.com/ggml-org/llama.cpp/discussions/6277" in document
    assert "https://github.com/abetlen/llama-cpp-python/issues/1245" in document
    assert "zero-downtime" in document.lower()
