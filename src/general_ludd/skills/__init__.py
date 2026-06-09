"""Skills — catalog, registry, loader, fetcher, and skill model."""

from __future__ import annotations

__all__ = (
    "CatalogSkillEntry",
    "GitHubSkillSource",
    "RemoteSkillFetcher",
    "Skill",
    "SkillCatalog",
    "SkillRegistry",
    "discover_skills",
    "fetch_github_skill",
    "fetch_raw_url_skill",
    "parse_skill_md",
)

from general_ludd.skills.catalog import CatalogSkillEntry, SkillCatalog
from general_ludd.skills.fetcher import (
    GitHubSkillSource,
    RemoteSkillFetcher,
    fetch_github_skill,
    fetch_raw_url_skill,
)
from general_ludd.skills.loader import discover_skills, parse_skill_md
from general_ludd.skills.registry import SkillRegistry
from general_ludd.skills.skill import Skill
