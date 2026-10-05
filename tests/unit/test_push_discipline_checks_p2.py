"""tests/unit/test_push_discipline_checks_p2.py — P18-P30 push discipline behavioral specs.

Verifies enforcement mechanisms for stash-before-push, CI-restart-cap,
pull-before-push, verdict-history, pre-commit-stash-audit, edit-commit-atomicity,
push-parameter-audit, stash-leak-guard, COMMIT_THRESHOLD, development-merge,
release-promote, and fix-forward-to-master guards.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
MAKEFILE_PATH = ROOT / "Makefile"
AGENTS_PATH = ROOT / "AGENTS.md"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"


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


def _section_exists_in_agents(heading: str) -> bool:
    """Check if an AGENTS.md heading (or substantial phrase) exists."""
    text = agents_text()
    return heading.lower() in text.lower()


# ── P18 — Stash-before-push guard verifies clean stash ────────────────────


class TestP18StashBeforePushGuard:
    """P18: _stash-before-push-guard (AA022) verifies clean working tree before push."""

    def test_guard_exists_in_makefile(self):
        assert guard_exists_in_makefile("_stash-before-push-guard"), (
            "P18: _stash-before-push-guard missing from Makefile"
        )

    def test_guard_checks_unstaged_changes(self):
        text = makefile_text()
        start = text.find("_stash-before-push-guard:")
        end = text.find("\n\n", start) if start >= 0 else 0
        block = text[start:end] if start >= 0 else ""
        assert "git diff --quiet" in block, "P18: _stash-before-push-guard does not check for unstaged changes"
        assert "exit 1" in block or "exit 0" in block, "P18: _stash-before-push-guard missing exit decision"

    def test_guard_wired_to_push_targets(self):
        text = makefile_text()
        push_re = re.search(r"^batch-push:.*", text, re.MULTILINE)
        push_dev_re = re.search(r"^push-dev:.*", text, re.MULTILINE)
        git_push_nv_re = re.search(r"^git-push-sandboxcom-nv:.*", text, re.MULTILINE)
        clean_tree = re.search(r"^pre-push-check:.*", text, re.MULTILINE)

        guards_wired = (
            (push_re and "_stash-before-push-guard" in push_re.group())
            or (push_dev_re and "_stash-before-push-guard" in push_dev_re.group())
            or (git_push_nv_re and "_stash-before-push-guard" in git_push_nv_re.group())
        )
        # Also check pre-push-check which is a push-safety target
        clean_tree_wired = clean_tree and "_stash-before-push-guard" in clean_tree.group() if clean_tree else False
        assert guards_wired or clean_tree_wired, "P18: _stash-before-push-guard not wired to any push target"


# ── P19 — Push guard detects and blocks unstaged changes ──────────────────


class TestP19UnstagedChangesGuard:
    """P19: Push guard (check-clean-tree / _stash-before-push-guard) blocks
    pushes when the working tree has unstaged changes."""

    def test_check_clean_tree_wired_to_push_targets(self):
        text = makefile_text()
        targets = [
            "batch-push",
            "batch-push-nv",
            "git-push-sandboxcom",
            "git-push-sandboxcom-nv",
            "push-dev",
        ]
        any_wired = False
        for t in targets:
            m = re.search(rf"^{t}:([^\n]+)", text, re.MULTILINE)
            if m:
                prereqs = m.group(1)
                if "check-clean-tree" in prereqs or "_stash-before-push-guard" in prereqs:
                    any_wired = True
                    break
        assert any_wired, "P19: no push target uses check-clean-tree or _stash-before-push-guard"

    def test_stash_before_push_blocks_without_force(self):
        text = makefile_text()
        start = text.find("_stash-before-push-guard:")
        end = text.find("\n\n", start) if start >= 0 else 0
        block = text[start:end] if start >= 0 else ""
        assert "FORCE" in block, "P19: _stash-before-push-guard missing FORCE bypass logic"
        assert "exit 1" in block, "P19: _stash-before-push-guard must exit non-zero on violation"

    def test_agents_documents_push_guard(self):
        text = agents_text()
        assert "unstaged changes" in text.lower() or "clean tree" in text.lower(), (
            "P19: AGENTS.md does not document unstaged-changes push guard"
        )


# ── P20 — CI-restart cap enforces max cancelled runs ──────────────────────


class TestP20CiRestartCap:
    """P20: _ci-restart-cap (AA023) blocks pushes after 3 CI restarts per session."""

    def test_guard_exists_in_makefile(self):
        assert guard_exists_in_makefile("_ci-restart-cap"), "P20: _ci-restart-cap missing from Makefile"

    def test_guard_enforces_limit_of_three(self):
        text = makefile_text()
        start = text.find("_ci-restart-cap:")
        end = text.find("\n\n", start) if start >= 0 else 0
        block = text[start:end] if start >= 0 else ""
        assert "3" in block, "P20: _ci-restart-cap missing restart limit (3)"
        assert "exit 1" in block, "P20: _ci-restart-cap must exit non-zero when blocked"
        assert "/tmp/gludd-ci-restart-count" in block, "P20: _ci-restart-cap missing state file for restart count"

    def test_guard_is_check_only_and_success_recorder_increments(self) -> None:
        """Failed preflight must not spend a restart; only a landed push may."""
        text = makefile_text()
        guard_start = text.find("_ci-restart-cap:")
        guard_end = text.find("\n\n", guard_start)
        guard = text[guard_start:guard_end]
        record_start = text.find("_record-push-verdict:")
        record_end = text.find("\n\n", record_start)
        recorder = text[record_start:record_end]

        assert "CI_NEW" not in guard, "restart-cap guard must not charge rejected push attempts"
        assert "ci_check_cooldown.py record-push" in recorder, (
            "successful-push recorder must delegate atomic state accounting"
        )

    @pytest.mark.parametrize("target", ["git-push-sandboxcom", "git-push-sandboxcom-nv", "push-dev", "batch-push"])
    def test_successful_push_targets_record_restart_after_push(self, target: str) -> None:
        text = makefile_text()
        start = text.find(f"{target}:")
        end = text.find("\n\n", start)
        recipe = text[start:end]
        assert "_record-push-verdict" in recipe, f"{target} must record one landed push"

    def test_guard_wired_to_push_targets(self):
        assert target_uses_guard("batch-push", "_ci-restart-cap") or target_uses_guard("push-dev", "_ci-restart-cap"), (
            "P20: _ci-restart-cap not wired to any push target"
        )


# ── P21 — Pull-before-push verifies local==remote before push ─────────────


class TestP21PullBeforePushGuard:
    """P21: _pull-before-push-guard (AA029) fetches remote and blocks push
    if remote is ahead of local."""

    def test_guard_exists_in_makefile(self):
        assert guard_exists_in_makefile("_pull-before-push-guard"), "P21: _pull-before-push-guard missing from Makefile"

    def test_guard_fetches_and_compares_local_remote(self):
        text = makefile_text()
        start = text.find("_pull-before-push-guard:")
        end = text.find("\n\n", start) if start >= 0 else 0
        block = text[start:end] if start >= 0 else ""
        assert "fetch" in block.lower(), "P21: _pull-before-push-guard does not fetch remote"
        assert "BEHIND" in block or "ahead" in block.lower(), (
            "P21: _pull-before-push-guard missing ahead/behind comparison"
        )
        assert "exit 1" in block, "P21: _pull-before-push-guard must exit non-zero when remote is ahead"

    def test_guard_wired_to_push_targets(self):
        assert target_uses_guard("batch-push", "_pull-before-push-guard") or target_uses_guard(
            "push-dev", "_pull-before-push-guard"
        ), "P21: _pull-before-push-guard not wired to any push target"


# ── P22 — CI-verdict-history-guard records push-triggered CI runs ─────────


class TestP22CiVerdictHistoryGuard:
    """P22: _ci-verdict-history-guard (AA032) records push-triggered CI run SHAs
    and blocks subsequent push until previous SHA's CI verdict was checked."""

    def test_guard_exists_in_makefile(self):
        assert guard_exists_in_makefile("_ci-verdict-history-guard"), (
            "P22: _ci-verdict-history-guard missing from Makefile"
        )

    def test_guard_records_push_sha_in_state_file(self):
        text = makefile_text()
        start = text.find("_ci-verdict-history-guard:")
        end = text.find("\n\n", start) if start >= 0 else 0
        block = text[start:end] if start >= 0 else ""
        assert "/tmp/gludd-ci-verdict-history.json" in block, "P22: _ci-verdict-history-guard missing state file"
        assert "last_push_sha" in block, "P22: _ci-verdict-history-guard does not record push SHA"
        assert "last_checked_sha" in block, "P22: _ci-verdict-history-guard does not track checked SHA"

    def test_guard_blocks_if_prior_sha_unverified(self):
        text = makefile_text()
        start = text.find("_ci-verdict-history-guard:")
        end = text.find("\n\n", start) if start >= 0 else 0
        block = text[start:end] if start >= 0 else ""
        assert "exit 1" in block, "P22: _ci-verdict-history-guard must exit non-zero when prior SHA unverified"

    def test_guard_wired_to_push_targets(self):
        assert target_uses_guard("batch-push", "_ci-verdict-history-guard") or target_uses_guard(
            "git-push-sandboxcom", "_ci-verdict-history-guard"
        ), "P22: _ci-verdict-history-guard not wired to any push target"


