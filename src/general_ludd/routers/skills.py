"""Skill discovery, installation, fetching, and rendering HTTP routes."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, Field, StrictStr, field_validator, model_validator

from general_ludd.routers._runtime import StrictRuntimeRequest
from general_ludd.security.capability_guard import RequireCapability
from general_ludd.security.sanitize import is_path_within
from general_ludd.skills.catalog import SkillCatalog
from general_ludd.skills.fetcher import (
    GitHubSkillSource,
    RemoteSkillFetcher,
    _safe_skill_filename,
    build_skill_frontmatter,
)
from general_ludd.skills.loader import discover_skills
from general_ludd.skills.renderer import SkillRenderError, render_skill


class SkillCatalogSearchRequest(BaseModel):
    """Filter and bound a skill catalog search."""

    query: str = ""
    tags: list[str] | None = None
    category: str | None = None
    limit: int = 20


class SkillCatalogInstallRequest(BaseModel):
    """Select a named catalog skill for installation."""

    name: str = ""


class SkillFetchRequest(BaseModel):
    """Select a remote skill URL for safe fetching."""

    url: str = ""


class SkillFetchGithubRequest(BaseModel):
    """Select a repository path and branch for safe skill fetching."""

    repo: str = ""
    path: str = ""
    branch: str = "main"


class SkillRenderRequest(StrictRuntimeRequest):
    """Bounded skill selection and rendering request."""

    name: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    trigger: StrictStr | None = Field(default=None, min_length=1, max_length=4096)
    variables: dict[StrictStr, object] = Field(default_factory=dict)
    skills_path: StrictStr | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def _select_exactly_one(self) -> SkillRenderRequest:
        if (self.name is None) == (self.trigger is None):
            raise ValueError("exactly one of name or trigger is required")
        return self

    @field_validator("variables")
    @classmethod
    def _bound_variables(cls, value: dict[str, object]) -> dict[str, object]:
        if len(value) > 128 or len(json.dumps(value, default=str)) > 65_536:
            raise ValueError("skill variables exceed the bounded payload size")
        return value


def _get_catalog(app: FastAPI) -> SkillCatalog:
    catalog = getattr(app.state, "_skill_catalog", None)
    if catalog is None:
        catalog = SkillCatalog()
        app.state._skill_catalog = catalog
    return catalog


def _allowed_skill_roots(app: FastAPI) -> list[Path]:
    roots: list[Path] = []
    config_dir = getattr(app.state, "_config_dir", None)
    if config_dir:
        roots.append(Path(config_dir) / "skills")
    project_dir = getattr(app.state, "_project_gludd_dir", None)
    if project_dir:
        roots.append(Path(project_dir) / "skills")
    return roots


def _required_skill_variables(body: str) -> list[str]:
    environment = SandboxedEnvironment(autoescape=False)
    environment.globals.clear()
    return sorted(meta.find_undeclared_variables(environment.parse(body)))


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register skill management and bounded rendering routes."""

    @app.post(
        "/admin/skills/render",
        dependencies=[Depends(RequireCapability(resource="admin:skills", action="render"))],
    )
    async def admin_skills_render(req: SkillRenderRequest) -> dict[str, object]:
        roots = _allowed_skill_roots(app)
        if req.skills_path is not None:
            requested = Path(req.skills_path)
            if not requested.is_absolute() or not any(
                is_path_within(str(requested), str(root)) for root in roots
            ):
                raise HTTPException(status_code=422, detail="skills_path is outside daemon skill roots")
            search_paths = [str(requested)]
        else:
            search_paths = [str(root) for root in roots]

        skills = await asyncio.to_thread(discover_skills, *search_paths)
        selected = next((skill for skill in skills if req.name and skill.name == req.name), None)
        if selected is None and req.trigger is not None:
            trigger = req.trigger.lower()
            selected = next(
                (
                    skill
                    for skill in skills
                    if any(pattern.lower() in trigger for pattern in skill.trigger_patterns)
                ),
                None,
            )
        if selected is None:
            raise HTTPException(status_code=404, detail="skill not found")
        required_vars = _required_skill_variables(selected.body)
        try:
            rendered = await asyncio.to_thread(render_skill, selected.body, req.variables)
        except SkillRenderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="skill renderer unavailable") from exc
        if len(rendered.encode("utf-8")) > 1_048_576:
            raise HTTPException(status_code=413, detail="rendered skill exceeds response limit")
        return {
            "skill_name": selected.name,
            "rendered_body": rendered.strip(),
            "required_vars": required_vars,
        }

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
