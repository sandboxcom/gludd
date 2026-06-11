from __future__ import annotations

import re
from pathlib import Path

from general_ludd.skills.skill import Skill

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_skill_md(raw: str, source_path: str = "") -> Skill:
    name = Path(source_path).stem
    body = raw
    description = ""
    trigger_patterns: list[str] = []
    tags: list[str] = []
    tools: list[str] = []
    category = ""
    model_profile: str | None = None
    if "---" in raw[:10]:
        match = FRONTMATTER_RE.match(raw)
        if match:
            import yaml
            try:
                frontmatter = yaml.safe_load(match.group(1)) or {}
            except yaml.YAMLError:
                frontmatter = {}
            name = frontmatter.get("name", name)
            description = frontmatter.get("description", "")
            trigger_patterns = frontmatter.get("trigger_patterns", [])
            if isinstance(trigger_patterns, str):
                trigger_patterns = [trigger_patterns]
            tags = frontmatter.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]
            category = frontmatter.get("category", "")
            tools = frontmatter.get("tools", [])
            if isinstance(tools, str):
                tools = [t.strip() for t in tools.split(",")]
            model_profile = frontmatter.get("model_profile") or None
            body = raw[match.end():]
    return Skill(
        name=name,
        description=description,
        body=body,
        tools=tools,
        trigger_patterns=trigger_patterns,
        tags=tags,
        category=category,
        model_profile=model_profile,
        source_path=source_path,
    )


def discover_skills(*search_paths: str) -> list[Skill]:
    merged: dict[str, Skill] = {}
    for search_path in search_paths:
        directory = Path(search_path)
        if not directory.is_dir():
            continue
        for md_file in sorted(directory.glob("**/*.md")):
            raw = md_file.read_text()
            skill = parse_skill_md(raw, source_path=str(md_file))
            merged[skill.name] = skill
    return list(merged.values())
