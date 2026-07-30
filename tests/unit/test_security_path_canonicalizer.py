"""Unit tests for security/path_canonicalizer.py — canonical path deny-list checking."""

from __future__ import annotations

from pathlib import Path

from general_ludd.security.path_canonicalizer import (
    CANONICAL_DENY_MARKERS,
    PROTECTED_FILE_STEMS,
    canonicalize_path,
    is_denied_path,
)


class TestCanonicalizePath:
    def test_none_returns_empty(self) -> None:
        assert canonicalize_path(None) == ""

    def test_empty_returns_empty(self) -> None:
        assert canonicalize_path("") == ""

    def test_lowercases(self) -> None:
        assert canonicalize_path("/Path/To/FILE.PY") == "/path/to/file.py"

    def test_backslash_to_slash(self) -> None:
        assert canonicalize_path("C:\\Users\\file.txt") == "c:/users/file.txt"

    def test_os_separator_normalized(self) -> None:
        result = canonicalize_path("a/b\\c")
        assert "\\" not in result


class TestProtectedFileStems:
    def test_guardrails_included(self) -> None:
        assert "guardrails" in PROTECTED_FILE_STEMS

    def test_capability_policy_included(self) -> None:
        assert "capability_policy" in PROTECTED_FILE_STEMS

    def test_permissions_included(self) -> None:
        assert "permissions" in PROTECTED_FILE_STEMS

    def test_enforce_make_included(self) -> None:
        assert "enforce_make" in PROTECTED_FILE_STEMS

    def test_policy_included(self) -> None:
        assert "policy" in PROTECTED_FILE_STEMS

    def test_is_frozenset(self) -> None:
        assert isinstance(PROTECTED_FILE_STEMS, frozenset)


class TestCanonicalDenyMarkers:
    def test_includes_guardrails(self) -> None:
        assert "guardrails" in CANONICAL_DENY_MARKERS

    def test_includes_secrets(self) -> None:
        assert "secrets" in CANONICAL_DENY_MARKERS

    def test_includes_opencode(self) -> None:
        assert ".opencode" in CANONICAL_DENY_MARKERS

    def test_includes_claude(self) -> None:
        assert ".claude" in CANONICAL_DENY_MARKERS

    def test_includes_agents_md(self) -> None:
        assert "agents.md" in CANONICAL_DENY_MARKERS

    def test_includes_enforce_prefix(self) -> None:
        assert "enforce-" in CANONICAL_DENY_MARKERS

    def test_includes_makefile(self) -> None:
        assert "makefile" in CANONICAL_DENY_MARKERS

    def test_includes_migrations(self) -> None:
        assert "/migrations/" in CANONICAL_DENY_MARKERS

    def test_includes_pyproject_toml(self) -> None:
        assert "pyproject.toml" in CANONICAL_DENY_MARKERS

    def test_is_frozenset(self) -> None:
        assert isinstance(CANONICAL_DENY_MARKERS, frozenset)


class TestIsDeniedPath:
    def test_opencode_path_denied(self) -> None:
        assert is_denied_path("/repo/.opencode/plugin/enforce.ts") is True

    def test_relative_claude_path_denied(self) -> None:
        assert is_denied_path(".claude/hooks/test.sh") is True

    def test_guardrails_file_denied(self) -> None:
        assert is_denied_path("src/guardrails.py") is True

    def test_agents_md_denied(self) -> None:
        assert is_denied_path("agents.md") is True

    def test_ordinary_file_allowed(self) -> None:
        assert is_denied_path("src/general_ludd/agents/capabilities.py") is False

    def test_empty_path_allowed(self) -> None:
        assert is_denied_path("") is False

    def test_none_path_allowed(self) -> None:
        assert is_denied_path(None) is False

    def test_enforce_prefix_denied(self) -> None:
        assert is_denied_path(".opencode/plugin/enforce-stop.ts") is True

    def test_github_workflows_denied(self) -> None:
        assert is_denied_path(".github/workflows/build.yml") is True

    def test_secrets_file_denied(self) -> None:
        assert is_denied_path("config/secrets.yml") is True

    def test_tasks_md_denied(self) -> None:
        assert is_denied_path("TASKS.md") is True

    def test_bugs_md_denied(self) -> None:
        assert is_denied_path("BUGS.md") is True

    def test_session_md_denied(self) -> None:
        assert is_denied_path("SESSION.md") is True

    def test_capability_policy_denied(self) -> None:
        assert is_denied_path("module_utils/capability_policy.py") is True

    def test_segment_exact_real_file_not_falsely_denied(self) -> None:
        assert is_denied_path("src/alembic_runner.py") is False

    def test_segment_exact_makefile_parser_not_denied(self) -> None:
        assert is_denied_path("src/makefile_parser.py") is False

    def test_permissions_singular_denied(self) -> None:
        assert is_denied_path("config/permission.py") is True

    def test_absolute_opencode_denied(self) -> None:
        assert is_denied_path("/Users/shawn/.opencode/config.json") is True

    def test_policy_file_denied(self) -> None:
        assert is_denied_path("src/policy.py") is True

    def test_workspace_resolution_detects_symlink_to_protected_path(
        self, tmp_path: Path
    ) -> None:
        protected = tmp_path / ".opencode"
        protected.mkdir()
        alias = tmp_path / "ordinary"
        alias.symlink_to(protected, target_is_directory=True)

        assert (
            is_denied_path("ordinary/plugin.ts", workspace_root=tmp_path) is True
        )
