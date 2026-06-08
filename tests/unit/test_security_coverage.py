from __future__ import annotations

from general_ludd.security import sanitize_job_id, sanitize_path


class TestSanitizePath:
    def test_normal_relative_path_returns_cleaned(self) -> None:
        assert sanitize_path("foo/bar/baz.txt") == "foo/bar/baz.txt"

    def test_whitespace_stripped(self) -> None:
        assert sanitize_path("  foo/bar  ") == "foo/bar"

    def test_empty_string_returns_none(self) -> None:
        assert sanitize_path("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert sanitize_path("   ") is None

    def test_path_traversal_dotdot_slash_returns_none(self) -> None:
        assert sanitize_path("../foo") is None

    def test_path_traversal_dotdot_backslash_returns_none(self) -> None:
        assert sanitize_path("..\\foo") is None

    def test_absolute_path_returns_none(self) -> None:
        assert sanitize_path("/etc/passwd") is None

    def test_windows_absolute_returns_none(self) -> None:
        assert sanitize_path("C:\\Users") is None

    def test_leading_dot_slash_stripped(self) -> None:
        assert sanitize_path("./foo") == "foo"

    def test_dot_slash_with_subpath(self) -> None:
        assert sanitize_path("./foo/bar") == "foo/bar"

    def test_multiple_dot_slash_prefixes(self) -> None:
        result = sanitize_path("././foo")
        assert result == "./foo"

    def test_path_traversal_mid_path(self) -> None:
        assert sanitize_path("foo/../bar") is None

    def test_path_traversal_backslash_mid(self) -> None:
        assert sanitize_path("foo\\..\\bar") is None


class TestSanitizeJobId:
    def test_valid_uppercase_with_hyphen(self) -> None:
        assert sanitize_job_id("JOB-123") == "JOB-123"

    def test_valid_with_underscores(self) -> None:
        assert sanitize_job_id("JOB_456") == "JOB_456"

    def test_valid_all_digits(self) -> None:
        assert sanitize_job_id("12345") == "12345"

    def test_valid_uppercase_only(self) -> None:
        assert sanitize_job_id("ABCXYZ") == "ABCXYZ"

    def test_empty_string_returns_none(self) -> None:
        assert sanitize_job_id("") is None

    def test_with_slash_returns_none(self) -> None:
        assert sanitize_job_id("job/123") is None

    def test_with_backslash_returns_none(self) -> None:
        assert sanitize_job_id("job\\123") is None

    def test_path_traversal_returns_none(self) -> None:
        assert sanitize_job_id("..") is None

    def test_lowercase_returns_none(self) -> None:
        assert sanitize_job_id("job-123") is None

    def test_mixed_case_returns_none(self) -> None:
        assert sanitize_job_id("Job-123") is None

    def test_with_spaces_returns_none(self) -> None:
        assert sanitize_job_id("JOB 123") is None

    def test_with_special_chars_returns_none(self) -> None:
        assert sanitize_job_id("JOB@123") is None
