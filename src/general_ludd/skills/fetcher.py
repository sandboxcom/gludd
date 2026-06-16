"""Remote skill fetching from GitHub repositories and raw URLs."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from general_ludd.security.auth import is_path_within, is_safe_fetch_url
from general_ludd.security.sanitize import sanitize_path
from general_ludd.skills.catalog import CatalogSkillEntry
from general_ludd.skills.loader import parse_skill_md
from general_ludd.skills.skill import Skill

logger = logging.getLogger(__name__)


def _safe_skill_filename(name: str) -> str | None:
    """Sanitize an attacker-controlled skill name into a single-segment file
    stem. Rejects path separators, traversal, and absolute paths so a skill
    named ``../../etc/cron.d/evil`` can never escape the install dir."""
    if not name:
        return None
    cleaned = sanitize_path(name.strip())
    if cleaned is None:
        return None
    # Must remain a single path segment — no nested dirs from the skill name.
    if "/" in cleaned or "\\" in cleaned or cleaned in {"", ".", ".."}:
        return None
    return cleaned

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
        # SSRF guard: only https + non-private/loopback/metadata hosts, checked
        # against the LITERAL host (no DNS resolution -> no blocking). Redirects
        # are disabled so a 30x cannot bounce us onto an internal target after
        # the URL passed the gate.
        if not is_safe_fetch_url(url):
            logger.warning("Refusing unsafe skill URL: %s", url)
            return None
        try:
            resp = httpx.get(url, timeout=15.0, follow_redirects=False)
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
        stem = _safe_skill_filename(skill.name)
        if stem is None:
            logger.warning("Refusing skill with unsafe name: %r", skill.name)
            return None
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        skill_file = target / f"{stem}.md"
        # Defense in depth: confirm the resolved file stays inside target_dir.
        if not is_path_within(str(target), f"{stem}.md"):
            logger.warning("Refusing skill path escaping %s: %r", target_dir, skill.name)
            return None
        content = f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n\n{skill.body}\n"
        skill_file.write_text(content)
        logger.info("Installed skill %s to %s", stem, skill_file)
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
