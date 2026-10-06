"""Structural contract for deterministic decision-log codification design."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs/design/specs/SPEC_DECISION_LOG_CODIFICATION.md"


def test_offline_learner_is_a_direct_pinned_dependency() -> None:
    """Mining must not depend on an accidental benchmark dependency edge."""
    project = tomllib.loads(
        (
            ROOT / "requirements/profiles/decision-codification/pyproject.toml"
        ).read_text(encoding="utf-8")
    )
    requirement = "scikit-learn==1.9.0"

    assert requirement in project["project"]["dependencies"]


def _spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


def test_spec_is_implementation_ready_and_reuses_repository_components() -> None:
    text = _spec_text()

    assert "Status: READY-TO-IMPLEMENT" in text
    for path in (
        "src/general_ludd/replay/schema.py",
        "src/general_ludd/replay/store.py",
        "src/general_ludd/replay/recorder.py",
        "src/general_ludd/memory/procedural.py",
        "src/general_ludd/rules/engine.py",
        "src/general_ludd/approval/gate.py",
    ):
        assert path in text
    assert "RapidFuzz" in text
    assert "Open Policy Agent" in text
    assert "do not add" in text.lower()


def test_spec_pins_primary_and_long_lived_user_evidence() -> None:
    text = _spec_text()

    urls = (
        "https://rapidfuzz.github.io/RapidFuzz/Usage/process.html",
        "https://www.openpolicyagent.org/docs/management-decision-logs",
        "https://github.com/open-policy-agent/opa/issues/2379",
        "https://github.com/open-policy-agent/opa/issues/1514",
        "https://github.com/scikit-learn/scikit-learn/issues/15629",
    )
    assert all(url in text for url in urls)


def test_spec_covers_safe_mining_approval_and_runtime_lifecycle() -> None:
    text = _spec_text().lower()

    required_phrases = (
        "normalized decision envelope",
        "minimum support",
        "minimum confidence",
        "offline replay",
        "immutable human approval",
        "deterministic runtime lookup",
        "agent/llm fallback",
        "drift",
        "expiry",
        "revocation",
        "zdd canary",
        "rollback",
        "privacy",
        "cardinality",
    )
    assert all(phrase in text for phrase in required_phrases)
    assert "dlc-ac-01" in text
    assert "dlc-ac-12" in text
    assert "85%" in text
    assert "75%" in text


def test_spec_pins_two_disjoint_coding_slices_without_claiming_completion() -> None:
    text = _spec_text()

    assert "### Coding agent A" in text
    assert "### Coding agent B" in text
    assert "Disjoint ownership" in text
    assert "**Status: IMPLEMENTED**" not in text
    assert "- [x] DLC-" not in text
