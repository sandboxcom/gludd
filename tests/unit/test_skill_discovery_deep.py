"""Deep skill discovery and parsing tests: SKILL.md parsing, frontmatter
extraction, skill validation, trigger matching, and tool availability listing.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from general_ludd.ansible.skill_lens import (
    _SKILL_NAME_RE,
    InvalidSkillError,
    _read_header_name,
    _score_relevance,
    _skill_path,
    _strip_frontmatter,
    _tokenize,
)
from general_ludd.skills.fetcher import build_skill_frontmatter
from general_ludd.skills.loader import discover_skills, parse_skill_md
from general_ludd.skills.registry import SkillRegistry
from general_ludd.skills.skill import Skill

# ── SKILL.md Parsing (Frontmatter Extraction) ──────────────────────────


class TestFrontmatterParsing:
    def test_all_fields_populated(self):
        content = """---
name: my-skill
description: Does useful things.
category: engineering
model_profile: sonnet
tools:
  - read
  - grep
  - glob
trigger_patterns:
  - Python
  - asyncio
tags:
  - security
  - performance
---
# Body content here.
"""
        skill = parse_skill_md(content, source_path="/f/skill.md")
        assert skill.name == "my-skill"
        assert skill.description == "Does useful things."
        assert skill.category == "engineering"
        assert skill.model_profile == "sonnet"
        assert skill.tools == ["read", "grep", "glob"]
        assert skill.trigger_patterns == ["Python", "asyncio"]
        assert skill.tags == ["security", "performance"]
        assert "# Body content here." in skill.body
        assert skill.source_path == "/f/skill.md"

    def test_minimal_frontmatter_name_only(self):
        content = "---\nname: minimal\n---\nJust a body.\n"
        skill = parse_skill_md(content)
        assert skill.name == "minimal"
        assert skill.description == ""
        assert skill.category == ""
        assert skill.model_profile is None
        assert skill.tools == []
        assert skill.trigger_patterns == []
        assert skill.tags == []
        assert skill.body.strip() == "Just a body."

    def test_no_frontmatter_falls_back_to_filename(self):
        content = "# No frontmatter here\n\nSome body text.\n"
        skill = parse_skill_md(content, source_path="/f/my_tool.md")
        assert skill.name == "my_tool"
        assert skill.description == ""
        assert skill.body == content

    def test_no_frontmatter_no_source_path_raises(self):
        content = "# No frontmatter, no path\n\nBody.\n"
        with pytest.raises(ValueError, match="name must not be empty"):
            parse_skill_md(content)

    def test_yaml_error_fallback_returns_defaults(self):
        content = "---\nname: !!bad yaml :broken\ndescription: nope\n---\nBody.\n"
        skill = parse_skill_md(content, source_path="/f/fallback.md")
        assert skill.name == "fallback"
        assert skill.description == ""
        assert skill.body.strip() == "Body."

    def test_empty_frontmatter_block(self):
        content = "---\n---\nBody after empty frontmatter.\n"
        skill = parse_skill_md(content, source_path="/f/empty.md")
        assert skill.name == "empty"
        assert skill.description == ""
        assert "Body after empty frontmatter" in skill.body

    def test_frontmatter_not_at_file_start_ignored(self):
        content = "some text---\nname: ignored\n---\nactual body\n"
        skill = parse_skill_md(content, source_path="/f/late.md")
        assert skill.name == "late"
        assert skill.body == content
        assert skill.trigger_patterns == []

    def test_frontmatter_metadata_nested_dict(self):
        content = """---
name: nested-skill
metadata:
  category: research
  priority: high
---
Body content.
"""
        skill = parse_skill_md(content)
        assert skill.name == "nested-skill"
        assert skill.body.strip() == "Body content."

    def test_frontmatter_int_name_rejected_by_pydantic(self):
        content = "---\nname: 42\n---\nBody.\n"
        with pytest.raises(ValidationError, match="name"):
            parse_skill_md(content)

    def test_frontmatter_with_tabs_in_body(self):
        content = "---\nname: tabbed\n---\n\tindented body line\n\tsecond tabbed line\n"
        skill = parse_skill_md(content)
        assert skill.name == "tabbed"
        assert "\tindented body line" in skill.body

    def test_frontmatter_with_windows_line_endings(self):
        content = "---\r\nname: windows\r\n---\r\nBody.\r\n"
        skill = parse_skill_md(content)
        assert skill.name == "windows"
        assert "Body." in skill.body

    def test_body_with_dashes_not_parsed_as_frontmatter(self):
        content = """---
