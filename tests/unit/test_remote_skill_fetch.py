"""Tests for remote skill fetching from GitHub and other URLs."""
from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

from general_ludd.skills.fetcher import (
    GitHubSkillSource,
    RemoteSkillFetcher,
    fetch_github_skill,
    fetch_raw_url_skill,
)


class TestGitHubSkillSource:
    def test_parse_github_url_https(self):
        src = GitHubSkillSource.from_url("https://github.com/mattpocock/skills")
        assert src.owner == "mattpocock"
        assert src.repo == "skills"
        assert src.branch == "main"

    def test_parse_github_url_with_branch(self):
        src = GitHubSkillSource.from_url("https://github.com/mattpocock/skills/tree/v2")
        assert src.owner == "mattpocock"
        assert src.repo == "skills"
        assert src.branch == "v2"

    def test_parse_github_url_with_subdir(self):
        src = GitHubSkillSource.from_url(
            "https://github.com/mattpocock/skills/tree/main/skills/engineering"
        )
        assert src.owner == "mattpocock"
        assert src.repo == "skills"
        assert src.branch == "main"
        assert src.subdir == "skills/engineering"

    def test_list_skills_returns_entries(self):
        src = GitHubSkillSource(owner="mattpocock", repo="skills", branch="main")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"name": "diagnose", "type": "dir", "path": "skills/engineering/diagnose"},
            {"name": "tdd", "type": "dir", "path": "skills/engineering/tdd"},
            {"name": "README.md", "type": "file", "path": "README.md"},
        ]
        with patch("httpx.get", return_value=mock_response):
            entries = src.list_skills()
        names = [e.name for e in entries]
        assert "diagnose" in names
        assert "tdd" in names
        assert "README.md" not in names

    def test_download_skill_fetches_content(self):
        src = GitHubSkillSource(owner="mattpocock", repo="skills", branch="main")
        skill_content = "---\nname: diagnose\ndescription: Bug diagnosis\n---\nBody here.\n"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = skill_content
        with patch("httpx.get", return_value=mock_response):
            result = src.download_skill("skills/engineering/diagnose")
        assert result is not None
        assert result.name == "diagnose"
        assert "Bug diagnosis" in result.description


class TestRemoteSkillFetcher:
    def test_fetch_from_github_url(self):
        fetcher = RemoteSkillFetcher()
        skill_content = "---\nname: grill-me\ndescription: Grill the user\n---\nInterview relentlessly.\n"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = skill_content
        with patch("httpx.get", return_value=mock_response):
            skill = fetcher.fetch("https://raw.githubusercontent.com/example/repo/main/skill.md")
        assert skill is not None
        assert skill.name == "grill-me"

    def test_fetch_from_raw_url(self):
        fetcher = RemoteSkillFetcher()
        skill_content = "---\nname: my-skill\ndescription: A test skill\n---\nDo the thing.\n"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = skill_content
        with patch("httpx.get", return_value=mock_response):
            skill = fetcher.fetch("https://example.com/skills/my-skill.md")
        assert skill is not None
        assert skill.name == "my-skill"

    def test_fetch_returns_none_on_failure(self):
        fetcher = RemoteSkillFetcher()
        mock_response = MagicMock()
        mock_response.status_code = 404
        with patch("httpx.get", return_value=mock_response):
            skill = fetcher.fetch("https://example.com/nonexistent.md")
        assert skill is None

    def test_install_fetched_skill_to_directory(self):
        fetcher = RemoteSkillFetcher()
        skill_content = "---\nname: tdd\ndescription: TDD discipline\n---\nRed-green-refactor.\n"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = skill_content
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("httpx.get", return_value=mock_response):
                path = fetcher.install("https://example.com/tdd.md", tmpdir)
            assert path is not None
            assert path.exists()
            content = path.read_text()
            assert "tdd" in content


class TestFetchGithubSkill:
    def test_fetch_single_skill_from_github(self):
        skill_content = "---\nname: diagnose\ndescription: Diagnose bugs\n---\nDebug loop.\n"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = skill_content
        with patch("httpx.get", return_value=mock_response):
            skill = fetch_github_skill("mattpocock/skills", "skills/engineering/diagnose/SKILL.md")
        assert skill is not None
        assert skill.name == "diagnose"


class TestFetchRawUrlSkill:
    def test_fetch_from_raw_url(self):
        skill_content = "---\nname: caveman\ndescription: Ultra-compressed mode\n---\nLess tokens.\n"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = skill_content
        with patch("httpx.get", return_value=mock_response):
            skill = fetch_raw_url_skill("https://example.com/caveman.md")
        assert skill is not None
        assert skill.name == "caveman"


class TestMattpocockSkillsIntegration:
    def test_all_mattpocock_skills_in_catalog(self):
        from general_ludd.skills.catalog import SkillCatalog

        catalog = SkillCatalog()
        mp_skills = catalog.search(query="mattpocock")
        names = [s.name for s in mp_skills]
        expected = [
            "mp-diagnose",
            "mp-tdd",
            "mp-grill-me",
            "mp-grill-with-docs",
            "mp-caveman",
            "mp-handoff",
            "mp-to-prd",
            "mp-to-issues",
            "mp-zoom-out",
            "mp-improve-architecture",
            "mp-teach",
            "mp-write-a-skill",
        ]
        for name in expected:
            assert name in names, f"Missing mattpocock skill: {name}"

    def test_mattpocock_skill_has_body_preview(self):
        from general_ludd.skills.catalog import SkillCatalog

        catalog = SkillCatalog()
        skill = catalog.get_skill("mp-diagnose")
        assert skill is not None
        assert len(skill.body_preview) > 50
        assert "reproduce" in skill.body_preview.lower()

    def test_mattpocock_skill_has_source_url(self):
        from general_ludd.skills.catalog import SkillCatalog

        catalog = SkillCatalog()
        skill = catalog.get_skill("mp-tdd")
        assert skill is not None
        assert "github.com/mattpocock/skills" in skill.source_url
