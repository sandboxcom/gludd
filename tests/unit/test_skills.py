"""Unit tests for skill loading and registry."""

from __future__ import annotations

import os
import tempfile

from agentic_harness.skills.loader import discover_skills, parse_skill_md
from agentic_harness.skills.registry import SkillRegistry
from agentic_harness.skills.skill import Skill


class TestParseSkillMd:
    def test_parse_skill_md_with_frontmatter(self):
        content = (
            "---\n"
            "name: return_review\n"
            "description: Review task return results\n"
            "model_profile: strong_model\n"
            "tools: [read, grep, glob]\n"
            "trigger_patterns: [\"return review\", \"review result\"]\n"
            "tags: [review, core]\n"
            "---\n"
            "# Return Review Skill\n"
            "\n"
            "Review the task return artifacts.\n"
        )
        skill = parse_skill_md(content, source_path="/path/to/return_review.md")
        assert skill.name == "return_review"
        assert skill.description == "Review task return results"
        assert skill.model_profile == "strong_model"
        assert skill.tools == ["read", "grep", "glob"]
        assert skill.trigger_patterns == ["return review", "review result"]
        assert skill.tags == ["review", "core"]
        assert "# Return Review Skill" in skill.body
        assert "Review the task return artifacts." in skill.body
        assert skill.source_path == "/path/to/return_review.md"

    def test_parse_skill_md_without_frontmatter(self):
        content = "# Just a skill\n\nSome body text.\n"
        skill = parse_skill_md(content, source_path="/path/to/my_skill.md")
        assert skill.name == "my_skill"
        assert skill.description == ""
        assert skill.model_profile is None
        assert skill.tools == []
        assert skill.trigger_patterns == []
        assert skill.tags == []
        assert skill.body == content
        assert skill.source_path == "/path/to/my_skill.md"

    def test_parse_skill_md_with_only_name(self):
        content = "---\nname: foo\n---\nBody here.\n"
        skill = parse_skill_md(content)
        assert skill.name == "foo"
        assert skill.description == ""
        assert skill.model_profile is None
        assert skill.tools == []
        assert skill.trigger_patterns == []
        assert skill.tags == []
        assert skill.body.strip() == "Body here."

    def test_skill_model_loads_content_after_frontmatter(self):
        content = "---\nname: test\n---\n\n# Heading\n\nParagraph text.\n"
        skill = parse_skill_md(content)
        assert "# Heading" in skill.body
        assert "Paragraph text." in skill.body
        assert "---" not in skill.body


class TestSkillDiscovery:
    def test_skill_discovery_from_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_content = (
                "---\n"
                "name: skill_a\n"
                "description: Skill A\n"
                "---\n"
                "Body A\n"
            )
            with open(os.path.join(tmpdir, "skill_a.md"), "w") as f:
                f.write(skill_content)

            with open(os.path.join(tmpdir, "not_a_skill.txt"), "w") as f:
                f.write("ignored")

            skills = discover_skills(tmpdir)
            assert len(skills) == 1
            assert skills[0].name == "skill_a"

    def test_skill_discovery_multiple_paths(self):
        with tempfile.TemporaryDirectory() as dir1, tempfile.TemporaryDirectory() as dir2:
            with open(os.path.join(dir1, "shared.md"), "w") as f:
                f.write("---\nname: shared\ndescription: first\n---\nFirst body.\n")

            with open(os.path.join(dir2, "shared.md"), "w") as f:
                f.write("---\nname: shared\ndescription: second\n---\nSecond body.\n")

            with open(os.path.join(dir1, "only_first.md"), "w") as f:
                f.write("---\nname: only_first\n---\nOnly in first.\n")

            skills = discover_skills(dir1, dir2)
            names = {s.name for s in skills}
            assert names == {"shared", "only_first"}

            shared = next(s for s in skills if s.name == "shared")
            assert shared.description == "second"

    def test_skill_discovery_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skills = discover_skills(tmpdir)
            assert skills == []


class TestSkillRegistry:
    def test_skill_registry_register_and_get(self):
        reg = SkillRegistry()
        skill = Skill(name="test_skill", description="A test")
        reg.register(skill)
        got = reg.get("test_skill")
        assert got is not None
        assert got.name == "test_skill"

    def test_skill_registry_get_missing_returns_none(self):
        reg = SkillRegistry()
        assert reg.get("nonexistent") is None

    def test_skill_registry_list_by_tag(self):
        reg = SkillRegistry()
        reg.register(Skill(name="a", tags=["review", "core"]))
        reg.register(Skill(name="b", tags=["deploy"]))
        reg.register(Skill(name="c", tags=["review"]))
        review_skills = reg.list_skills(tag="review")
        names = [s.name for s in review_skills]
        assert "a" in names
        assert "c" in names
        assert "b" not in names

    def test_skill_registry_list_all(self):
        reg = SkillRegistry()
        reg.register(Skill(name="a"))
        reg.register(Skill(name="b"))
        all_skills = reg.list_skills()
        assert len(all_skills) == 2

    def test_skill_registry_match_trigger(self):
        reg = SkillRegistry()
        reg.register(Skill(
            name="review",
            trigger_patterns=["return review", "review result"],
        ))
        reg.register(Skill(
            name="deploy",
            trigger_patterns=["deploy", "ship it"],
        ))
        matches = reg.match_trigger("please return review for this task")
        names = [s.name for s in matches]
        assert "review" in names
        assert "deploy" not in names

    def test_skill_registry_match_trigger_no_match(self):
        reg = SkillRegistry()
        reg.register(Skill(name="a", trigger_patterns=["xyz"]))
        assert reg.match_trigger("nothing matches") == []


class TestSkillModel:
    def test_skill_has_required_fields(self):
        skill = Skill(
            name="test",
            description="desc",
            model_profile="profile",
            tools=["read"],
            trigger_patterns=["trigger"],
            body="body text",
            source_path="/path/to/skill.md",
            tags=["tag1"],
        )
        assert skill.name == "test"
        assert skill.description == "desc"
        assert skill.model_profile == "profile"
        assert skill.tools == ["read"]
        assert skill.trigger_patterns == ["trigger"]
        assert skill.body == "body text"
        assert skill.source_path == "/path/to/skill.md"
        assert skill.tags == ["tag1"]

    def test_skill_defaults(self):
        skill = Skill(name="minimal")
        assert skill.description == ""
        assert skill.model_profile is None
        assert skill.tools == []
        assert skill.trigger_patterns == []
        assert skill.tags == []
        assert skill.body == ""
        assert skill.source_path is None
