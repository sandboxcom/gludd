"""Security tests for skills fetcher (D-32) and catalog (D-43).

D-32: GitHubSkillSource.download_skill and RemoteSkillFetcher.fetch must cap
      response bodies at 512 KB to prevent memory exhaustion from malicious remotes.

D-43: SkillCatalog.download_skill must confine the output file path within the
      target directory; a skill name containing path traversal must be rejected.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIZE_CAP = 512 * 1024  # 512 KB


def _make_streaming_response(body_bytes: bytes, status_code: int = 200):
    """Build a mock that mimics httpx's streaming context-manager interface.

    httpx.stream() returns a context manager whose __enter__ yields a Response-like
    object that exposes .status_code and .iter_bytes(chunk_size=...).
    """
    response = MagicMock()
    response.status_code = status_code

    # iter_bytes must yield the body in chunks
    def _iter_bytes(chunk_size: int = 8192):
        offset = 0
        while offset < len(body_bytes):
            yield body_bytes[offset : offset + chunk_size]
            offset += chunk_size

    response.iter_bytes = _iter_bytes

    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=response)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


VALID_SKILL_MD = b"---\nname: test-skill\ndescription: A test\n---\n\n# Body\n"

OVERSIZED_BODY = b"X" * (SIZE_CAP + 1)  # 512 KB + 1 byte


# ---------------------------------------------------------------------------
# D-32: GitHubSkillSource — streaming cap
# ---------------------------------------------------------------------------


class TestGitHubDownloadSkillSizeCap:
    """GitHubSkillSource.download_skill must use streaming with a 512 KB cap."""

    def test_github_download_skill_size_cap_rejects_oversized(self):
        """A 600 KB response must cause download_skill to return None."""
        from general_ludd.skills.fetcher import GitHubSkillSource

        oversized_cm = _make_streaming_response(OVERSIZED_BODY, status_code=200)

        with patch("httpx.stream", return_value=oversized_cm):
            src = GitHubSkillSource(owner="owner", repo="repo", branch="main")
            result = src.download_skill("skills/my-skill")

        assert result is None, (
            "download_skill must return None when the response body exceeds 512 KB"
        )

    def test_github_download_skill_size_cap_allows_small(self):
        """A small valid SKILL.md must be parsed and returned as a Skill."""
        from general_ludd.skills.fetcher import GitHubSkillSource

        small_cm = _make_streaming_response(VALID_SKILL_MD, status_code=200)

        with patch("httpx.stream", return_value=small_cm):
            src = GitHubSkillSource(owner="owner", repo="repo", branch="main")
            result = src.download_skill("skills/my-skill")

        assert result is not None, (
            "download_skill must return a Skill object for a valid small response"
        )
        assert result.name == "test-skill"

    def test_github_download_skill_404_primary_then_oversized_fallback_returns_none(self):
        """If the primary SKILL.md is 404 and the .md fallback is oversized, return None."""
        from general_ludd.skills.fetcher import GitHubSkillSource

        not_found_cm = _make_streaming_response(b"", status_code=404)
        oversized_cm = _make_streaming_response(OVERSIZED_BODY, status_code=200)

        call_count = 0

        @contextmanager
        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield not_found_cm.__enter__()
            else:
                yield oversized_cm.__enter__()

        with patch("httpx.stream", side_effect=_side_effect):
            src = GitHubSkillSource(owner="owner", repo="repo", branch="main")
            result = src.download_skill("skills/my-skill")

        assert result is None, (
            "download_skill must return None when the fallback response exceeds 512 KB"
        )

    def test_github_download_skill_non_200_returns_none(self):
        """A non-200 status (both attempts) must return None."""
        from general_ludd.skills.fetcher import GitHubSkillSource

        not_found_cm = _make_streaming_response(b"Not Found", status_code=404)

        with patch("httpx.stream", return_value=not_found_cm):
            src = GitHubSkillSource(owner="owner", repo="repo", branch="main")
            result = src.download_skill("skills/my-skill")

        assert result is None


# ---------------------------------------------------------------------------
# D-32: RemoteSkillFetcher — streaming cap
# ---------------------------------------------------------------------------


class TestRemoteSkillFetcherSizeCap:
    """RemoteSkillFetcher.fetch must use streaming with a 512 KB cap."""

    # A URL that passes is_safe_fetch_url (https + public host)
    SAFE_URL = "https://example.com/skills/my-skill.md"

    def test_remote_fetcher_size_cap_rejects_oversized(self):
        """A 600 KB response body must cause fetch to return None."""
        from general_ludd.skills.fetcher import RemoteSkillFetcher

        oversized_cm = _make_streaming_response(OVERSIZED_BODY, status_code=200)

        with patch("httpx.stream", return_value=oversized_cm):
            fetcher = RemoteSkillFetcher()
            result = fetcher.fetch(self.SAFE_URL)

        assert result is None, (
            "fetch must return None when the response body exceeds 512 KB"
        )

    def test_remote_fetcher_size_cap_allows_small(self):
        """A small valid response must be parsed and returned as a Skill."""
        from general_ludd.skills.fetcher import RemoteSkillFetcher

        small_cm = _make_streaming_response(VALID_SKILL_MD, status_code=200)

        with patch("httpx.stream", return_value=small_cm):
            fetcher = RemoteSkillFetcher()
            result = fetcher.fetch(self.SAFE_URL)

        assert result is not None, (
            "fetch must return a Skill object for a valid small response"
        )
        assert result.name == "test-skill"

    def test_remote_fetcher_ssrf_guard_still_applied(self):
        """A private/loopback URL must be rejected before any HTTP call is made."""
        from general_ludd.skills.fetcher import RemoteSkillFetcher

        with patch("httpx.stream") as mock_stream:
            fetcher = RemoteSkillFetcher()
            result = fetcher.fetch("https://169.254.169.254/latest/meta-data/")

        assert result is None
        mock_stream.assert_not_called()

    def test_remote_fetcher_non_200_returns_none(self):
        """A non-200 status must return None."""
        from general_ludd.skills.fetcher import RemoteSkillFetcher

        error_cm = _make_streaming_response(b"Not Found", status_code=404)

        with patch("httpx.stream", return_value=error_cm):
            fetcher = RemoteSkillFetcher()
            result = fetcher.fetch(self.SAFE_URL)

        assert result is None


# ---------------------------------------------------------------------------
# D-43: _safe_catalog_name helper
# ---------------------------------------------------------------------------


class TestSafeCatalogName:
    """_safe_catalog_name must reject traversal, absolute paths, and slashes."""

    def test_traversal_rejected(self):
        from general_ludd.skills.catalog import _safe_catalog_name

        assert _safe_catalog_name("../../etc/evil") is None

    def test_absolute_path_rejected(self):
        from general_ludd.skills.catalog import _safe_catalog_name

        assert _safe_catalog_name("/etc/passwd") is None

    def test_slash_rejected(self):
        from general_ludd.skills.catalog import _safe_catalog_name

        assert _safe_catalog_name("foo/bar") is None

    def test_backslash_rejected(self):
        from general_ludd.skills.catalog import _safe_catalog_name

        assert _safe_catalog_name("foo\\bar") is None

    def test_dotdot_segment_rejected(self):
        from general_ludd.skills.catalog import _safe_catalog_name

        assert _safe_catalog_name("..") is None

    def test_empty_rejected(self):
        from general_ludd.skills.catalog import _safe_catalog_name

        assert _safe_catalog_name("") is None

    def test_normal_name_accepted(self):
        from general_ludd.skills.catalog import _safe_catalog_name

        result = _safe_catalog_name("mp-diagnose")
        assert result == "mp-diagnose"

    def test_name_with_hyphens_accepted(self):
        from general_ludd.skills.catalog import _safe_catalog_name

        result = _safe_catalog_name("tdd-discipline")
        assert result == "tdd-discipline"


# ---------------------------------------------------------------------------
# D-43: SkillCatalog.download_skill — path confinement
# ---------------------------------------------------------------------------


class TestCatalogDownloadConfinement:
    """SkillCatalog.download_skill must confine files within target_dir."""

    def test_catalog_download_traversal_rejected(self, tmp_path):
        """A skill name with traversal (injected into catalog) must be rejected."""
        from general_ludd.skills.catalog import _CURATED_SKILLS, CatalogSkillEntry, SkillCatalog

        evil_name = "../../etc/evil"
        evil_entry = CatalogSkillEntry(
            name="evil",  # CatalogSkillEntry strips/validates, so we inject differently
            description="evil",
        )

        # Inject the malicious key directly into the catalog dict
        original = dict(_CURATED_SKILLS)
        _CURATED_SKILLS[evil_name] = evil_entry

        try:
            catalog = SkillCatalog()
            result = catalog.download_skill(evil_name, str(tmp_path))
        finally:
            _CURATED_SKILLS.clear()
            _CURATED_SKILLS.update(original)

        assert result is None, (
            "download_skill must reject a traversal skill name even when present in catalog"
        )
        # Confirm no file was written outside tmp_path
        assert not Path("/etc/evil.md").exists()

    def test_catalog_download_absolute_rejected(self, tmp_path):
        """A skill name starting with '/' must be rejected."""
        from general_ludd.skills.catalog import _safe_catalog_name

        assert _safe_catalog_name("/etc/passwd") is None

    def test_catalog_download_slash_rejected(self, tmp_path):
        """A skill name containing '/' must be rejected."""
        from general_ludd.skills.catalog import _safe_catalog_name

        assert _safe_catalog_name("foo/bar") is None

    def test_catalog_download_normal_confined(self, tmp_path):
        """A normal curated skill must be written inside target_dir."""
        from general_ludd.skills.catalog import SkillCatalog

        catalog = SkillCatalog()
        result = catalog.download_skill("mp-diagnose", str(tmp_path))

        assert result is not None, "mp-diagnose must be downloadable"
        # Verify the file is genuinely inside tmp_path
        real_result = os.path.realpath(str(result))
        real_target = os.path.realpath(str(tmp_path))
        assert real_result.startswith(real_target), (
            f"Skill file {real_result!r} escaped target dir {real_target!r}"
        )
        assert result.exists()

    def test_catalog_download_rejects_dotdot_segment(self, tmp_path):
        """A skill name that is exactly '..' must be rejected."""
        from general_ludd.skills.catalog import _safe_catalog_name

        assert _safe_catalog_name("..") is None
