"""Remote skill fetching from GitHub repositories and raw URLs."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from general_ludd.security.auth import is_join_within, is_safe_fetch_url
from general_ludd.security.sanitize import sanitize_path
from general_ludd.skills.catalog import CatalogSkillEntry
from general_ludd.skills.loader import parse_skill_md
from general_ludd.skills.skill import Skill

logger = logging.getLogger(__name__)


def build_skill_frontmatter(skill: Skill) -> str:
    """Build a SKILL.md frontmatter block from a fetched skill SAFELY.

    Attacker-controlled ``skill.name`` / ``skill.description`` are emitted via
    ``yaml.safe_dump`` of a dict — NOT raw f-string interpolation. Raw
    interpolation (the old ``f"---\\nname: {name}\\n..."``) let a malicious
    remote SKILL.md inject extra YAML keys (e.g. a description of
    ``"x\\nmodel_profile: attacker\\ntools: [shell]\\ntrigger_patterns: ['.*']"``)
    that would silently override the agent's model/tool/trigger config on the
    next parse. ``safe_dump`` quotes/escapes any value containing YAML
    structure, so the payload round-trips back as a single opaque string and
    cannot inject keys. Single source of truth for both the fetcher and the
    router so the fix can't drift."""
    import yaml

    front = yaml.safe_dump(
        {"name": skill.name, "description": skill.description},
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    return f"---\n{front}---\n\n{skill.body}\n"


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

# Shared response-size cap (M-12): every skill fetch site — GitHub API listing,
# GitHub raw content, and arbitrary raw-URL fetches — must reject an oversized
# response the same way, instead of each site growing its own ad-hoc check.
_MAX_SKILL_RESPONSE_BYTES = 1_000_000  # 1 MB


def _capped_get(url: str, *, max_bytes: int = _MAX_SKILL_RESPONSE_BYTES, **kwargs: object) -> httpx.Response | None:
    """GET ``url``, rejecting the response if it (or its declared size) exceeds ``max_bytes``.

    Checks ``Content-Length`` up front (fast path, avoids reading an obviously
    oversized body) and re-checks the actual body length afterward — a
    malicious/misconfigured server can omit or lie about ``Content-Length``.
    Returns ``None`` on transport failure or an oversized response; callers
    treat ``None`` the same as any other failed fetch.
    """
    kwargs.setdefault("timeout", 15.0)
    try:
        resp = httpx.get(url, **kwargs)  # type: ignore[arg-type]
    except httpx.HTTPError:
        logger.warning("Request failed for %s", url)
        return None
    if resp.status_code == 200:
        clen = resp.headers.get("content-length")
        if clen and clen.isdigit() and int(clen) > max_bytes:
            logger.warning("Response too large (%s bytes) from %s", clen, url)
            return None
        if len(resp.content) > max_bytes:
            logger.warning("Response body exceeded %d bytes from %s", max_bytes, url)
            return None
    return resp


@dataclass
class GitHubSkillSource:
    owner: str
    repo: str
    branch: str = "main"
    subdir: str = ""

    @classmethod
    def from_url(cls, url: str) -> GitHubSkillSource:
        # A bare "owner" (no "/repo") used to do parts[1] and raise IndexError,
        # which surfaced as an unhandled 500. Require owner AND repo and raise a
        # clean ValueError (callers map this to a 4xx) instead.
        parts = [p for p in url.replace("https://github.com/", "").split("/") if p]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"Invalid GitHub skill URL/repo: {url!r} (expected 'owner/repo')"
            )
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
        # URL-encode every component so a branch/subdir/owner/repo carrying a
        # '?' or '#' (extractable from a crafted GitHub URL) cannot inject a
        # query/fragment and redirect the request. `path` keeps '/' so nested
        # content paths survive; the rest are fully escaped.
        return (
            f"{GITHUB_API_BASE}/repos/{quote(self.owner, safe='')}/"
            f"{quote(self.repo, safe='')}/contents/{quote(path, safe='/')}"
            f"?ref={quote(self.branch, safe='')}"
        )

    def _raw_url(self, path: str) -> str:
        return (
            f"{GITHUB_RAW_BASE}/{quote(self.owner, safe='')}/"
            f"{quote(self.repo, safe='')}/{quote(self.branch, safe='')}/"
            f"{quote(path, safe='/')}"
        )

    def list_skills(self) -> list[CatalogSkillEntry]:
        path = self.subdir.rstrip("/") if self.subdir else ""
        resp = _capped_get(self._api_url(path))
        if resp is None:
            return []
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
        resp = _capped_get(self._raw_url(skill_md_path))
        if resp is None or resp.status_code != 200:
            skill_md_path = f"{skill_path.rstrip('/')}.md"
            resp = _capped_get(self._raw_url(skill_md_path))
        if resp is None or resp.status_code != 200:
            logger.warning(
                "Failed to fetch skill from %s: %s",
                skill_md_path,
                getattr(resp, "status_code", "request failed"),
            )
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
        resp = _capped_get(url, follow_redirects=False)
        if resp is None or resp.status_code != 200:
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
        if not is_join_within(str(target), f"{stem}.md"):
            logger.warning("Refusing skill path escaping %s: %r", target_dir, skill.name)
            return None
        content = build_skill_frontmatter(skill)
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
