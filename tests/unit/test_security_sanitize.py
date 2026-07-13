"""Unit tests for security/sanitize.py — credential redaction, path sanitization, SSRF guards."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from general_ludd.security.sanitize import (
    confine_path,
    confine_path_multi,
    is_path_within,
    is_safe_fetch_url,
    sanitize_error_message,
    sanitize_job_id,
    sanitize_path,
    sanitize_skill_name,
    validate_fetch_url,
    workspace_roots,
)


class TestSanitizeErrorMessage:
    def test_passthrough_empty_string(self) -> None:
        assert sanitize_error_message("") == ""

    def test_passthrough_clean_message(self) -> None:
        msg = "Connection timed out after 30 seconds"
        assert sanitize_error_message(msg) == msg

    def test_redacts_openai_key(self) -> None:
        msg = "Failed with key sk-abcdefghijklmnopqrstuvwx"
        result = sanitize_error_message(msg)
        assert "sk-abcdef" not in result
        assert "[REDACTED_OPENAI_KEY]" in result

    def test_redacts_bearer_token(self) -> None:
        msg = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaaa"
        result = sanitize_error_message(msg)
        assert "eyJhbGci" not in result
        assert "[REDACTED_BEARER_TOKEN]" in result

    def test_redacts_basic_auth(self) -> None:
        msg = "Authorization: Basic dXNlcjpwYXNz"
        result = sanitize_error_message(msg)
        assert "dXNlcjpwYXNz" not in result
        assert "[REDACTED_BASIC_AUTH]" in result

    def test_redacts_api_key(self) -> None:
        msg = "api_key=deadbeef1234567890abcdef"
        result = sanitize_error_message(msg)
        assert "deadbeef" not in result
        assert "[REDACTED_API_KEY]" in result

    def test_redacts_password(self) -> None:
        msg = "password=super_secret_123"
        result = sanitize_error_message(msg)
        assert "super_secret_123" not in result
        assert "[REDACTED_CREDENTIAL]" in result

    def test_redacts_loopback_ip(self) -> None:
        msg = "Connection refused to 127.0.0.1:8080"
        result = sanitize_error_message(msg)
        assert "127.0.0.1" not in result
        assert "[REDACTED_LOOPBACK_IP]" in result

    def test_redacts_metadata_ip(self) -> None:
        msg = "Failed to reach 169.254.169.254"
        result = sanitize_error_message(msg)
        assert "169.254.169.254" not in result
        assert "[REDACTED_METADATA_IP]" in result

    def test_redacts_localhost_hostname(self) -> None:
        msg = "Error connecting to localhost:5432"
        result = sanitize_error_message(msg)
        assert "localhost" not in result
        assert "[REDACTED_INTERNAL_HOST]" in result

    def test_redacts_url_credentials(self) -> None:
        msg = "Failed URL: https://user:pass123@api.example.com/endpoint"
        result = sanitize_error_message(msg)
        assert "user:pass123" not in result
        assert "[REDACTED_CREDS_IN_URL]" in result


class TestSanitizePath:
    def test_clean_relative_path(self) -> None:
        assert sanitize_path("src/foo.py") == "src/foo.py"

    def test_rejects_parent_traversal(self) -> None:
        assert sanitize_path("../etc/passwd") is None

    def test_rejects_bare_dot_dot_segment(self) -> None:
        assert sanitize_path("foo/../bar.py") is None

    def test_rejects_absolute_path(self) -> None:
        assert sanitize_path("/etc/passwd") is None

    def test_rejects_empty_string(self) -> None:
        assert sanitize_path("") is None

    def test_strips_leading_dot_slash(self) -> None:
        assert sanitize_path("./foo.py") == "foo.py"

    def test_rejects_backslash_traversal(self) -> None:
        assert sanitize_path("..\\windows\\etc") is None

    def test_default_safe_path(self) -> None:
        assert sanitize_path("playbooks/deploy.yml") == "playbooks/deploy.yml"


class TestSanitizeJobId:
    def test_valid_job_id(self) -> None:
        assert sanitize_job_id("JOB-2025_001") == "JOB-2025_001"

    def test_rejects_empty(self) -> None:
        assert sanitize_job_id("") is None

    def test_rejects_slash(self) -> None:
        assert sanitize_job_id("foo/bar") is None

    def test_rejects_traversal(self) -> None:
        assert sanitize_job_id("../escapist") is None

    def test_rejects_special_chars(self) -> None:
        assert sanitize_job_id("job; rm -rf /") is None

    def test_rejects_period(self) -> None:
        assert sanitize_job_id("foo.bar") is None


class TestSanitizeSkillName:
    def test_valid_name(self) -> None:
        assert sanitize_skill_name("my-skill") == "my-skill"

    def test_strips_whitespace(self) -> None:
        assert sanitize_skill_name("  hello  ") == "hello"

    def test_rejects_empty(self) -> None:
        assert sanitize_skill_name("") is None

    def test_rejects_dot(self) -> None:
        assert sanitize_skill_name(".") is None

    def test_rejects_double_dot(self) -> None:
        assert sanitize_skill_name("..") is None

    def test_rejects_slash(self) -> None:
        assert sanitize_skill_name("../evil") is None

    def test_rejects_nul_byte(self) -> None:
        assert sanitize_skill_name("good\x00bad") is None

    def test_rejects_consecutive_dots(self) -> None:
        assert sanitize_skill_name("hidden..traverse") is None

    def test_rejects_unsafe_chars(self) -> None:
        assert sanitize_skill_name("bad;name") is None

    def test_accepts_spaces_in_name(self) -> None:
        assert sanitize_skill_name("My Skill Name") == "My Skill Name"


class TestConfinePath:
    def test_path_within_root(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "sub"), exist_ok=True)
            subfile = os.path.join(root, "sub", "nested.txt")
            result = confine_path("sub/nested.txt", root)
            assert result is not None
            assert os.path.realpath(subfile) == result

    def test_path_escaping_root(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            outside = tempfile.mkdtemp()
            result = confine_path(outside, root)
            assert result is None

    def test_relative_path_joined_to_root(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            fpath = os.path.join(root, "bar.txt")
            Path(fpath).touch()
            result = confine_path("bar.txt", root)
            assert result is not None
            assert os.path.realpath(fpath) == result

    def test_empty_candidate_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            assert confine_path("", root) is None

    def test_empty_root_returns_none(self) -> None:
        assert confine_path("foo", "") is None

    def test_identical_path(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            result = confine_path(root, root)
            assert result is not None
            assert os.path.realpath(root) == result


class TestConfinePathMulti:
    def test_matches_first_root(self) -> None:
        with tempfile.TemporaryDirectory() as r1, tempfile.TemporaryDirectory() as r2:
            f = os.path.join(r1, "x.txt")
            Path(f).touch()
            result = confine_path_multi("x.txt", [r1, r2])
            assert result is not None
            assert os.path.realpath(f) == result

    def test_matches_second_root(self) -> None:
        with tempfile.TemporaryDirectory() as r1, tempfile.TemporaryDirectory() as r2:
            f = os.path.join(r2, "y.txt")
            Path(f).touch()
            result = confine_path_multi("y.txt", [r1, r2])
            assert result is not None

    def test_no_match_returns_none(self) -> None:
        outside = tempfile.mkdtemp()
        with tempfile.TemporaryDirectory() as r:
            result = confine_path_multi(outside, [r])
            assert result is None


class TestIsPathWithin:
    def test_inside_is_true(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "deep"), exist_ok=True)
            assert is_path_within("deep", root) is True

    def test_outside_is_false(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            outside = tempfile.mkdtemp()
            assert is_path_within(outside, root) is False


class TestIsSafeFetchUrl:
    def test_https_url_allowed(self) -> None:
        assert is_safe_fetch_url("https://example.com/api") is True

    def test_http_url_blocked(self) -> None:
        assert is_safe_fetch_url("http://example.com/api") is False

    def test_empty_url_blocked(self) -> None:
        assert is_safe_fetch_url("") is False

    def test_none_url_blocked(self) -> None:
        assert is_safe_fetch_url(None) is False

    def test_non_string_blocked(self) -> None:
        assert is_safe_fetch_url(42) is False

    def test_loopback_blocked(self) -> None:
        assert is_safe_fetch_url("https://127.0.0.1/api") is False


class TestValidateFetchUrl:
    def test_valid_https_url(self) -> None:
        result = validate_fetch_url("https://api.github.com/repos/owner/repo")
        assert result is not None

    def test_http_scheme_rejected(self) -> None:
        assert validate_fetch_url("http://example.com") is None

    def test_no_host_rejected(self) -> None:
        assert validate_fetch_url("https://") is None

    def test_empty_rejected(self) -> None:
        assert validate_fetch_url("") is None

    def test_loopback_rejected(self) -> None:
        assert validate_fetch_url("https://localhost/api") is None


class TestWorkspaceRoots:
    def test_includes_cwd_and_tmpdir(self) -> None:
        roots = workspace_roots()
        assert len(roots) >= 2
        cwd = os.path.realpath(os.getcwd())
        tmp = os.path.realpath(tempfile.gettempdir())
        assert cwd in roots
        assert tmp in roots

    def test_extra_roots_merged(self) -> None:
        with tempfile.TemporaryDirectory() as extra:
            roots = workspace_roots(extra)
            assert os.path.realpath(extra) in roots

    def test_none_entries_dropped(self) -> None:
        roots = workspace_roots(None)
        assert None not in roots
