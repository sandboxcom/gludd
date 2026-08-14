from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock

import general_ludd.git_automation.repo as git_repo
from general_ludd.git_automation.repo import GitAutomation, _reject_clone_url, _reject_leading_dash

# ── _reject_leading_dash ────────────────────────────────────────────────


def test_reject_leading_dash_raises_on_dash_ref() -> None:
    try:
        _reject_leading_dash("--force", kind="branch name")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "branch name" in str(e)
        assert "--force" in str(e)


def test_reject_leading_dash_allows_normal_ref() -> None:
    assert _reject_leading_dash("feature/foo", kind="branch name") == "feature/foo"


def test_reject_leading_dash_allows_underscore_prefixed() -> None:
    assert _reject_leading_dash("_foo", kind="branch name") == "_foo"


# ── reject_unsafe_repo_url ──────────────────────────────────────────────


def test_reject_unsafe_repo_url_empty_string() -> None:
    try:
        git_repo.reject_unsafe_repo_url("")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "empty" in str(e)


def test_reject_unsafe_repo_url_whitespace_only() -> None:
    try:
        git_repo.reject_unsafe_repo_url("   ")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "empty" in str(e)


def test_reject_unsafe_repo_url_smart_transport_colons() -> None:
    try:
        git_repo.reject_unsafe_repo_url("ext::sh -c 'echo pwned'")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "::" in str(e)


def test_reject_unsafe_repo_url_leading_dash() -> None:
    try:
        git_repo.reject_unsafe_repo_url("--upload-pack=test")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "begins with '-'" in str(e)


def test_reject_unsafe_repo_url_allows_https() -> None:
    url = "https://github.com/user/repo.git"
    assert git_repo.reject_unsafe_repo_url(url) == url


def test_reject_unsafe_repo_url_allows_git_protocol() -> None:
    url = "git://github.com/user/repo.git"
    assert git_repo.reject_unsafe_repo_url(url) == url


def test_reject_unsafe_repo_url_allows_ssh() -> None:
    url = "ssh://git@github.com/user/repo.git"
    assert git_repo.reject_unsafe_repo_url(url) == url


def test_reject_unsafe_repo_url_allows_scp_syntax() -> None:
    url = "git@github.com:org/repo.git"
    assert git_repo.reject_unsafe_repo_url(url) == url


def test_reject_unsafe_repo_url_rejects_file_scheme() -> None:
    try:
        git_repo.reject_unsafe_repo_url("file:///etc/passwd")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "file" in str(e)


def test_reject_unsafe_repo_url_rejects_unknown_transport_no_at() -> None:
    try:
        git_repo.reject_unsafe_repo_url("badformat/path")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "no recognized transport" in str(e)


def test_reject_unsafe_repo_url_rejects_blocked_host_https() -> None:
    try:
        git_repo.reject_unsafe_repo_url("https://127.0.0.1/repo.git")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "SSRF" in str(e)


def test_reject_unsafe_repo_url_rejects_blocked_host_scp() -> None:
    try:
        git_repo.reject_unsafe_repo_url("user@127.0.0.1:repo.git")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "SSRF" in str(e)


def test_reject_unsafe_repo_url_rejects_empty_hostname_https() -> None:
    try:
        git_repo.reject_unsafe_repo_url("https:///repo.git")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "SSRF" in str(e) or "host" in str(e)


# ── _reject_clone_url ───────────────────────────────────────────────────


def test_reject_clone_url_empty() -> None:
    try:
        _reject_clone_url("", allow_local=True)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "empty" in str(e)


def test_reject_clone_url_leading_dash() -> None:
    try:
        _reject_clone_url("--upload-pack=evil", allow_local=True)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "option injection" in str(e)


def test_reject_clone_url_smart_transport() -> None:
    try:
        _reject_clone_url("ext::sh -c bad", allow_local=True)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "RCE" in str(e)


def test_reject_clone_url_proxycommand() -> None:
    try:
        _reject_clone_url("ssh://host/-oProxyCommand=touch /tmp/pwned", allow_local=True)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "ProxyCommand" in str(e)


def test_reject_clone_url_proxycommand_space_form() -> None:
    try:
        _reject_clone_url("ssh://host/ -o ProxyCommand=evil", allow_local=True)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "ProxyCommand" in str(e)


