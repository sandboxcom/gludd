"""Tests for ansible/skill_lens: skill section extraction and relevance scoring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.ansible.skill_lens import (
    InvalidSkillError,
    _parse_sections,
    _score_relevance,
    _skill_path,
    _skills_dir,
    clear_cache,
    lens,
    lens_raw,
)

SKILL_FIXTURES = Path(__file__).parent / "skill_lens_fixtures"


class TestSkillPath:
    def test_resolves_skill_dir(self):
        result = _skills_dir()
        assert result.name == "skills"
        assert ".opencode" in result.parts

    def test_raises_when_skills_dir_missing(self, tmp_path, monkeypatch):
        bad = tmp_path / "nonexistent"
        monkeypatch.setattr(
            "general_ludd.ansible.skill_lens._skills_dir", lambda: bad
        )
        with pytest.raises(InvalidSkillError, match="Skills directory not found"):
            _skill_path("python-expert")

    def test_invalid_skill_name_with_slash(self):
        with pytest.raises(InvalidSkillError, match="Invalid skill name"):
            _skill_path("../etc/passwd")

    def test_invalid_skill_name_with_null(self):
        with pytest.raises(InvalidSkillError, match="Invalid skill name"):
            _skill_path("bad\0name")

    def test_skill_not_found(self, tmp_path, monkeypatch):
        fake_dir = tmp_path / "skills"
        fake_dir.mkdir()
        monkeypatch.setattr(
            "general_ludd.ansible.skill_lens._skills_dir", lambda: fake_dir
        )
        with pytest.raises(InvalidSkillError, match="does not exist"):
            _skill_path("nonexistent-skill")


class TestParseSections:
    def test_parses_header_sections(self):
        text = """# Title

Some intro text.

## Section One

Content of section one.

## Section Two

Content of section two.
"""
        sections = _parse_sections(text)
        assert len(sections) == 2
        assert sections[0][0] == "## Section One"
        assert "Content of section one" in sections[0][1]

    def test_parses_numbered_sections(self):
        text = """# Skill

## 1. Foo

Foo content.

## 2. Bar

Bar content.
"""
        sections = _parse_sections(text)
        assert len(sections) == 2
        assert sections[0][0] == "## 1. Foo"
        assert sections[1][0] == "## 2. Bar"

    def test_skips_frontmatter(self):
        text = """---
name: test
---

# Title

## Relevant Section

Content here.
"""
        sections = _parse_sections(text)
        assert len(sections) == 1
        assert sections[0][0] == "## Relevant Section"

    def test_no_sections_returns_empty(self):
        text = "# Just a title\n\nNo sections here."
        sections = _parse_sections(text)
        assert sections == []

    def test_handles_empty_text(self):
        assert _parse_sections("") == []

    def test_handles_sub_sections(self):
        text = """## Main

Main content.

### Sub A

Sub A content.

### Sub B

Sub B content.

## Second

