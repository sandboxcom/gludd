from __future__ import annotations

from general_ludd.skills.loader import discover_skills, parse_skill_md
from general_ludd.skills.skill import Skill


def test_parse_skill_md_basic():
    content = "---\nname: test-skill\ndescription: A test skill\n---\n\n# Body content"
    skill = parse_skill_md(content)
    assert isinstance(skill, Skill)
    assert skill.name == "test-skill"
    assert skill.description == "A test skill"


def test_discover_skills_empty_dir_returns_empty(tmp_path):
    skills = discover_skills(str(tmp_path))
    assert skills == []
