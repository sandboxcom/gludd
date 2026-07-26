"""Behavioral contract tests for agent-facing Make targets."""

from __future__ import annotations

import json
import subprocess
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
    for phrase in ("read `make help`", "set every documented target variable explicitly", "behavioral example"):
        assert phrase in agent_rules_lower


def test_azure_and_runpod_wrappers_use_declared_variables() -> None:
    cases = (
        ["make", "azure-harness", "AZURE_SUBSCRIPTION_ID=sub-123", "AZURE_TENANT_ID=tenant-456"],
        ["make", "runpod-harness", "RUNPOD_API_KEY=placeholder", "RUNPOD_BUDGET_USD=5"],
    )
    for command in cases:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        assert payload["ok"] is True
        assert payload["mode"] == "dry-run"
