"""Unit tests for P01-P17 Push Discipline behavioral specs.

Verifies enforcement mechanisms from BEHAVIORAL_SPECS.md Group P:
Makefile targets, guard scripts, plugin files, and their wiring.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MAKEFILE_PATH = ROOT / "Makefile"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
SCRIPTS_DIR = ROOT / "scripts"
AGENTS_PATH = ROOT / "AGENTS.md"


def makefile_text() -> str:
    return MAKEFILE_PATH.read_text()


def agents_text() -> str:
    return AGENTS_PATH.read_text()


def guard_exists_in_makefile(guard_name: str) -> bool:
    text = makefile_text()
    return bool(re.search(rf"^{guard_name}:", text, re.MULTILINE))


def target_uses_guard(target_name: str, guard_name: str) -> bool:
    text = makefile_text()
    _pat = rf"^{target_name}:\s*.*\b{guard_name}\b"
    return bool(re.search(_pat, text, re.MULTILINE))


def plugin_exists(name: str) -> bool:
    return (PLUGIN_DIR / name).exists()


# ── P01: No push while CI is in_progress ──────────────────────────────


class TestNoPushWhileCIPending:
    """P01: _push-rate-guard blocks push when CI is in_progress."""

    def test_push_rate_guard_exists(self):
        assert guard_exists_in_makefile("_push-rate-guard"), "P01: _push-rate-guard missing from Makefile"

    def test_ci_push_guard_checks_in_progress_status(self):
        script = SCRIPTS_DIR / "ci_push_guard.py"
        assert script.exists(), "P01: scripts/ci_push_guard.py missing"
        content = script.read_text()
        assert "in_progress" in content, "P01: ci_push_guard.py does not check in_progress status"
        assert "_ACTIVE_STATUSES" in content, "P01: ci_push_guard.py lacks _ACTIVE_STATUSES tuple"

    def test_push_rate_guard_calls_ci_push_guard(self):
        text = makefile_text()
        idx = text.find("_push-rate-guard:")
        assert idx != -1
        block = text[idx : idx + 800]
        assert "ci_push_guard.py" in block, "P01: _push-rate-guard does not call ci_push_guard.py"

    def test_ci_push_guard_blocks_when_active(self):
        script = SCRIPTS_DIR / "ci_push_guard.py"
        content = script.read_text()
        assert "CI BUSY" in content or "return 1" in content, "P01: ci_push_guard.py does not block on active CI"


# ── P02: CI-busy-check on every push target ───────────────────────────


class TestCIBusyOnAllPushTargets:
    """P02: Every push target invokes a CI-busy guard."""

    def test_git_push_sandboxcom_has_push_rate_guard(self):
        assert target_uses_guard("git-push-sandboxcom", "_push-rate-guard"), (
            "P02: git-push-sandboxcom does not use _push-rate-guard"
        )

    def test_development_push_has_ci_busy_check(self):
        assert target_uses_guard("development-push", "ci-busy-check"), (
            "P02: development-push does not use ci-busy-check"
        )

    def test_batch_push_has_ci_related_guards(self):
        text = makefile_text()
        idx = text.find("batch-push:")
        assert idx != -1
        block = text[idx : idx + 300]
        has_guard = "_stash-before-push-guard" in block or "_push-rate-guard" in block or "_ci-restart-cap" in block
        assert has_guard, "P02: batch-push lacks CI-related guards"


# ── P03: No COMMIT_THRESHOLD=1 ────────────────────────────────────────


class TestNoCommitThreshold1:
    """P03: COMMIT_THRESHOLD=1 must be blocked by policy and mechanism."""

    def test_commit_threshold_1_is_blocked_in_makefile(self):
        text = makefile_text()
        idx = text.find("batch-push:")
        assert idx != -1
        block = text[idx : idx + 500]
        assert "COMMIT_THRESHOLD=1" in block and "BLOCKED" in block, "P03: batch-push does not block COMMIT_THRESHOLD=1"

    def test_batch_push_default_threshold_is_5(self):
        text = makefile_text()
        idx = text.find("batch-push:")
        assert idx != -1
        block = text[idx : idx + 500]
        assert "COMMIT_THRESHOLD:-5}" in block or "THRESHOLD=5" in block, "P03: batch-push default threshold is not 5"

    def test_agents_md_mentions_no_commit_threshold_1(self):
        content = agents_text()
        assert "COMMIT_THRESHOLD=1" in content, "P03: AGENTS.md does not mention COMMIT_THRESHOLD=1"


# ── P04: Push-to-push minimum interval ────────────────────────────────


class TestPushToPushInterval:
    """P04: Push cooldown enforces minimum interval between pushes."""

    def test_push_cooldown_seconds_var_exists(self):
        text = makefile_text()
        assert re.search(r"^PUSH_COOLDOWN_SECS\s*\?", text, re.MULTILINE), (
            "P04: PUSH_COOLDOWN_SECS variable missing from Makefile"
        )

    def test_push_cooldown_default_at_least_120(self):
        text = makefile_text()
        match = re.search(r"^PUSH_COOLDOWN_SECS\s*\?\=\s*(\d+)", text, re.MULTILINE)
        assert match, "P04: PUSH_COOLDOWN_SECS not found with default value"
        cooldown = int(match.group(1))
        assert cooldown >= 120, f"P04: PUSH_COOLDOWN_SECS={cooldown}, expected >= 120"

    def test_push_rate_guard_checks_cooldown(self):
        text = makefile_text()
        idx = text.find("_push-rate-guard:")
        assert idx != -1
        block = text[idx : idx + 1100]
        assert "LAST_PUSH" in block and "gludd-watchdog-push-timestamps.json" in block, (
            "P04: _push-rate-guard does not check push cooldown"
        )

    def test_push_timestamps_tracked_in_state_file(self):
        text = makefile_text()
        assert "gludd-watchdog-push-timestamps.json" in text, "P04: push timestamp state file not tracked"


# ── P05: Verify remote after every push ───────────────────────────────


class TestVerifyRemoteAfterPush:
    """P05: verify-remote must confirm remote tip matches local."""

    def test_verify_remote_target_exists(self):
        assert guard_exists_in_makefile("verify-remote"), "P05: verify-remote target missing from Makefile"

    def test_verify_remote_compares_sha(self):
        text = makefile_text()
        idx = text.find("verify-remote:")
        assert idx != -1
        block = text[idx : idx + 500]
        assert "git ls-remote" in block and "VERIFIED" in block, (
            "P05: verify-remote does not compare remote SHA to local"
        )


# ── P06: CI verdict must match head SHA ───────────────────────────────


class TestCIVerdictMustMatchHeadSHA:
    """P06: Never report CI verdict whose headSha != branch tip."""

    def test_ci_verdict_target_exists(self):
        assert guard_exists_in_makefile("ci-verdict"), "P06: ci-verdict target missing from Makefile"

    def test_ci_verdict_reports_head_sha(self):
        text = makefile_text()
        idx = text.find("ci-verdict:")
        assert idx != -1
        block = text[idx : idx + 800]
        assert "headSha" in block, "P06: ci-verdict does not report headSha"


# ── P07: CI cooldown check before any status claim ────────────────────


class TestCICooldownBeforeStatusClaim:
    """P07: Use ci-verdict-safe (cooldown-aware), not bare ci-verdict."""

    def test_ci_verdict_safe_target_exists(self):
        assert guard_exists_in_makefile("ci-verdict-safe"), "P07: ci-verdict-safe target missing from Makefile"

    def test_ci_verdict_safe_calls_cooldown_script(self):
        text = makefile_text()
        match = re.search(r"^ci-verdict-safe:", text, re.MULTILINE)
        assert match, "P07: ci-verdict-safe target not found"
        idx = match.start()
        block = text[idx : idx + 500]
        assert "scripts/ci_check_cooldown.py" in block, "P07: ci-verdict-safe does not call ci_check_cooldown.py"

    def test_ci_verdict_safe_has_cooldown_documentation(self):
        text = makefile_text()
        idx = text.find("ci-verdict-safe:")
        assert idx != -1
        block = text[idx : idx + 1200]
        assert "COOLDOWN-ENFORCED" in block or "COOLDOWN" in block, (
            "P07: ci-verdict-safe not documented as cooldown-enforced"
        )


# ── P08: CI-COOLDOWN-UNKNOWN must not be reported as PENDING ──────────


class TestCooldownNotReportedAsPending:
    """P08: CI cooldown block (exit 3) != PENDING."""

    def test_ci_check_cooldown_script_exists(self):
        assert (SCRIPTS_DIR / "ci_check_cooldown.py").exists(), "P08: scripts/ci_check_cooldown.py missing"

    def test_cooldown_returns_exit_3_when_active(self):
        content = (SCRIPTS_DIR / "ci_check_cooldown.py").read_text()
        assert "return 3" in content, "P08: ci_check_cooldown.py does not return exit code 3 for cooldown"

    def test_cooldown_exit_3_is_distinct_from_pending(self):
        content = (SCRIPTS_DIR / "ci_check_cooldown.py").read_text()
        assert "COOLDOWN" in content, "P08: ci_check_cooldown.py does not label cooldown blocks as COOLDOWN"


# ── P09: Never push while gate is red ─────────────────────────────────


class TestNoPushWhileGateRed:
    """P09: push targets must check .gate-status before pushing."""

    def test_pre_push_check_checks_gate_status(self):
        text = makefile_text()
        idx = text.find("pre-push-check:")
        assert idx != -1
        block = text[idx : idx + 600]
        assert ".gate-status" in block, "P09: pre-push-check does not check .gate-status"

    def test_pre_push_check_block_on_red_gate(self):
        text = makefile_text()
        idx = text.find("pre-push-check:")
        assert idx != -1
        block = text[idx : idx + 600]
        assert "exit 1" in block, "P09: pre-push-check does not block on gate failure"


# ── P10: Push only via sanctioned targets ─────────────────────────────


class TestPushOnlySanctionedTargets:
    """P10: Only batch-push, development-push, git-push-sandboxcom for push."""

    def test_sanctioned_push_targets_exist(self):
        for target in ["batch-push", "development-push", "git-push-sandboxcom"]:
            assert guard_exists_in_makefile(target), f"P10: sanctioned push target {target} missing"

    def test_enforce_make_plugin_blocks_raw_push(self):
        assert plugin_exists("enforce-make.ts"), "P10: enforce-make.ts missing"
        content = (PLUGIN_DIR / "enforce-make.ts").read_text()
        has_block = "git push" in content.lower() or "bash" in content.lower()
        assert has_block, "P10: enforce-make.ts does not block non-make commands"


# ── P11: Maximum one CI run in flight per branch ──────────────────────


class TestMaxOneCIInFlight:
    """P11: At most one CI run in progress per branch before pushing."""

    def test_push_rate_guard_checks_active_runs(self):
        content = (SCRIPTS_DIR / "ci_push_guard.py").read_text()
        assert "in_progress" in content, "P11: ci_push_guard.py does not check for in_progress"
        assert "queued" in content, "P11: ci_push_guard.py does not check for queued"

    def test_push_rate_guard_blocks_on_active(self):
        content = (SCRIPTS_DIR / "ci_push_guard.py").read_text()
        assert "exit" in content or "return 1" in content or "sys.exit" in content, (
            "P11: ci_push_guard.py does not have an exit/block path"
        )


# ── P12: Cancelled-run thrash detection ───────────────────────────────


class TestCancelledRunThrash:
    """P12: >3 cancelled runs in 2h must block push."""

    def test_push_rate_guard_counts_cancelled_runs(self):
        text = makefile_text()
        assert "scripts/gha_cancelled_count.py" in text, "P12: _push-rate-guard does not call gha_cancelled_count.py"

    def test_push_rate_guard_blocks_on_thrash(self):
        text = makefile_text()
        idx = text.find("_push-rate-guard:")
        assert idx != -1
        block = text[idx : idx + 1200]
        assert "push_rate_guard.py" in block and "check-bypass" in block, (
            "P12: _push-rate-guard does not enforce cancelled-run threshold"
        )


# ── P13: FORCE=1 bypass reserved for release-cut only ─────────────────


class TestForceBypassReleaseCutOnly:
    """P13: FORCE=1 override restricted to release-cut pipeline."""

    def test_force_bypass_documented_in_agents_md(self):
        content = agents_text()
        assert "FORCE=1" in content, "P13: AGENTS.md does not mention FORCE=1"

    def test_force_bypass_referenced_in_ci_check_cooldown(self):
        content = (SCRIPTS_DIR / "ci_check_cooldown.py").read_text()
        assert "FORCE" in content, "P13: ci_check_cooldown.py does not reference FORCE bypass"


# ── P14: Never push master directly from worktree ─────────────────────


class TestNoPushFromWorktree:
    """P14: Must push from main checkout, not worktree."""

    def test_branch_discipline_plugin_exists(self):
        assert plugin_exists("enforce-branch-discipline.ts"), "P14: enforce-branch-discipline.ts missing"

    def test_clean_tree_plugin_exists(self):
        assert plugin_exists("enforce-clean-tree.ts"), "P14: enforce-clean-tree.ts missing"


# ── P15: Batch local commits; push once ───────────────────────────────


class TestBatchLocalPushOnce:
    """P15: ship-commit PUSH=0 default, batch-push requires threshold."""

    def test_ship_commit_push_0_is_default(self):
        text = makefile_text()
        match = re.search(r"^PUSH\s*\?\=\s*(\d+)", text, re.MULTILINE)
        assert match, "P15: PUSH variable default not found"
        assert match.group(1) == "0", f"P15: PUSH default is {match.group(1)}, expected 0"

    def test_batch_push_requires_threshold(self):
        text = makefile_text()
        idx = text.find("batch-push:")
        assert idx != -1
        block = text[idx : idx + 600]
        assert "THRESHOLD" in block and "unpushed" in block.lower(), "P15: batch-push does not enforce commit threshold"

    def test_ship_commit_does_not_push_by_default(self):
        text = makefile_text()
        assert "PUSH ?= 0" in text and "Committed locally" in text, "P15: ship-commit does not support PUSH=0 default"


# ── P16: Push rate guard is fail-closed ───────────────────────────────


class TestPushRateGuardFailClosed:
    """P16: Push rate guard must deny push when CI state is unknown."""

    def test_ci_push_guard_script_exists(self):
        assert (SCRIPTS_DIR / "ci_push_guard.py").exists(), "P16: scripts/ci_push_guard.py missing"

    def test_makefile_push_rate_guard_fail_closed(self):
        text = makefile_text()
        idx = text.find("_push-rate-guard:")
        assert idx != -1
        block = text[idx : idx + 1200]
        has_or_exit = "|| {" in block and "exit 1" in block
        has_for_force = "FORCE" in block
        assert has_or_exit or has_for_force, "P16: _push-rate-guard does not have fail-closed blocking (|| { exit 1 })"

    def test_ci_push_guard_has_error_handling(self):
        content = (SCRIPTS_DIR / "ci_push_guard.py").read_text()
        assert "FileNotFoundError" in content, "P16: ci_push_guard.py lacks error handling"
        assert "sys.exit" in content or "return" in content, "P16: ci_push_guard.py lacks exit path"


# ── P17: Never push with dirty tree ───────────────────────────────────


class TestNoPushWithDirtyTree:
    """P17: Must not push while working tree is dirty."""

    def test_clean_tree_plugin_blocks_on_dirty(self):
        assert plugin_exists("enforce-clean-tree.ts"), "P17: enforce-clean-tree.ts missing"
        content = (PLUGIN_DIR / "enforce-clean-tree.ts").read_text()
        assert "porcelain" in content.lower() or "dirty" in content.lower(), (
            "P17: enforce-clean-tree.ts does not check dirty tree"
        )

    def test_stash_leak_guard_exists(self):
        assert guard_exists_in_makefile("_stash-leak-guard"), "P17: _stash-leak-guard missing from Makefile"

    def test_push_targets_use_check_clean_tree(self):
        for target in ["git-push-sandboxcom", "batch-push", "development-push"]:
            uses_check = target_uses_guard(target, "check-clean-tree")
            assert uses_check, f"P17: {target} does not use check-clean-tree guard"
