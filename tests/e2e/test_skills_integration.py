"""E2E: Skill loading, discovery, and registry operations."""

from __future__ import annotations

import os
import tempfile

from general_ludd.skills.loader import discover_skills, parse_skill_md
from general_ludd.skills.registry import SkillRegistry
from general_ludd.skills.skill import Skill


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


class TestSkillLoading:
    def test_load_example_skill(self):
        path = os.path.join(_repo_root(), "config", "skills", "return_review.md")
        with open(path) as f:
            content = f.read()
        skill = parse_skill_md(content, source_path=path)
        assert skill.name == "return_review"
        assert skill.description == "Review task return results and make decisions"
        assert skill.model_profile == "strong_model"
        assert skill.tools == ["read", "grep", "glob"]
        assert "return review" in skill.trigger_patterns
        assert "review" in skill.tags
        assert "core" in skill.tags

    def test_discover_skills_from_config_dir(self):
        skills_dir = os.path.join(_repo_root(), "config", "skills")
        skills = discover_skills(skills_dir)
        names = {s.name for s in skills}
        assert "return_review" in names

    def test_skill_body_loaded(self):
        path = os.path.join(_repo_root(), "config", "skills", "return_review.md")
        with open(path) as f:
            content = f.read()
        skill = parse_skill_md(content, source_path=path)
        assert skill.body
        assert "Return Review Skill" in skill.body

    def test_skill_discovery_empty_dir(self):
        skills = discover_skills("/no/such/directory")
        assert skills == []


class TestSkillRegistry:
    def test_skill_registry_register_and_get(self):
        reg = SkillRegistry()
        skill = Skill(
            name="test_skill",
            description="A test",
            body="Do something",
        )
        reg.register(skill)
        found = reg.get("test_skill")
        assert found is not None
        assert found.name == "test_skill"

    def test_skill_registry_list_by_tag(self):
        reg = SkillRegistry()
        reg.register(
            Skill(
                name="a",
                tags=["review", "core"],
            )
        )
        reg.register(
            Skill(
                name="b",
                tags=["deploy"],
            )
        )
        review_skills = reg.list_skills(tag="review")
        assert len(review_skills) == 1
        assert review_skills[0].name == "a"

    def test_skill_registry_match_trigger(self):
        reg = SkillRegistry()
        reg.register(
            Skill(
                name="return_review",
                trigger_patterns=["return review", "review result"],
            )
        )
        reg.register(
            Skill(
                name="deploy",
                trigger_patterns=["deploy", "ship it"],
            )
        )
        matches = reg.match_trigger("please run a return review")
        assert len(matches) == 1
        assert matches[0].name == "return_review"

    def test_skill_model_profile_field(self):
        skill = Skill(
            name="profiled",
            model_profile="fast_model",
        )
        assert skill.model_profile == "fast_model"

    def test_multiple_skills_override_by_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = os.path.join(tmpdir, "first")
            os.makedirs(first)
            with open(os.path.join(first, "shared.md"), "w") as f:
                f.write("---\nname: shared\ndescription: first\n---\nBody A")

            second = os.path.join(tmpdir, "second")
            os.makedirs(second)
            with open(os.path.join(second, "shared.md"), "w") as f:
                f.write("---\nname: shared\ndescription: second\n---\nBody B")

            skills = discover_skills(first, second)
            assert len(skills) == 1
            assert skills[0].description == "second"
            assert skills[0].body == "Body B"
