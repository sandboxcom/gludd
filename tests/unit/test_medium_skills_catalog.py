"""Tests for the 10 medium-2026 skills catalog entries."""

from __future__ import annotations

import pytest

from general_ludd.skills.catalog import SkillCatalog

MEDIUM_SKILL_NAMES = [
    "medium-frontend-design",
    "medium-browser-use",
    "medium-code-reviewer",
    "medium-remotion",
    "medium-google-workspace",
    "medium-valyu",
    "medium-antigravity-skills",
    "medium-planetscale",
    "medium-shannon",
    "medium-excalidraw",
]


class TestMediumSkillsExist:
    @pytest.mark.parametrize("name", MEDIUM_SKILL_NAMES)
    def test_skill_exists_in_catalog(self, name):
        catalog = SkillCatalog()
        entry = catalog.get_skill(name)
        assert entry is not None, f"Skill {name} not found in catalog"
        assert entry.name == name

    def test_all_ten_medium_skills_present(self):
        catalog = SkillCatalog()
        for name in MEDIUM_SKILL_NAMES:
            assert catalog.get_skill(name) is not None


class TestMediumSkillsMetadata:
    @pytest.mark.parametrize("name", MEDIUM_SKILL_NAMES)
    def test_has_source(self, name):
        catalog = SkillCatalog()
        entry = catalog.get_skill(name)
        assert entry is not None
        assert entry.source == "unicodeveloper"

    @pytest.mark.parametrize("name", MEDIUM_SKILL_NAMES)
    def test_has_source_url(self, name):
        catalog = SkillCatalog()
        entry = catalog.get_skill(name)
        assert entry is not None
        assert "medium.com" in entry.source_url
        assert "unicodeveloper" in entry.source_url

    @pytest.mark.parametrize("name", MEDIUM_SKILL_NAMES)
    def test_has_medium_tag(self, name):
        catalog = SkillCatalog()
        entry = catalog.get_skill(name)
        assert entry is not None
        assert "medium-2026" in entry.tags

    @pytest.mark.parametrize("name", MEDIUM_SKILL_NAMES)
    def test_has_description(self, name):
        catalog = SkillCatalog()
        entry = catalog.get_skill(name)
        assert entry is not None
        assert len(entry.description) > 20

    @pytest.mark.parametrize("name", MEDIUM_SKILL_NAMES)
    def test_has_category(self, name):
        catalog = SkillCatalog()
        entry = catalog.get_skill(name)
        assert entry is not None
        assert entry.category != ""

    @pytest.mark.parametrize("name", MEDIUM_SKILL_NAMES)
    def test_has_body_preview(self, name):
        catalog = SkillCatalog()
        entry = catalog.get_skill(name)
        assert entry is not None
        assert len(entry.body_preview) > 50


class TestMediumSkillsCategories:
    def test_frontend_design_category(self):
        catalog = SkillCatalog()
        entry = catalog.get_skill("medium-frontend-design")
        assert entry is not None
        assert entry.category == "frontend"

    def test_browser_use_category(self):
        catalog = SkillCatalog()
        entry = catalog.get_skill("medium-browser-use")
        assert entry is not None
        assert entry.category == "automation"

    def test_code_reviewer_category(self):
        catalog = SkillCatalog()
        entry = catalog.get_skill("medium-code-reviewer")
        assert entry is not None
        assert entry.category == "quality"

    def test_remotion_category(self):
        catalog = SkillCatalog()
        entry = catalog.get_skill("medium-remotion")
        assert entry is not None
        assert entry.category == "media"

    def test_google_workspace_category(self):
        catalog = SkillCatalog()
        entry = catalog.get_skill("medium-google-workspace")
        assert entry is not None
        assert entry.category == "automation"

    def test_valyu_category(self):
        catalog = SkillCatalog()
        entry = catalog.get_skill("medium-valyu")
        assert entry is not None
        assert entry.category == "data"

    def test_antigravity_category(self):
        catalog = SkillCatalog()
        entry = catalog.get_skill("medium-antigravity-skills")
        assert entry is not None
        assert entry.category == "meta"

    def test_planetscale_category(self):
        catalog = SkillCatalog()
        entry = catalog.get_skill("medium-planetscale")
        assert entry is not None
        assert entry.category == "database"

    def test_shannon_category(self):
        catalog = SkillCatalog()
        entry = catalog.get_skill("medium-shannon")
        assert entry is not None
        assert entry.category == "security"

    def test_excalidraw_category(self):
        catalog = SkillCatalog()
        entry = catalog.get_skill("medium-excalidraw")
        assert entry is not None
        assert entry.category == "documentation"


class TestMediumSkillsSearchable:
    def test_search_frontend(self):
        catalog = SkillCatalog()
        results = catalog.search(query="typography")
        assert any(r.name == "medium-frontend-design" for r in results)

    def test_search_browser(self):
        catalog = SkillCatalog()
        results = catalog.search(query="browser automation")
        assert any(r.name == "medium-browser-use" for r in results)

    def test_search_pentesting(self):
        catalog = SkillCatalog()
        results = catalog.search(query="pentesting")
        assert any(r.name == "medium-shannon" for r in results)

    def test_search_by_tag_medium_2026(self):
        catalog = SkillCatalog()
        results = catalog.search(tags=["medium-2026"])
        assert len(results) == 10

    def test_search_by_category_data(self):
        catalog = SkillCatalog()
        results = catalog.search(category="data")
        assert any(r.name == "medium-valyu" for r in results)

    def test_search_by_category_media(self):
        catalog = SkillCatalog()
        results = catalog.search(category="media")
        assert any(r.name == "medium-remotion" for r in results)


class TestMediumSkillsDownloadable:
    @pytest.mark.parametrize("name", MEDIUM_SKILL_NAMES)
    def test_download_creates_file(self, name, tmp_path):
        catalog = SkillCatalog()
        result = catalog.download_skill(name, str(tmp_path))
        assert result is not None
        assert result.exists()

    @pytest.mark.parametrize("name", MEDIUM_SKILL_NAMES)
    def test_downloaded_file_has_frontmatter(self, name, tmp_path):
        catalog = SkillCatalog()
        result = catalog.download_skill(name, str(tmp_path))
        assert result is not None
        content = result.read_text()
        assert content.startswith("---")
        assert name in content
