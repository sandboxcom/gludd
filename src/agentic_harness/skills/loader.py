from __future__ import annotations

from pathlib import Path

import yaml

from agentic_harness.skills.skill import Skill


def parse_skill_md(content: str, source_path: str | None = None) -> Skill:
    text = content.strip()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter_str = parts[1].strip()
            body = parts[2].strip()
            data = yaml.safe_load(frontmatter_str) or {}
            name = data.get("name", "")
            if not name and source_path:
                name = Path(source_path).stem
            return Skill(
                name=name,
                description=data.get("description", ""),
                model_profile=data.get("model_profile"),
                tools=data.get("tools", []),
                trigger_patterns=data.get("trigger_patterns", []),
                tags=data.get("tags", []),
                body=body,
                source_path=source_path,
            )

    fallback_name = Path(source_path).stem if source_path else "unknown"
    return Skill(
        name=fallback_name,
        body=content,
        source_path=source_path,
    )


def discover_skills(*search_paths: str) -> list[Skill]:
    merged: dict[str, Skill] = {}
    for search_path in search_paths:
        directory = Path(search_path)
        if not directory.is_dir():
            continue
        for md_file in sorted(directory.glob("*.md")):
            raw = md_file.read_text()
            skill = parse_skill_md(raw, source_path=str(md_file))
            merged[skill.name] = skill
    return list(merged.values())