def test_reject_clone_url_file_local_disallowed() -> None:
    try:
        _reject_clone_url("file:///tmp/repo", allow_local=False)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "file" in str(e)
        assert "disclosure" in str(e)


def test_reject_clone_url_file_local_allowed() -> None:
    assert _reject_clone_url("file:///tmp/repo", allow_local=True) == "file:///tmp/repo"


def test_reject_clone_url_allows_https() -> None:
    assert _reject_clone_url("https://github.com/user/repo", allow_local=False) == "https://github.com/user/repo"


def test_reject_clone_url_whitespace_only() -> None:
    try:
        _reject_clone_url("   ", allow_local=True)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "empty" in str(e)


# ── GitAutomation static helpers ─────────────────────────────────────────


def test_state_short_branch_refs_heads() -> None:
    assert GitAutomation._state_short_branch("refs/heads/feature/x") == "feature/x"


def test_state_short_branch_non_heads() -> None:
    assert GitAutomation._state_short_branch("refs/tags/v1.0") == "refs/tags/v1.0"


def test_state_short_branch_detached() -> None:
    assert GitAutomation._state_short_branch("HEAD") == "HEAD"


def test_state_short_branch_empty() -> None:
    assert GitAutomation._state_short_branch("") == "DETACHED"


def test_state_is_protected_trunk_true() -> None:
    assert GitAutomation._state_is_protected_trunk_branch("master") is True
    assert GitAutomation._state_is_protected_trunk_branch("main") is True
    assert GitAutomation._state_is_protected_trunk_branch("development") is True


def test_state_is_protected_trunk_false() -> None:
    assert GitAutomation._state_is_protected_trunk_branch("feature/foo") is False
    assert GitAutomation._state_is_protected_trunk_branch("release/v1") is False


def test_state_branch_matches_simple() -> None:
    patterns = ("preserve-*", "main-dirty-preserve-*")
    assert GitAutomation._state_branch_matches("preserve-abc123", patterns) is True
    assert GitAutomation._state_branch_matches("main-dirty-preserve-xyz", patterns) is True


def test_state_branch_matches_no_match() -> None:
    patterns = ("preserve-*",)
    assert GitAutomation._state_branch_matches("feature/x", patterns) is False


def test_state_branch_matches_empty_patterns() -> None:
    assert GitAutomation._state_branch_matches("anything", ()) is False


def test_state_branch_entries_parses_output() -> None:
    output = "feature/foo aaaaaa111\nbugfix/bar bbbbbb222\n"
    entries = GitAutomation._state_branch_entries(output)
    assert len(entries) == 2
    assert entries[0] == {"branch": "feature/foo", "head": "aaaaaa111"}
    assert entries[1] == {"branch": "bugfix/bar", "head": "bbbbbb222"}


def test_state_branch_entries_skips_malformed() -> None:
    output = "feature/foo abc123def\nmalformed_without_space\nbugfix/bar 456789abc\n"
    entries = GitAutomation._state_branch_entries(output)
    assert len(entries) == 2


def test_state_branch_entries_empty() -> None:
    assert GitAutomation._state_branch_entries("") == []


def test_state_worktree_entries_parses_porcelain() -> None:
    output = (
        "worktree /tmp/wt-1\n"
        "HEAD 1111111111111111\n"
        "branch refs/heads/feature/x\n"
        "\n"
        "worktree /tmp/wt-2\n"
        "HEAD 2222222222222222\n"
        "branch refs/heads/bugfix/y\n"
        "\n"
    )
    entries = GitAutomation._state_worktree_entries(output)
    assert len(entries) == 2
    assert entries[0]["path"] == "/tmp/wt-1"
    assert entries[0]["head"] == "1111111111111111"
    assert entries[0]["branch"] == "feature/x"
    assert entries[1]["path"] == "/tmp/wt-2"
    assert entries[1]["branch"] == "bugfix/y"


def test_state_worktree_entries_skips_blank_header_missing() -> None:
    output = "worktree /tmp/wt-incomplete\nHEAD abcd\n"
    entries = GitAutomation._state_worktree_entries(output)
    assert len(entries) == 1
    assert entries[0]["path"] == "/tmp/wt-incomplete"


def test_state_worktree_entries_no_blank_lines() -> None:
    output = "worktree /tmp/wt-1\nHEAD 1111111111111111\nbranch refs/heads/feature/x\n"
    entries = GitAutomation._state_worktree_entries(output)
    assert len(entries) == 1
    assert entries[0]["path"] == "/tmp/wt-1"


