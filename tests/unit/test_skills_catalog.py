"""Tests for skills catalog: search, download, and install curated skills."""

from __future__ import annotations

from general_ludd.skills.catalog import CatalogSkillEntry, SkillCatalog


class TestCatalogSkillEntry:
    def test_entry_has_required_fields(self):
        entry = CatalogSkillEntry(
            name="tdd-discipline",
            description="Enforce TDD",
            category="methodology",
            tags=["testing", "tdd"],
        )
        assert entry.name == "tdd-discipline"
        assert entry.category == "methodology"

    def test_entry_defaults(self):
        entry = CatalogSkillEntry(name="test")
        assert entry.tags == []
        assert entry.source == ""


class TestSkillCatalogSearch:
    def test_search_returns_results(self):
        catalog = SkillCatalog()
        results = catalog.search(query="tdd")
        assert len(results) >= 1
        assert any(r.name == "tdd-discipline" for r in results)

    def test_search_by_tag(self):
        catalog = SkillCatalog()
        results = catalog.search(tags=["security"])
        assert len(results) >= 1
        assert any(r.name == "security-first" for r in results)

    def test_search_by_category(self):
        catalog = SkillCatalog()
        results = catalog.search(category="patterns")
        assert len(results) >= 2

    def test_search_no_match_returns_empty(self):
        catalog = SkillCatalog()
        results = catalog.search(query="nonexistent-xyzzy")
        assert results == []

    def test_search_respects_limit(self):
        catalog = SkillCatalog()
        results = catalog.search(limit=2)
        assert len(results) <= 2


class TestSkillCatalogGet:
    def test_get_skill_returns_entry(self):
        catalog = SkillCatalog()
        entry = catalog.get_skill("tdd-discipline")
        assert entry is not None
        assert entry.name == "tdd-discipline"

    def test_get_skill_returns_none_for_unknown(self):
        catalog = SkillCatalog()
        assert catalog.get_skill("nonexistent") is None


class TestSkillCatalogDownload:
    def test_download_skill_creates_file(self, tmp_path):
        catalog = SkillCatalog()
        result = catalog.download_skill("tdd-discipline", str(tmp_path))
        assert result is not None
        assert result.exists()
        content = result.read_text()
        assert "tdd-discipline" in content
        assert "test" in content.lower() or "TDD" in content

    def test_download_unknown_returns_none(self, tmp_path):
        catalog = SkillCatalog()
        result = catalog.download_skill("nonexistent", str(tmp_path))
        assert result is None

    def test_download_skill_file_is_valid_md(self, tmp_path):
        catalog = SkillCatalog()
        result = catalog.download_skill("security-first", str(tmp_path))
        assert result is not None
        content = result.read_text()
        assert content.startswith("---")
        assert "name" in content
        assert "security" in content.lower()


class TestSkillCatalogInstall:
    def test_install_skill_creates_in_skills_subdir(self, tmp_path):
        catalog = SkillCatalog()
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        result = catalog.install_skill("git-conventional-commits", str(config_dir))
        assert result is not None
        assert "skills" in str(result)
        assert result.exists()

    def test_install_creates_skills_dir(self, tmp_path):
        catalog = SkillCatalog()
        config_dir = tmp_path / "newconfig"
        config_dir.mkdir()
        result = catalog.install_skill("tdd-discipline", str(config_dir))
        assert result is not None
        skills_dir = config_dir / "skills"
        assert skills_dir.exists()


class TestSkillCatalogMetadata:
    def test_list_categories(self):
        catalog = SkillCatalog()
        cats = catalog.list_categories()
        assert "methodology" in cats
        assert "security" in cats
        assert "patterns" in cats

    def test_list_tags(self):
        catalog = SkillCatalog()
        tags = catalog.list_tags()
        assert "testing" in tags
        assert "security" in tags
        assert "git" in tags
