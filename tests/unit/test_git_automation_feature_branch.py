"""Tests for git_automation/feature_branch.py — feature-start / feature-done / rebranch-onto."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.git_automation.feature_branch import (
    feature_done,
    feature_start,
    rebranch_onto,
)
from general_ludd.git_automation.repo import GitAutomation
from general_ludd.git_automation.types import GitStateResult, MergeResult


class TestFeatureBranchCreate:
    def test_creates_branch_from_master(self):
        git = MagicMock(spec=GitAutomation)
        git.current_branch.return_value = "master"
        git.list_branches.return_value = ["master"]
        git.create_branch.return_value = "feature/my-feature"

        result = feature_start(git=git, name="my-feature", base="master")

        assert result == "feature/my-feature"
        git._run_git.assert_called_once_with("checkout", "master", "--")
        git.list_branches.assert_called_once()
        git.create_branch.assert_called_once_with("feature/my-feature")

    def test_creates_branch_from_specified_base(self):
        git = MagicMock(spec=GitAutomation)
        git.current_branch.return_value = "feature/other"
        git.list_branches.return_value = ["master", "development", "feature/other"]
        git.create_branch.return_value = "feature/new-feature"

        result = feature_start(git=git, name="new-feature", base="development")

        assert result == "feature/new-feature"
        git._run_git.assert_called_once_with("checkout", "development", "--")
        git.create_branch.assert_called_once_with("feature/new-feature")

    def test_prefixes_with_feature_by_default(self):
        git = MagicMock(spec=GitAutomation)
        git.current_branch.return_value = "master"
        git.list_branches.return_value = ["master"]
        git.create_branch.return_value = "feature/bugfix"

        result = feature_start(git=git, name="bugfix")

        assert result == "feature/bugfix"
        git.create_branch.assert_called_once_with("feature/bugfix")

    def test_preserves_explicit_prefix(self):
        git = MagicMock(spec=GitAutomation)
        git.current_branch.return_value = "master"
        git.list_branches.return_value = ["master"]
        git.create_branch.return_value = "release/v1.0"

        result = feature_start(git=git, name="release/v1.0")

        assert result == "release/v1.0"
        git.create_branch.assert_called_once_with("release/v1.0")

    def test_validates_branch_name_format(self):
        git = MagicMock(spec=GitAutomation)

        with pytest.raises(ValueError, match="begins with '-'"):
            feature_start(git=git, name="--upload-pack=evil")

        with pytest.raises(ValueError, match="must not be empty"):
            feature_start(git=git, name="")

    def test_rejects_existing_branch(self):
        git = MagicMock(spec=GitAutomation)
        git.current_branch.return_value = "master"
        git.list_branches.return_value = ["master", "feature/exists"]

        with pytest.raises(ValueError, match="already exists"):
            feature_start(git=git, name="feature/exists")


class TestFeatureBranchDone:
    def test_merges_no_ff_into_target(self):
        git = MagicMock(spec=GitAutomation, repo_path="/repo")
        git.list_branches.return_value = ["master", "development", "feature/my-feat"]
        state = MagicMock(spec=GitStateResult, success=True)
        git.workflow_state.return_value = state
        merge_ok = MergeResult(success=True, strategy="no-ff", message="Merge made")
        git.merge_branch.return_value = merge_ok

        result = feature_done(git=git, name="feature/my-feat", target="master")

        assert result["success"] is True
        git.workflow_state.assert_called_once_with(assert_clean=True)
        git.merge_branch.assert_called_once_with(
            "/repo", "feature/my-feat", "master", strategy="no-ff"
        )
        git.delete_branch.assert_called_once_with("feature/my-feat")

    def test_can_specify_target_branch(self):
        git = MagicMock(spec=GitAutomation, repo_path="/repo")
        git.list_branches.return_value = ["master", "development", "feature/my-feat"]
        state = MagicMock(spec=GitStateResult, success=True)
        git.workflow_state.return_value = state
        merge_ok = MergeResult(success=True, strategy="no-ff", message="Merge made")
        git.merge_branch.return_value = merge_ok

        result = feature_done(git=git, name="feature/my-feat", target="development")

        assert result["success"] is True
        git.merge_branch.assert_called_once_with(
            "/repo", "feature/my-feat", "development", strategy="no-ff"
        )

    def test_deletes_feature_branch_after_merge(self):
        git = MagicMock(spec=GitAutomation, repo_path="/repo")
        git.list_branches.return_value = ["master", "feature/my-feat"]
        state = MagicMock(spec=GitStateResult, success=True)
        git.workflow_state.return_value = state
        merge_ok = MergeResult(success=True, strategy="no-ff", message="Merge made")
        git.merge_branch.return_value = merge_ok

        feature_done(git=git, name="feature/my-feat")

        git.delete_branch.assert_called_once_with("feature/my-feat")

    def test_auto_removes_feature_prefix(self):
        git = MagicMock(spec=GitAutomation, repo_path="/repo")
        git.list_branches.return_value = ["master", "feature/my-feat"]
        state = MagicMock(spec=GitStateResult, success=True)
        git.workflow_state.return_value = state
        merge_ok = MergeResult(success=True, strategy="no-ff", message="Merge made")
        git.merge_branch.return_value = merge_ok

        result = feature_done(git=git, name="my-feat")

        assert result["success"] is True
        git.merge_branch.assert_called_once_with(
            "/repo", "feature/my-feat", "master", strategy="no-ff"
        )

    def test_requires_clean_tree(self):
        git = MagicMock(spec=GitAutomation)
        git.list_branches.return_value = ["master", "feature/my-feat"]
        state = MagicMock(spec=GitStateResult, success=False, errors=["dirty"])
        git.workflow_state.return_value = state

        with pytest.raises(ValueError, match="not clean"):
            feature_done(git=git, name="feature/my-feat")

    def test_fails_if_merge_conflicts(self):
        git = MagicMock(spec=GitAutomation, repo_path="/repo")
        git.list_branches.return_value = ["master", "feature/my-feat"]
        state = MagicMock(spec=GitStateResult, success=True)
        git.workflow_state.return_value = state
        merge_fail = MergeResult(
            success=False, strategy="no-ff",
            message="CONFLICT", conflicts=["file.py"],
        )
        git.merge_branch.return_value = merge_fail

        with pytest.raises(RuntimeError, match="merge of"):
            feature_done(git=git, name="feature/my-feat")

    def test_rejects_nonexistent_branch(self):
        git = MagicMock(spec=GitAutomation)
        git.list_branches.return_value = ["master", "development"]

        with pytest.raises(ValueError, match="not found"):
            feature_done(git=git, name="feature/missing")


class TestRebranchOnto:
    def test_rebases_current_branch_onto_new_base(self):
        git = MagicMock(spec=GitAutomation)
        git.current_branch.return_value = "feature/old-base"
        git.repo_path = "."

        result = rebranch_onto(git=git, new_base="new-base-sha")

        assert result["success"] is True
        assert result["base"] == "new-base-sha"
        assert result["original"] == "feature/old-base"
        assert "rebranch-feature/old-base-onto" in result["branch"]

    def test_cherry_picks_commits(self):
        git = MagicMock(spec=GitAutomation)
        git.current_branch.return_value = "feature/x"
        git.repo_path = "."
        commits = ["abc123", "def456"]

        result = rebranch_onto(git=git, new_base="base-sha", commits=commits)

        assert result["success"] is True
        cherry_calls = [
            c for c in git._run_git.call_args_list
            if c.args[0] == "cherry-pick"
        ]
        assert cherry_calls == [
            (("cherry-pick", "abc123"),),
            (("cherry-pick", "def456"),),
        ]

    def test_handles_rebase_conflicts(self):
        git = MagicMock(spec=GitAutomation)
        git.current_branch.return_value = "feature/x"
        git.repo_path = "."
        import subprocess

        def _side_effect(*args, **kwargs):
            if args == ("cherry-pick", "bad"):
                raise subprocess.CalledProcessError(1, ["git", "cherry-pick"], stderr="CONFLICT")

        git._run_git.side_effect = _side_effect

        with pytest.raises(RuntimeError, match=r"cherry-pick .* conflicted"):
            rebranch_onto(git=git, new_base="base", commits=["bad"])

        git._run_git.assert_any_call("cherry-pick", "--abort", check=False)
        git._run_git.assert_any_call("checkout", "-f", "feature/x", check=False)

    def test_aborts_cleanly_on_interrupt(self):
        git = MagicMock(spec=GitAutomation)
        git.current_branch.return_value = "feature/x"
        git.repo_path = "."
        import subprocess

        def _side_effect(*args, **kwargs):
            if args == ("cherry-pick", "bad"):
                raise subprocess.CalledProcessError(1, ["git", "cherry-pick"], stderr="CONFLICT")

        git._run_git.side_effect = _side_effect

        with pytest.raises(RuntimeError, match=r"cherry-pick .* conflicted"):
            rebranch_onto(git=git, new_base="base", commits=["bad"])

        git._run_git.assert_any_call("cherry-pick", "--abort", check=False)
        git._run_git.assert_any_call("checkout", "-f", "feature/x", check=False)
        git._run_git.assert_any_call("branch", "-D", "rebranch-feature/x-onto-base", check=False)

    def test_validates_base_is_commit(self):
        git = MagicMock(spec=GitAutomation)
        git.current_branch.return_value = "feature/x"
        git.repo_path = "."
        import subprocess
        git._run_git.side_effect = [
            subprocess.CalledProcessError(128, ["git", "rev-parse"], stderr="unknown revision"),
        ]

        with pytest.raises(ValueError, match="is not a valid commit"):
            rebranch_onto(git=git, new_base="nonesuch")

    def test_rejects_leading_dash_base(self):
        git = MagicMock(spec=GitAutomation)

        with pytest.raises(ValueError, match="begins with '-'"):
            rebranch_onto(git=git, new_base="--upload-pack=evil")