def test_state_worktree_entries_empty() -> None:
    assert GitAutomation._state_worktree_entries("") == []


def test_state_worktree_entries_detached_head() -> None:
    output = "worktree /tmp/wt-detached\nHEAD deadbeef\nbranch \n\n"
    entries = GitAutomation._state_worktree_entries(output)
    assert len(entries) == 1
    assert entries[0]["branch"] == "DETACHED"


def test_state_protected_branch_names_filters() -> None:
    entries = [
        {"branch": "master", "head": "aaa"},
        {"branch": "feature/x", "head": "bbb"},
        {"branch": "development", "head": "ccc"},
        {"branch": "main", "head": "ddd"},
        {"branch": "release/v1", "head": "eee"},
    ]
    protected = GitAutomation._state_protected_branch_names(entries)
    assert set(protected) == {"master", "development", "main"}


def test_state_protected_branch_names_empty() -> None:
    assert GitAutomation._state_protected_branch_names([]) == []


def test_state_reconciled_preserve_head_tokens_parses() -> None:
    text = "abc123 feature/x\n456def bugfix/y\n# comment line\n789ghi\n"
    heads = GitAutomation._state_reconciled_preserve_head_tokens(text)
    assert heads == {"abc123", "456def", "789ghi"}


def test_state_reconciled_preserve_head_tokens_comments_stripped() -> None:
    text = "abc123 # optional comment\n456def more text # trailing\n"
    heads = GitAutomation._state_reconciled_preserve_head_tokens(text)
    assert heads == {"abc123", "456def"}


def test_state_reconciled_preserve_head_tokens_empty() -> None:
    assert GitAutomation._state_reconciled_preserve_head_tokens("") == set()


def test_state_reconciled_preserve_head_tokens_blank_lines() -> None:
    text = "\n  \nabc123\n\n456def\n"
    heads = GitAutomation._state_reconciled_preserve_head_tokens(text)
    assert heads == {"abc123", "456def"}


# ── _state_status_lines ─────────────────────────────────────────────────


def test_state_status_lines_filters_blanks() -> None:
    output = " M Makefile\n\nA  new.py\n  \n?? scratch.txt\n"
    lines = GitAutomation._state_status_lines(output)
    assert lines == [" M Makefile", "A  new.py", "?? scratch.txt"]
    assert len(lines) == 3


# ── _state_staged_count ─────────────────────────────────────────────────


def test_state_staged_count_index_changes() -> None:
    lines = ["M  modified.py", "A  added.py", "R  renamed.py"]
    assert GitAutomation._state_staged_count(lines) == 3


def test_state_staged_count_worktree_changes_only() -> None:
    lines = [" M worktree_modified.py", " D worktree_deleted.py"]
    assert GitAutomation._state_staged_count(lines) == 0


def test_state_staged_count_untracked() -> None:
    lines = ["?? untracked.py"]
    assert GitAutomation._state_staged_count(lines) == 0


# ── _state_untracked_count ──────────────────────────────────────────────


def test_state_untracked_count() -> None:
    lines = ["?? new.py", "?? scratch.py", "M  staged.py"]
    assert GitAutomation._state_untracked_count(lines) == 2


# ── _state_remote_head ──────────────────────────────────────────────────


def test_state_remote_head_with_tab() -> None:
    assert GitAutomation._state_remote_head("abc123\trefs/heads/master\nxyz\trefs/tags/v1") == "abc123"


def test_state_remote_head_no_output() -> None:
    assert GitAutomation._state_remote_head("") == ""


def test_state_remote_head_single_line_no_tab() -> None:
    assert GitAutomation._state_remote_head("abc123\n") == "abc123"


# ── _reject_escaping_path ───────────────────────────────────────────────


def test_reject_escaping_path_dotdot_refused() -> None:
    try:
        GitAutomation._reject_escaping_path("/repo", "../escape")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert ".." in str(e)


def test_reject_escaping_path__within_repo_allowed() -> None:
    GitAutomation._reject_escaping_path("/repo", "/repo/worktrees/wt1")


def test_reject_escaping_path_sibling_directory_allowed() -> None:
    GitAutomation._reject_escaping_path("/repo", "/tmp/gludd-worktrees/wt1")


def test_reject_escaping_path_escapes_parent_absolute() -> None:
    try:
        GitAutomation._reject_escaping_path("/home/user/repo", "/etc/passwd")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "escapes" in str(e)