# ── P23 — Pre-commit-stash-audit detects hook modifications ───────────────


class TestP23PreCommitStashAudit:
    """P23: _pre-commit-stash-audit (AA034) detects unstaged modifications
    after a commit that may indicate stash conflicts from pre-commit hooks."""

    def test_guard_exists_in_makefile(self):
        assert guard_exists_in_makefile("_pre-commit-stash-audit"), "P23: _pre-commit-stash-audit missing from Makefile"

    def test_guard_detects_stash_conflicts(self):
        text = makefile_text()
        start = text.find("_pre-commit-stash-audit:")
        end = text.find("\n\n", start) if start >= 0 else 0
        block = text[start:end] if start >= 0 else ""
        assert "stash" in block.lower(), "P23: _pre-commit-stash-audit does not check stash state"
        assert "unstaged" in block.lower(), "P23: _pre-commit-stash-audit does not detect unstaged modifications"

    def test_guard_wired_to_commit_targets(self):
        assert target_uses_guard("git-commit", "_pre-commit-stash-audit") or target_uses_guard(
            "ship-commit", "_pre-commit-stash-audit"
        ), "P23: _pre-commit-stash-audit not wired to git-commit or ship-commit"


# ── P24 — Edit-commit-atomicity prevents split commits ────────────────────


