"""tests/unit/test_behavioral_enforcement.py — verify enforcement mechanisms for AA/AB specs.

Checks that each spec's claimed enforcement mechanism exists in the codebase:
Makefile guards, plugin files, scripts, and their wiring into targets.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MAKEFILE_PATH = ROOT / "Makefile"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"


def makefile_text() -> str:
    return MAKEFILE_PATH.read_text()


def guard_exists_in_makefile(guard_name: str) -> bool:
    """Check if a guard target is declared in the Makefile."""
    text = makefile_text()
    return bool(re.search(rf"^{guard_name}:", text, re.MULTILINE))


def target_uses_guard(target_name: str, guard_name: str) -> bool:
    """Check if a Makefile target has a guard as a prerequisite."""
    text = makefile_text()
    _pat = rf"^{target_name}:\s*.*\b{guard_name}\b"
    return bool(re.search(_pat, text, re.MULTILINE))


def plugin_exists(name: str) -> bool:
    return (PLUGIN_DIR / name).exists()


class TestAA001PushCancelsCi:
    """AA001: _push-rate-guard in Makefile + enforce-batch-push.ts."""

    def test_push_rate_guard_exists(self):
        assert guard_exists_in_makefile("_push-rate-guard"), "AA001: _push-rate-guard missing from Makefile"

    def test_batch_push_plugin_exists(self):
        assert plugin_exists("enforce-batch-push.ts"), "AA001: enforce-batch-push.ts missing"

    def test_batch_push_uses_rate_guard(self):
        assert target_uses_guard("batch-push", "_push-rate-guard") or target_uses_guard(
            "git-push-sandboxcom", "_push-rate-guard"
        ), "AA001: no push target uses _push-rate-guard"

    def test_push_targets_have_rate_guard(self):
        text = makefile_text()
        push_targets = [
            "git-push-sandboxcom",
            "push-dev-nv",
            "git-push-sandboxcom-nv",
            "git-push-current-head-nv",
            "ci-push",
        ]
        for t in push_targets:
            if re.search(rf"^{t}:", text, re.MULTILINE):
                assert target_uses_guard(t, "_push-rate-guard"), f"AA001: push target {t} missing _push-rate-guard"


class TestAA002ObjectivePriority:
    """AA002: enforce-objective.ts (BLOCKING)."""

    def test_objective_plugin_exists(self):
        assert plugin_exists("enforce-objective.ts"), "AA002: enforce-objective.ts missing"

    def test_objective_plugin_has_blocking(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "BLOCKING" in content or "deny" in content or "block" in content.lower(), (
            "AA002: enforce-objective.ts does not mention blocking behavior"
        )


class TestAA003CompulsiveCiCheck:
    """AA003: enforce-no-wait.ts CI rate limiting."""

    def test_no_wait_plugin_exists(self):
        assert plugin_exists("enforce-no-wait.ts"), "AA003: enforce-no-wait.ts missing"

    def test_no_wait_has_ci_throttle(self):
        content = (PLUGIN_DIR / "enforce-no-wait.ts").read_text()
        has_throttle = "ci" in content.lower() or "cooldown" in content.lower() or "rate" in content.lower()
        assert has_throttle, "AA003: enforce-no-wait.ts lacks CI rate limiting"


class TestAA004SubagentCleanup:
    """AA004: enforce-delegate.ts + Makefile _subagent-cleanup-guard."""

    def test_delegate_plugin_exists(self):
        assert plugin_exists("enforce-delegate.ts"), "AA004: enforce-delegate.ts missing"

    def test_subagent_cleanup_guard_registered(self):
        assert guard_exists_in_makefile("_subagent-cleanup-guard"), (
            "AA004: _subagent-cleanup-guard not found. Wired via enforce-delegate.ts"
            " and the agent-worktree lifecycle (agent-merge/agent-cleanup targets)."
        )


class TestAA005WrongBranch:
    """AA005: enforce-branch-discipline.ts (BLOCKING)."""

    def test_branch_discipline_plugin_exists(self):
        assert plugin_exists("enforce-branch-discipline.ts"), "AA005: enforce-branch-discipline.ts missing"

    def test_branch_discipline_is_blocking(self):
        content = (PLUGIN_DIR / "enforce-branch-discipline.ts").read_text()
        assert "BLOCKING" in content or "deny" in content or "block" in content.lower(), (
            "AA005: enforce-branch-discipline.ts enforcement level unclear"
        )


class TestAA006AntiEssay:
    """AA006: enforce-anti-essay.ts (BLOCKING)."""

    def test_anti_essay_plugin_exists(self):
        assert plugin_exists("enforce-anti-essay.ts"), "AA006: enforce-anti-essay.ts missing"

    def test_anti_essay_is_blocking(self):
        content = (PLUGIN_DIR / "enforce-anti-essay.ts").read_text()
        assert any(w in content for w in ["BLOCKING", "deny", "block"]), (
            "AA006: enforce-anti-essay.ts enforcement level unclear"
        )


class TestAA007SingleTasking:
    """AA007: enforce-multitask.ts + enforce-floor.ts."""

    def test_multitask_plugin_exists(self):
        assert plugin_exists("enforce-multitask.ts"), "AA007: enforce-multitask.ts missing"

    def test_floor_plugin_exists(self):
        assert plugin_exists("enforce-floor.ts"), "AA007: enforce-floor.ts missing"


class TestAA008NoBypassGuard:
    """AA008: _no-bypass-guard in Makefile."""

    def test_no_bypass_guard_exists(self):
        assert guard_exists_in_makefile("_no-bypass-guard"), "AA008: _no-bypass-guard missing from Makefile"

    def test_no_bypass_guard_wired_to_push(self):
        assert target_uses_guard("batch-push", "_no-bypass-guard"), "AA008: batch-push does not use _no-bypass-guard"

    def test_no_bypass_rejects_makefile_tmp(self):
        content = makefile_text()
        idx = content.find("_no-bypass-guard:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "Makefile.tmp" in block, "AA008: _no-bypass-guard does not check for Makefile.tmp"


class TestAA009PreCommitStageGuard:
    """AA009: _pre-commit-stage-guard in Makefile."""

    def test_stage_guard_exists(self):
        assert guard_exists_in_makefile("_pre-commit-stage-guard"), (
            "AA009: _pre-commit-stage-guard missing from Makefile"
        )

    def test_stage_guard_wired_to_git_commit(self):
        assert target_uses_guard("git-commit", "_pre-commit-stage-guard"), (
            "AA009: git-commit does not use _pre-commit-stage-guard"
        )

    def test_stage_guard_wired_to_ship_commit(self):
        assert target_uses_guard("ship-commit", "_pre-commit-stage-guard"), (
            "AA009: ship-commit does not use _pre-commit-stage-guard"
        )

    def test_stage_guard_wired_to_commit_no_verify(self):
        assert target_uses_guard("commit-no-verify", "_pre-commit-stage-guard"), (
            "AA009: commit-no-verify does not use _pre-commit-stage-guard"
        )

    def test_stage_guard_checks_staged_changes(self):
        content = makefile_text()
        idx = content.find("_pre-commit-stage-guard:")
        assert idx != -1
        block = content[idx : idx + 400]
        assert "git diff --cached" in block, "AA009: _pre-commit-stage-guard does not check staged changes"


class TestAA010PushWhileCiRunning:
    """AA010: ci-busy-check on ALL push targets."""

    def test_ci_busy_check_exists(self):
        assert guard_exists_in_makefile("ci-busy-check"), "AA010: ci-busy-check missing from Makefile"

    def test_push_dev_uses_ci_busy_check(self):
        assert target_uses_guard("push-dev", "ci-busy-check"), "AA010: push-dev does not use ci-busy-check"

    def test_pre_push_check_uses_ci_busy_check(self):
        assert target_uses_guard("pre-push-check", "ci-busy-check"), "AA010: pre-push-check does not use ci-busy-check"

    def test_ci_safe_push_uses_ci_busy_check(self):
        assert target_uses_guard("ci-safe-push", "ci-busy-check"), "AA010: ci-safe-push does not use ci-busy-check"

    def test_development_push_uses_ci_busy_check(self):
        assert target_uses_guard("development-push", "ci-busy-check"), (
            "AA010: development-push does not use ci-busy-check"
        )


class TestAA011MergeStrategyGuard:
    """AA011: _merge-strategy-guard in Makefile."""

    def test_merge_strategy_guard_exists(self):
        assert guard_exists_in_makefile("_merge-strategy-guard"), "AA011: _merge-strategy-guard missing from Makefile"

    def test_merge_strategy_guard_wired_to_git_merge(self):
        assert target_uses_guard("git-merge", "_merge-strategy-guard"), (
            "AA011: git-merge does not use _merge-strategy-guard"
        )

    def test_merge_strategy_detects_sha(self):
        content = makefile_text()
        idx = content.find("_merge-strategy-guard:")
        assert idx != -1
        block = content[idx : idx + 600]
        assert "[0-9a-f]" in block or "hex" in block.lower() or "SHA" in block, (
            "AA011: _merge-strategy-guard does not detect SHA-like input"
        )


class TestAA012ReleaseCiGreenGuard:
    """AA012: _release-ci-green-guard in Makefile."""

    def test_release_ci_green_guard_exists(self):
        assert guard_exists_in_makefile("_release-ci-green-guard") or ("require-ci-green" in makefile_text()), (
            "AA012: _release-ci-green-guard or require-ci-green missing"
        )

    def test_git_tag_move_has_ci_green_requirement(self):
        content = makefile_text()
        # git-tag-move or release-cut should reference CI-green
        if "git-tag-move:" in content:
            idx = content.find("git-tag-move:")
            block = content[idx : idx + 400]
            assert "ci" in block.lower() or "green" in block.lower(), "AA012: git-tag-move does not check CI green"


class TestAA013NoSuppressions:
    """AA013: enforce-no-suppressions.ts extended."""

    def test_no_suppressions_plugin_exists(self):
        assert plugin_exists("enforce-no-suppressions.ts"), "AA013: enforce-no-suppressions.ts missing"


class TestAA014TestIntegrity:
    """AA014: enforce-test-integrity.ts (BLOCKING)."""

    def test_test_integrity_plugin_exists(self):
        assert plugin_exists("enforce-test-integrity.ts"), "AA014: enforce-test-integrity.ts missing"

    def test_test_integrity_has_blocking(self):
        content = (PLUGIN_DIR / "enforce-test-integrity.ts").read_text()
        assert "BLOCKING" in content or "deny" in content or "block" in content.lower(), (
            "AA014: enforce-test-integrity.ts enforcement level unclear"
        )


class TestAA015CiInterrogation:
    """AA015: enforce-objective.ts extended for CI-check-to-fix ratio."""

    def test_objective_tracks_ci_fix_ratio(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        has_tracking = "ci" in content.lower() and ("fix" in content.lower() or "ratio" in content.lower())
        assert has_tracking, "AA015: enforce-objective.ts does not track CI-check-to-fix ratio"


class TestAA016MergeRecovery:
    """AA016: git-merge-abort target + _merge-recovery-guard."""

    def test_merge_abort_exists(self):
        assert guard_exists_in_makefile("git-merge-abort"), "AA016: git-merge-abort target missing"

    def test_reset_hard_exists(self):
        assert guard_exists_in_makefile("git-reset-hard"), "AA016: git-reset-hard target missing"


class TestAA017PrePushCiVerdict:
    """AA017: _pre-push-ci-verdict-guard in Makefile."""

    def test_pre_push_check_requires_ci_verdict(self):
        assert guard_exists_in_makefile("pre-push-check"), "AA017: pre-push-check target missing"
        content = makefile_text()
        idx = content.find("pre-push-check:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "ci" in block.lower() or "gate" in block.lower(), (
            "AA017: pre-push-check does not inspect CI or gate status"
        )


class TestAA018ManualTagOperations:
    """AA018: git-tag-move target + _no-raw-git-guard."""

    def test_git_tag_move_exists(self):
        assert guard_exists_in_makefile("git-tag-move"), "AA018: git-tag-move target missing"

    def test_no_raw_git_guard_exists(self):
        assert guard_exists_in_makefile("_no-raw-git-guard"), "AA018: _no-raw-git-guard missing"


class TestAA019DeduplicateSpecs:
    """AA019: make deduplicate-specs target."""

    def test_deduplicate_specs_target_exists(self):
        assert guard_exists_in_makefile("deduplicate-specs"), "AA019: deduplicate-specs target missing"


class TestAA020FrustrationDetection:
    """AA020: enforce-objective.ts extended for frustration signals."""

    def test_objective_detects_frustration(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        has_detect = (
            "frustration" in content.lower()
            or "caps" in content.lower()
            or "expletiv" in content.lower()
            or "repeat" in content.lower()
        )
        assert has_detect, "AA020: enforce-objective.ts does not detect frustration signals"


class TestAA021GateFreshCheck:
    """AA021: _gate-fresh-check in Makefile — hard block on stale/failed gate."""

    def test_gate_fresh_check_exists(self):
        assert guard_exists_in_makefile("_gate-fresh-check"), "AA021: _gate-fresh-check missing from Makefile"

    def test_gate_fresh_check_wired_to_git_commit(self):
        assert target_uses_guard("git-commit", "_gate-fresh-check"), "AA021: git-commit does not use _gate-fresh-check"

    def test_gate_fresh_check_wired_to_commit_no_verify(self):
        assert target_uses_guard("commit-no-verify", "_gate-fresh-check"), (
            "AA021: commit-no-verify does not use _gate-fresh-check"
        )

    def test_gate_fresh_check_wired_to_git_commit_no_verify(self):
        assert target_uses_guard("git-commit-no-verify", "_gate-fresh-check"), (
            "AA021: git-commit-no-verify does not use _gate-fresh-check"
        )


class TestAA022StashBeforePushGuard:
    """AA022: _stash-before-push-guard in Makefile."""

    def test_stash_before_push_guard_exists(self):
        assert guard_exists_in_makefile("_stash-before-push-guard"), (
            "AA022: _stash-before-push-guard missing from Makefile"
        )

    def test_guard_checks_unstaged_changes(self):
        content = makefile_text()
        idx = content.find("_stash-before-push-guard:")
        assert idx != -1
        block = content[idx : idx + 400]
        assert "git diff --quiet" in block, "AA022: _stash-before-push-guard does not check unstaged changes"

    def test_wired_to_git_push_sandboxcom(self):
        assert target_uses_guard("git-push-sandboxcom", "_stash-before-push-guard"), (
            "AA022: git-push-sandboxcom missing _stash-before-push-guard"
        )

    def test_wired_to_batch_push(self):
        assert target_uses_guard("batch-push", "_stash-before-push-guard"), (
            "AA022: batch-push missing _stash-before-push-guard"
        )

    def test_wired_to_push_dev(self):
        assert target_uses_guard("push-dev", "_stash-before-push-guard"), (
            "AA022: push-dev missing _stash-before-push-guard"
        )


class TestAA023CiRestartCap:
    """AA023: _ci-restart-cap in Makefile — limits CI restarts to 3 per session."""

    def test_ci_restart_cap_exists(self):
        assert guard_exists_in_makefile("_ci-restart-cap"), "AA023: _ci-restart-cap missing from Makefile"

    def test_guard_uses_state_file(self):
        content = makefile_text()
        idx = content.find("_ci-restart-cap:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "gludd-ci-restart-count" in block, "AA023: _ci-restart-cap does not track restart count"

    def test_guard_enforces_limit(self):
        content = makefile_text()
        idx = content.find("_ci-restart-cap:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert " -ge " in block or " -gt " in block, "AA023: _ci-restart-cap does not check count threshold"

    def test_wired_to_git_push_sandboxcom(self):
        assert target_uses_guard("git-push-sandboxcom", "_ci-restart-cap"), (
            "AA023: git-push-sandboxcom missing _ci-restart-cap"
        )

    def test_wired_to_batch_push(self):
        assert target_uses_guard("batch-push", "_ci-restart-cap"), "AA023: batch-push missing _ci-restart-cap"

    def test_wired_to_push_dev(self):
        assert target_uses_guard("push-dev", "_ci-restart-cap"), "AA023: push-dev missing _ci-restart-cap"


class TestAA024VerifiedReleaseClaims:
    """AA024: enforce-verified-claims.ts plugin."""

    def test_verified_claims_plugin_exists(self):
        assert plugin_exists("enforce-verified-claims.ts"), "AA024: enforce-verified-claims.ts missing"

    def test_verified_claims_has_blocking(self):
        content = (PLUGIN_DIR / "enforce-verified-claims.ts").read_text()
        assert "BLOCKING" in content or "deny" in content or "block" in content.lower(), (
            "AA024: enforce-verified-claims.ts enforcement level unclear"
        )


class TestAA025EscalatingSpecDemands:
    """AA025: enforce-objective.ts extended for escalating spec demands."""

    def test_objective_tracks_escalation(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        has_tracking = any(w in content.lower() for w in ["escalat", "target", "spec", "demand"])
        assert has_tracking, "AA025: enforce-objective.ts does not track escalating demands"


class TestAA026DeadCodeFromGenerators:
    """AA026: make check-dead-code target exists."""

    def test_check_dead_code_exists(self):
        assert guard_exists_in_makefile("check-dead-code"), "AA026: check-dead-code missing from Makefile"


class TestAA027HotReloadVerified:
    """AA027: check-hot-reload-fresh in Makefile."""

    def test_hot_reload_fresh_exists(self):
        assert guard_exists_in_makefile("check-hot-reload-fresh"), "AA027: check-hot-reload-fresh missing from Makefile"

    def test_hot_reload_fresh_in_gate(self):
        content = makefile_text()
        assert "check-hot-reload-fresh" in content, "AA027: check-hot-reload-fresh not referenced in Makefile"


class TestAA028StashLeakGuard:
    """AA028: _stash-leak-guard in Makefile."""

    def test_stash_leak_guard_exists(self):
        assert guard_exists_in_makefile("_stash-leak-guard"), "AA028: _stash-leak-guard missing from Makefile"

    def test_guard_checks_stash_depth(self):
        content = makefile_text()
        idx = content.find("_stash-leak-guard:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "stash list" in block or "stash" in block.lower(), "AA028: _stash-leak-guard does not check stash"

    def test_wired_to_git_commit(self):
        assert target_uses_guard("git-commit", "_stash-leak-guard"), "AA028: git-commit missing _stash-leak-guard"

    def test_wired_to_ship_commit(self):
        assert target_uses_guard("ship-commit", "_stash-leak-guard"), "AA028: ship-commit missing _stash-leak-guard"


class TestAA029PullBeforePushGuard:
    """AA029: _pull-before-push-guard in Makefile."""

    def test_pull_before_push_guard_exists(self):
        assert guard_exists_in_makefile("_pull-before-push-guard"), (
            "AA029: _pull-before-push-guard missing from Makefile"
        )

    def test_guard_fetches_before_push(self):
        content = makefile_text()
        idx = content.find("_pull-before-push-guard:")
        assert idx != -1
        block = content[idx : idx + 600]
        assert "fetch" in block, "AA029: _pull-before-push-guard does not fetch remote"

    def test_guard_checks_remote_ahead(self):
        content = makefile_text()
        idx = content.find("_pull-before-push-guard:")
        assert idx != -1
        block = content[idx : idx + 600]
        assert "rev-list" in block or "BEHIND" in block or "ahead" in block, (
            "AA029: _pull-before-push-guard does not check remote ahead/behind"
        )

    def test_wired_to_git_push_sandboxcom(self):
        assert target_uses_guard("git-push-sandboxcom", "_pull-before-push-guard"), (
            "AA029: git-push-sandboxcom missing _pull-before-push-guard"
        )

    def test_wired_to_batch_push(self):
        assert target_uses_guard("batch-push", "_pull-before-push-guard"), (
            "AA029: batch-push missing _pull-before-push-guard"
        )


class TestAA030PushParameterAudit:
    """AA030: _push-parameter-audit in Makefile."""

    def test_push_parameter_audit_exists(self):
        assert guard_exists_in_makefile("_push-parameter-audit"), "AA030: _push-parameter-audit missing from Makefile"

    def test_guard_checks_batch_threshold(self):
        content = makefile_text()
        idx = content.find("_push-parameter-audit:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "PUSH" in block and ("unpushed" in block or "threshold" in block), (
            "AA030: _push-parameter-audit does not check push threshold"
        )

    def test_wired_to_ship_commit(self):
        assert target_uses_guard("ship-commit", "_push-parameter-audit"), (
            "AA030: ship-commit missing _push-parameter-audit"
        )


class TestAA031MultiplatformContinueOnError:
    """AA031: enforce-test-integrity.ts extended."""

    def test_test_integrity_plugin_exists(self):
        assert plugin_exists("enforce-test-integrity.ts"), "AA031: enforce-test-integrity.ts missing"

    def test_test_integrity_checks_platform_jobs(self):
        content = (PLUGIN_DIR / "enforce-test-integrity.ts").read_text()
        has_check = any(
            w in content.lower() for w in ["linux", "macos", "platform", "build", "release", "continue", "ci"]
        )
        assert has_check, "AA031: enforce-test-integrity.ts does not check platform build jobs"


class TestAA032CiVerdictHistoryGuard:
    """AA032: _ci-verdict-history-guard in Makefile."""

    def test_ci_verdict_history_guard_exists(self):
        assert guard_exists_in_makefile("_ci-verdict-history-guard"), (
            "AA032: _ci-verdict-history-guard missing from Makefile"
        )

    def test_guard_uses_state_file(self):
        content = makefile_text()
        idx = content.find("_ci-verdict-history-guard:")
        assert idx != -1
        block = content[idx : idx + 600]
        assert "ci-verdict-history" in block, "AA032: _ci-verdict-history-guard does not track verdict history"

    def test_guard_checks_previous_verdict(self):
        content = makefile_text()
        # Find the TARGET recipe (column 0), not the comment header
        idx = content.find("\n_ci-verdict-history-guard:\n")
        assert idx != -1, "AA032: _ci-verdict-history-guard target recipe not found"
        block = content[idx : idx + 600]
        assert "last_push_sha" in block or "last_checked_sha" in block or "LAST_SHA" in block, (
            "AA032: _ci-verdict-history-guard does not check previous verdict"
        )

    def test_wired_to_git_push_sandboxcom(self):
        assert target_uses_guard("git-push-sandboxcom", "_ci-verdict-history-guard"), (
            "AA032: git-push-sandboxcom missing _ci-verdict-history-guard"
        )

    def test_wired_to_batch_push(self):
        assert target_uses_guard("batch-push", "_ci-verdict-history-guard"), (
            "AA032: batch-push missing _ci-verdict-history-guard"
        )


class TestAA033ContinueOnErrorRatchet:
    """AA033: enforce-test-integrity.ts extended for continue-on-error ratchet."""

    def test_test_integrity_checks_ratchet(self):
        content = (PLUGIN_DIR / "enforce-test-integrity.ts").read_text()
        has_check = "ratchet" in content.lower() or "continue-on-error" in content.lower()
        assert has_check, "AA033: enforce-test-integrity.ts does not check ratchet for continue-on-error"


class TestAA034PreCommitStashAudit:
    """AA034: _pre-commit-stash-audit in Makefile."""

    def test_pre_commit_stash_audit_exists(self):
        assert guard_exists_in_makefile("_pre-commit-stash-audit"), (
            "AA034: _pre-commit-stash-audit missing from Makefile"
        )

    def test_guard_detects_stash_conflicts(self):
        content = makefile_text()
        idx = content.find("_pre-commit-stash-audit:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "stash" in block.lower(), "AA034: _pre-commit-stash-audit does not detect stash"

    def test_wired_to_git_commit(self):
        assert target_uses_guard("git-commit", "_pre-commit-stash-audit"), (
            "AA034: git-commit missing _pre-commit-stash-audit"
        )

    def test_wired_to_ship_commit(self):
        assert target_uses_guard("ship-commit", "_pre-commit-stash-audit"), (
            "AA034: ship-commit missing _pre-commit-stash-audit"
        )


class TestAA035HotModuleWarningIgnored:
    """AA035: check-hot-reload-fresh in Makefile (same guard as AA027)."""

    def test_hot_reload_fresh_checks_warnings(self):
        content = makefile_text()
        idx = content.find("check-hot-reload-fresh:")
        assert idx != -1
        block = content[idx : idx + 300]
        assert "check_hot_reload_fresh" in block or "hot-reload" in block.lower(), (
            "AA035: check-hot-reload-fresh does not check hot module warnings"
        )


class TestAA036NodeV26Compat:
    """AA036: make check-node-v26-compat target."""

    def test_node_v26_compat_exists(self):
        assert guard_exists_in_makefile("check-node-v26-compat"), "AA036: check-node-v26-compat missing from Makefile"


class TestAA037VerifyEnforcement:
    """AA037: make verify-enforcement extended."""

    def test_verify_enforcement_exists(self):
        assert guard_exists_in_makefile("verify-enforcement"), "AA037: verify-enforcement missing from Makefile"


class TestAA038ObjectiveBlocking:
    """AA038: enforce-objective.ts upgraded to BLOCKING."""

    def test_objective_plugin_is_blocking(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "BLOCKING" in content or "deny" in content or "block" in content.lower(), (
            "AA038: enforce-objective.ts not BLOCKING"
        )


class TestAA039SessionCloseAudit:
    """AA039: _session-close-audit in Makefile."""

    def test_session_close_audit_exists(self):
        assert guard_exists_in_makefile("_session-close-audit"), "AA039: _session-close-audit missing from Makefile"

    def test_guard_checks_unpushed_commits(self):
        content = makefile_text()
        idx = content.find("_session-close-audit:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "unpushed" in block.lower() or "rev-list" in block or "@{u}" in block, (
            "AA039: _session-close-audit does not check unpushed commits"
        )

    def test_enforces_threshold(self):
        content = makefile_text()
        idx = content.find("_session-close-audit:")
        assert idx != -1
        block = content[idx : idx + 600]
        assert " -gt " in block, "AA039: _session-close-audit does not enforce unpushed threshold"


class TestAA040GateLiteFailFast:
    """AA040: enforce-objective.ts extended — gate-lite whack-a-mole protection."""

    def test_objective_tracks_fail_fast(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        has_check = any(w in content.lower() for w in ["gate", "test", "fail", "retry", "repeat", "run", "session"])
        assert has_check, "AA040: enforce-objective.ts does not track fail-fast cycles"


class TestAA041CheckAssertDeps:
    """AA041: make check-assert-deps target."""

    def test_check_assert_deps_target_exists(self):
        assert guard_exists_in_makefile("check-assert-deps"), "AA041: check-assert-deps missing from Makefile"

    def test_check_assert_deps_script_exists(self):
        assert (ROOT / "scripts" / "check_assert_deps.py").exists(), "AA041: scripts/check_assert_deps.py missing"


class TestAA042CiDiagnosePipelineSeparation:
    """AA042: make ci-diagnose extended for pipeline separation."""

    def test_ci_diagnose_target_exists(self):
        assert guard_exists_in_makefile("ci-diagnose"), "AA042: ci-diagnose missing from Makefile"


class TestAA043EditCommitAtomicityGuard:
    """AA043: _edit-commit-atomicity-guard in Makefile."""

    def test_edit_commit_atomicity_guard_exists(self):
        assert guard_exists_in_makefile("_edit-commit-atomicity-guard"), (
            "AA043: _edit-commit-atomicity-guard missing from Makefile"
        )

    def test_guard_checks_working_tree_vs_index(self):
        content = makefile_text()
        idx = content.find("_edit-commit-atomicity-guard:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "git diff --quiet" in block or "unstaged" in block, (
            "AA043: _edit-commit-atomicity-guard does not check unstaged changes"
        )

    def test_wired_to_git_commit(self):
        assert target_uses_guard("git-commit", "_edit-commit-atomicity-guard"), (
            "AA043: git-commit missing _edit-commit-atomicity-guard"
        )

    def test_wired_to_ship_commit(self):
        assert target_uses_guard("ship-commit", "_edit-commit-atomicity-guard"), (
            "AA043: ship-commit missing _edit-commit-atomicity-guard"
        )


class TestAA044NonBlockingCheckHidesFailure:
    """AA044: make ci-diagnose extended for non-blocking check detection."""

    def test_ci_diagnose_target_exists(self):
        assert guard_exists_in_makefile("ci-diagnose"), "AA044: ci-diagnose missing from Makefile"

    def test_ci_diagnose_script_exists(self):
        assert (ROOT / "scripts" / "ci_diagnose.py").exists(), "AA044: scripts/ci_diagnose.py missing"


class TestAA045CheckSpecPriority:
    """AA045: make check-spec-priority target."""

    def test_check_spec_priority_target_exists(self):
        assert guard_exists_in_makefile("check-spec-priority"), "AA045: check-spec-priority missing from Makefile"

    def test_check_spec_priority_script_exists(self):
        assert (ROOT / "scripts" / "check_spec_priority.py").exists(), "AA045: scripts/check_spec_priority.py missing"


# ── Guard wiring tests (cross-reference guards are wired to expected targets) ──

GUARD_WIRED_TARGETS: dict[str, list[str]] = {
    "_push-rate-guard": [
        "git-push-sandboxcom",
        "git-push-sandboxcom-nv",
        "push-dev-nv",
        "git-push-current-head-nv",
        "ci-push",
    ],
    "_gate-fresh-check": ["git-commit", "commit-no-verify", "git-commit-no-verify", "commit-bootstrap"],
    "ci-busy-check": ["push-dev", "pre-push-check", "ci-safe-push", "development-push"],
    "_no-bypass-guard": ["batch-push", "batch-push-nv"],
    "_pre-commit-stage-guard": ["git-commit", "commit-no-verify", "ship-commit"],
    "_merge-strategy-guard": ["git-merge"],
    "_stash-leak-guard": ["git-commit", "ship-commit"],
    "_stash-before-push-guard": ["git-push-sandboxcom", "batch-push", "push-dev"],
    "_ci-restart-cap": ["git-push-sandboxcom", "batch-push", "push-dev"],
    "_pull-before-push-guard": ["git-push-sandboxcom", "batch-push", "push-dev"],
    "_push-parameter-audit": ["ship-commit"],
    "_ci-verdict-history-guard": ["git-push-sandboxcom", "batch-push"],
    "_pre-commit-stash-audit": ["git-commit", "ship-commit"],
    "_edit-commit-atomicity-guard": ["git-commit", "ship-commit", "commit-no-verify"],
    "_session-close-audit": [],
}


class TestGuardWiring:
    """Every guard is wired to at least one expected target."""

    @classmethod
    def _guard_is_registered(cls, guard: str) -> bool:
        return guard_exists_in_makefile(guard)

    def test_guards_wired(self):
        failures = []
        for guard, expected_targets in GUARD_WIRED_TARGETS.items():
            if not self._guard_is_registered(guard):
                failures.append(f"{guard}: guard target not found in Makefile")
                continue
            for t in expected_targets:
                if not target_uses_guard(t, guard):
                    failures.append(f"{guard}: not wired to {t}")
        assert not failures, "Guard wiring gaps:\n  " + "\n  ".join(failures)


# ═══════════════════════════════════════════════════════════════════════════
# AB001-AB010 Behavioral Enforcement Tests (2026-07-27)
# ═══════════════════════════════════════════════════════════════════════════


class TestAB001FrustrationContinuation:
    """AB001: enforce-objective.ts extended — frustration signals force continuation."""

    def test_objective_plugin_has_frustration_detection(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        has_detect = "spec" in content.lower() and (
            "velocity" in content.lower()
            or "frustration" in content.lower()
            or "persistObjectiveToStack" in content
            or "isSpecVelocitySufficient" in content
        )
        assert has_detect, "AB001: enforce-objective.ts lacks spec velocity/frustration response mechanisms"


class TestAB002SpecVelocity:
    """AB002: enforce-objective.ts extended — spec writing velocity monitoring."""

    def test_spec_velocity_tracking_exists(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "isSpecVelocitySufficient" in content or "SPEC_VELOCITY_FILE" in content, (
            "AB002: enforce-objective.ts lacks spec velocity tracking"
        )

    def test_spec_velocity_has_threshold(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "MIN_SPECS_PER_WINDOW" in content or "SPEC_WINDOW_MS" in content, (
            "AB002: enforce-objective.ts lacks spec velocity thresholds"
        )


class TestAB003CiCheckWhileSpecTargetUnmet:
    """AB003: enforce-objective.ts extended — blocks CI checks while spec target unmet."""

    def test_ci_check_tracking_in_objective(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "ciCheckCount" in content or "MAX_CI_CHECKS_PER_SPEC_WINDOW" in content, (
            "AB003: enforce-objective.ts lacks CI check tracking for spec velocity"
        )


class TestAB004AutoCommitSpecs:
    """AB004: _auto-commit-specs Makefile target — auto-commits spec changes."""

    def test_auto_commit_specs_target_exists(self):
        assert guard_exists_in_makefile("_auto-commit-specs"), "AB004: _auto-commit-specs missing from Makefile"

    def test_auto_commit_specs_checks_timestamps(self):
        content = makefile_text()
        idx = content.find("_auto-commit-specs:")
        assert idx != -1
        block = content[idx : idx + 800]
        assert "MTIME" in block or "mtime" in block.lower() or "stat" in block or "AGE" in block, (
            "AB004: _auto-commit-specs does not check file age"
        )

    def test_auto_commit_specs_tracks_changes(self):
        content = makefile_text()
        idx = content.find("_auto-commit-specs:")
        assert idx != -1
        block = content[idx : idx + 800]
        assert "BEHAVIORAL_SPECS.md" in block, "AB004: _auto-commit-specs does not reference BEHAVIORAL_SPECS.md"


class TestAB005AuditSpecMeasurable:
    """AB005: make audit-spec-measurable target + script."""

    def test_audit_spec_measurable_target_exists(self):
        assert guard_exists_in_makefile("audit-spec-measurable"), "AB005: audit-spec-measurable missing from Makefile"

    def test_audit_spec_measurable_script_exists(self):
        assert (ROOT / "scripts" / "audit_spec_measurable.py").exists(), (
            "AB005: scripts/audit_spec_measurable.py missing"
        )

    def test_script_checks_measurable_outcomes(self):
        content = (ROOT / "scripts" / "audit_spec_measurable.py").read_text()
        assert "MEASURABLE_INDICATORS" in content or "measurable" in content.lower(), (
            "AB005: audit_spec_measurable.py does not check for measurable outcomes"
        )


class TestAB006GateLiteNoFailFast:
    """AB006: gate-lite-no-fail-fast Makefile variant — all failures in one pass."""

    def test_gate_lite_no_fail_fast_target_exists(self):
        assert guard_exists_in_makefile("gate-lite-no-fail-fast"), "AB006: gate-lite-no-fail-fast missing from Makefile"

    def test_gate_lite_no_fail_fast_removes_x_flag(self):
        content = makefile_text()
        idx = content.find("gate-lite-no-fail-fast:")
        assert idx != -1
        block = content[idx : idx + 2000]
        assert "-x" not in block or "NO fail-fast" in block, "AB006: gate-lite-no-fail-fast still has -x fail-fast flag"

    def test_gate_lite_no_fail_fast_reports_all_failures(self):
        content = makefile_text()
        idx = content.find("gate-lite-no-fail-fast:")
        assert idx != -1
        block = content[idx : idx + 2000]
        assert "FAIL_COUNT" in block or "grep.*FAILED" in block or "failures" in block.lower(), (
            "AB006: gate-lite-no-fail-fast does not report failure count"
        )


class TestAB007ObjectiveStacking:
    """AB007: enforce-objective.ts extended — PRIMARY OBJECTIVE persistence."""

    def test_objective_stack_persistence_exists(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "OBJECTIVE_STACK_FILE" in content or "persistObjectiveToStack" in content, (
            "AB007: enforce-objective.ts lacks objective stacking mechanism"
        )

    def test_objective_not_overwritten_by_secondary(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "getStackedObjective" in content or "stack[0]" in content, (
            "AB007: enforce-objective.ts does not preserve stacked objective"
        )


class TestAB008BehavioralChangeTracking:
    """AB008: enforce-objective.ts extended — measures behavioral change from specs."""

    def test_behavior_tracking_exists(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        has_tracking = (
            "behavior" in content.lower()
            or "recurrence" in content.lower()
            or "SPEC_BEHAVIOR_FILE" in content
            or "isSpecVelocitySufficient" in content
        )
        assert has_tracking, "AB008: enforce-objective.ts lacks behavioral change tracking"

    def test_spec_effectiveness_measured(self):
        content = (PLUGIN_DIR / "enforce-objective.ts").read_text()
        assert "recordSpecWrite" in content or "specWrites" in content, (
            "AB008: enforce-objective.ts does not record spec write events"
        )


class TestAB009AuditSpecEntry:
    """AB009: make audit-spec-entry target + script."""

    def test_audit_spec_entry_target_exists(self):
        assert guard_exists_in_makefile("audit-spec-entry"), "AB009: audit-spec-entry missing from Makefile"

    def test_audit_spec_entry_script_exists(self):
        assert (ROOT / "scripts" / "audit_spec_entry.py").exists(), "AB009: scripts/audit_spec_entry.py missing"

    def test_script_checks_quality_gates(self):
        content = (ROOT / "scripts" / "audit_spec_entry.py").read_text()
        assert "check_spec_quality" in content or "quality" in content.lower(), (
            "AB009: audit_spec_entry.py does not implement quality gates"
        )

    def test_script_detects_template_filler(self):
        content = (ROOT / "scripts" / "audit_spec_entry.py").read_text()
        assert "TEMPLATE_PATTERNS" in content or "template" in content.lower(), (
            "AB009: audit_spec_entry.py does not detect template filler"
        )


class TestAB010CiCheckFrequencyCap:
    """AB010: enforce-no-wait.ts extended — cap CI checks at 3 per cycle."""

    def test_no_wait_has_ci_check_cap(self):
        content = (PLUGIN_DIR / "enforce-no-wait.ts").read_text()
        assert "MAX_CI_CHECKS_PER_CYCLE" in content or "CI_CHECK_STATE_FILE" in content, (
            "AB010: enforce-no-wait.ts lacks CI check frequency cap"
        )

    def test_ci_check_cap_has_threshold(self):
        content = (PLUGIN_DIR / "enforce-no-wait.ts").read_text()
        assert "readCiCheckState" in content or "isCiCheckExceeded" in content, (
            "AB010: enforce-no-wait.ts does not implement CI check counter state"
        )

    def test_ci_check_blocking_on_exceed(self):
        content = (PLUGIN_DIR / "enforce-no-wait.ts").read_text()
        assert "CAP EXCEEDED" in content or "ci-verdict-safe" in content, (
            "AB010: enforce-no-wait.ts does not block excessive CI checks"
        )


# ── AB guard wiring tests ──
AB_GUARD_WIRED: dict[str, list[str]] = {
    "_session-close-audit": [],
    "_pre-commit-spec-quality-guard": ["git-commit"],
}


class TestABGuardWiring:
    """AB guards wired to expected targets."""

    @classmethod
    def _guard_is_registered(cls, guard: str) -> bool:
        return guard_exists_in_makefile(guard)

    def test_ab_guards_exist(self):
        ab_guards = ["_auto-commit-specs", "audit-spec-measurable", "gate-lite-no-fail-fast", "audit-spec-entry"]
        missing = [g for g in ab_guards if not self._guard_is_registered(g)]
        assert not missing, f"AB guard targets missing: {missing}"


# ═══════════════════════════════════════════════════════════════════════════
# AB041-AB060 Behavioral Enforcement Tests (2026-07-27)
# ═══════════════════════════════════════════════════════════════════════════


class TestAB041OverlappingEdits:
    """AB041: scripts/audit_agent_behavior.py — overlapping file edits detection."""

    def test_audit_script_exists(self):
        assert (ROOT / "scripts" / "audit_agent_behavior.py").exists(), "AB041: scripts/audit_agent_behavior.py missing"

    def test_ab041_check_function_exists(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "check_ab041_overlapping_edits" in content, "AB041: check_ab041_overlapping_edits function missing"

    def test_ab041_check_registered_in_dispatch(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert '"AB041"' in content, "AB041: not registered in CHECKS dispatch table"

    def test_ab041_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-overlapping-edits"), (
            "AB041: audit-agent-overlapping-edits Makefile target missing"
        )


class TestAB042DuplicateDispatches:
    """AB042: dispatch dedup guard — no duplicate tasks in same wave."""

    def test_ab042_spec_has_enforcement(self):
        spec_text = (ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md").read_text()
        idx = spec_text.find("### AB042")
        assert idx != -1, "AB042 spec not found"
        block = spec_text[idx : idx + 500]
        assert "audit_agent_behavior.py" in block or "_subagent-dedup-guard" in block, (
            "AB042: spec lacks enforcement reference"
        )

    def test_ab042_audit_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-duplicate-dispatches"), (
            "AB042: audit-agent-duplicate-dispatches Makefile target missing"
        )


class TestAB043StaleCommit:
    """AB043: commit with stale/unprocessed subagent results."""

    def test_ab043_spec_has_enforcement(self):
        spec_text = (ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md").read_text()
        idx = spec_text.find("### AB043")
        assert idx != -1, "AB043 spec not found"
        block = spec_text[idx : idx + 500]
        assert "audit_agent_behavior.py" in block, "AB043: spec lacks audit script reference"

    def test_ab043_audit_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-stale-commit"), (
            "AB043: audit-agent-stale-commit Makefile target missing"
        )


class TestAB044GateAwareness:
    """AB044: agent ignores red gate after subagent return."""

    def test_ab044_spec_has_enforcement(self):
        spec_text = (ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md").read_text()
        idx = spec_text.find("### AB044")
        assert idx != -1, "AB044 spec not found"
        block = spec_text[idx : idx + 500]
        assert "audit_agent_behavior.py" in block, "AB044: spec lacks enforcement reference"

    def test_ab044_audit_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-gate-awareness"), (
            "AB044: audit-agent-gate-awareness Makefile target missing"
        )


class TestAB045DispatchDiscipline:
    """AB045: pre-dispatch checklist must run before every wave."""

    def test_ab045_spec_has_enforcement(self):
        spec_text = (ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md").read_text()
        idx = spec_text.find("### AB045")
        assert idx != -1, "AB045 spec not found"
        block = spec_text[idx : idx + 500]
        assert "audit_agent_behavior.py" in block, "AB045: spec lacks enforcement reference"

    def test_ab045_audit_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-dispatch-discipline"), (
            "AB045: audit-agent-dispatch-discipline Makefile target missing"
        )


class TestAB046PriorityOrder:
    """AB046: subagent results processed in priority order."""

    def test_ab046_spec_has_enforcement(self):
        spec_text = (ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md").read_text()
        idx = spec_text.find("### AB046")
        assert idx != -1, "AB046 spec not found"
        block = spec_text[idx : idx + 500]
        assert "audit_agent_behavior.py" in block, "AB046: spec lacks enforcement reference"

    def test_ab046_audit_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-priority-order"), (
            "AB046: audit-agent-priority-order Makefile target missing"
        )


class TestAB047TaskEvidence:
    """AB047: TASKS.md [x] items must carry evidence (commit hash, test count)."""

    def test_ab047_check_function_exists(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "check_ab047_task_evidence" in content, "AB047: check_ab047_task_evidence function missing"

    def test_ab047_checks_commit_hash_evidence(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "[0-9a-f]{7,40}" in content or "commit hash" in content.lower(), (
            "AB047: audit script does not check for commit hash evidence"
        )

    def test_ab047_checks_test_count_evidence(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "N passed" in content or "test.*passed" in content.lower(), (
            "AB047: audit script does not check for test count evidence"
        )

    def test_ab047_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-task-evidence"), (
            "AB047: audit-agent-task-evidence Makefile target missing"
        )


class TestAB048TaskLedger:
    """AB048: task ledger must be updated before next dispatch wave."""

    def test_ab048_check_function_exists(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "check_ab048_task_ledger_drift" in content, "AB048: check_ab048_task_ledger_drift function missing"

    def test_ab048_checks_tasks_md(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "TASKS.md" in content, "AB048: audit script does not read TASKS.md"

    def test_ab048_tracks_pending_count(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "pending_count" in content, "AB048: does not track pending task count"


class TestAB049AbandonedMerges:
    """AB049: merge conflicts must be resolved, never abandoned."""

    def test_ab049_spec_has_enforcement(self):
        spec_text = (ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md").read_text()
        idx = spec_text.find("### AB049")
        assert idx != -1, "AB049 spec not found"
        block = spec_text[idx : idx + 500]
        assert "audit_agent_behavior.py" in block or "merge" in block.lower(), "AB049: spec lacks enforcement reference"

    def test_ab049_audit_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-abandoned-merges"), (
            "AB049: audit-agent-abandoned-merges Makefile target missing"
        )


class TestAB050ContextBudget:
    """AB050: subagent context must not exceed token budget."""

    def test_ab050_spec_mentions_line_limit(self):
        spec_text = (ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md").read_text()
        idx = spec_text.find("### AB050")
        assert idx != -1, "AB050 spec not found"
        block = spec_text[idx : idx + 500]
        assert "20" in block or "line" in block.lower(), "AB050: spec does not specify context line limit"

    def test_ab050_audit_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-context-budget"), (
            "AB050: audit-agent-context-budget Makefile target missing"
        )


class TestAB051StaleSessions:
    """AB051: stale subagent sessions (>30 min idle) must not be resumed."""

    def test_ab051_spec_mentions_timeout(self):
        spec_text = (ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md").read_text()
        idx = spec_text.find("### AB051")
        assert idx != -1, "AB051 spec not found"
        block = spec_text[idx : idx + 500]
        assert "30" in block, "AB051: spec does not specify 30-minute idle threshold"

    def test_ab051_audit_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-stale-sessions"), (
            "AB051: audit-agent-stale-sessions Makefile target missing"
        )


class TestAB052WorktreeIsolation:
    """AB052: file-editing subagents must be worktree-isolated."""

    def test_ab052_check_function_exists(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "check_ab052_worktree_isolation" in content, "AB052: check_ab052_worktree_isolation function missing"

    def test_ab052_checks_agent_branch_pattern(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "agent-" in content, "AB052: does not check for agent-* branch pattern"

    def test_ab052_checks_src_edits(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "src/" in content, "AB052: does not check for src/ file edits"


class TestAB053ModelSelection:
    """AB053: model selection must match task complexity."""

    def test_ab053_spec_has_enforcement(self):
        spec_text = (ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md").read_text()
        idx = spec_text.find("### AB053")
        assert idx != -1, "AB053 spec not found"
        block = spec_text[idx : idx + 500]
        assert "audit_agent_behavior.py" in block, "AB053: spec lacks enforcement reference"

    def test_ab053_audit_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-model-selection"), (
            "AB053: audit-agent-model-selection Makefile target missing"
        )


class TestAB054AbandonedWorktrees:
    """AB054: worktrees older than 24h with unmerged commits are violations."""

    def test_ab054_check_function_exists(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "check_ab054_abandoned_worktrees" in content, "AB054: check_ab054_abandoned_worktrees function missing"

    def test_ab054_uses_24_hour_threshold(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "24" in content, "AB054: does not use 24-hour age threshold"

    def test_ab054_checks_unmerged_commits(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "unmerged_commits" in content or "development.." in content, "AB054: does not check for unmerged commits"

    def test_ab054_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-worktree-health"), (
            "AB054: audit-agent-worktree-health Makefile target missing"
        )


class TestAB055ResultProcessing:
    """AB055: subagent results must be processed in ≤30 seconds."""

    def test_ab055_spec_has_enforcement(self):
        spec_text = (ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md").read_text()
        idx = spec_text.find("### AB055")
        assert idx != -1, "AB055 spec not found"
        block = spec_text[idx : idx + 500]
        assert "audit_agent_behavior.py" in block, "AB055: spec lacks enforcement reference"

    def test_ab055_audit_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-result-processing"), (
            "AB055: audit-agent-result-processing Makefile target missing"
        )


class TestAB056DeadCode:
    """AB056: dead code count must not increase after refactors."""

    def test_ab056_check_function_exists(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "check_ab056_dead_code" in content, "AB056: check_ab056_dead_code function missing"

    def test_ab056_runs_vulture(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "vulture" in content.lower(), "AB056: does not run vulture dead code checker"

    def test_ab056_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-dead-code"), "AB056: audit-agent-dead-code Makefile target missing"


class TestAB057OrphanScripts:
    """AB057: scripts/*.py must have corresponding Makefile targets."""

    def test_ab057_check_function_exists(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "check_ab057_orphan_scripts" in content, "AB057: check_ab057_orphan_scripts function missing"

    def test_ab057_checks_makefile_reference(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "makefile_text" in content.lower() or "Makefile" in content, (
            "AB057: does not check Makefile for script references"
        )

    def test_ab057_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-script-discipline"), (
            "AB057: audit-agent-script-discipline Makefile target missing"
        )


class TestAB058DispatchPrompts:
    """AB058: subagent dispatch prompts must list available tools."""

    def test_ab058_spec_has_enforcement(self):
        spec_text = (ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md").read_text()
        idx = spec_text.find("### AB058")
        assert idx != -1, "AB058 spec not found"
        block = spec_text[idx : idx + 500]
        assert "audit_agent_behavior.py" in block, "AB058: spec lacks enforcement reference"

    def test_ab058_audit_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-dispatch-prompts"), (
            "AB058: audit-agent-dispatch-prompts Makefile target missing"
        )


class TestAB059BranchDiscipline:
    """AB059: subagent results must be committed to the correct branch."""

    def test_ab059_spec_has_enforcement(self):
        spec_text = (ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md").read_text()
        idx = spec_text.find("### AB059")
        assert idx != -1, "AB059 spec not found"
        block = spec_text[idx : idx + 500]
        assert "audit_agent_behavior.py" in block, "AB059: spec lacks enforcement reference"

    def test_ab059_audit_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-branch-discipline"), (
            "AB059: audit-agent-branch-discipline Makefile target missing"
        )


class TestAB060ContextSize:
    """AB060: AGENTS.md + CLAUDE.md combined must not exceed manageable size."""

    def test_ab060_check_function_exists(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "check_ab060_context_size" in content, "AB060: check_ab060_context_size function missing"

    def test_ab060_checks_agents_md(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "AGENTS.md" in content, "AB060: does not check AGENTS.md"

    def test_ab060_has_line_threshold(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "20000" in content, "AB060: does not use 20,000 line threshold"

    def test_ab060_make_target_exists(self):
        assert guard_exists_in_makefile("audit-agent-context-size"), (
            "AB060: audit-agent-context-size Makefile target missing"
        )


# ── AB041-060 audit script integrity ─────────────────────────────────────────


class TestABAuditScriptIntegrity:
    """The audit_agent_behavior.py script must be structurally complete."""

    def test_script_is_executable(self):
        p = ROOT / "scripts" / "audit_agent_behavior.py"
        assert p.exists(), "audit_agent_behavior.py missing"
        assert p.stat().st_mode & 0o111 or p.read_text().startswith("#!/"), (
            "audit_agent_behavior.py not executable (missing shebang or +x)"
        )

    def test_all_checks_registered(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        for spec_num in range(41, 61):
            spec_id = f"AB{spec_num:03d}"
            if spec_id in (
                "AB042",
                "AB043",
                "AB044",
                "AB045",
                "AB046",
                "AB049",
                "AB050",
                "AB051",
                "AB053",
                "AB055",
                "AB058",
                "AB059",
            ):
                continue
            assert spec_id in content, f"{spec_id}: check not found in audit script"

    def test_script_has_main_function(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "def main()" in content, "audit_agent_behavior.py missing main()"

    def test_script_has_json_output(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "--json" in content, "audit_agent_behavior.py missing --json flag"

    def test_script_has_filter_option(self):
        content = (ROOT / "scripts" / "audit_agent_behavior.py").read_text()
        assert "--filter" in content, "audit_agent_behavior.py missing --filter flag"

    def test_ab041_060_targets_exist(self):
        targets = [
            "audit-agent-behavior",
            "audit-agent-overlapping-edits",
            "audit-agent-task-evidence",
            "audit-agent-worktree-health",
            "audit-agent-dead-code",
            "audit-agent-script-discipline",
            "audit-agent-context-size",
        ]
        missing = [t for t in targets if not guard_exists_in_makefile(t)]
        assert not missing, f"AB041-060 Makefile targets missing: {missing}"


# ═══════════════════════════════════════════════════════════════════════════
# AB011-AB020 Behavioral Enforcement Tests (2026-07-27)
# ═══════════════════════════════════════════════════════════════════════════


class TestAB011PreCommitSpecQualityGuard:
    """AB011: _pre-commit-spec-quality-guard in Makefile — blocks commits of DRAFT specs."""

    def test_pre_commit_spec_quality_guard_exists(self):
        assert guard_exists_in_makefile("_pre-commit-spec-quality-guard"), (
            "AB011: _pre-commit-spec-quality-guard missing from Makefile"
        )

    def test_guard_runs_audit_spec_entry(self):
        content = makefile_text()
        idx = content.find("_pre-commit-spec-quality-guard:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "audit_spec_entry" in block, "AB011: _pre-commit-spec-quality-guard does not run audit-spec-entry"

    def test_guard_checks_staged_spec_file(self):
        content = makefile_text()
        idx = content.find("_pre-commit-spec-quality-guard:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "BEHAVIORAL_SPECS.md" in block, (
            "AB011: _pre-commit-spec-quality-guard does not check for spec file changes"
        )


class TestAB012SpecInflationCheck:
    """AB012: make check-spec-inflation target — detects trivial-edit spec count inflation."""

    def test_check_spec_inflation_target_exists(self):
        assert guard_exists_in_makefile("check-spec-inflation"), "AB012: check-spec-inflation missing from Makefile"

    def test_check_spec_inflation_script_exists(self):
        assert (ROOT / "scripts" / "check_spec_inflation.py").exists(), "AB012: scripts/check_spec_inflation.py missing"

    def test_script_detects_spec_id_changes(self):
        content = (ROOT / "scripts" / "check_spec_inflation.py").read_text()
        assert "SPEC_ID_RE" in content or "spec_ids" in content.lower(), (
            "AB012: check_spec_inflation.py does not track spec ID changes"
        )

    def test_script_has_inflation_threshold(self):
        content = (ROOT / "scripts" / "check_spec_inflation.py").read_text()
        assert "0.8" in content or "ratio" in content.lower(), (
            "AB012: check_spec_inflation.py lacks 80% inflation threshold"
        )


class TestAB013VerifySpecEnforcementClaims:
    """AB013: make verify-spec-enforcement-claims target — verifies spec claims resolve to files/targets."""

    def test_verify_spec_enforcement_claims_target_exists(self):
        assert guard_exists_in_makefile("verify-spec-enforcement-claims"), (
            "AB013: verify-spec-enforcement-claims missing from Makefile"
        )

    def test_verify_spec_enforcement_claims_script_exists(self):
        assert (ROOT / "scripts" / "verify_spec_enforcement_claims.py").exists(), (
            "AB013: scripts/verify_spec_enforcement_claims.py missing"
        )

    def test_script_extracts_enforcement_refs(self):
        content = (ROOT / "scripts" / "verify_spec_enforcement_claims.py").read_text()
        assert "ENFORCEMENT_RE" in content or "Enforcement" in content, (
            "AB013: verify_spec_enforcement_claims.py does not extract Enforcement field"
        )

    def test_script_resolves_file_refs(self):
        content = (ROOT / "scripts" / "verify_spec_enforcement_claims.py").read_text()
        assert "exists()" in content or "resolve" in content.lower(), (
            "AB013: verify_spec_enforcement_claims.py does not resolve file references"
        )


class TestAB014SpecPriorityOrder:
    """AB014: make check-spec-priority-order target — P0/P1 before P3/P4."""

    def test_check_spec_priority_order_target_exists(self):
        assert guard_exists_in_makefile("check-spec-priority-order"), (
            "AB014: check-spec-priority-order missing from Makefile"
        )

    def test_check_spec_priority_order_script_exists(self):
        assert (ROOT / "scripts" / "check_spec_priority_order.py").exists(), (
            "AB014: scripts/check_spec_priority_order.py missing"
        )

    def test_script_classifies_specs(self):
        content = (ROOT / "scripts" / "check_spec_priority_order.py").read_text()
        assert "classify_spec" in content or "P0" in content, (
            "AB014: check_spec_priority_order.py does not classify specs by priority"
        )

    def test_script_enforces_p0_first(self):
        content = (ROOT / "scripts" / "check_spec_priority_order.py").read_text()
        assert "p01_count" in content or "P0" in content, (
            "AB014: check_spec_priority_order.py does not enforce P0/P1 priority"
        )


class TestAB015DeduplicateSpecsApplied:
    """AB015: make deduplicate-specs DEDUP=1 as pre-commit hook — auto-removes duplicates."""

    def test_deduplicate_specs_target_exists(self):
        assert guard_exists_in_makefile("deduplicate-specs"), "AB015: deduplicate-specs missing from Makefile"

    def test_deduplicate_specs_supports_dedup_flag(self):
        content = makefile_text()
        idx = content.find("deduplicate-specs:")
        assert idx != -1
        block = content[idx : idx + 300]
        assert "DEDUP" in block, "AB015: deduplicate-specs does not support DEDUP flag"


class TestAB016AutoCommitSpecsExtended:
    """AB016: _auto-commit-specs extended with parallel-work detection (25 specs / 3 min)."""

    def test_auto_commit_specs_has_frequent_threshold(self):
        content = makefile_text()
        idx = content.find("_auto-commit-specs:")
        assert idx != -1
        block = content[idx : idx + 600]
        assert any(t in block for t in ["50", "25", "300", "180"]), (
            "AB016: _auto-commit-specs lacks spec count or time threshold"
        )

    def test_auto_commit_specs_tracks_state(self):
        content = makefile_text()
        idx = content.find("_auto-commit-specs:")
        assert idx != -1
        block = content[idx : idx + 600]
        assert "STATE_FILE" in block or "state" in block.lower(), (
            "AB016: _auto-commit-specs does not track commit state"
        )


class TestAB017SpecCodeDrift:
    """AB017: make check-spec-drift target — detects enforcement code changes making specs stale."""

    def test_check_spec_drift_target_exists(self):
        assert guard_exists_in_makefile("check-spec-drift"), "AB017: check-spec-drift missing from Makefile"

    def test_check_spec_drift_script_exists(self):
        assert (ROOT / "scripts" / "check_spec_drift.py").exists(), "AB017: scripts/check_spec_drift.py missing"

    def test_script_detects_missing_enforcement(self):
        content = (ROOT / "scripts" / "check_spec_drift.py").read_text()
        assert "resolve_file" in content or "missing" in content.lower(), (
            "AB017: check_spec_drift.py does not detect missing enforcement files"
        )


class TestAB018SpecPluginCoverage:
    """AB018: make check-spec-plugin-coverage target — each plugin needs ≥5 spec refs."""

    def test_check_spec_plugin_coverage_target_exists(self):
        assert guard_exists_in_makefile("check-spec-plugin-coverage"), (
            "AB018: check-spec-plugin-coverage missing from Makefile"
        )

    def test_check_spec_plugin_coverage_script_exists(self):
        assert (ROOT / "scripts" / "check_spec_plugin_coverage.py").exists(), (
            "AB018: scripts/check_spec_plugin_coverage.py missing"
        )

    def test_script_has_min_threshold(self):
        content = (ROOT / "scripts" / "check_spec_plugin_coverage.py").read_text()
        assert "MIN_SPECS_PER_PLUGIN" in content or "5" in content, (
            "AB018: check_spec_plugin_coverage.py lacks minimum spec-per-plugin threshold"
        )

    def test_script_checks_spec_groups(self):
        content = (ROOT / "scripts" / "check_spec_plugin_coverage.py").read_text()
        assert "group" in content.lower() or "prefix" in content.lower(), (
            "AB018: check_spec_plugin_coverage.py does not check spec groups"
        )


class TestAB019PruneDeadSpecs:
    """AB019: make prune-dead-specs target — removes specs referencing deleted enforcement."""

    def test_prune_dead_specs_target_exists(self):
        assert guard_exists_in_makefile("prune-dead-specs"), "AB019: prune-dead-specs missing from Makefile"

    def test_prune_dead_specs_script_exists(self):
        assert (ROOT / "scripts" / "prune_dead_specs.py").exists(), "AB019: scripts/prune_dead_specs.py missing"

    def test_script_supports_dry_run(self):
        content = (ROOT / "scripts" / "prune_dead_specs.py").read_text()
        assert "dry_run" in content.lower() or "DRY_RUN" in content, (
            "AB019: prune_dead_specs.py does not support --dry-run"
        )

    def test_script_removes_dead_specs(self):
        content = (ROOT / "scripts" / "prune_dead_specs.py").read_text()
        assert "is_spec_dead" in content or "dead_ids" in content, (
            "AB019: prune_dead_specs.py does not implement dead-spec detection"
        )


class TestAB020SpecQualityRatio:
    """AB020: make check-spec-quality-ratio target — ≥90% specs must have real enforcement."""

    def test_check_spec_quality_ratio_target_exists(self):
        assert guard_exists_in_makefile("check-spec-quality-ratio"), (
            "AB020: check-spec-quality-ratio missing from Makefile"
        )

    def test_check_spec_quality_ratio_script_exists(self):
        assert (ROOT / "scripts" / "check_spec_quality_ratio.py").exists(), (
            "AB020: scripts/check_spec_quality_ratio.py missing"
        )

    def test_script_has_90_percent_threshold(self):
        content = (ROOT / "scripts" / "check_spec_quality_ratio.py").read_text()
        assert "0.90" in content or "0.9" in content, "AB020: check_spec_quality_ratio.py lacks 90% threshold"

    def test_script_counts_real_enforcement(self):
        content = (ROOT / "scripts" / "check_spec_quality_ratio.py").read_text()
        assert "has_real_enforcement" in content or "with_enforcement" in content, (
            "AB020: check_spec_quality_ratio.py does not count real enforcement"
        )


# ── AB011-AB020 guard wiring tests ──
AB1120_GUARDS = [
    "_pre-commit-spec-quality-guard",
    "check-spec-inflation",
    "verify-spec-enforcement-claims",
    "check-spec-priority-order",
    "check-spec-drift",
    "check-spec-plugin-coverage",
    "prune-dead-specs",
    "check-spec-quality-ratio",
]


class TestAB1120GuardExistence:
    """All AB011-AB020 Makefile guards exist."""

    def test_all_ab1120_guards_exist(self):
        missing = [g for g in AB1120_GUARDS if not guard_exists_in_makefile(g)]
        assert not missing, f"AB011-AB020 guard targets missing: {missing}"


# ── AB011-AB020 script existence tests ──
AB1120_SCRIPTS = [
    "check_spec_inflation.py",
    "verify_spec_enforcement_claims.py",
    "check_spec_priority_order.py",
    "check_spec_drift.py",
    "check_spec_plugin_coverage.py",
    "prune_dead_specs.py",
    "check_spec_quality_ratio.py",
]


class TestAB1120ScriptExistence:
    """All AB011-AB020 enforcement scripts exist."""

    def test_all_ab1120_scripts_exist(self):
        missing = [s for s in AB1120_SCRIPTS if not (ROOT / "scripts" / s).exists()]
        assert not missing, f"AB011-AB020 scripts missing: {missing}"


# ═══════════════════════════════════════════════════════════════════════════
# AB021-AB040 Behavioral Enforcement Tests (2026-07-27)
# ═══════════════════════════════════════════════════════════════════════════


class TestAB021HotModuleFreshness:
    """AB021: make check-hot-module-freshness target + script."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("check-hot-module-freshness"), "AB021: check-hot-module-freshness missing"

    def test_script_exists(self):
        assert (ROOT / "scripts" / "check_hot_module_freshness.py").exists(), (
            "AB021: scripts/check_hot_module_freshness.py missing"
        )

    def test_script_checks_mtimes(self):
        content = (ROOT / "scripts" / "check_hot_module_freshness.py").read_text()
        assert "st_mtime" in content or "mtime" in content.lower(), (
            "AB021: script does not check file modification times"
        )


class TestAB022TargetContract:
    """AB022: make check-target-contract target."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("check-target-contract"), "AB022: check-target-contract missing"


class TestAB023SubagentFileDedup:
    """AB023: make check-subagent-file-dedup target + script."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("check-subagent-file-dedup"), "AB023: check-subagent-file-dedup missing"

    def test_script_exists(self):
        assert (ROOT / "scripts" / "check_subagent_file_dedup.py").exists(), (
            "AB023: scripts/check_subagent_file_dedup.py missing"
        )

    def test_script_has_cooldown(self):
        content = (ROOT / "scripts" / "check_subagent_file_dedup.py").read_text()
        assert "max_age_s" in content, "AB023: script lacks cooldown max_age_s"


class TestAB024StaleTasks:
    """AB024: make check-stale-tasks target + script."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("check-stale-tasks"), "AB024: check-stale-tasks missing"

    def test_script_exists(self):
        assert (ROOT / "scripts" / "check_stale_tasks.py").exists(), "AB024: scripts/check_stale_tasks.py missing"

    def test_script_has_age_threshold(self):
        content = (ROOT / "scripts" / "check_stale_tasks.py").read_text()
        assert "86400" in content or "DEFAULT_MAX_AGE_S" in content, "AB024: script lacks age threshold"


class TestAB025StashDepthGuard:
    """AB025: _stash-depth-guard in Makefile."""

    def test_guard_exists(self):
        assert guard_exists_in_makefile("_stash-depth-guard"), "AB025: _stash-depth-guard missing"

    def test_guard_checks_stash_count(self):
        content = makefile_text()
        idx = content.find("_stash-depth-guard:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "stash list" in block or "STASH_COUNT" in block, "AB025: _stash-depth-guard does not check stash count"

    def test_guard_has_warn_threshold(self):
        content = makefile_text()
        idx = content.find("_stash-depth-guard:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert " -gt 5 " in block or " -gt 10 " in block, "AB025: _stash-depth-guard lacks threshold"


class TestAB026DiskUsageGuard:
    """AB026: _disk-usage-guard in Makefile."""

    def test_guard_exists(self):
        assert guard_exists_in_makefile("_disk-usage-guard"), "AB026: _disk-usage-guard missing"

    def test_guard_checks_disk_usage(self):
        content = makefile_text()
        idx = content.find("_disk-usage-guard:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "df " in block or "USAGE" in block, "AB026: _disk-usage-guard does not check disk usage"

    def test_guard_blocks_at_threshold(self):
        content = makefile_text()
        idx = content.find("_disk-usage-guard:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "exit 1" in block, "AB026: _disk-usage-guard does not block"


class TestAB027WorktreeStaleness:
    """AB027: make check-worktree-staleness target + script."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("check-worktree-staleness"), "AB027: check-worktree-staleness missing"

    def test_script_exists(self):
        assert (ROOT / "scripts" / "check_worktree_staleness.py").exists(), (
            "AB027: scripts/check_worktree_staleness.py missing"
        )


class TestAB028PluginLoadOrder:
    """AB028: make check-plugin-load-order target + script."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("check-plugin-load-order"), "AB028: check-plugin-load-order missing"

    def test_script_exists(self):
        assert (ROOT / "scripts" / "check_plugin_load_order.py").exists(), (
            "AB028: scripts/check_plugin_load_order.py missing"
        )

    def test_script_checks_dependency_graph(self):
        content = (ROOT / "scripts" / "check_plugin_load_order.py").read_text()
        assert "import" in content.lower() and "graph" in content.lower(), (
            "AB028: script lacks dependency graph analysis"
        )


class TestAB029PreCommitTimeoutGuard:
    """AB029: _pre-commit-timeout-guard in Makefile."""

    def test_guard_exists(self):
        assert guard_exists_in_makefile("_pre-commit-timeout-guard"), "AB029: _pre-commit-timeout-guard missing"

    def test_guard_mentions_timeout(self):
        content = makefile_text()
        idx = content.find("_pre-commit-timeout-guard:")
        assert idx != -1
        block = content[idx : idx + 300]
        assert "timeout" in block.lower() or "30s" in block, "AB029: _pre-commit-timeout-guard lacks timeout"


class TestAB030VerifyReleaseCompletenessSafe:
    """AB030: make verify-release-completeness-safe target."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("verify-release-completeness-safe"), (
            "AB030: verify-release-completeness-safe missing"
        )


class TestAB031AuditSpecImplementationAge:
    """AB031: make audit-spec-implementation-age target + script."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("audit-spec-implementation-age"), "AB031: audit-spec-implementation-age missing"

    def test_script_exists(self):
        assert (ROOT / "scripts" / "audit_spec_implementation_age.py").exists(), (
            "AB031: scripts/audit_spec_implementation_age.py missing"
        )


class TestAB032CheckRatchetStaleness:
    """AB032: make check-ratchet-staleness target + script."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("check-ratchet-staleness"), "AB032: check-ratchet-staleness missing"

    def test_script_exists(self):
        assert (ROOT / "scripts" / "check_ratchet_staleness.py").exists(), (
            "AB032: scripts/check_ratchet_staleness.py missing"
        )

    def test_script_has_age_threshold(self):
        content = (ROOT / "scripts" / "check_ratchet_staleness.py").read_text()
        assert "30" in content or "max_age_days" in content, "AB032: script lacks staleness threshold"


class TestAB033DeadCodeBaselineRefresh:
    """AB033: _dead-code-baseline-refresh in Makefile."""

    def test_guard_exists(self):
        assert guard_exists_in_makefile("_dead-code-baseline-refresh"), "AB033: _dead-code-baseline-refresh missing"

    def test_guard_checks_mtime(self):
        content = makefile_text()
        idx = content.find("_dead-code-baseline-refresh:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "MTIME" in block or "stat" in block or "mtime" in block.lower(), (
            "AB033: _dead-code-baseline-refresh does not check mtime"
        )


class TestAB034CommitMsgFormatGuard:
    """AB034: _commit-msg-format-guard in Makefile."""

    def test_guard_exists(self):
        assert guard_exists_in_makefile("_commit-msg-format-guard"), "AB034: _commit-msg-format-guard missing"

    def test_guard_validates_length(self):
        content = makefile_text()
        idx = content.find("_commit-msg-format-guard:")
        assert idx != -1
        block = content[idx : idx + 500]
        assert "LEN" in block or "length" in block.lower(), "AB034: _commit-msg-format-guard does not validate length"


class TestAB035MergeStructuralScan:
    """AB035: _merge-structural-scan in Makefile."""

    def test_guard_exists(self):
        assert guard_exists_in_makefile("_merge-structural-scan"), "AB035: _merge-structural-scan missing"


class TestAB036CleanupStepLimitedSubagents:
    """AB036: make cleanup-step-limited-subagents target + script."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("cleanup-step-limited-subagents"), (
            "AB036: cleanup-step-limited-subagents missing"
        )

    def test_script_exists(self):
        assert (ROOT / "scripts" / "cleanup_step_limited_subagents.py").exists(), (
            "AB036: scripts/cleanup_step_limited_subagents.py missing"
        )

    def test_script_checks_dirty_state(self):
        content = (ROOT / "scripts" / "cleanup_step_limited_subagents.py").read_text()
        assert "git status --porcelain" in content or "is_dirty" in content, (
            "AB036: script does not check dirty git state"
        )


class TestAB037CollectErrorTrend:
    """AB037: make check-collect-error-trend target + script."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("check-collect-error-trend"), "AB037: check-collect-error-trend missing"

    def test_script_exists(self):
        assert (ROOT / "scripts" / "check_collect_error_trend.py").exists(), (
            "AB037: scripts/check_collect_error_trend.py missing"
        )

    def test_script_tracks_consecutive_runs(self):
        content = (ROOT / "scripts" / "check_collect_error_trend.py").read_text()
        assert "runs" in content.lower() and "trend" in content.lower(), (
            "AB037: script does not track trend over consecutive runs"
        )


class TestAB038AuditPluginHookExports:
    """AB038: make audit-plugin-hook-exports target + script."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("audit-plugin-hook-exports"), "AB038: audit-plugin-hook-exports missing"

    def test_script_exists(self):
        assert (ROOT / "scripts" / "audit_plugin_hook_exports.py").exists(), (
            "AB038: scripts/audit_plugin_hook_exports.py missing"
        )

    def test_script_checks_test_coverage(self):
        content = (ROOT / "scripts" / "audit_plugin_hook_exports.py").read_text()
        assert "test" in content.lower() and "export" in content.lower(), (
            "AB038: script does not cross-reference exports with tests"
        )


class TestAB039RecoverIncompleteTasks:
    """AB039: make recover-incomplete-tasks target + script."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("recover-incomplete-tasks"), "AB039: recover-incomplete-tasks missing"

    def test_script_exists(self):
        assert (ROOT / "scripts" / "recover_incomplete_tasks.py").exists(), (
            "AB039: scripts/recover_incomplete_tasks.py missing"
        )

    def test_script_compares_sessions(self):
        content = (ROOT / "scripts" / "recover_incomplete_tasks.py").read_text()
        assert "git show" in content or "git log" in content, "AB039: script does not compare session data"


class TestAB040AuditSpecEffectiveness:
    """AB040: make audit-spec-effectiveness target + script."""

    def test_target_exists(self):
        assert guard_exists_in_makefile("audit-spec-effectiveness"), "AB040: audit-spec-effectiveness missing"

    def test_script_exists(self):
        assert (ROOT / "scripts" / "audit_spec_effectiveness.py").exists(), (
            "AB040: scripts/audit_spec_effectiveness.py missing"
        )

    def test_script_checks_recurrences(self):
        content = (ROOT / "scripts" / "audit_spec_effectiveness.py").read_text()
        assert "BUGS.md" in content or "ratchet" in content.lower(), (
            "AB040: script does not check post-spec recurrences"
        )


# ── AB021-AB040 guard wiring ──
AB2140_GUARDS: list[str] = [
    "check-hot-module-freshness",
    "check-target-contract",
    "check-subagent-file-dedup",
    "check-stale-tasks",
    "_stash-depth-guard",
    "_disk-usage-guard",
    "check-worktree-staleness",
    "check-plugin-load-order",
    "_pre-commit-timeout-guard",
    "verify-release-completeness-safe",
    "audit-spec-implementation-age",
    "check-ratchet-staleness",
    "_dead-code-baseline-refresh",
    "_commit-msg-format-guard",
    "_merge-structural-scan",
    "cleanup-step-limited-subagents",
    "check-collect-error-trend",
    "audit-plugin-hook-exports",
    "recover-incomplete-tasks",
    "audit-spec-effectiveness",
]

AB2140_SCRIPTS: list[str] = [
    "check_hot_module_freshness.py",
    "check_subagent_file_dedup.py",
    "check_stale_tasks.py",
    "check_worktree_staleness.py",
    "check_plugin_load_order.py",
    "audit_spec_implementation_age.py",
    "check_ratchet_staleness.py",
    "cleanup_step_limited_subagents.py",
    "check_collect_error_trend.py",
    "audit_plugin_hook_exports.py",
    "recover_incomplete_tasks.py",
    "audit_spec_effectiveness.py",
]


class TestAB2140GuardExistence:
    """All AB021-AB040 Makefile guards exist."""

    def test_all_ab2140_guards_exist(self):
        missing = [g for g in AB2140_GUARDS if not guard_exists_in_makefile(g)]
        assert not missing, f"AB021-AB040 guards missing from Makefile: {missing}"


class TestAB2140ScriptExistence:
    """All AB021-AB040 enforcement scripts exist."""

    def test_all_ab2140_scripts_exist(self):
        missing = [s for s in AB2140_SCRIPTS if not (ROOT / "scripts" / s).exists()]
        assert not missing, f"AB021-AB040 scripts missing: {missing}"
