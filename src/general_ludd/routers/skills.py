from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException

from general_ludd.security.auth import is_path_within
from general_ludd.skills.catalog import SkillCatalog
from general_ludd.skills.fetcher import (
    GitHubSkillSource,
    RemoteSkillFetcher,
    _safe_skill_filename,
    build_skill_frontmatter,
)


def _get_catalog(app: FastAPI) -> Any:
    catalog = getattr(app.state, "_skill_catalog", None)
    if catalog is None:
        catalog = SkillCatalog()
        app.state._skill_catalog = catalog
    return catalog


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/skills/catalog/search")
    async def admin_skills_catalog_search(req: dict[str, Any]) -> dict[str, Any]:
        catalog = _get_catalog(app)
        results = catalog.search(
            query=req.get("query", ""),
            tags=req.get("tags"),
            category=req.get("category"),
            limit=req.get("limit", 20),
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
    async def admin_skills_catalog() -> dict[str, Any]:
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
    async def admin_skills_catalog_install(req: dict[str, Any]) -> dict[str, Any]:
        catalog = _get_catalog(app)
        name = req.get("name", "")
        config_dir = getattr(app.state, "_config_dir", None) or "/etc/general-ludd"
        path = catalog.install_skill(name, config_dir)
        if path is None:
            raise HTTPException(status_code=404, detail=f"Skill {name} not found")
        return {"installed": str(path), "name": name}

    @app.post("/admin/skills/fetch")
    async def admin_skills_fetch(req: dict[str, Any]) -> dict[str, Any]:
        url = req.get("url", "")
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
        path = fetcher.install(url, target)
        if path is None:
            raise HTTPException(status_code=404, detail=f"Failed to fetch skill from {url}")
        return {"installed": str(path), "url": url}

    @app.post("/admin/skills/fetch-github")
    async def admin_skills_fetch_github(req: dict[str, Any]) -> dict[str, Any]:
        repo = req.get("repo", "")
        skill_path = req.get("path", "")
        branch = req.get("branch", "main")
        if not repo or not skill_path:
            raise HTTPException(status_code=422, detail="repo and path required")
        try:
            src = GitHubSkillSource.from_url(f"https://github.com/{repo}")
        except ValueError as exc:
            # repo without an 'owner/repo' slash -> clean 422, not an
            # unhandled IndexError 500.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        src.branch = branch
        skill = src.download_skill(skill_path)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"Failed to fetch skill from {repo}/{skill_path}")
        config_dir = getattr(app.state, "_config_dir", None) or "/etc/general-ludd"
        target = os.path.join(config_dir, "skills")
        os.makedirs(target, exist_ok=True)
        # Sanitize the attacker-controlled skill name into a single safe path
        # segment, then confirm the resolved file stays inside the skills dir.
        stem = _safe_skill_filename(skill.name)
        if stem is None or not is_path_within(target, f"{stem}.md"):
            raise HTTPException(status_code=422, detail=f"Unsafe skill name: {skill.name!r}")
        skill_file = os.path.join(target, f"{stem}.md")
        content = build_skill_frontmatter(skill)
        with open(skill_file, "w") as f:
            f.write(content)
        return {"installed": skill_file, "name": skill.name, "source": f"github:{repo}"}
