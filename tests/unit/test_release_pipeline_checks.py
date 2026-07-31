"""Unit tests for AC003 check_tag_immutability.py and AC006 validate_release_checksums.py."""

import hashlib
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from check_tag_immutability import ci_green_for_sha, tag_has_artifacts
from validate_release_checksums import parse_checksums


class TestCheckTagImmutability:
    """AC003: Tag immutability — CI green guard."""

    def test_ci_green_for_sha_no_gh(self, monkeypatch):
        """When gh CLI is unavailable, returns False (fail-closed)."""

        def raise_fnf(*args, **kwargs):
            raise FileNotFoundError("gh not found")

        monkeypatch.setattr(subprocess, "run", raise_fnf)
        assert ci_green_for_sha("abc123") is False

    def test_ci_green_for_sha_timeout(self, monkeypatch):
        """When gh times out, returns False (fail-closed)."""

        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("gh", 30)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        assert ci_green_for_sha("abc123") is False

    def test_ci_green_for_sha_success(self, monkeypatch):
        """Returns True for GREEN CI verdict."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout='[{"conclusion": "success"}]',
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert ci_green_for_sha("abc123") is True

    def test_ci_green_for_sha_failure(self, monkeypatch):
        """Returns False for RED CI verdict."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout='[{"conclusion": "failure"}]',
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert ci_green_for_sha("abc123") is False

    def test_ci_green_for_sha_empty(self, monkeypatch):
        """Returns False when no runs found."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout="[]",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert ci_green_for_sha("abc123") is False

    def test_ci_green_for_sha_gh_error(self, monkeypatch):
        """Returns False when gh exits non-zero."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=1,
                stdout="",
                stderr="error",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert ci_green_for_sha("abc123") is False

    def test_ci_green_for_sha_bad_json(self, monkeypatch):
        """Returns False when gh output is not valid JSON."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout="not json",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert ci_green_for_sha("abc123") is False

    def test_tag_has_artifacts_no_gh(self, monkeypatch):
        """Returns False when gh CLI unavailable."""

        def raise_fnf(*args, **kwargs):
            raise FileNotFoundError("gh not found")

        monkeypatch.setattr(subprocess, "run", raise_fnf)
        assert tag_has_artifacts("v1.0.0") is False

    def test_tag_has_artifacts_timeout(self, monkeypatch):
        """Returns False on timeout."""

        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("gh", 30)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        assert tag_has_artifacts("v1.0.0") is False

    def test_tag_has_artifacts_with_assets(self, monkeypatch):
        """Returns True when release has assets and is not draft."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout="false\n3\n",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert tag_has_artifacts("v1.0.0") is True

    def test_tag_has_artifacts_draft(self, monkeypatch):
        """Returns False when release is a draft."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout="true\n3\n",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert tag_has_artifacts("v1.0.0") is False

    def test_tag_has_artifacts_zero_assets(self, monkeypatch):
        """Returns False when release has 0 assets."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout="false\n0\n",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert tag_has_artifacts("v1.0.0") is False

    def test_tag_has_artifacts_gh_error(self, monkeypatch):
        """Returns False when gh exits non-zero."""

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args,
                returncode=1,
                stdout="",
                stderr="error",
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert tag_has_artifacts("v1.0.0") is False


class TestValidateReleaseChecksums:
    """AC006: Checksum validation."""

    def test_parse_checksums_empty(self):
        assert parse_checksums("") == {}

    def test_parse_checksums_comments_only(self):
        assert parse_checksums("# comment\n# another") == {}

    def test_parse_checksums_standard_format(self):
        content = (
            "abc123def4567890abc123def4567890abc123def4567890abc123def4567890  file1.tar.gz\n"
            "fed456cba7890abc123def4567890abcfed456cba7890abc123def4567890abc  file2.exe\n"
        )
        entries = parse_checksums(content)
        assert entries == {
            "file1.tar.gz": "abc123def4567890abc123def4567890abc123def4567890abc123def4567890",
            "file2.exe": "fed456cba7890abc123def4567890abcfed456cba7890abc123def4567890abc",
        }

    def test_parse_checksums_binary_prefix(self):
        content = "abc123def4567890abc123def4567890abc123def4567890abc123def4567890 *binary.tar.gz\n"
        entries = parse_checksums(content)
        assert entries == {"binary.tar.gz": "abc123def4567890abc123def4567890abc123def4567890abc123def4567890"}

    def test_parse_checksums_skips_short_entries(self):
        """Short checksums (non-SHA256) are skipped."""
        content = "abcd1234 short.hash\n"
        assert parse_checksums(content) == {}

    def test_parse_checksums_skips_non_hex(self):
        """Non-hex 'checksums' are skipped."""
        content = "zzzz1234zzzz5678zzzz1234zzzz5678zzzz1234zzzz5678zzzz1234zzzz5678  bad.bin\n"
        assert parse_checksums(content) == {}

    def test_parse_checksums_multiline_with_blanks(self):
        content = (
            "\n"
            "abc123def4567890abc123def4567890abc123def4567890abc123def4567890  a.bin\n"
            "\n"
            "# ignore this\n"
            "fed456cba7890abc123def4567890abcfed456cba7890abc123def4567890abc  b.bin\n"
            "\n"
        )
        entries = parse_checksums(content)
        assert entries == {
            "a.bin": "abc123def4567890abc123def4567890abc123def4567890abc123def4567890",
            "b.bin": "fed456cba7890abc123def4567890abcfed456cba7890abc123def4567890abc",
        }
