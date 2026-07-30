"""Behavioral contract tests for agent-facing Make targets."""

from __future__ import annotations

from pathlib import Path

from scripts.check_make_target_contract import (
    load_contract,
    validate_contract,
)

ROOT = Path(__file__).resolve().parents[2]


def test_make_target_contract_is_valid() -> None:
    contract = load_contract(ROOT / "config/make_target_contract.json")
    errors = validate_contract(ROOT / "Makefile", contract)
    assert errors == [], "\n".join(errors)


def test_contract_documents_prompting_rules() -> None:
    guidance = (ROOT / "docs/MAKE_TARGET_CONTRACT.md").read_text(encoding="utf-8")
    agent_rules = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for phrase in (
        "read `make help`",
        "set every required variable explicitly",
        "behavioral smoke",
        "bare shell commands",
        "`make ps`",
    ):
        assert phrase in guidance
    agent_rules_lower = agent_rules.lower()
    for phrase in (
        "read `make help`",
        "set every documented target variable explicitly",
        "behavioral example",
    ):
        assert phrase in agent_rules_lower