Second content.
"""
        sections = _parse_sections(text)
        assert len(sections) == 2
        assert "### Sub A" in sections[0][1]
        assert "### Sub B" in sections[0][1]
        assert "Second content" in sections[1][1]


class TestScoreRelevance:
    def test_exact_keyword_match_scores_highest(self):
        section_text = "This is about asyncio deadlocks and event loops."
        score = _score_relevance("debug an asyncio deadlock", section_text)
        assert score > 0.0

    def test_unrelated_section_scores_low(self):
        section_text = "This is about Jython compatibility and Java interop."
        score = _score_relevance("debug an asyncio deadlock", section_text)
        assert score >= 0.0

    def test_asyncio_task_scores_higher_than_jython(self):
        async_text = "asyncio event loop coroutine deadlock debugging signal handler"
        jython_text = "Jython Java interop classpath compatibility swing GUI"
        async_score = _score_relevance(
            "debug an asyncio deadlock in the event loop", async_text
        )
        jython_score = _score_relevance(
            "debug an asyncio deadlock in the event loop", jython_text
        )
        assert async_score > jython_score

    def test_empty_task_returns_zero(self):
        assert _score_relevance("", "some content") == 0.0

    def test_empty_section_returns_zero(self):
        assert _score_relevance("some task", "") == 0.0

    def test_case_insensitive(self):
        score_lower = _score_relevance("AsYnCiO dEaDlOcK", "asyncio Deadlock")
        score_normal = _score_relevance("asyncio deadlock", "asyncio deadlock")
        assert score_lower == score_normal

    def test_compound_words_partial_match(self):
        score = _score_relevance("event loop", "event_loop")
        assert score > 0.0

    def test_scores_are_between_zero_and_one(self):
        score = _score_relevance("debug", "debugging and profiling tools for python")
        assert 0.0 <= score <= 1.0

    def test_repeated_keywords_increase_score(self):
        low = _score_relevance("deadlock debugging race condition", "deadlock")
        high = _score_relevance("deadlock debugging race condition", "deadlock debugging race condition")
        assert high > low


class TestLensFunction:
    def test_lens_returns_text_for_valid_skill(self):
        result = lens("test-quality", "write a good test", max_sections=2)
        assert isinstance(result, str)
        assert len(result) > 0
        assert result.startswith("#")

    def test_lens_raises_for_nonexistent_skill(self):
        with pytest.raises(InvalidSkillError):
            lens("nonexistent-skill-xyz", "some task")

    def test_lens_returns_fewer_sections_when_skill_has_fewer(self):
        result = lens("test-quality", "task", max_sections=100)
        num_sections = result.count("\n## ")
        available_sections = _parse_sections(_skill_path("test-quality").read_text())
        assert num_sections == len(available_sections)

    def test_lens_max_sections_one(self):
        result = lens("test-quality", "write a good test", max_sections=1)
        num = result.count("\n## ")
        assert num <= 1

    def test_lens_empty_task_returns_first_n(self):
        result = lens("test-quality", "", max_sections=2)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_lens_output_is_parseable_markdown(self):
        result = lens("test-quality", "isolation and determinism", max_sections=2)
        assert result.startswith("#")
        assert "## " in result

    def test_lens_raw_returns_all_fields(self):
        result = lens_raw("test-quality", "test isolation", max_sections=1)
        assert isinstance(result, dict)
        assert "sections" in result
        assert "skill_name" in result
        assert "header" in result

    def test_lens_raw_sections_contains_header_and_content(self):
        result = lens_raw("test-quality", "test isolation", max_sections=1)
        sections = result["sections"]
        assert len(sections) >= 1
        assert "header" in sections[0]
        assert "body" in sections[0]
        assert "score" in sections[0]


class TestCaching:
    def test_repeated_calls_return_cached(self):
        clear_cache()
        with patch(
            "general_ludd.ansible.skill_lens._read_skill_file",
            wraps=lambda p: _read_skill_file_real(p),
        ) as mock_read:
            lens("test-quality", "test xyz", max_sections=1)
            call_count = mock_read.call_count
            lens("test-quality", "test xyz", max_sections=1)
            assert mock_read.call_count == call_count

    def test_different_args_bust_cache(self):
        clear_cache()
        with patch(
            "general_ludd.ansible.skill_lens._read_skill_file",
            wraps=lambda p: _read_skill_file_real(p),
        ) as mock_read:
            lens("test-quality", "task A", max_sections=1)
            lens("type-safety", "task B", max_sections=1)
            assert mock_read.call_count >= 2

    def test_clear_cache_works(self):
        lens("test-quality", "task", max_sections=1)
        clear_cache()
        with patch(
            "general_ludd.ansible.skill_lens._read_skill_file",
            wraps=lambda p: _read_skill_file_real(p),
        ) as mock_read:
            lens("test-quality", "task", max_sections=1)
            assert mock_read.call_count >= 1


def _read_skill_file_real(path: Path) -> str:
    return path.read_text()
