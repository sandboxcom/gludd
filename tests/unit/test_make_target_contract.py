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


def test_sync_llama_cpp_uses_locked_extra_and_dry_run_contract() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("\nsync-llama-cpp:", 1)[1].split("\n\n", 1)[0]

    assert "0.1.0-beta" not in target
    assert "sync --locked --extra local-inference" in target
    assert "SYNC_LLAMA_CPP_VALIDATE_ONLY" in target

    contract = load_contract(ROOT / "config/make_target_contract.json")
    entry = next(
        item for item in contract["targets"]
        if item["name"] == "sync-llama-cpp"
    )
    assert entry["make_variables"] == ["SYNC_LLAMA_CPP_VALIDATE_ONLY"]
    assert entry["behavior"] == (
        "make sync-llama-cpp SYNC_LLAMA_CPP_VALIDATE_ONLY=1"
    )


def test_typecheck_scope_keeps_errors_but_drops_global_unused_override_noise() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    command = next(
        line for line in makefile.splitlines()
        if "run mypy" in line and "$(FILES)" in line
    )

    assert "--no-warn-unused-configs" in command
    assert "--no-incremental" in command
    assert "|| true" not in command

    contract = load_contract(ROOT / "config/make_target_contract.json")
    entry = next(
        item for item in contract["targets"]
        if item["name"] == "typecheck-scope"
    )
    assert entry["make_variables"] == ["FILES"]
    assert entry["behavior"] == "make typecheck-scope FILES=scripts/status_snapshot.py"


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


def test_development_conflict_recovery_is_tracked_and_dry_runnable() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "resolve-development-conflicts:" in makefile
    assert 'APPLY="$(APPLY)"' in makefile
    assert 'MERGE_SOURCE="$(MERGE_SOURCE)"' in makefile
    assert "diff --name-only --diff-filter=U" in makefile

    result = subprocess.run(
        [
            "make",
            "resolve-development-conflicts",
            "MERGE_SOURCE=master",
            "APPLY=0",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRY RUN" in result.stdout


def test_patch_equivalence_target_uses_git_cherry() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "git-patch-equivalence:" in makefile
    assert 'PATCH_UPSTREAM="$(PATCH_UPSTREAM)"' in makefile
    assert 'PATCH_HEAD="$(PATCH_HEAD)"' in makefile
    assert 'PATCH_LIMIT="$(PATCH_LIMIT)"' in makefile
    assert "git cherry" in makefile

    result = subprocess.run(
        [
            "make",
            "git-patch-equivalence",
            "PATCH_UPSTREAM=development",
            "PATCH_HEAD=master",
            "PATCH_LIMIT=3",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "patch-equivalent" in result.stdout
    assert "unique" in result.stdout
