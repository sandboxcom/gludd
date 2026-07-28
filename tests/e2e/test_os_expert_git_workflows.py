"""E2E tests covering os_expert and git_automation subsystems.

os_expert: os_events, security_architectures, logging_systems, system_buses, package_management
git_automation: issue_ingestor, repo, locking, types, pr_delivery
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# ── git_automation imports ─────────────────────────────────────────────────
from general_ludd.git_automation.issue_ingestor import GitHubIssueIngestor
from general_ludd.git_automation.locking import git_repo_lock
from general_ludd.git_automation.pr_delivery import _SAFE_REF, PRDelivery, _validate_ref
from general_ludd.git_automation.repo import (
    GitAutomation,
    _reject_clone_url,
    _reject_leading_dash,
    reject_unsafe_repo_url,
)
from general_ludd.git_automation.types import (
    CloneResult,
    GatedCommitResult,
    GitStateResult,
    InitResult,
    MergeResult,
    PushResult,
    WorktreeInfo,
    WorktreeResult,
)
from general_ludd.os_expert.logging_systems import LOGGING_SYSTEMS, LoggingSystem

# ── os_expert imports ──────────────────────────────────────────────────────
from general_ludd.os_expert.os_events import OS_EVENT_MAP, OSEventSource
from general_ludd.os_expert.package_management import PACKAGE_MANAGERS, PackageManager
from general_ludd.os_expert.security_architectures import (
    SECURITY_ARCHITECTURES,
    SecurityArchitecture,
    SecurityLayer,
)
from general_ludd.os_expert.system_buses import SYSTEM_BUSES, SystemBus

# ═══════════════════════════════════════════════════════════════════════════════
# os_expert  E2E
# ═══════════════════════════════════════════════════════════════════════════════


class TestOsExpertE2E:
    """End-to-end shape and content validation for all os_expert modules."""

    # ── os_events ────────────────────────────────────────────────────────

    def test_os_event_map_is_list(self) -> None:
        assert isinstance(OS_EVENT_MAP, list)

    def test_os_event_source_typeddict_keys(self) -> None:
        keys = {"platform", "category", "source_name", "log_path", "query_api"}
        assert keys <= set(OSEventSource.__annotations__)

    def test_every_os_event_entry_has_all_keys(self) -> None:
        required = {"platform", "category", "source_name", "log_path", "query_api"}
        for entry in OS_EVENT_MAP:
            assert required <= entry.keys(), f"entry missing keys: {entry}"

    def test_os_event_platforms_valid(self) -> None:
        valid_platforms = {"linux", "darwin", "windows", "android", "ios", "freebsd", "macos"}
        for entry in OS_EVENT_MAP:
            assert entry["platform"] in valid_platforms, (
                f"unknown platform {entry['platform']!r}"
            )

    # ── security_architectures ───────────────────────────────────────────

    def test_security_layer_enum_values(self) -> None:
        layer_values = {m.value for m in SecurityLayer}
        assert "kernel" in layer_values
        assert "mandatory_access" in layer_values
        assert "code_signing" in layer_values
        assert "anti_malware" in layer_values
        assert "firewall" in layer_values
        assert "trusted_execution" in layer_values

    def test_security_architectures_is_list(self) -> None:
        assert isinstance(SECURITY_ARCHITECTURES, list)

    def test_security_architecture_typeddict_keys(self) -> None:
        keys = {"platform", "layer", "name", "config_path", "audit_command"}
        assert keys <= set(SecurityArchitecture.__annotations__)

    def test_every_security_architecture_entry_valid(self) -> None:
        valid_layers = {m.value for m in SecurityLayer}
        for entry in SECURITY_ARCHITECTURES:
            assert entry["layer"] in valid_layers, (
                f"unknown layer {entry['layer']!r}"
            )
            assert entry.get("name"), "security architecture missing name"

    # ── logging_systems ──────────────────────────────────────────────────

    def test_logging_systems_is_list(self) -> None:
        assert isinstance(LOGGING_SYSTEMS, list)

    def test_logging_system_typeddict_keys(self) -> None:
        keys = {"platform", "system_name", "log_path", "query_command", "stream_command"}
        assert keys <= set(LoggingSystem.__annotations__)

    def test_every_logging_system_entry_has_all_keys(self) -> None:
        required = {"platform", "system_name", "log_path", "query_command", "stream_command"}
        for entry in LOGGING_SYSTEMS:
            assert required <= entry.keys(), f"logging entry missing keys: {entry}"

    def test_logging_system_platforms_valid(self) -> None:
        valid_platforms = {"linux", "darwin", "windows", "android", "ios", "freebsd", "macos"}
        for entry in LOGGING_SYSTEMS:
            assert entry["platform"] in valid_platforms, (
                f"unknown platform {entry['platform']!r}"
            )

    # ── system_buses ─────────────────────────────────────────────────────

    def test_system_buses_is_list(self) -> None:
        assert isinstance(SYSTEM_BUSES, list)

    def test_system_bus_typeddict_keys(self) -> None:
        keys = {"platform", "bus_name", "transport", "default_address", "introspection_tool"}
        assert keys <= set(SystemBus.__annotations__)

    def test_every_system_bus_entry_valid(self) -> None:
        for entry in SYSTEM_BUSES:
            assert entry.get("platform"), "bus missing platform"
            assert entry.get("bus_name"), "bus missing name"

    # ── package_management ───────────────────────────────────────────────

    def test_package_managers_is_list(self) -> None:
        assert isinstance(PACKAGE_MANAGERS, list)

    def test_package_manager_typeddict_keys(self) -> None:
        keys = {"platform", "name", "format", "install_command", "query_command", "update_command", "audit_command"}
        assert keys <= set(PackageManager.__annotations__)

    def test_every_package_manager_entry_has_all_keys(self) -> None:
        required = {"platform", "name", "format", "install_command", "query_command", "update_command", "audit_command"}
        for entry in PACKAGE_MANAGERS:
            assert required <= entry.keys(), f"package manager missing keys: {entry}"


# ═══════════════════════════════════════════════════════════════════════════════
# git_automation  E2E — helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _dirty_file(repo_dir: str, name: str = "e2e.txt") -> str:
    path = os.path.join(repo_dir, name)
    with open(path, "w") as fh:
        fh.write("e2e-content")
    return path


def _init_git_dir(path: str) -> None:
    subprocess.run(["git", "init"], cwd=path, capture_output=True, text=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "agent@harness.local"],
        cwd=path, capture_output=True, text=True, check=False,
    )
    subprocess.run(
        ["git", "config", "user.name", "Agentic Harness Agent"],
        cwd=path, capture_output=True, text=True, check=False,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "root"],
        cwd=path, capture_output=True, text=True, check=True,
    )


@pytest.fixture()
def git_repo() -> tuple[GitAutomation, str]:
    with tempfile.TemporaryDirectory() as d:
        _init_git_dir(d)
        yield GitAutomation(repo_path=d), d


@pytest.fixture()
def git_repo_with_dev() -> tuple[GitAutomation, str]:
    with tempfile.TemporaryDirectory() as d:
        _init_git_dir(d)
        ga = GitAutomation(repo_path=d)
        ga.create_branch("development")
        ga._run_git("checkout", "master", "--", _cwd=d)
        yield ga, d


# ═══════════════════════════════════════════════════════════════════════════════
# git_automation  E2E — types
# ═══════════════════════════════════════════════════════════════════════════════


class TestGitAutomationTypesE2E:
    def test_init_result_roundtrip(self) -> None:
        r = InitResult(path="/tmp/x", created=True, message="ok")
        assert r.path == "/tmp/x"
        assert r.created is True
        assert r.message == "ok"

    def test_worktree_result_roundtrip(self) -> None:
        r = WorktreeResult(path="/tmp/wt", branch="agent-x", success=True, message="")
        assert r.path == "/tmp/wt"
        assert r.branch == "agent-x"
        assert r.success is True

    def test_merge_result_success(self) -> None:
        r = MergeResult(success=True, strategy="no-ff")
        assert r.success
        assert r.strategy == "no-ff"
        assert r.conflicts == []

    def test_clone_result_failure(self) -> None:
        r = CloneResult(path="/tmp/cl", url="https://example.com/repo", success=False, message="timeout")
        assert not r.success
        assert r.message == "timeout"
        assert not r.already_present

    def test_push_result_roundtrip(self) -> None:
        r = PushResult(success=True, remote="sandboxcom", branch="master")
        assert r.success
        assert r.remote == "sandboxcom"
        assert r.branch == "master"

    def test_gated_commit_result_roundtrip(self) -> None:
        r = GatedCommitResult(success=True, commit_sha="abc123", gate_returncode=0)
        assert r.success
        assert r.commit_sha == "abc123"
        assert r.gate_returncode == 0

    def test_git_state_result_all_fields(self) -> None:
        r = GitStateResult(
            success=True,
            branch="master",
            head="abc123",
            dirty_count=0,
            staged_count=0,
            untracked_count=0,
            status=[],
            remote="sandboxcom",
            remote_ref="refs/heads/master",
            remote_head="abc123",
            master_head="abc123",
            development_head="def456",
            master_is_ancestor_of_development=True,
        )
        assert r.success
        assert r.branch == "master"
        assert r.master_is_ancestor_of_development is True

    def test_worktree_info_roundtrip(self) -> None:
        r = WorktreeInfo(path="/tmp/wt", branch="agent-x", is_main=False, commit="abc123")
        assert r.path == "/tmp/wt"
        assert r.branch == "agent-x"
        assert r.commit == "abc123"


# ═══════════════════════════════════════════════════════════════════════════════
# git_automation  E2E — repo
# ═══════════════════════════════════════════════════════════════════════════════


class TestGitAutomationRepoE2E:
    def test_init_repo_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ga = GitAutomation(repo_path=d)
            r1 = ga.init_repo()
            r2 = ga.init_repo()
            assert r1.created
            assert not r2.created
            assert ga.is_repo()

    def test_is_repo_false_on_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ga = GitAutomation(repo_path=d)
            assert not ga.is_repo()

    def test_current_branch(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, _ = git_repo
        assert ga.current_branch() in ("master", "main")

    def test_commit_returns_sha(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, d = git_repo
        _dirty_file(d)
        sha = ga.commit(message="e2e commit")
        assert len(sha) == 40
        assert ga.get_current_commit() == sha

    def test_lines_changed_in_commit(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, d = git_repo
        _dirty_file(d)
        ga.commit(message="add file")
        assert ga.lines_changed_in_commit() > 0

    def test_changed_files_empty_after_commit(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, d = git_repo
        _dirty_file(d)
        ga.commit(message="clean")
        assert ga.changed_files() == []

    def test_create_and_delete_branch(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, d = git_repo
        ga.create_branch("feature/e2e-test")
        branches = ga.list_branches()
        assert "feature/e2e-test" in branches
        # create_branch intentionally checks out the new branch. Git refuses
        # to delete the currently checked-out branch, and delete_branch
        # preserves that safety boundary instead of switching implicitly.
        assert not ga.delete_branch("feature/e2e-test")
        ga._run_git("checkout", "master", "--", _cwd=d)
        assert ga.delete_branch("feature/e2e-test")

    def test_merge_branch_ff(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, d = git_repo
        ga.create_branch("feature/ff")
        _dirty_file(d, "ff.txt")
        ga.commit(message="ff work")
        ga._run_git("checkout", "master", "--", _cwd=d)
        result = ga.merge_branch(d, "feature/ff", "master", strategy="ff")
        assert result.success

    def test_merge_branch_no_ff(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, d = git_repo
        ga.create_branch("feature/noff")
        _dirty_file(d, "noff.txt")
        ga.commit(message="noff work")
        ga._run_git("checkout", "master", "--", _cwd=d)
        result = ga.merge_branch(d, "feature/noff", "master", strategy="no-ff")
        assert result.success

    def test_tag_release(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, _ = git_repo
        tag = ga.tag_release("v1.0.0-e2e")
        assert tag == "v1.0.0-e2e"

    def test_tag_checkpoint(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, _ = git_repo
        tag = ga.tag_checkpoint("ckpt-e2e")
        assert tag == "ckpt-e2e"

    def test_worktree_create_list_remove(self, git_repo: tuple[GitAutomation, str], tmp_path: Path) -> None:
        ga, d = git_repo
        _dirty_file(d)
        ga.commit(message="base")
        wt_path = os.path.join(str(tmp_path), "e2e-wt")
        result = ga.create_worktree(d, "wt-e2e", wt_path)
        assert result.success
        assert os.path.isdir(wt_path)

        worktrees = ga.list_worktrees(d)
        assert len(worktrees) >= 2
        paths = [w.path for w in worktrees]
        assert os.path.realpath(wt_path) in [os.path.realpath(p) for p in paths]

        assert ga.remove_worktree(d, wt_path)
        assert not os.path.isdir(wt_path)

    def test_worktree_rejects_dash_leading_path(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, d = git_repo
        result = ga.create_worktree(d, "ok-branch", "--evil")
        assert not result.success

    def test_worktree_rejects_traversal_path(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, d = git_repo
        result = ga.create_worktree(d, "ok-branch", os.path.join(d, "../../etc"))
        assert not result.success

    def test_reject_force_push(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, _ = git_repo
        assert ga.reject_force_push() is False

    def test_changed_files_before_commit(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, d = git_repo
        _dirty_file(d)
        files = ga.changed_files()
        assert len(files) >= 1
        assert any("e2e" in f for f in files)

    def test_remote_url_returns_empty_for_no_remote(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, _ = git_repo
        assert ga.remote_url("origin") == ""

    def test_workflow_state_basic(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, _ = git_repo
        st = ga.workflow_state()
        assert st.success
        assert st.dirty_count == 0

    def test_workflow_state_detects_dirty(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, d = git_repo
        _dirty_file(d)
        st = ga.workflow_state()
        assert st.dirty_count >= 1
        assert len(st.status) >= 1

    def test_workflow_state_assert_clean_fails_with_dirty(self, git_repo: tuple[GitAutomation, str]) -> None:
        ga, d = git_repo
        _dirty_file(d)
        st = ga.workflow_state(assert_clean=True)
        assert not st.success
        assert any("dirty" in e.lower() for e in st.errors)


# ═══════════════════════════════════════════════════════════════════════════════
# git_automation  E2E — repo  (security)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGitAutomationRepoSecurityE2E:
    def test_reject_leading_dash_branch(self) -> None:
        with pytest.raises(ValueError, match="refusing"):
            _reject_leading_dash("--upload-pack=evil", kind="branch name")

    def test_reject_leading_dash_ref(self) -> None:
        with pytest.raises(ValueError, match="refusing"):
            _reject_leading_dash("--receive-pack=evil", kind="commit ref")

    def test_reject_unsafe_repo_url_smart_transport(self) -> None:
        with pytest.raises(ValueError, match="::"):
            reject_unsafe_repo_url("ext::sh -c 'evil'")

    def test_reject_unsafe_repo_url_file_scheme(self) -> None:
        with pytest.raises(ValueError, match="file"):
            reject_unsafe_repo_url("file:///etc/passwd")

    def test_reject_unsafe_repo_url_empty(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            reject_unsafe_repo_url("   ")

    def test_reject_unsafe_repo_url_loopback(self) -> None:
        with pytest.raises(ValueError, match="internal"):
            reject_unsafe_repo_url("http://127.0.0.1/repo.git")

    def test_reject_clone_url_proxy_command(self) -> None:
        with pytest.raises(ValueError, match="ProxyCommand"):
            _reject_clone_url("ssh://evil.com/repo -oProxyCommand=sh -c 'id'")

    def test_reject_clone_url_leading_dash(self) -> None:
        with pytest.raises(ValueError, match="'-'"):
            _reject_clone_url("-oProxyCommand=sh -c evil")

    def test_reject_clone_url_file_when_disallowed(self) -> None:
        with pytest.raises(ValueError, match="file:"):
            _reject_clone_url("file:///tmp/repo", allow_local=False)

    def test_reject_clone_url_empty(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _reject_clone_url("   ")

    def test_accept_valid_https_clone_url(self) -> None:
        result = reject_unsafe_repo_url("https://github.com/org/repo.git")
        assert result == "https://github.com/org/repo.git"

    def test_accept_valid_ssh_clone_url(self) -> None:
        result = reject_unsafe_repo_url("git@github.com:org/repo.git")
        assert "github.com:org/repo.git" in result


# ═══════════════════════════════════════════════════════════════════════════════
# git_automation  E2E — issue_ingestor
# ═══════════════════════════════════════════════════════════════════════════════


class TestGitHubIssueIngestorE2E:
    def test_ingestor_unconfigured_polls_empty(self) -> None:
        import asyncio

        ingestor = GitHubIssueIngestor()
        assert not ingestor.is_configured()
        todos = asyncio.run(ingestor.poll_issues())
        assert todos == []

    def test_ingestor_configured_detected(self) -> None:
        ingestor = GitHubIssueIngestor(owner="test", repo="test")
        assert ingestor.is_configured()

    def test_ingestor_skips_pull_requests(self) -> None:
        import asyncio

        ingestor = GitHubIssueIngestor(owner="test", repo="test")
        pr_payload = [{
            "id": 1, "number": 10, "title": "PR",
            "pull_request": {"url": "..."}, "labels": [],
        }]
        with mock.patch.object(ingestor, "_fetch_labeled_issues", return_value=pr_payload):
            todos = asyncio.run(ingestor.poll_issues())
            assert todos == []

    def test_ingestor_dedup_seen_ids(self) -> None:
        import asyncio

        ingestor = GitHubIssueIngestor(owner="test", repo="test", seen_ids={1})
        payload = [{"id": 1, "number": 10, "title": "dup", "labels": []}]
        with mock.patch.object(ingestor, "_fetch_labeled_issues", return_value=payload):
            todos = asyncio.run(ingestor.poll_issues())
            assert todos == []

    def test_ingestor_classifies_bug_label(self) -> None:
        import asyncio

        ingestor = GitHubIssueIngestor(owner="test", repo="test")
        payload = [{
            "id": 99, "number": 5, "title": "crash",
            "body": "it broke", "labels": [{"name": "bug"}],
        }]
        with mock.patch.object(ingestor, "_fetch_labeled_issues", return_value=payload):
            todos = asyncio.run(ingestor.poll_issues())
            assert len(todos) == 1
            assert todos[0]["work_type"] == "bug_fix"

    def test_ingestor_classifies_docs_label(self) -> None:
        import asyncio

        ingestor = GitHubIssueIngestor(owner="test", repo="test")
        payload = [{
            "id": 100, "number": 6, "title": "readme",
            "body": "", "labels": [{"name": "docs"}],
        }]
        with mock.patch.object(ingestor, "_fetch_labeled_issues", return_value=payload):
            todos = asyncio.run(ingestor.poll_issues())
            assert len(todos) == 1
            assert todos[0]["work_type"] == "docs"

    def test_ingestor_classifies_test_label(self) -> None:
        import asyncio

        ingestor = GitHubIssueIngestor(owner="test", repo="test")
        payload = [{
            "id": 101, "number": 7, "title": "more tests",
            "body": "", "labels": [{"name": "testing"}],
        }]
        with mock.patch.object(ingestor, "_fetch_labeled_issues", return_value=payload):
            todos = asyncio.run(ingestor.poll_issues())
            assert len(todos) == 1
            assert todos[0]["work_type"] == "test"

    def test_ingestor_fallback_to_number_id(self) -> None:
        import asyncio

        ingestor = GitHubIssueIngestor(owner="test", repo="test")
        payload = [{"number": 8, "title": "no-id", "labels": []}]
        with mock.patch.object(ingestor, "_fetch_labeled_issues", return_value=payload):
            todos = asyncio.run(ingestor.poll_issues())
            assert len(todos) == 1
            assert "number:8" in ingestor._seen_ids

    def test_ingestor_fetches_labeled_none_handled(self) -> None:
        import asyncio

        ingestor = GitHubIssueIngestor(owner="test", repo="test")
        with mock.patch.object(ingestor, "_fetch_labeled_issues", return_value=None):
            todos = asyncio.run(ingestor.poll_issues())
            assert todos == []

    def test_ingestor_fetches_non_list_handled(self) -> None:
        import asyncio

        ingestor = GitHubIssueIngestor(owner="test", repo="test")
        with mock.patch.object(ingestor, "_fetch_labeled_issues", return_value={"error": "oops"}):
            todos = asyncio.run(ingestor.poll_issues())
            assert todos == []


# ═══════════════════════════════════════════════════════════════════════════════
# git_automation  E2E — locking
# ═══════════════════════════════════════════════════════════════════════════════


class TestGitAutomationLockingE2E:
    def test_git_repo_lock_acquires_and_releases(self, git_repo: tuple[GitAutomation, str]) -> None:
        _, d = git_repo
        with git_repo_lock(d):
            lockfile = os.path.join(d, ".git", "gludd-git.lock")
            assert os.path.exists(lockfile)

    def test_git_repo_lock_reentrant(self, git_repo: tuple[GitAutomation, str]) -> None:
        _, d = git_repo
        with git_repo_lock(d), git_repo_lock(d):
            assert True  # nested acquisition must not deadlock

    def test_git_repo_lock_missing_git_dir(self) -> None:
        with tempfile.TemporaryDirectory() as d, git_repo_lock(d):
            assert True


# ═══════════════════════════════════════════════════════════════════════════════
# git_automation  E2E — pr_delivery
# ═══════════════════════════════════════════════════════════════════════════════


class TestPRDeliveryE2E:
    def test_validate_ref_accepts_valid(self) -> None:
        assert _validate_ref("feature/my-branch_v2", "branch") is None
        assert _validate_ref("fix/123_thing", "branch") is None
        assert _validate_ref("sandboxcom", "remote") is None

    def test_validate_ref_rejects_leading_dash(self) -> None:
        err = _validate_ref("--upload-pack=evil", "branch")
        assert err is not None
        assert "dash" in err.lower() or "-" in err

    def test_validate_ref_rejects_empty(self) -> None:
        err = _validate_ref("", "branch")
        assert err is not None

    def test_validate_ref_rejects_whitespace(self) -> None:
        err = _validate_ref("bad ref", "branch")
        assert err is not None

    def test_safe_ref_regex_coverage(self) -> None:
        assert _SAFE_REF.match("main")
        assert _SAFE_REF.match("feature/ABC-123_thing")
        assert _SAFE_REF.match("release/v2.0-beta.1")
        assert _SAFE_REF.match("fix@something")
        assert not _SAFE_REF.match("--evil")
        assert not _SAFE_REF.match("bad;id")
        assert not _SAFE_REF.match("bad|id")

    def test_pr_delivery_no_gh_available(self) -> None:
        pr = PRDelivery(base_branch="main")
        result = pr.push_and_create_pr(
            repo_path="/tmp/x", branch_name="feat/x",
            todo_id="T-1", title="test",
            remote="origin",
        )
        assert result["pr_url"] is None
        assert result.get("error") is not None

    def test_pr_delivery_rejects_dash_branch(self) -> None:
        pr = PRDelivery()
        result = pr.push_and_create_pr(
            repo_path="/tmp/x", branch_name="--evil",
            todo_id="T-1", title="test",
        )
        assert result["pr_url"] is None
        assert result.get("error") is not None