# ── is_force_push ───────────────────────────────────────────────────────


def test_is_force_push_detects_f() -> None:
    assert GitAutomation.is_force_push("git push -f origin main") is True


def test_is_force_push_detects_force_flag() -> None:
    assert GitAutomation.is_force_push("git push --force origin main") is True


def test_is_force_push_detects_force_with_lease() -> None:
    assert GitAutomation.is_force_push("git push --force-with-lease origin main") is True


def test_is_force_push_normal_push() -> None:
    assert GitAutomation.is_force_push("git push origin main") is False


def test_is_force_push_empty() -> None:
    assert GitAutomation.is_force_push("") is False


# ── generate_branch_name ────────────────────────────────────────────────


def test_generate_branch_name_format() -> None:
    name = GitAutomation.generate_branch_name("42", "fix-bug")
    assert name.startswith("agent/TODO-42/fix-bug-")
    parts = name.split("/")
    assert len(parts) == 3
    slug_part = parts[2]
    chunks = slug_part.split("-")
    assert len(chunks) >= 3


def test_generate_branch_name_uid_length() -> None:
    name = GitAutomation.generate_branch_name("7", "my-slug")
    uid_part = name.split("-")[-1]
    assert len(uid_part) == 8


def test_generate_branch_name_timestamp_present() -> None:
    name1 = GitAutomation.generate_branch_name("1", "slug")
    last_slug = name1.split("/")[-1]
    chunks = last_slug.split("-")
    ts1 = chunks[-2]
    assert len(ts1) == 14
    assert ts1.isdigit()


# ── _state_load_reconciled_preserve_heads ───────────────────────────────


def test_state_load_reconciled_preserve_heads_explicit_heads() -> None:
    ga = GitAutomation()
    heads = ga._state_load_reconciled_preserve_heads(
        head_file="",
        explicit_heads=("abc123", "def456"),
    )
    assert heads == {"abc123", "def456"}


def test_state_load_reconciled_preserve_heads_explicit_strips_whitespace() -> None:
    ga = GitAutomation()
    heads = ga._state_load_reconciled_preserve_heads(
        head_file="",
        explicit_heads=("  abc123  ",),
    )
    assert heads == {"abc123"}


def test_state_load_reconciled_preserve_heads_file_not_found() -> None:
    ga = GitAutomation("/nonexistent/repo")
    with mock.patch.object(ga, "_git_stdout_or_empty", return_value=""):
        heads = ga._state_load_reconciled_preserve_heads(
            head_file="config/nonexistent_file.txt",
        )
    assert heads == set()


def test_state_load_reconciled_preserve_heads_reads_file(tmp_path: Path) -> None:
    head_file = tmp_path / "preserved_heads.txt"
    head_file.write_text("abc123\n456def\n")

    ga = GitAutomation(str(tmp_path))
    with (
        mock.patch.object(ga, "_run_git", return_value=mock.Mock(stdout="")),
        mock.patch.object(ga, "_git_stdout_or_empty", return_value=""),
    ):
        heads = ga._state_load_reconciled_preserve_heads(
            head_file=str(head_file),
        )
    assert heads == {"abc123", "456def"}


def test_state_load_reconciled_preserve_heads_relative_path_resolved(tmp_path: Path) -> None:
    head_file = tmp_path / "config" / "preserved_heads.txt"
    head_file.parent.mkdir(parents=True, exist_ok=True)
    head_file.write_text("commit1\ncommit2\n")

    ga = GitAutomation(str(tmp_path))
    with (
        mock.patch.object(ga, "_run_git", return_value=mock.Mock(stdout="")),
        mock.patch.object(ga, "_git_stdout_or_empty", return_value=""),
    ):
        heads = ga._state_load_reconciled_preserve_heads(
            head_file="config/preserved_heads.txt",
        )
    assert heads == {"commit1", "commit2"}


# ── reject_force_push ───────────────────────────────────────────────────


def test_reject_force_push_always_false() -> None:
    ga = GitAutomation()
    assert ga.reject_force_push() is False


# ── _host_is_blocked (indirect, via reject_unsafe_repo_url) ─────────────


def test_host_is_blocked_loopback() -> None:
    with mock.patch.object(git_repo, "resolved_host_is_blocked", return_value=True):
        assert git_repo._host_is_blocked("127.0.0.1") is True


