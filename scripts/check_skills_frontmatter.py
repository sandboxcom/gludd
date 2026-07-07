#!/usr/bin/env python3
"""Validate YAML frontmatter in .opencode/skills/*/SKILL.md files."""

import os
import sys

import yaml


SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", ".opencode", "skills")
SKILLS_NESTED_SENTINEL = "skills"


def find_skill_files(root: str) -> list[str]:
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        parts = rel.split(os.sep)
        if parts and parts[0] == SKILLS_NESTED_SENTINEL and len(parts) > 0:
            dirnames.clear()
            continue
        if "SKILL.md" in filenames:
            files.append(os.path.join(dirpath, "SKILL.md"))
    return sorted(files)


def parse_frontmatter(path: str) -> tuple[dict | None, str | None]:
    with open(path) as fh:
        content = fh.read()
    if not content.startswith("---"):
        return None, "missing frontmatter delimiter (file does not start with ---)"
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None, "unclosed frontmatter (missing closing ---)"
    raw = parts[1].strip()
    if not raw:
        return None, "empty frontmatter"
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return None, f"YAML parse error: {exc}"
    if data is None:
        return None, "frontmatter parsed to None (empty or comment-only)"
    if not isinstance(data, dict):
        return None, f"frontmatter is not a mapping (got {type(data).__name__})"
    return data, None


def validate_skill(path: str) -> list[str]:
    errors: list[str] = []
    dirname = os.path.basename(os.path.dirname(path))
    data, err = parse_frontmatter(path)
    if err:
        errors.append(f"{path}: {err}")
        return errors
    assert data is not None
    for key in ("name", "description"):
        if key not in data:
            errors.append(f"{path}: missing required key '{key}'")
        elif not isinstance(data[key], str):
            errors.append(
                f"{path}: '{key}' must be a string (got {type(data[key]).__name__})"
            )
    if "name" in data and isinstance(data["name"], str):
        if data["name"] != dirname:
            errors.append(
                f"{path}: name '{data['name']}' does not match directory '{dirname}'"
            )
    return errors


def main() -> int:
    if not os.path.isdir(SKILLS_DIR):
        print(f"SKILLS_DIR not found: {SKILLS_DIR}", file=sys.stderr)
        return 0
    skill_files = find_skill_files(SKILLS_DIR)
    if not skill_files:
        print("No SKILL.md files found", file=sys.stderr)
        return 0
    all_errors: list[str] = []
    for path in skill_files:
        all_errors.extend(validate_skill(path))
    for error in all_errors:
        print(error, file=sys.stderr)
    if all_errors:
        print(f"\n{len(all_errors)} error(s) found", file=sys.stderr)
        return 1
    print(f"OK: {len(skill_files)} SKILL.md file(s) validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