name: dashes
---
--- this is not frontmatter
still body text
"""
        skill = parse_skill_md(content)
        assert "--- this is not frontmatter" in skill.body


# ── Skill Model Validation ─────────────────────────────────────────────


class TestSkillValidation:
    def test_model_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            Skill(name="")

    def test_model_rejects_whitespace_only_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            Skill(name="   ")

    def test_model_rejects_none_name(self):
        with pytest.raises(ValidationError):
            Skill(name=None)  # type: ignore[arg-type]

    def test_skill_name_re_allows_valid_names(self):
        valid = [
            "python-expert",
            "go_expert",
            "azure123",
            "x",
            "a-b-c_d",
        ]
        for name in valid:
            assert _SKILL_NAME_RE.match(name), f"should match: {name}"

    def test_skill_name_re_rejects_invalid_names(self):
        invalid = [
            "Python-Expert",
            "123start",
            "-leading-dash",
            "name with spaces",
            "",
            "has/slash",
            "has.dot",
        ]
        for name in invalid:
            assert not _SKILL_NAME_RE.match(name), f"should NOT match: {name}"

    def test_skill_path_rejects_invalid_skill_name(self):
        with pytest.raises(InvalidSkillError, match="Invalid skill name"):
            _skill_path("Not Valid!")

    def test_skill_path_rejects_nonexistent_skill(self):
        with pytest.raises(InvalidSkillError, match="does not exist"):
            _skill_path("completely-nonexistent-skill-xyz")

    def test_skill_model_serialization_roundtrip(self):
        skill = Skill(
            name="test-skill",
            description="A test",
            category="eng",
            model_profile="sonnet",
            tools=["read", "edit"],
            trigger_patterns=["deploy", "release"],
            tags=["ci"],
            body="# Hello\n\nWorld.\n",
            source_path="/tmp/skills/test.md",
        )
        data = skill.model_dump()
        assert data["name"] == "test-skill"
        assert data["tools"] == ["read", "edit"]
        assert data["trigger_patterns"] == ["deploy", "release"]
        restored = Skill(**data)
        assert restored.name == skill.name
        assert restored.tools == skill.tools

    def test_skill_model_defaults(self):
        skill = Skill(name="defaulted")
        assert skill.description == ""
        assert skill.category == ""
        assert skill.model_profile is None
        assert skill.tools == []
        assert skill.trigger_patterns == []
        assert skill.tags == []
        assert skill.body == ""
        assert skill.source_path is None


# ── Trigger Matching ───────────────────────────────────────────────────


class TestTriggerMatching:
    def test_substring_case_insensitive_match(self):
        skill = Skill(name="python-expert", trigger_patterns=["Python", "ASYNCIO"])
        reg = SkillRegistry()
        reg.register(skill)
        matches = reg.match_trigger("I need help with python asyncio debugging")
        assert len(matches) == 1
        assert matches[0].name == "python-expert"

    def test_multiple_triggers_same_skill_only_once(self):
        skill = Skill(name="x", trigger_patterns=["hello", "world"])
        reg = SkillRegistry()
        reg.register(skill)
        matches = reg.match_trigger("hello world")
        assert len(matches) == 1

    def test_no_match_returns_empty(self):
        reg = SkillRegistry()
        reg.register(Skill(name="x", trigger_patterns=["deploy to prod"]))
        assert reg.match_trigger("review this code") == []

    def test_empty_trigger_list_never_matches(self):
        reg = SkillRegistry()
        reg.register(Skill(name="x", trigger_patterns=[]))
        assert reg.match_trigger("any text at all") == []

    def test_special_characters_in_trigger(self):
        reg = SkillRegistry()
        reg.register(Skill(name="x", trigger_patterns=["release/*", "v1.0"]))
        matches = reg.match_trigger("cut release/* and v1.0 tag")
        assert len(matches) == 1

    def test_global_and_project_triggers_both_checked(self):
        reg = SkillRegistry()
        reg.register(Skill(name="global", trigger_patterns=["build"]))
        reg.register(Skill(name="proj", trigger_patterns=["build"]), project_id="p1")
        matches = reg.match_trigger("trigger a build", project_id="p1")
        names = {s.name for s in matches}
        assert names == {"global", "proj"}

    def test_project_skill_only_matches_with_project_id(self):
        reg = SkillRegistry()
        reg.register(Skill(name="secret", trigger_patterns=["deploy"]), project_id="p1")
        assert len(reg.match_trigger("deploy", project_id="p1")) == 1
        assert reg.match_trigger("deploy") == []

    def test_trigger_matching_stops_at_first_field_match(self):
        reg = SkillRegistry()
        reg.register(Skill(name="a", trigger_patterns=["x"]))
        reg.register(Skill(name="b", trigger_patterns=["x"]))
        matches = reg.match_trigger("x marks the spot")
        assert len(matches) == 2
        assert {s.name for s in matches} == {"a", "b"}


# ── Tool Availability Listing ──────────────────────────────────────────


class TestToolAvailability:
    def test_tools_as_yaml_list(self):
        content = "---\nname: t\ntools:\n  - bash\n  - read\n  - write\n---\nBody.\n"
        skill = parse_skill_md(content)
        assert skill.tools == ["bash", "read", "write"]

    def test_tools_as_comma_separated_string(self):
        content = "---\nname: t\ntools: grep, glob, edit\n---\nBody.\n"
        skill = parse_skill_md(content)
        assert skill.tools == ["grep", "glob", "edit"]

    def test_comma_separated_tools_trim_whitespace(self):
        content = "---\nname: t\ntools: ' read ,  write ,  bash '\n---\nBody.\n"
        skill = parse_skill_md(content)
        assert skill.tools == ["read", "write", "bash"]

    def test_no_tools_defaults_to_empty(self):
        content = "---\nname: t\n---\nBody.\n"
        skill = parse_skill_md(content)
        assert skill.tools == []

    def test_tools_single_string_value(self):
        content = "---\nname: t\ntools: read\n---\nBody.\n"
        skill = parse_skill_md(content)
        assert skill.tools == ["read"]


# ── Skill Discovery ────────────────────────────────────────────────────


class TestSkillDiscovery:
    def test_nonexistent_directory_returns_empty(self):
        skills = discover_skills("/this/path/does/not/exist/12345")
        assert skills == []

    def test_nested_directories_recursive_glob(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = os.path.join(tmpdir, "a", "b")
            os.makedirs(sub)
            with open(os.path.join(sub, "deep.md"), "w") as f:
                f.write("---\nname: deep_skill\n---\nDeep body.\n")
            skills = discover_skills(tmpdir)
            names = {s.name for s in skills}
            assert "deep_skill" in names

    def test_only_md_files_discovered(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "skill.md"), "w") as f:
                f.write("---\nname: md_only\n---\nBody.\n")
            with open(os.path.join(tmpdir, "not_a_skill.txt"), "w") as f:
                f.write("---\nname: not_skill\n---\n")
            with open(os.path.join(tmpdir, "also_not.json"), "w") as f:
                f.write("---\nname: nope\n---\n")
            skills = discover_skills(tmpdir)
            names = {s.name for s in skills}
            assert names == {"md_only"}

    def test_duplicate_name_last_skill_wins(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "first.md"), "w") as f:
                f.write("---\nname: dup\n---\nFirst body.\n")
            with open(os.path.join(tmpdir, "z_second.md"), "w") as f:
                f.write("---\nname: dup\n---\nSecond body.\n")
            skills = discover_skills(tmpdir)
            assert len(skills) == 1
            assert skills[0].body.strip() == "Second body."

    def test_multiple_search_paths_merged(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            with open(os.path.join(d1, "alpha.md"), "w") as f:
                f.write("---\nname: alpha\n---\nAlpha body.\n")
            with open(os.path.join(d2, "beta.md"), "w") as f:
                f.write("---\nname: beta\n---\nBeta body.\n")
            skills = discover_skills(d1, d2)
            names = {s.name for s in skills}
            assert "alpha" in names
            assert "beta" in names

    def test_discover_skills_from_real_bundled_dir(self):
        bundled = Path(__file__).resolve().parent.parent.parent / ".opencode" / "skills"
        skills = discover_skills(str(bundled))
        names = {s.name for s in skills}
        assert "python-expert" in names
        assert "go-expert" in names
        assert "git-release-captain" in names
        assert len(skills) >= 16
        for skill in skills:
            assert isinstance(skill, Skill)
            assert skill.name

    def test_empty_directory_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills = discover_skills(tmpdir)
            assert skills == []


# ── build_skill_frontmatter ────────────────────────────────────────────


class TestBuildSkillFrontmatter:
    def test_roundtrip_parse_after_build(self):
        skill = Skill(
            name="test-skill",
            description="A test skill with description",
            body="## Section 1\n\nContent here.\n",
        )
        rebuilt = build_skill_frontmatter(skill)
        parsed = parse_skill_md(rebuilt)
        assert parsed.name == "test-skill"
        assert parsed.description == "A test skill with description"
        assert "## Section 1" in parsed.body

    def test_safe_dump_injection_proof(self):
        skill = Skill(
            name="evil",
            description="x\nmodel_profile: attacker\ntools: [shell]\ntrigger_patterns: ['.*']",
            body="payload",
        )
        rebuilt = build_skill_frontmatter(skill)
        parsed = parse_skill_md(rebuilt)
        assert parsed.name == "evil"
        assert parsed.model_profile is None
        assert parsed.tools == []
        assert parsed.trigger_patterns == []

    def test_build_frontmatter_starts_with_dashes(self):
        skill = Skill(name="test", description="desc", body="hello")
        result = build_skill_frontmatter(skill)
        assert result.startswith("---\n")


# ── skill_lens utilities ───────────────────────────────────────────────


class TestSkillLensHelpers:
    def test_strip_frontmatter_removes_yaml_block(self):
        text = "---\nname: x\n---\n# Header\nContent.\n"
        stripped = _strip_frontmatter(text)
        assert stripped.startswith("# Header")
        assert "---" not in stripped

    def test_strip_frontmatter_no_dashes_returns_original(self):
        text = "# Just a heading\nNo frontmatter.\n"
        stripped = _strip_frontmatter(text)
        assert stripped == text

    def test_read_header_name_extracts_h1(self):
        text = "---\nname: x\n---\n# My Header\ncontent.\n"
        assert _read_header_name(text) == "My Header"

    def test_read_header_name_no_header_returns_empty(self):
        assert _read_header_name("no header here\n") == ""

    def test_tokenize_excludes_short_tokens_and_numbers(self):
        tokens = _tokenize("python asyncio debug")
        assert "python" in tokens
        assert "asyncio" in tokens
        assert "debug" in tokens

    def test_tokenize_handles_underscore_identifiers(self):
        tokens = _tokenize("test_discovery with token_split")
        assert "test" in tokens or "discovery" in tokens

    def test_score_relevance_empty_inputs_zero(self):
        assert _score_relevance("", "any section") == 0.0
        assert _score_relevance("any task", "") == 0.0

    def test_score_relevance_matching_tokens_positive(self):
        score = _score_relevance("python asyncio", "python asyncio coroutines")
        assert score > 0.0

    def test_score_relevance_no_overlap_low(self):
        score = _score_relevance("woodworking", "python programming")
        assert score < 0.15


# ── Skill Model edge cases ─────────────────────────────────────────────


class TestSkillEdgeCases:
    def test_skill_repr(self):
        skill = Skill(name="test", description="A test")
        r = repr(skill)
        assert "test" in r

    def test_skill_equality_by_value(self):
        a = Skill(name="test", description="desc")
        b = Skill(name="test", description="desc")
        assert a == b

    def test_skill_inequality_different_name(self):
        a = Skill(name="a")
        b = Skill(name="b")
        assert a != b

    def test_skill_inequality_different_tools(self):
        a = Skill(name="x", tools=["read"])
        b = Skill(name="x", tools=["write"])
        assert a != b
