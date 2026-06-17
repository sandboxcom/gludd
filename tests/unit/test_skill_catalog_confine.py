"""Tests for SkillCatalog.download_skill path-confinement guard.

Mirrors the confinement check in RemoteSkillFetcher.install (fetcher.py):
a crafted skill name containing path separators or ".." must NOT write outside
the target directory and must return None (or raise) without writing anything.
"""

from __future__ import annotations  # noqa: I001

from pathlib import Path

from general_ludd.skills.catalog import _CURATED_SKILLS, CatalogSkillEntry, SkillCatalog


# Pick a real curated skill name to exercise the happy path.
_VALID_SKILL = next(iter(_CURATED_SKILLS))


class TestDownloadSkillConfinement:
    """Crafted dangerous names must never escape the target directory."""

    def test_traversal_dotdot_slash_returns_none(self, tmp_path: Path) -> None:
        """A name like '../evil' must be rejected before any write."""
        catalog = SkillCatalog()
        # Inject a fake entry under the traversal key so the catalog lookup
        # succeeds and we reach the confinement guard.
        evil_name = "../evil"
        _CURATED_SKILLS[evil_name] = CatalogSkillEntry(
            name=evil_name, description="attacker"
        )
        try:
            result = catalog.download_skill(evil_name, str(tmp_path))
        finally:
            del _CURATED_SKILLS[evil_name]

        assert result is None
        # Nothing must have been written outside tmp_path.
        evil_file = tmp_path.parent / "evil.md"
        assert not evil_file.exists(), "traversal escape must not create a file outside target"

    def test_traversal_dotdot_no_slash_returns_none(self, tmp_path: Path) -> None:
        """A name of '..' (bare) must be rejected."""
        catalog = SkillCatalog()
        evil_name = ".."
        _CURATED_SKILLS[evil_name] = CatalogSkillEntry(
            name=evil_name, description="attacker"
        )
        try:
            result = catalog.download_skill(evil_name, str(tmp_path))
        finally:
            del _CURATED_SKILLS[evil_name]

        assert result is None

    def test_slash_in_name_returns_none(self, tmp_path: Path) -> None:
        """A name like 'subdir/evil' (forward slash) must be rejected."""
        catalog = SkillCatalog()
        evil_name = "subdir/evil"
        _CURATED_SKILLS[evil_name] = CatalogSkillEntry(
            name=evil_name, description="attacker"
        )
        try:
            result = catalog.download_skill(evil_name, str(tmp_path))
        finally:
            del _CURATED_SKILLS[evil_name]

        assert result is None
        # No file should exist inside or outside target.
        assert not (tmp_path / "subdir" / "evil.md").exists()

    def test_backslash_in_name_returns_none(self, tmp_path: Path) -> None:
        """A name containing a backslash must be rejected."""
        catalog = SkillCatalog()
        evil_name = "..\\evil"
        _CURATED_SKILLS[evil_name] = CatalogSkillEntry(
            name=evil_name, description="attacker"
        )
        try:
            result = catalog.download_skill(evil_name, str(tmp_path))
        finally:
            del _CURATED_SKILLS[evil_name]

        assert result is None

    def test_dotdot_embedded_returns_none(self, tmp_path: Path) -> None:
        """A name with embedded '..' (e.g. 'foo..bar') must be rejected."""
        catalog = SkillCatalog()
        evil_name = "foo..bar"
        _CURATED_SKILLS[evil_name] = CatalogSkillEntry(
            name=evil_name, description="attacker"
        )
        try:
            result = catalog.download_skill(evil_name, str(tmp_path))
        finally:
            del _CURATED_SKILLS[evil_name]

        assert result is None

    def test_no_write_outside_target_on_traversal(self, tmp_path: Path) -> None:
        """Parameterised check: none of the traversal patterns must write outside."""
        catalog = SkillCatalog()
        dangerous_names = [
            "../escape",
            "../../etc/cron.d/evil",
            "/absolute/path",
            "a/b",
            "..",
            ".",
        ]
        for evil_name in dangerous_names:
            _CURATED_SKILLS[evil_name] = CatalogSkillEntry(
                name=evil_name, description="attacker"
            )
            try:
                result = catalog.download_skill(evil_name, str(tmp_path))
            finally:
                del _CURATED_SKILLS[evil_name]

            assert result is None, f"Expected None for dangerous name {evil_name!r}"

        # The target dir should be entirely empty (no writes occurred).
        assert list(tmp_path.iterdir()) == [], (
            "target dir must be empty — no files written for any dangerous name"
        )


class TestDownloadSkillHappyPath:
    """A normal curated skill name must still download successfully."""

    def test_valid_curated_name_writes_file(self, tmp_path: Path) -> None:
        catalog = SkillCatalog()
        result = catalog.download_skill(_VALID_SKILL, str(tmp_path))

        assert result is not None
        assert result.exists()
        assert result.parent == tmp_path
        assert result.suffix == ".md"
        content = result.read_text()
        assert len(content) > 0

    def test_valid_name_file_stays_within_target(self, tmp_path: Path) -> None:
        """The written file must be directly inside target, not outside."""
        catalog = SkillCatalog()
        result = catalog.download_skill(_VALID_SKILL, str(tmp_path))

        assert result is not None
        # Confirm the real resolved path starts with tmp_path.
        assert str(result.resolve()).startswith(str(tmp_path.resolve()))

    def test_missing_skill_name_returns_none(self, tmp_path: Path) -> None:
        """A name not in the curated catalog returns None without writing."""
        catalog = SkillCatalog()
        result = catalog.download_skill("nonexistent-skill-xyz", str(tmp_path))

        assert result is None
        assert list(tmp_path.iterdir()) == []
