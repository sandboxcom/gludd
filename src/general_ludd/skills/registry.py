from __future__ import annotations

from typing import Any

from general_ludd.skills.loader import discover_skills
from general_ludd.skills.skill import Skill


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._project_skills: dict[str, dict[str, Skill]] = {}

    def register(self, skill: Skill, *, project_id: str | None = None) -> None:
        if project_id:
            self._project_skills.setdefault(project_id, {})[skill.name] = skill
        else:
            self._skills[skill.name] = skill

    def get(self, name: str, *, project_id: str | None = None) -> Skill | None:
        if project_id and project_id in self._project_skills:
            found = self._project_skills[project_id].get(name)
            if found:
                return found
        return self._skills.get(name)

    def list_skills(self, tag: str | None = None, *, project_id: str | None = None) -> list[Skill]:
        skills = list(self._skills.values())
        if project_id and project_id in self._project_skills:
            skills.extend(self._project_skills[project_id].values())
        if tag is None:
            return skills
        return [s for s in skills if tag in s.tags]

    def match_trigger(self, text: str, *, project_id: str | None = None) -> list[Skill]:
        text_lower = text.lower()
        results: list[Skill] = []
        to_check = list(self._skills.values())
        if project_id and project_id in self._project_skills:
            to_check.extend(self._project_skills[project_id].values())
        for skill in to_check:
            for pattern in skill.trigger_patterns:
                if pattern.lower() in text_lower:
                    results.append(skill)
                    break
        return results

    def refresh(self, search_paths: list[str] | None = None, *, project_id: str | None = None) -> dict[str, Any]:
        if search_paths:
            discovered = discover_skills(*search_paths)
            for skill in discovered:
                if project_id:
                    self._project_skills.setdefault(project_id, {})[skill.name] = skill
                else:
                    self._skills[skill.name] = skill
        return {"skills": list(self._skills.keys())}
