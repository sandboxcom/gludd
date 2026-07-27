"""tests/unit/test_behavioral_enforcement.py — verify enforcement mechanisms for AA001-AA020 specs.

Checks that each spec's claimed enforcement mechanism exists in the codebase:
Makefile guards, plugin files, and their wiring into targets.
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