class TestP24EditCommitAtomicity:
    """P24: _edit-commit-atomicity-guard (AA043) warns when working tree has
    unstaged changes that may not be included in the commit."""

    def test_guard_exists_in_makefile(self):
        assert guard_exists_in_makefile("_edit-commit-atomicity-guard"), (
            "P24: _edit-commit-atomicity-guard missing from Makefile"
        )

    def test_guard_detects_unstaged_changes_before_commit(self):
        text = makefile_text()
        start = text.find("_edit-commit-atomicity-guard:")
        end = text.find("\n\n", start) if start >= 0 else 0
        block = text[start:end] if start >= 0 else ""
        assert "unstaged" in block.lower(), "P24: _edit-commit-atomicity-guard does not detect unstaged changes"
        assert "git diff --quiet" in block, "P24: _edit-commit-atomicity-guard does not check working tree"

    def test_guard_wired_to_commit_targets(self):
        assert target_uses_guard("git-commit", "_edit-commit-atomicity-guard") or target_uses_guard(
            "ship-commit", "_edit-commit-atomicity-guard"
        ), "P24: _edit-commit-atomicity-guard not wired to git-commit or ship-commit"


# ── P25 — Push-parameter-audit validates push arguments ───────────────────


class TestP25PushParameterAudit:
    """P25: _push-parameter-audit (AA030) validates PUSH=1 against the batch
    threshold to prevent bypassing batch-push discipline."""

    def test_guard_exists_in_makefile(self):
        assert guard_exists_in_makefile("_push-parameter-audit"), "P25: _push-parameter-audit missing from Makefile"

    def test_guard_checks_batch_threshold(self):
        text = makefile_text()
        start = text.find("_push-parameter-audit:")
        end = text.find("\n\n", start) if start >= 0 else 0
        block = text[start:end] if start >= 0 else ""
        assert "PUSH=1" in block or "PUSH" in block, "P25: _push-parameter-audit does not check PUSH parameter"
        assert "THRESHOLD" in block, "P25: _push-parameter-audit does not check COMMIT_THRESHOLD"
        assert "exit 1" in block, "P25: _push-parameter-audit must exit non-zero on violation"

    def test_guard_wired_to_ship_commit(self):
        assert target_uses_guard("ship-commit", "_push-parameter-audit"), (
            "P25: _push-parameter-audit not wired to ship-commit"
        )


# ── P26 — Stash-leak-guard prevents stash accumulation ────────────────────


class TestP26StashLeakGuard:
    """P26: _stash-leak-guard (AA028) is a BLOCKING check that prevents commit
    when stash entries exist (pre-commit hooks stashed without popping)."""

    def test_guard_exists_in_makefile(self):
        assert guard_exists_in_makefile("_stash-leak-guard"), "P26: _stash-leak-guard missing from Makefile"

    def test_guard_is_blocking(self):
        text = makefile_text()
        start = text.find("_stash-leak-guard:")
        end = text.find("\n\n", start) if start >= 0 else 0
        block = text[start:end] if start >= 0 else ""
        assert "BLOCK" in block.upper(), "P26: _stash-leak-guard must be BLOCKING (not advisory)"
        assert "exit 1" in block, "P26: _stash-leak-guard must exit non-zero when stash entries exist"
        assert "git stash list" in block, "P26: _stash-leak-guard does not check stash list"

    def test_guard_wired_to_commit_targets(self):
        assert target_uses_guard("git-commit", "_stash-leak-guard") or target_uses_guard(
            "ship-commit", "_stash-leak-guard"
        ), "P26: _stash-leak-guard not wired to git-commit or ship-commit"


