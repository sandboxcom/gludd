from __future__ import annotations

from typing import Any

from general_ludd.skills.loader import discover_skills
from general_ludd.skills.skill import Skill


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_skills(self, tag: str | None = None) -> list[Skill]:
        skills = list(self._skills.values())
        if tag is None:
            return skills
        return [s for s in skills if tag in s.tags]

    def match_trigger(self, text: str) -> list[Skill]:
        text_lower = text.lower()
        results: list[Skill] = []
        for skill in self._skills.values():
            for pattern in skill.trigger_patterns:
                if pattern.lower() in text_lower:
                    results.append(skill)
                    break
        return results

    def refresh(self, search_paths: list[str] | None = None) -> dict[str, Any]:
        if search_paths:
            discovered = discover_skills(*search_paths)
            for skill in discovered:
                self._skills[skill.name] = skill
        return {"skills": list(self._skills.keys())}
