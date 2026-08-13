"""Skill-lens context injection for agent system prompts.

Integrates the surgical skill-lens into the agent dispatch pipeline so agents
receive context-relevant skill sections instead of full skill files. Reduces
token waste by loading 2-3 targeted sections per skill instead of 1000+ lines.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from general_ludd.ansible.skill_lens import InvalidSkillError, lens, lens_raw

logger = logging.getLogger(__name__)

DEFAULT_MAX_SECTIONS = 3


@dataclass
class SkillContext:
    skills_used: list[str]
    context_text: str
    token_savings: int


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


# Mapping from task-description keywords to skill names. Each entry is
# (keyword, skill_name) where keyword is matched case-insensitively in the
# task description. Keywords with spaces match as phrases (word boundary).
_KEYWORD_SKILL_MAP: list[tuple[str, str]] = [
    (r"\bpython\b", "python-expert"),
    (r"\basyncio\b", "python-expert"),
    (r"\bcoroutine\b", "python-expert"),
    (r"\bpytest\b", "python-expert"),
    (r"\bpydantic\b", "python-expert"),
    (r"\bflask\b", "python-expert"),
    (r"\bfastapi\b", "python-expert"),
    (r"\bdjango\b", "python-expert"),
    (r"\.py\b", "python-expert"),
    (r"\bgolang\b", "go-expert"),
    (r"\bgoroutine\b", "go-expert"),
    (r"\bjava\b", "java-expert"),
    (r"\bjvm\b", "java-expert"),
    (r"\bmaven\b", "java-expert"),
    (r"\bgradle\b", "java-expert"),
    (r"\bspring\b", "java-expert"),
    (r"\btests?\b", "test-quality"),
    (r"\btdd\b", "test-quality"),
    (r"\bassertion\b", "test-quality"),
    (r"\bisolation\b", "test-quality"),
    (r"\bdeterminism\b", "test-quality"),
    (r"\btype annotations?\b", "type-safety"),
    (r"\btype hint\b", "type-safety"),
    (r"\bmypy\b", "type-safety"),
    (r"\btyping\b", "type-safety"),
    (r"\btype safety\b", "type-safety"),
    (r"\bguardrail\b", "guardrail-pattern"),
    (r"\benforcement\b", "guardrail-pattern"),
    (r"\bhook\b", "guardrail-pattern"),
    (r"\bpresentation\b", "revealjs-presentation"),
    (r"\bslide\b", "revealjs-presentation"),
    (r"\bdeck\b", "revealjs-presentation"),
    (r"\breveal\b", "revealjs-presentation"),
    (r"\bbackground test\b", "background-test-runner"),
    (r"\blong.?running\b", "background-test-runner"),
    (r"\bbootstrap\b", "enforce-bootstrap"),
    (r"\benforcement bypass\b", "enforce-bootstrap"),
    (r"\bculinary\b", "culinary-expert"),
    (r"\bcooking\b", "culinary-expert"),
    (r"\brecipe\b", "culinary-expert"),
    (r"\belectronics\b", "electronics-expert"),
    (r"\bcircuit\b", "electronics-expert"),
    (r"\bopencode\b", "opencode-customize"),
    (r"\bplugin\b", "opencode-customize"),
]


def _validate_skill_exists(skill_name: str) -> bool:
    skills_dir = Path(__file__).resolve().parent.parent.parent.parent / ".opencode" / "skills"
    path = skills_dir / skill_name / "SKILL.md"
    return path.is_file()


class SkillContextProvider:
    def __init__(
        self,
        skill_names: list[str] | None = None,
        max_sections: int = DEFAULT_MAX_SECTIONS,
    ) -> None:
        self._skill_names = skill_names
        self._max_sections = max_sections

    def identify_skills(self, task_description: str) -> list[str]:
        if not task_description:
            return []

        seen: set[str] = set()
        task_lower = task_description.lower()
        for pattern, skill_name in _KEYWORD_SKILL_MAP:
            if skill_name in seen:
                continue
            if re.search(pattern, task_lower):
                if self._skill_names is not None and skill_name not in self._skill_names:
                    continue
                if _validate_skill_exists(skill_name):
                    seen.add(skill_name)

        return list(seen)

    def provide(self, task_description: str) -> SkillContext:
        skills = self.identify_skills(task_description)
        if not skills:
            return SkillContext(
                skills_used=[],
                context_text="",
                token_savings=0,
            )

        sections: list[str] = []
        total_savings = 0

        for skill_name in skills:
            try:
                raw = lens_raw(skill_name, task_description, self._max_sections)
                lensed_text = lens(skill_name, task_description, self._max_sections)

                full_text = _read_full_skill_text(skill_name)
                full_tokens = _estimate_tokens(full_text)
                lensed_tokens = _estimate_tokens(lensed_text)
                savings = max(0, full_tokens - lensed_tokens)
                total_savings += savings

                lensed_lines = lensed_text.split("\n")
                header_line = (
                    f"## Skill Context: {skill_name} ({len(raw['sections'])} sections, "
                    f"~{savings} tokens saved vs full skill)"
                )
                sections.append(header_line)
                sections.append("")
                sections.extend(lensed_lines)
                sections.append("")

            except InvalidSkillError:
                logger.warning("Skill %r not available for lensing", skill_name)
                continue

        context_text = "\n".join(sections) if sections else ""

        return SkillContext(
            skills_used=skills,
            context_text=context_text,
            token_savings=total_savings,
        )


def _read_full_skill_text(skill_name: str) -> str:
    skills_dir = Path(__file__).resolve().parent.parent.parent.parent / ".opencode" / "skills"
    path = skills_dir / skill_name / "SKILL.md"
    return path.read_text()