# ── P27 — Batch-push respects COMMIT_THRESHOLD minimum ────────────────────


class TestP27BatchPushCommitThreshold:
    """P27: batch-push respects COMMIT_THRESHOLD minimum and blocks
    COMMIT_THRESHOLD=1 bypass."""

    def test_batch_push_target_exists(self):
        assert guard_exists_in_makefile("batch-push"), "P27: batch-push target missing from Makefile"

    def test_batch_push_blocks_threshold_one(self):
        text = makefile_text()
        start = text.find("batch-push:")
        end = text.find("\n\n", start) if start >= 0 else 0
        block = text[start:end] if start >= 0 else ""
        assert "COMMIT_THRESHOLD=1" in block or "COMMIT_THRESHOLD" in block, (
            "P27: batch-push does not reference COMMIT_THRESHOLD"
        )

    def test_agents_forbids_threshold_one_bypass(self):
        text = agents_text()
        assert "COMMIT_THRESHOLD=1" in text, "P27: AGENTS.md does not forbid COMMIT_THRESHOLD=1"


# ── P28 — Development-merge-to-master requires CI green ───────────────────


class TestP28DevelopmentMergeRequiresCiGreen:
    """P28: development-merge-to-master requires CI green on development tip
    before allowing the merge."""

    def test_target_exists_in_makefile(self):
        assert guard_exists_in_makefile("development-merge-to-master"), (
            "P28: development-merge-to-master target missing from Makefile"
        )

    def test_target_calls_require_ci_green(self):
        text = makefile_text()
        start = text.find("development-merge-to-master:")
        end = text.find("\n\n", start) if start >= 0 else 0
        block = text[start:end] if start >= 0 else ""
        assert "require-ci-green" in block, "P28: development-merge-to-master does not call require-ci-green"

    def test_require_ci_green_script_exists(self):
        assert (ROOT / "scripts" / "require_ci_green.py").exists(), "P28: scripts/require_ci_green.py missing"


# ── P29 — Release-promote requires CI green + artifact check ──────────────


class TestP29ReleasePromoteCiGreenArtifact:
    """P29: release-promote requires CI green plus artifact-completeness check.
    Release-promote is the ONLY sanctioned way to ship a release branch to master."""

    def test_agents_documents_release_promote(self):
        text = agents_text()
        assert "release-promote" in text.lower(), "P29: AGENTS.md does not document release-promote"

    def test_release_cut_requires_ci_green(self):
        text = makefile_text()
        start = text.find("release-cut:")
        if start < 0:
            pytest.skip("P29: release-cut target not found in Makefile")
        end = text.find("\n\n", start)
        block = text[start:end] if start >= 0 else ""
        assert "require-dual-track-green" in block, (
            "P29: release-cut does not gate on exact-SHA local and hosted CI"
        )

    def test_release_cut_verifies_artifact(self):
        text = makefile_text()
        start = text.find("release-cut:")
        if start < 0:
            pytest.skip("P29: release-cut target not found in Makefile")
        end = text.find("\n\n", start)
        block = text[start:end] if start >= 0 else ""
        assert "verify-release" in block, "P29: release-cut does not verify release artifact"


# ── P30 — Never push fix-forward waves to master without CI ───────────────


class TestP30NeverPushFixForwardToMaster:
    """P30: Agent MUST never push fix-forward waves directly to master.
    Use release-candidate branches + CI-green merge instead."""

    def test_agents_forbids_fix_forward_to_master(self):
        text = agents_text()
        assert "fix-forward waves straight to" in text.lower() or "fix-forward waves" in text.lower(), (
            "P30: AGENTS.md does not forbid pushing fix-forward waves to master"
        )

    def test_agents_requires_release_candidate_or_ci_green(self):
        text = agents_text()
        assert "release-candidate" in text.lower(), (
            "P30: AGENTS.md does not reference release-candidate branch discipline"
        )
        assert "ci green" in text.lower() or "CI-green" in text or "CI green" in text, (
            "P30: AGENTS.md does not require CI green before master push"
        )

    def test_release_branch_new_target_exists(self):
        target_found = guard_exists_in_makefile("release-branch-new")
        # Not a hard fail if missing — release-branch-new may be planned
        assert target_found, "P30: release-branch-new target missing from Makefile"
