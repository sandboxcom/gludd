"""Behavioral contract tests for agent-facing Make targets."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from scripts.check_make_target_contract import (
    load_contract,
    validate_contract,
)
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]


def test_make_target_contract_is_valid() -> None:
    contract = load_contract(ROOT / "config/make_target_contract.json")
    errors = validate_contract(ROOT / "Makefile", contract)
    assert errors == [], "\n".join(errors)


def test_sync_llama_cpp_uses_locked_profile_and_dry_run_contract() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    target = makefile.split("\nsync-llama-cpp:", 1)[1].split("\n\n", 1)[0]

    assert "0.1.0-beta" not in target
    assert "sync" in target
    assert "DEPENDENCY_PROFILE_SET=local-inference" in target
    assert "DEPENDENCY_PROFILE_VALIDATE_ONLY=$(SYNC_LLAMA_CPP_VALIDATE_ONLY)" in target
    assert "SYNC_LLAMA_CPP_VALIDATE_ONLY" in target

    contract = load_contract(ROOT / "config/make_target_contract.json")
    entry = next(item for item in contract["targets"] if item["name"] == "sync-llama-cpp")
    assert entry["make_variables"] == ["SYNC_LLAMA_CPP_VALIDATE_ONLY"]
    assert entry["behavior"] == ("make sync-llama-cpp SYNC_LLAMA_CPP_VALIDATE_ONLY=1")


def test_typecheck_scope_keeps_errors_but_drops_global_unused_override_noise() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    command = next(line for line in makefile.splitlines() if "run mypy" in line and "$(FILES)" in line)

    assert "--no-warn-unused-configs" in command
    assert "--no-incremental" in command
    assert "MYPYPATH=src:scripts" in command
    assert "|| true" not in command

    contract = load_contract(ROOT / "config/make_target_contract.json")
    entry = next(item for item in contract["targets"] if item["name"] == "typecheck-scope")
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
    makefile = compose_makefile(ROOT / "Makefile")
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
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()
    if branch != "development":
        # CI shards check out detached HEADs; the target must REFUSE there.
        assert result.returncode != 0, result.stdout + result.stderr
        assert "Refusing conflict resolution on branch" in result.stdout
        assert "expected development" in result.stdout
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRY RUN" in result.stdout


def test_patch_equivalence_target_uses_git_cherry() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
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


def test_key_make_targets_recommends_guarded_release_owners() -> None:
    """Keep the mandatory first read consistent with release promotion policy."""
    guidance = (ROOT / ".agents/policy/key-make-targets.md").read_text(encoding="utf-8")
    for owner in (
        "release-proof-admission",
        "development-merge-forward",
        "release-promote",
        "check-make-target-contract",
        "worktree-state",
    ):
        assert f"make {owner}" in guidance, f"mandatory guide omits {owner}"

    for shortcut in (
        "make feature-done",
        "make agent-merge BRANCH",
        "make git-rebranch-onto",
        "make git-pull-sandboxcom",
        "make gate-async",
    ):
        assert shortcut not in guidance, f"mandatory guide recommends unsafe shortcut: {shortcut}"
    assert "canonical main checkout" in guidance
    assert "exact-SHA" in guidance
    assert "85%" in guidance and "75%" in guidance
    assert "gate_evidence=false" in guidance


def test_key_make_targets_release_examples_use_declared_variables() -> None:
    """Catch stale copyable examples when the executable target contract changes."""
    guidance = (ROOT / ".agents/policy/key-make-targets.md").read_text(encoding="utf-8")
    contract = load_contract(ROOT / "config/make_target_contract.json")
    names = {"release-proof-admission", "development-merge-forward", "release-promote", "test-files"}
    for entry in contract["targets"]:
        if entry["name"] not in names:
            continue
        prefix = f"make {entry['name']} "
        commands = [line for line in guidance.splitlines() if line.startswith(prefix)]
        assert commands, f"mandatory guide has no copyable example for {entry['name']}"
        for command in commands:
            arguments = shlex.split(command)
            supplied = {arg.partition("=")[0] for arg in arguments[2:] if "=" in arg}
            assert set(entry["make_variables"]) <= supplied, command
    assert "GATE_TEST_PROFILE=release" in guidance
    assert "GATE_EXACT_SHA_RESUME=0" in guidance
    assert "GATE_MAX_FILES_PER_BATCH=16" in guidance
    assert "GATE_BATCH_WORKERS=1" in guidance
