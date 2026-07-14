"""Structural tests for skills/registry.py — SkillRegistry."""
from __future__ import annotations

from general_ludd.skills.registry import SkillRegistry


class TestModuleImport:
    def test_import(self):
        assert SkillRegistry is not None


class TestSkillRegistryInit:
    def test_default_construction(self):
        registry = SkillRegistry()
        assert isinstance(registry._skills, dict)
        assert isinstance(registry._project_skills, dict)
        assert len(registry._skills) == 0
        assert len(registry._project_skills) == 0

    def test_embedder_initialized(self):
        registry = SkillRegistry()
        from general_ludd.skills.embeddings import SkillEmbedder
        assert isinstance(registry._embedder, SkillEmbedder)


class TestSkillRegistryRegister:
    def test_register_non_project(self):
        from general_ludd.skills.skill import Skill
        registry = SkillRegistry()
        skill = Skill(name="test", description="desc", tags=[], trigger_patterns=[])
        registry.register(skill)
        assert "test" in registry._skills
        assert registry._skills["test"] is skill

    def test_register_project_scoped(self):
        from general_ludd.skills.skill import Skill
        registry = SkillRegistry()
        skill = Skill(name="proj-skill", description="d", tags=[], trigger_patterns=[])
        registry.register(skill, project_id="proj-1")
        assert "proj-1" in registry._project_skills
        assert "proj-skill" in registry._project_skills["proj-1"]

    def test_register_overwrites_existing(self):
        from general_ludd.skills.skill import Skill
        registry = SkillRegistry()
        s1 = Skill(name="dup", description="first", tags=[], trigger_patterns=[])
        s2 = Skill(name="dup", description="second", tags=[], trigger_patterns=[])
        registry.register(s1)
        registry.register(s2)
        assert registry._skills["dup"] is s2
        assert registry._skills["dup"].description == "second"


class TestSkillRegistryGet:
    def test_get_existing(self):
        from general_ludd.skills.skill import Skill
        registry = SkillRegistry()
        skill = Skill(name="alpha", description="d", tags=[], trigger_patterns=[])
        registry.register(skill)
        assert registry.get("alpha") is skill

    def test_get_missing_returns_none(self):
        registry = SkillRegistry()
        assert registry.get("nonexistent") is None

    def test_get_project_scoped_falls_back_to_global(self):
        from general_ludd.skills.skill import Skill
        registry = SkillRegistry()
        global_skill = Skill(name="shared", description="d", tags=[], trigger_patterns=[])
        registry.register(global_skill)
        assert registry.get("shared", project_id="proj-1") is global_skill

    def test_get_project_takes_priority(self):
        from general_ludd.skills.skill import Skill
        registry = SkillRegistry()
        global_s = Skill(name="collide", description="global", tags=[], trigger_patterns=[])
        proj_s = Skill(name="collide", description="project", tags=[], trigger_patterns=[])
        registry.register(global_s)
        registry.register(proj_s, project_id="proj-1")
        found = registry.get("collide", project_id="proj-1")
        assert found is proj_s
        assert found.description == "project"


class TestSkillRegistryListSkills:
    def test_list_skills_empty(self):
        registry = SkillRegistry()
        assert registry.list_skills() == []

    def test_list_skills_returns_registered(self):
        from general_ludd.skills.skill import Skill
        registry = SkillRegistry()
        s1 = Skill(name="a", description="d", tags=[], trigger_patterns=[])
        s2 = Skill(name="b", description="d", tags=[], trigger_patterns=[])
        registry.register(s1)
        registry.register(s2)
        names = [s.name for s in registry.list_skills()]
        assert sorted(names) == ["a", "b"]

    def test_list_skills_includes_project_skills(self):
        from general_ludd.skills.skill import Skill
        registry = SkillRegistry()
        registry.register(Skill(name="g", description="d", tags=[], trigger_patterns=[]))
        registry.register(Skill(name="p", description="d", tags=[], trigger_patterns=[]), project_id="proj-1")
        names = [s.name for s in registry.list_skills(project_id="proj-1")]
        assert sorted(names) == ["g", "p"]

    def test_list_skills_tag_filter(self):
        from general_ludd.skills.skill import Skill
        registry = SkillRegistry()
        registry.register(Skill(name="py", description="d", tags=["python"], trigger_patterns=[]))
        registry.register(Skill(name="ts", description="d", tags=["typescript"], trigger_patterns=[]))
        py_skills = registry.list_skills(tag="python")
        assert len(py_skills) == 1
        assert py_skills[0].name == "py"

    def test_list_skills_tag_no_match(self):
        from general_ludd.skills.skill import Skill
        registry = SkillRegistry()
        registry.register(Skill(name="py", description="d", tags=["python"], trigger_patterns=[]))
        assert registry.list_skills(tag="rust") == []


class TestSkillRegistryMatchTrigger:
    def test_match_substring(self):
        from general_ludd.skills.skill import Skill
        registry = SkillRegistry()
        skill = Skill(
            name="greet",
            description="Greeting skill",
            tags=[],
            trigger_patterns=["hello", "hi there"],
        )
        registry.register(skill)
        matches = registry.match_trigger("say hello to the world")
        assert len(matches) == 1
        assert matches[0].name == "greet"

    def test_match_case_insensitive(self):
        from general_ludd.skills.skill import Skill
        registry = SkillRegistry()
        skill = Skill(name="upper", description="d", tags=[], trigger_patterns=["HELLO"])
        registry.register(skill)
        matches = registry.match_trigger("hello")
        assert len(matches) == 1

    def test_match_no_matches_returns_empty(self):
        registry = SkillRegistry()
        assert registry.match_trigger("nothing matches") == []

    def test_match_project_scoped(self):
        from general_ludd.skills.skill import Skill
        registry = SkillRegistry()
        registry.register(
            Skill(name="global-only", description="d", tags=[], trigger_patterns=["deploy"]),
        )
        registry.register(
            Skill(name="proj-only", description="d", tags=[], trigger_patterns=["deploy"]),
            project_id="proj-1",
        )
        matches = registry.match_trigger("deploy", project_id="proj-1")
        names = [s.name for s in matches]
        assert sorted(names) == ["global-only", "proj-only"]

    def test_match_default_similarity_threshold(self):
        registry = SkillRegistry()
        import inspect
        sig = inspect.signature(registry.match_trigger)
        assert sig.parameters["similarity_threshold"].default == 0.7


class TestSkillRegistryRefresh:
    def test_refresh_no_paths_returns_skill_keys(self):
        registry = SkillRegistry()
        result = registry.refresh()
        assert isinstance(result, dict)
        assert "skills" in result
        assert isinstance(result["skills"], list)

    def test_refresh_with_project_id(self):
        registry = SkillRegistry()
        result = registry.refresh(search_paths=[], project_id="proj-1")
        assert isinstance(result, dict)
        assert "skills" in result