def test_host_is_blocked_public() -> None:
    with mock.patch.object(git_repo, "resolved_host_is_blocked", return_value=False):
        assert git_repo._host_is_blocked("github.com") is False


# ── _NON_INTERACTIVE_GIT_ENV ────────────────────────────────────────────


def test_non_interactive_env_present() -> None:
    assert git_repo._NON_INTERACTIVE_GIT_ENV["GIT_TERMINAL_PROMPT"] == "0"
    assert git_repo._NON_INTERACTIVE_GIT_ENV["GIT_ASKPASS"] == "echo"


# ── _GIT_TIMEOUT_SECONDS ────────────────────────────────────────────────


def test_git_timeout_is_60() -> None:
    assert git_repo._GIT_TIMEOUT_SECONDS == 60.0


# ── _ALLOWED_REPO_SCHEMES ───────────────────────────────────────────────


def test_allowed_repo_schemes() -> None:
    assert "https" in git_repo._ALLOWED_REPO_SCHEMES
    assert "http" in git_repo._ALLOWED_REPO_SCHEMES
    assert "git" in git_repo._ALLOWED_REPO_SCHEMES
    assert "ssh" in git_repo._ALLOWED_REPO_SCHEMES
    assert "file" not in git_repo._ALLOWED_REPO_SCHEMES


# ── Direct static helper via classmethod edge cases ─────────────────────


class TestGitAutomationInit:
    def test_init_default_path(self) -> None:
        ga = GitAutomation()
        assert ga.repo_path == "."

    def test_init_explicit_path(self) -> None:
        ga = GitAutomation("/some/custom/path")
        assert ga.repo_path == "/some/custom/path"


# ── ansible role delegation ──────────────────────────────────────────


def test_invoke_role_uses_isolated_local_ansible_runner(tmp_path: Path) -> None:
    """The delegation boundary uses an isolated local-only runner payload."""
    role_dir = tmp_path / "role"
    role_dir.mkdir()
    runner = mock.Mock()
    runner.run.return_value = mock.Mock(rc=0, status="successful")

    with (
        mock.patch.object(git_repo, "_HAS_ANSIBLE_RUNNER", True),
        mock.patch.object(git_repo, "_ROLE_DIR", role_dir),
        mock.patch.object(git_repo, "ansible_runner", runner),
    ):
        result = GitAutomation("/repo")._invoke_role(
            "status",
            requested_branch="feature/example",
        )

    assert result == {"status": "successful", "rc": 0, "events": []}
    runner.run.assert_called_once()
    kwargs = runner.run.call_args.kwargs
    assert kwargs["quiet"] is True
    assert kwargs["envvars"] == {
        "ANSIBLE_COLLECTIONS_PATH": str(git_repo._COLLECTIONS_ROOT),
    }
    assert Path(kwargs["playbook"]).name == "playbook.yml"
    assert Path(kwargs["inventory"]).name == "inventory"


def test_invoke_role_fails_closed_on_runner_exception(tmp_path: Path) -> None:
    """An Ansible runner exception is returned as a bounded failed result."""
    role_dir = tmp_path / "role"
    role_dir.mkdir()
    runner = mock.Mock()
    runner.run.side_effect = RuntimeError("runner unavailable")

    with (
        mock.patch.object(git_repo, "_HAS_ANSIBLE_RUNNER", True),
        mock.patch.object(git_repo, "_ROLE_DIR", role_dir),
        mock.patch.object(git_repo, "ansible_runner", runner),
    ):
        result = GitAutomation("/repo")._invoke_role("status")

    assert result == {
        "status": "failed",
        "rc": 1,
        "error": "ansible-runner error: runner unavailable",
    }


class TestRejectUnsafeRepoUrlEdgeCases:
    def test_rejects_non_string_type(self) -> None:
        bad: Any = 42
        caught = False
        try:
            git_repo.reject_unsafe_repo_url(bad)
        except (ValueError, TypeError):
            caught = True
        assert caught, "expected ValueError or TypeError for non-string input"

    def test_strips_whitespace(self) -> None:
        url = "  https://github.com/user/repo.git  "
        assert git_repo.reject_unsafe_repo_url(url) == url.strip()

    def test_scp_like_no_at_sign(self) -> None:
        try:
            git_repo.reject_unsafe_repo_url("no-colon-no-scheme/path")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "no recognized transport" in str(e)
