"""Remote skill fetching from GitHub repositories and raw URLs."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from general_ludd.skills.loader import parse_skill_md
from general_ludd.skills.skill import Skill

if TYPE_CHECKING:
    from general_ludd.skills.catalog import CatalogSkillEntry

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"


@dataclass
class GitHubSkillSource:
    owner: str
    repo: str
    branch: str = "main"
    subdir: str = ""

    @classmethod
    def from_url(cls, url: str) -> GitHubSkillSource:
        parts = url.replace("https://github.com/", "").split("/")
        owner = parts[0]
        repo = parts[1]
        branch = "main"
        subdir = ""
        if len(parts) > 2 and parts[2] == "tree":
            branch = parts[3] if len(parts) > 3 else "main"
            if len(parts) > 4:
                subdir = "/".join(parts[4:])
        return cls(owner=owner, repo=repo, branch=branch, subdir=subdir)

    def _api_url(self, path: str) -> str:
        return f"{GITHUB_API_BASE}/repos/{self.owner}/{self.repo}/contents/{path}?ref={self.branch}"

    def _raw_url(self, path: str) -> str:
        return f"{GITHUB_RAW_BASE}/{self.owner}/{self.repo}/{self.branch}/{path}"

    def list_skills(self) -> list[CatalogSkillEntry]:
        from general_ludd.skills.catalog import CatalogSkillEntry

        path = self.subdir.rstrip("/") if self.subdir else ""
        resp = httpx.get(self._api_url(path), timeout=15.0)
        if resp.status_code != 200:
            logger.warning("GitHub API returned %d for %s", resp.status_code, path)
            return []

        entries: list[CatalogSkillEntry] = []
        for item in resp.json():
            if item.get("type") != "dir":
                continue
            name = item["name"]
            item_path = item["path"]
            entries.append(CatalogSkillEntry(
                name=name,
                source="github",
                source_url=f"https://github.com/{self.owner}/{self.repo}/tree/{self.branch}/{item_path}",
                tags=["github"],
            ))
        return entries

    def download_skill(self, skill_path: str) -> Skill | None:
        skill_md_path = f"{skill_path.rstrip('/')}/SKILL.md"
        resp = httpx.get(self._raw_url(skill_md_path), timeout=15.0)
        if resp.status_code != 200:
            skill_md_path = f"{skill_path.rstrip('/')}.md"
            resp = httpx.get(self._raw_url(skill_md_path), timeout=15.0)
        if resp.status_code != 200:
            logger.warning("Failed to fetch skill from %s: %d", skill_md_path, resp.status_code)
            return None
        return parse_skill_md(resp.text, source_path=skill_md_path)


class RemoteSkillFetcher:
    def fetch(self, url: str) -> Skill | None:
        try:
            resp = httpx.get(url, timeout=15.0, follow_redirects=True)
        except httpx.HTTPError:
            logger.warning("Failed to fetch skill from %s", url)
            return None
        if resp.status_code != 200:
            return None
        return parse_skill_md(resp.text, source_path=url)

    def install(self, url: str, target_dir: str) -> Path | None:
        skill = self.fetch(url)
        if skill is None:
            return None
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        skill_file = target / f"{skill.name}.md"
        content = f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n\n{skill.body}\n"
        skill_file.write_text(content)
        logger.info("Installed skill %s to %s", skill.name, skill_file)
        return skill_file


def fetch_github_skill(repo: str, skill_path: str, branch: str = "main") -> Skill | None:
    parts = repo.split("/")
    if len(parts) < 2:
        return None
    owner, repo_name = parts[0], parts[1]
    src = GitHubSkillSource(owner=owner, repo=repo_name, branch=branch)
    return src.download_skill(skill_path)


def fetch_raw_url_skill(url: str) -> Skill | None:
    fetcher = RemoteSkillFetcher()
    return fetcher.fetch(url)
