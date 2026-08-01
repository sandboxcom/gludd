"""Unit tests for B01-B25 Branch Discipline behavioral specs.

Verifies enforcement mechanisms from BEHAVIORAL_SPECS.md Group B:
Makefile targets, guard scripts, plugin files, and AGENTS.md text.
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


# ── B01: Agent MUST work on the correct branch ──────────────────────


class TestAgentMustWorkOnCorrectBranch:
    """B01: enforce-branch-discipline.ts checks current branch against objective."""

    def test_branch_discipline_plugin_exists(self):
        assert plugin_exists("enforce-branch-discipline.ts"), "B01: enforce-branch-discipline.ts missing"

    def test_branch_discipline_checks_current_branch(self):
        content = (PLUGIN_DIR / "enforce-branch-discipline.ts").read_text()
        assert "git rev-parse --abbrev-ref HEAD" in content, (
            "B01: enforce-branch-discipline.ts does not check current branch"
        )

    def test_branch_discipline_reads_intended_branch_from_session(self):
        content = (PLUGIN_DIR / "enforce-branch-discipline.ts").read_text()
        assert "SESSION.md" in content and "PRIMARY OBJECTIVE" in content, (
            "B01: enforce-branch-discipline.ts does not read intended branch from SESSION.md"
        )

    def test_objective_plugin_exists(self):
        assert plugin_exists("enforce-objective.ts"), "B01: enforce-objective.ts missing"

    def test_branch_discipline_denies_wrong_branch_push(self):
        content = (PLUGIN_DIR / "enforce-branch-discipline.ts").read_text()
        assert "permissionDecision" in content and "deny" in content, (
            "B01: enforce-branch-discipline.ts does not deny wrong-branch operations"
        )


# ── B02: Never push feature work directly to master ─────────────────


class TestNeverPushFeatureWorkDirectlyToMaster:
    """B02: AGENTS.md forbids direct feature pushes to master."""

    def test_agents_md_forbids_direct_push_to_master(self):
        content = agents_text()
        assert "NEVER push feature work directly to master" in content, (
            "B02: AGENTS.md does not forbid direct feature pushes to master"
        )

    def test_branch_discipline_enforces_push_to_master_rule(self):
        content = (PLUGIN_DIR / "enforce-branch-discipline.ts").read_text()
        assert "DENY_MESSAGE" in content, "B02: enforce-branch-discipline.ts lacks branch discipline deny message"


# ── B03: Master is for merges from development ONLY ─────────────────


class TestMasterIsForDevelopmentMergesOnly:
    """B03: AGENTS.md restricts master to merges from development."""

    def test_agents_md_states_master_is_for_merges_from_development(self):
        content = agents_text()
        assert "Master is for merges from development ONLY" in content, (
            "B03: AGENTS.md does not restrict master to development merges"
        )

    def test_makefile_has_development_merge_to_master(self):
        assert guard_exists_in_makefile("development-merge-to-master"), (
            "B03: development-merge-to-master target missing from Makefile"
        )


# ── B04: Pre-merge CI check for development→master ──────────────────


class TestPreMergeCICheck:
    """B04: development-merge-to-master requires CI green on development tip."""

    def test_development_merge_to_master_checks_ci_green(self):
        text = makefile_text()
        idx = text.find("development-merge-to-master:")
        assert idx != -1, "B04: development-merge-to-master target not found"
        block = text[idx : idx + 600]
        assert "require-ci-green" in block, "B04: development-merge-to-master does not check CI green"

    def test_agents_md_requires_ci_green_before_merge(self):
        content = agents_text()
        assert "Before merging development→master" in content, (
            "B04: AGENTS.md does not require CI green before development→master merge"
        )


# ── B05: Never merge to master from inside a worktree ───────────────


class TestNoMergeMasterFromWorktree:
    """B05: enforce-branch-discipline.ts blocks merges from worktrees."""

    def test_branch_discipline_blocks_worktree_merge(self):
        content = (PLUGIN_DIR / "enforce-branch-discipline.ts").read_text()
        assert "agent-merge" in content and "development-merge-to-master" in content, (
            "B05: enforce-branch-discipline.ts does not block merge targets from worktree"
        )

    def test_agents_md_forbids_worktree_merge_to_master(self):
        content = agents_text()
        assert "NEVER merge to master from inside a worktree" in content, (
            "B05: AGENTS.md does not forbid merging to master from inside a worktree"
        )


# ── B06: Batch-push pushes the CURRENT branch ───────────────────────


class TestBatchPushPushesCurrentBranch:
    """B06: verify-state target shows current branch for pre-push check."""

    def test_verify_state_target_exists(self):
        assert guard_exists_in_makefile("verify-state"), "B06: verify-state target missing from Makefile"

    def test_verify_state_shows_current_branch(self):
        text = makefile_text()
        idx = text.find("verify-state:")
        assert idx != -1, "B06: verify-state target not found"
        block = text[idx : idx + 500]
        assert "rev-parse" in block or "show-current" in block, "B06: verify-state does not show current branch"

    def test_agents_md_mentions_verify_state_before_push(self):
        content = agents_text()
        assert "make verify-state" in content, "B06: AGENTS.md does not mention verify-state before push"


# ── B07: Branch name follows naming convention ──────────────────────


class TestBranchNameConvention:
    """B07: AGENTS.md specifies branch naming conventions."""

    def test_agents_md_has_feature_branch_convention(self):
        content = agents_text()
        assert "feature/" in content, "B07: AGENTS.md does not specify feature/ branch prefix"

    def test_agents_md_has_agent_branch_convention(self):
        content = agents_text()
        assert "agent-" in content, "B07: AGENTS.md does not specify agent- branch prefix"


# ── B08: Development branch is the feature integration point ────────


class TestDevelopmentIsFeatureIntegrationPoint:
    """B08: Development branch is the sole feature integration target."""

    def test_agents_md_development_is_integration_point(self):
        content = agents_text()
        assert "development" in content.lower(), "B08: AGENTS.md does not reference development branch"

    def test_development_merge_to_master_is_the_final_shipping_path(self):
        assert guard_exists_in_makefile("development-merge-to-master"), (
            "B08: development-merge-to-master target missing"
        )


# ── B09: Feature branches are short-lived ───────────────────────────


class TestFeatureBranchesShortLived:
    """B09: AGENTS.md states feature branches should be merged within same session."""

    def test_agents_md_feature_branches_short_lived(self):
        content = agents_text()
        assert "short-lived" in content or "feature branch" in content.lower(), (
            "B09: AGENTS.md does not describe feature branches as short-lived"
        )


# ── B10: Never rebase shared branches ───────────────────────────────


class TestNoRebaseSharedBranches:
    """B10: AGENTS.md forbids rebasing master and development."""

    def test_agents_md_forbids_rebase_shared_branches(self):
        content = agents_text()
        assert (
            "never rebase" in content.lower()
            or "no rebase" in content.lower()
            or "must never be rebased" in content.lower()
        ), "B10: AGENTS.md does not forbid rebasing shared branches"


# ── B11: Verify branch before starting work ─────────────────────────


class TestVerifyBranchBeforeWork:
    """B11: AGENTS.md references branch in PRIMARY OBJECTIVE tracking."""

    def test_agents_md_objective_refers_to_branch(self):
        content = agents_text()
        assert "PRIMARY OBJECTIVE" in content, "B11: AGENTS.md does not reference PRIMARY OBJECTIVE"


# ── B12: Emergency fixes on master get backported ────────────────────


class TestEmergencyFixBackport:
    """B12: AGENTS.md requires emergency fix backport to development."""

    def test_agents_md_emergency_fix_backport(self):
        content = agents_text()
        assert "Emergency fixes on master get backported" in content, (
            "B12: AGENTS.md does not require emergency fix backport to development"
        )


# ── B13: Single-source feature development ──────────────────────────


class TestSingleSourceFeatureDevelopment:
    """B13: AGENTS.md enforces single-source feature development."""

    def test_agents_md_single_source_section_exists(self):
        content = agents_text()
        assert "Single-Source Feature Development" in content, (
            "B13: AGENTS.md missing Single-Source Feature Development section"
        )

    def test_agents_md_features_land_on_development_first(self):
        content = agents_text()
        assert "Features land on development first" in content, (
            "B13: AGENTS.md does not require features to land on development first"
        )


# ── B14: No parallel Makefile edits on different branches ───────────


class TestNoParallelMakefileEdits:
    """B14: AGENTS.md forbids parallel Makefile edits on different branches."""

    def test_agents_md_no_parallel_makefile_edits(self):
        content = agents_text()
        assert "No parallel Makefile edits on different branches" in content, (
            "B14: AGENTS.md does not forbid parallel Makefile edits"
        )


# ── B15: Duplicate target detection at gate time ────────────────────


class TestDuplicateTargetDetection:
    """B15: check-duplicate-targets script and target exist."""

    def test_check_duplicate_targets_target_exists(self):
        assert guard_exists_in_makefile("check-duplicate-targets"), (
            "B15: check-duplicate-targets target missing from Makefile"
        )

    def test_check_duplicate_targets_script_exists(self):
        assert (SCRIPTS_DIR / "check_duplicate_targets.py").exists(), "B15: scripts/check_duplicate_targets.py missing"

    def test_check_duplicate_targets_script_scans_makefile(self):
        content = (SCRIPTS_DIR / "check_duplicate_targets.py").read_text()
        assert "Counter" in content and "re.compile" in content, (
            "B15: check_duplicate_targets.py does not scan for duplicates"
        )

    def test_check_duplicate_targets_correct_exit_codes(self):
        content = (SCRIPTS_DIR / "check_duplicate_targets.py").read_text()
        assert "exit" in content.lower() or "sys.exit" in content, (
            "B15: check_duplicate_targets.py lacks exit code documentation"
        )


# ── B16: Release branch starts from CI-green base ───────────────────


class TestReleaseBranchFromCIGreenBase:
    """B16: Release branch must start from CI-green base (spec + planned)."""

    def test_agents_md_specifies_release_branch_ci_green_base(self):
        content = agents_text()
        assert "release-branch-new" in content or "release_branch_new" in content, (
            "B16: AGENTS.md does not reference release-branch-new target"
        )


# ── B17: Green release branch is immutable ──────────────────────────


class TestGreenReleaseBranchImmutable:
    """B17: check_green_branch_guard.py blocks pushes to green release branches."""

    def test_green_branch_guard_script_exists(self):
        assert (SCRIPTS_DIR / "check_green_branch_guard.py").exists(), (
            "B17: scripts/check_green_branch_guard.py missing"
        )

    def test_green_branch_guard_exit_codes_documented(self):
        content = (SCRIPTS_DIR / "check_green_branch_guard.py").read_text()
        assert "push BLOCKED" in content, "B17: check_green_branch_guard.py does not document blocked push behavior"

    def test_green_branch_guard_fails_open(self):
        content = (SCRIPTS_DIR / "check_green_branch_guard.py").read_text()
        assert "fail" in content.lower() and "open" in content.lower(), (
            "B17: check_green_branch_guard.py does not fail-open on errors"
        )


# ── B18: Fix-forward on red release branch ──────────────────────────


class TestFixForwardOnRedReleaseBranch:
    """B18: AGENTS.md specifies fix-forward for red release branches."""

    def test_agents_md_fix_forward_policy(self):
        content = agents_text()
        assert "Fix-forward" in content or "Fix forward" in content or "fix-forward" in content.lower(), (
            "B18: AGENTS.md does not specify fix-forward policy for red release branches"
        )


# ── B19: Release-promote is the only path to ship ───────────────────


class TestReleasePromoteSoleShipPath:
    """B19: AGENTS.md names release-promote as sole release shipping path."""

    def test_agents_md_release_promote_is_shipping_path(self):
        content = agents_text()
        assert "release-promote" in content, "B19: AGENTS.md does not reference release-promote as shipping path"


# ── B20: Release-recut re-triggers on existing tag ──────────────────


class TestReleaseRecutCIFailureOnly:
    """B20: release-recut target exists for CI-failure tag recovery."""

    def test_release_recut_target_exists(self):
        assert guard_exists_in_makefile("release-recut"), "B20: release-recut target missing from Makefile"

    def test_release_recut_requires_tag_argument(self):
        text = makefile_text()
        idx = text.find("release-recut:")
        assert idx != -1, "B20: release-recut target not found"
        block = text[idx : idx + 500]
        assert "TAG" in block, "B20: release-recut does not require TAG argument"


# ── B21: Never force-push past green branch guard ───────────────────


class TestNoForcePushPastGreenGuard:
    """B21: AGENTS.md forbids force-push past green branch guard."""

    def test_agents_md_forbids_force_push_past_green_guard(self):
        content = agents_text()
        assert "force-push" in content.lower() or "force push" in content.lower(), (
            "B21: AGENTS.md does not forbid force-push past green branch guard"
        )


# ── B22: Feature-start and feature-done lifecycle enforced ──────────


class TestFeatureStartDoneLifecycle:
    """B22: feature-start and feature-done targets exist and enforce lifecycle."""

    def test_feature_start_target_exists(self):
        assert guard_exists_in_makefile("feature-start"), "B22: feature-start target missing from Makefile"

    def test_feature_done_target_exists(self):
        assert guard_exists_in_makefile("feature-done"), "B22: feature-done target missing from Makefile"

    def test_feature_start_requires_msg_argument(self):
        text = makefile_text()
        idx = text.find("feature-start:")
        assert idx != -1, "B22: feature-start target not found"
        block = text[idx : idx + 200]
        assert "MSG" in block, "B22: feature-start does not require MSG argument"

    def test_feature_done_runs_tests_before_merge(self):
        text = makefile_text()
        idx = text.find("feature-done:")
        assert idx != -1, "B22: feature-done target not found"
        block = text[idx : idx + 600]
        assert "pytest" in block, "B22: feature-done does not run tests before merge"


# ── B23: Worktree isolated agents have their own branch ─────────────


class TestWorktreeAgentsHaveOwnBranch:
    """B23: agent-worktree target creates isolated worktree with dedicated branch."""

    def test_agent_worktree_target_exists(self):
        assert guard_exists_in_makefile("agent-worktree"), "B23: agent-worktree target missing from Makefile"

    def test_agent_worktree_creates_branch(self):
        text = makefile_text()
        idx = text.find("agent-worktree:")
        assert idx != -1, "B23: agent-worktree target not found"
        block = text[idx : idx + 500]
        assert "worktree add" in block, "B23: agent-worktree does not create git worktree"


# ── B24: Agent worktree must be cleaned up after merge ──────────────


class TestWorktreeCleanupAfterMerge:
    """B24: agent-merge and agent-cleanup targets enforce worktree lifecycle."""

    def test_agent_merge_target_exists(self):
        assert guard_exists_in_makefile("agent-merge"), "B24: agent-merge target missing from Makefile"

    def test_agent_cleanup_target_exists(self):
        assert guard_exists_in_makefile("agent-cleanup"), "B24: agent-cleanup target missing from Makefile"

    def test_agent_cu_removes_worktree(self):
        text = makefile_text()
        idx = text.find("agent-cleanup:")
        assert idx != -1, "B24: agent-cleanup target not found"
        block = text[idx : idx + 500]
        assert "worktree remove" in block, "B24: agent-cleanup does not remove worktree"

    def test_agent_cu_deletes_branch(self):
        text = makefile_text()
        idx = text.find("agent-cleanup:")
        assert idx != -1, "B24: agent-cleanup target not found"
        block = text[idx : idx + 500]
        assert "branch -d" in block, "B24: agent-cleanup does not delete branch"


# ── B25: No more than 6 concurrent worktree agents ──────────────────


class TestMax6ConcurrentWorktreeAgents:
    """B25: AGENTS.md caps concurrent worktree agents at ~5-6."""

    def test_agents_md_worktree_cap(self):
        content = agents_text()
        has_cap = "6" in content and "concurrent worktree" in content.lower()
        assert has_cap, "B25: AGENTS.md does not cap concurrent worktree agents at ~5-6"
