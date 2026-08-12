"""Surgical skill-lens: extract context-relevant sections from expert skills.

The hybrid pattern: skills store full knowledge, this "lens" extracts only the
sections relevant to the current task. Reduces token waste by loading 3-5
targeted sections instead of 1000+ lines.

Usage::

    from general_ludd.ansible.skill_lens import lens

    prompt = lens("python-expert", "debug an asyncio deadlock in the event loop")
    # Returns sections 3 (asyncio), 7 (debugging), 12 (performance) — ~300 lines
    # instead of the full 1977-line skill.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast


class InvalidSkillError(ValueError):
    """Raised when a skill name is invalid or the skill does not exist."""


_SKILLS_CACHE: dict[str, str] = {}
_LENS_CACHE: dict[str, str] = {}


def _skills_dir() -> Path:
    root = Path(__file__).resolve().parent.parent.parent.parent
    return root / ".opencode" / "skills"


_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


def _skill_path(skill_name: str) -> Path:
    if not _SKILL_NAME_RE.match(skill_name):
        raise InvalidSkillError(
            f"Invalid skill name: {skill_name!r}. "
            f"Must match pattern: {_SKILL_NAME_RE.pattern}"
        )
    skills = _skills_dir()
    if not skills.is_dir():
        raise InvalidSkillError(
            f"Skills directory not found: {skills}"
        )
    path = skills / skill_name / "SKILL.md"
    if not path.is_file():
        raise InvalidSkillError(
            f"Skill {skill_name!r} does not exist at: {path}"
        )
    return path


def _read_skill_file(path: Path) -> str:
    return path.read_text()


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            return text[end + 3:].lstrip()
    return text


def _parse_sections(text: str) -> list[tuple[str, str]]:
    body = _strip_frontmatter(text)
    sections: list[tuple[str, str]] = []
    current_header: str | None = None
    current_lines: list[str] = []

    for line in body.split("\n"):
        if line.startswith("## ") and not line.startswith("### "):
            if current_header is not None:
                sections.append((current_header, "\n".join(current_lines)))
            current_header = line.strip()
            current_lines = []
        elif current_header is not None:
            current_lines.append(line)

    if current_header is not None:
        sections.append((current_header, "\n".join(current_lines)))

    return sections


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    result: set[str] = set()
    for w in words:
        result.add(w)
        for i in range(1, len(w)):
            result.add(w[:i])
            result.add(w[i:])
    for w in list(words):
        if "_" in w:
            parts = w.split("_")
            result.update(parts)
            result.add("".join(parts))
    return result


def _score_relevance(task_description: str, section_text: str) -> float:
    if not task_description or not section_text:
        return 0.0

    # Keep exact short words (for example ``go``) while excluding the one- and
    # two-character synthetic fragments produced by subword decomposition.
    # Those fragments made unrelated text overlap on letters such as ``a`` or
    # suffixes such as ``on``.
    task_words = set(re.findall(r"[a-z0-9_]+", task_description.lower()))
    section_words = set(re.findall(r"[a-z0-9_]+", section_text.lower()))
    task_tokens = task_words | {token for token in _tokenize(task_description) if len(token) >= 3}
    section_tokens = section_words | {token for token in _tokenize(section_text) if len(token) >= 3}

    if not task_tokens:
        return 0.0

    matches = task_tokens & section_tokens
    if not matches:
        return 0.0
    raw = len(matches) / len(task_tokens)

    section_lower = section_text.lower()
    dense_matches = sum(section_lower.count(token) for token in matches)
    density = min(dense_matches / max(len(task_tokens), 1), 2.0) / 2.0

    return 0.5 * raw + 0.5 * density


def _read_header_name(text: str) -> str:
    h = _strip_frontmatter(text)
    lines = h.split("\n")
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip()
    return ""


def lens_raw(
    skill_name: str,
    task_description: str,
    max_sections: int = 3,
) -> dict[str, Any]:
    path = _skill_path(skill_name)

    if skill_name not in _SKILLS_CACHE:
        _SKILLS_CACHE[skill_name] = _read_skill_file(path)

    full_text = _SKILLS_CACHE[skill_name]
    header = _read_header_name(full_text)
    sections = _parse_sections(full_text)

    if not sections:
        return {
            "skill_name": skill_name,
            "header": header,
            "sections": [],
            "task_description": task_description,
        }

    scored = [
        {
            "header": h,
            "body": b.strip(),
            "score": _score_relevance(task_description, b),
        }
        for h, b in sections
    ]

    if task_description:
        scored.sort(key=lambda x: cast(float, x["score"]), reverse=True)

    top = scored[:max_sections]

    return {
        "skill_name": skill_name,
        "header": header,
        "sections": top,
        "task_description": task_description,
    }


def lens(
    skill_name: str,
    task_description: str,
    max_sections: int = 3,
) -> str:
    cache_key = f"{skill_name}|{task_description}|{max_sections}"
    if cache_key in _LENS_CACHE:
        return _LENS_CACHE[cache_key]

    raw = lens_raw(skill_name, task_description, max_sections)

    lines: list[str] = []
    lines.append(f"# {raw['header']} (lens: {skill_name})")
    lines.append("")
    lines.append(f"_Context: {raw['task_description']}_")
    lines.append("")

    for _i, section in enumerate(raw["sections"], 1):
        score_str = f"(relevance: {section['score']:.2f})"
        lines.append(f"## {section['header'].lstrip('#').strip()} {score_str}")
        lines.append("")
        lines.append(section["body"])
        lines.append("")

    result = "\n".join(lines)
    _LENS_CACHE[cache_key] = result
    return result


def clear_cache() -> None:
    _SKILLS_CACHE.clear()
    _LENS_CACHE.clear()
