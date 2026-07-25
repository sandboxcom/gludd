"""Verify git_automation ansible role structure and required files.

SUPERSEDED: The molecule default scenario (molecule/default/verify.yml in the
git_automation role) performs the same structural + YAML validation using
ansible-native tasks.  This pytest file is retained as a fast-pass option but
is no longer the canonical test; run ``molecule test -s default`` instead.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

ROLE_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "collections" / "ansible_collections" / "general_ludd" / "agent" / "roles" / "git_automation"
)


class TestGitAutomationRoleStructure:
    """Verify the role directory and file structure exists."""

    def test_role_directory_exists(self) -> None:
        assert ROLE_ROOT.is_dir(), f"Role directory missing: {ROLE_ROOT}"

    @pytest.mark.parametrize(
        "subdir",
        ["tasks", "defaults", "meta"],
    )
    def test_required_subdirectories_exist(self, subdir: str) -> None:
        path = ROLE_ROOT / subdir
        assert path.is_dir(), f"Missing subdirectory: {path}"


    @pytest.mark.parametrize(
        "task_file",
        [
            "main.yml",
            "clone.yml",
            "commit.yml",
            "push.yml",
            "merge.yml",
            "branch.yml",
            "worktree.yml",
            "state.yml",
            "verify_remote.yml",
            "ship_commit.yml",
        ],
    )
    def test_required_task_files_exist(self, task_file: str) -> None:
        path = ROLE_ROOT / "tasks" / task_file
        assert path.is_file(), f"Missing task file: {path}"

    def test_defaults_main_exists(self) -> None:
        path = ROLE_ROOT / "defaults" / "main.yml"
        assert path.is_file(), f"Missing defaults: {path}"

    def test_meta_main_exists(self) -> None:
        path = ROLE_ROOT / "meta" / "main.yml"
        assert path.is_file(), f"Missing meta: {path}"


class TestTaskFilesAreValidYaml:
    """Verify each task file parses as valid YAML."""

    @pytest.mark.parametrize(
        "task_file",

        [
            "main.yml",
            "clone.yml",
            "commit.yml",
            "push.yml",
            "merge.yml",
            "branch.yml",
            "worktree.yml",
            "state.yml",
            "verify_remote.yml",
        ],
    )
    def test_task_file_is_valid_yaml(self, task_file: str) -> None:
        path = ROLE_ROOT / "tasks" / task_file
        content = path.read_text()
        docs = list(yaml.safe_load_all(content))
        assert len(docs) >= 1, f"No YAML document in {task_file}"
        first = docs[0]
        assert isinstance(first, list), f"{task_file} must be a list of tasks, got {type(first)}"
        assert len(first) >= 1, f"{task_file} must contain at least one task"

    def test_defaults_is_valid_yaml(self) -> None:
        path = ROLE_ROOT / "defaults" / "main.yml"
        content = path.read_text()
        doc = yaml.safe_load(content)
        assert isinstance(doc, dict), f"defaults must be a dict, got {type(doc)}"

    def test_meta_is_valid_yaml(self) -> None:
        path = ROLE_ROOT / "meta" / "main.yml"
        content = path.read_text()
        doc = yaml.safe_load(content)
        assert isinstance(doc, dict), f"meta must be a dict, got {type(doc)}"


class TestMainYmlImportsAllSubTasks:
    """Verify main.yml includes all six sub-task files."""


    REQUIRED_INCLUDES: ClassVar[set[str]] = {
        "clone.yml",
        "commit.yml",
        "push.yml",
        "merge.yml",
        "branch.yml",
        "worktree.yml",
        "state.yml",
        "batch_push.yml",
        "ci_verdict.yml",
        "ci_cancel.yml",
        "release_cut.yml",
        "release_delete.yml",
        "release_recut.yml",
    }

    def test_main_includes_all_sub_tasks(self) -> None:
        path = ROLE_ROOT / "tasks" / "main.yml"
        content = path.read_text()
        found = set()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for req in self.REQUIRED_INCLUDES:
                if req in stripped and "include_tasks" in content:
                    found.add(req)
        # Re-parse properly via yaml
        docs = list(yaml.safe_load_all(content))
        tasks = docs[0]
        for task in tasks:
            if "ansible.builtin.include_tasks" in task:
                file_ref = task["ansible.builtin.include_tasks"]
                found.add(file_ref)
        missing = self.REQUIRED_INCLUDES - found
        assert not missing, f"main.yml missing includes: {missing}"


class TestMetaRoleInfo:
    """Verify meta/main.yml has required fields."""

    def test_meta_has_role_name(self) -> None:
        path = ROLE_ROOT / "meta" / "main.yml"
        doc = yaml.safe_load(path.read_text())
        galaxy = doc.get("galaxy_info", {})
        assert galaxy.get("role_name") == "git_automation", (
            f"role_name should be git_automation, got {galaxy.get('role_name')}"
        )

    def test_meta_has_required_fields(self) -> None:
        path = ROLE_ROOT / "meta" / "main.yml"
        doc = yaml.safe_load(path.read_text())
        galaxy = doc.get("galaxy_info", {})
        for field in ("role_name", "author", "description", "license", "min_ansible_version"):
            assert field in galaxy, f"meta missing galaxy_info.{field}"


class TestDefaultsHaveRequiredParams:
    """Verify defaults/main.yml has the expected parameter namespace."""


    REQUIRED_DEFAULTS: ClassVar[list[str]] = [
        "git_op",
        "repo_path",
        "clone_url",
        "target_dir",
        "git_clone_timeout",
"commit_message",
        "commit_files",
        "gate_cmd",
        "push_branch",
        "push_remote",
        "push_args",
        "merge_source",
        "merge_target",
        "merge_strategy",
        "branch_op",
        "branch_name",
        "worktree_op",
        "worktree_branch",
        "worktree_path_param",
        "state_ref",
        "state_remote",
        "state_assert_clean",
        "state_assert_no_feature_on_master",
        "state_assert_merge_ready",
        "state_assert_remote_head",
        "state_assert_gha_matches_local",
        "git_retry_count",
    ]

    def test_defaults_have_all_required_params(self) -> None:
        path = ROLE_ROOT / "defaults" / "main.yml"
        doc = yaml.safe_load(path.read_text())
        missing = [k for k in self.REQUIRED_DEFAULTS if k not in doc]
        assert not missing, f"defaults missing keys: {missing}"

    def test_safe_defaults(self) -> None:
        """Defaults must be safe: empty strings for URLs/names, no auto-push."""
        path = ROLE_ROOT / "defaults" / "main.yml"
        doc = yaml.safe_load(path.read_text())
        assert doc.get("fail_on_push_error") is False, "fail_on_push_error default must be false"
        assert doc.get("clone_url") == "", "clone_url default must be empty"
        assert doc.get("push_remote") == "origin", "push_remote default must be origin"


class TestTaskFilesReferenceCorrectVariables:
    """Verify task files use the expected variable names."""

    @pytest.mark.parametrize(
        "task_file,expected_vars",
        [

            ("clone.yml", ["clone_url", "target_dir", "git_clone_timeout"]),
            ("commit.yml", ["repo_path", "commit_message", "gate_cmd"]),
            ("push.yml", ["repo_path", "push_branch", "push_remote"]),
            ("merge.yml", ["repo_path", "merge_source", "merge_target", "merge_strategy"]),
            ("branch.yml", ["repo_path", "branch_op", "branch_name"]),
            ("worktree.yml", ["repo_path", "worktree_op", "worktree_branch", "worktree_path_param"]),
            ("state.yml", ["repo_path", "state_ref", "state_remote", "state_assert_clean"]),
        ],
    )
    def test_task_file_references_expected_vars(self, task_file: str, expected_vars: list[str]) -> None:
        path = ROLE_ROOT / "tasks" / task_file
        content = path.read_text()
        missing = [v for v in expected_vars if v not in content]
        assert not missing, f"{task_file} missing variable references: {missing}"


class TestRoleHasNoForbiddenPatterns:
    """Verify role tasks don't use forbidden patterns per AGENTS.md."""

    FORBIDDEN: ClassVar[list[tuple[str, str | None]]] = [
        ("force", "push.yml"),
        ("force-push", "push.yml"),
        ("no-verify", None),
        ("--no-verify", None),
    ]

    def test_push_yaml_rejects_force(self) -> None:
        path = ROLE_ROOT / "tasks" / "push.yml"
        content = path.read_text()
        assert "force-push" not in content.lower() or "'force'" in content, (
            "push.yml must reject force-push"
        )

    def test_no_file_contains_force_push_command(self) -> None:
        """Only push.yml should mention force in a push context.  git worktree
        remove --force is legitimate and not a force-PUSH."""
        for root, _dirs, files in os.walk(str(ROLE_ROOT)):
            if "molecule" in root:
                continue
            for f in files:
                if f.endswith(".yml") and f != "worktree.yml":
                    content = Path(root, f).read_text()
                    lines_that_add_force_cmd = [
                        line for line in content.splitlines()
                        if "force" in line
                        and "assert" not in line.lower()
                        and "fail_msg" not in line
                        and "join" not in line
                        and "forbidden" not in line.lower()
                        and "reject" not in line.lower()
                        and "'-f'" not in line
                        and "force-push" not in line
                        and "register" not in line.lower()
                    ]
                    assert not lines_that_add_force_cmd, (
                        f"{f} contains force-push command: {lines_that_add_force_cmd[:3]}"
                    )

    def test_no_file_contains_no_verify(self) -> None:
        """No task file should use --no-verify in a git command."""
        for root, _dirs, files in os.walk(str(ROLE_ROOT)):
            if "molecule" in root:
                continue
            for f in files:
                if f.endswith(".yml"):
                    content = Path(root, f).read_text()
                    has_no_verify = "--no-verify" in content
                    assert not has_no_verify, (
                        f"{f} contains --no-verify, which bypasses pre-commit hooks"
                    )
