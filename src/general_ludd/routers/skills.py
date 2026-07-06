from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from general_ludd.security.sanitize import is_path_within
from general_ludd.skills.catalog import SkillCatalog
from general_ludd.skills.fetcher import (
    GitHubSkillSource,
    RemoteSkillFetcher,
    _safe_skill_filename,
    build_skill_frontmatter,
)


class SkillCatalogSearchRequest(BaseModel):
    query: str = ""
    tags: list[str] | None = None
    category: str | None = None
    limit: int = 20


class SkillCatalogInstallRequest(BaseModel):
    name: str = ""


class SkillFetchRequest(BaseModel):
    url: str = ""


class SkillFetchGithubRequest(BaseModel):
    repo: str = ""
    path: str = ""
    branch: str = "main"


def _get_catalog(app: FastAPI) -> SkillCatalog:
    catalog = getattr(app.state, "_skill_catalog", None)
    if catalog is None:
        catalog = SkillCatalog()
        app.state._skill_catalog = catalog
    return catalog


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.post("/admin/skills/catalog/search")
    async def admin_skills_catalog_search(
        req: SkillCatalogSearchRequest,
    ) -> dict[str, object]:
        catalog = _get_catalog(app)
        results = catalog.search(
            query=req.query,
            tags=req.tags,
            category=req.category,
            limit=req.limit,
        )
        return {
            "results": [
                {
                    "name": r.name,
                    "description": r.description,
                    "source": r.source,
                    "tags": r.tags,
                    "category": r.category,
                }
                for r in results
            ]
        }

    @app.get("/admin/skills/catalog")
    async def admin_skills_catalog() -> dict[str, object]:
        catalog = _get_catalog(app)
        results = catalog.search(limit=100)
        return {
            "skills": [
                {
                    "name": r.name,
                    "description": r.description,
                    "source": r.source,
                    "tags": r.tags,
                    "category": r.category,
                }
                for r in results
            ]
        }

    @app.post("/admin/skills/catalog/install")
    async def admin_skills_catalog_install(
        req: SkillCatalogInstallRequest,
    ) -> dict[str, object]:
        catalog = _get_catalog(app)
        name = req.name
        config_dir = getattr(app.state, "_config_dir", None) or "/etc/general-ludd"
        path = catalog.install_skill(name, config_dir)
        if path is None:
            raise HTTPException(status_code=404, detail=f"Skill {name} not found")
        return {"installed": str(path), "name": name}

    @app.post("/admin/skills/fetch")
    async def admin_skills_fetch(req: SkillFetchRequest) -> dict[str, object]:
        url = req.url
        if not url:
            raise HTTPException(status_code=422, detail="url required")
        # SSRF guard: reject non-https or internal-host URLs before any network
        # call, returning 422 so the caller gets a meaningful validation error
        # rather than a 404 from the fetcher silently refusing the URL.
        from general_ludd.security.sanitize import is_safe_fetch_url

        if not is_safe_fetch_url(url):
            raise HTTPException(
                status_code=422,
                detail=f"URL rejected by SSRF guard (must be https and not a local/internal host): {url}",
            )
        config_dir = getattr(app.state, "_config_dir", None) or "/etc/general-ludd"
        target = os.path.join(config_dir, "skills")
        fetcher = RemoteSkillFetcher()
        # RemoteSkillFetcher.install performs a blocking sync httpx.get (15s
        # timeout) plus disk write; offload it so the async handler does not
        # freeze the event loop during the network fetch.
        path = await asyncio.to_thread(fetcher.install, url, target)
        if path is None:
            raise HTTPException(status_code=404, detail=f"Failed to fetch skill from {url}")
        return {"installed": str(path), "url": url}

    @app.post("/admin/skills/fetch-github")
    async def admin_skills_fetch_github(
        req: SkillFetchGithubRequest,
    ) -> dict[str, object]:
        repo = req.repo
        skill_path = req.path
        branch = req.branch
        if not repo or not skill_path:
            raise HTTPException(status_code=422, detail="repo and path required")
        try:
            src = GitHubSkillSource.from_url(f"https://github.com/{repo}")
        except ValueError as exc:
            # repo without an 'owner/repo' slash -> clean 422, not an
            # unhandled IndexError 500.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        src.branch = branch
        # download_skill performs blocking sync httpx.get calls (15s timeout
        # each); offload it so the async handler does not freeze the event loop
        # during the network fetch.
        skill = await asyncio.to_thread(src.download_skill, skill_path)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"Failed to fetch skill from {repo}/{skill_path}")
        config_dir = getattr(app.state, "_config_dir", None) or "/etc/general-ludd"
        target = os.path.join(config_dir, "skills")
        os.makedirs(target, exist_ok=True)
        # Sanitize the attacker-controlled skill name into a single safe path
        # segment, then confirm the resolved file stays inside the skills dir.
        stem = _safe_skill_filename(skill.name)
        if stem is None or not is_path_within(f"{stem}.md", target):
            raise HTTPException(status_code=422, detail=f"Unsafe skill name: {skill.name!r}")
        skill_file = os.path.join(target, f"{stem}.md")
        content = build_skill_frontmatter(skill)

        # AB-2: write the skill file off the event loop so the async handler
        # does not block on disk I/O.
        def _write_skill() -> None:
            with open(skill_file, "w") as f:
                f.write(content)

        await asyncio.to_thread(_write_skill)
        return {"installed": skill_file, "name": skill.name, "source": f"github:{repo}"}
