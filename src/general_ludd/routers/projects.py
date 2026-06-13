from __future__ import annotations

import os
from typing import Any, cast

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from general_ludd.db.repository import ProjectRepository
from general_ludd.self_improve.harness import SelfImprovementHarness
from general_ludd.skills.catalog import SkillCatalog
from general_ludd.skills.loader import discover_skills
from general_ludd.skills.registry import SkillRegistry


class AddProjectRequest(BaseModel):
    name: str
    weight: float
    description: str = ""
    repo_url: str = ""
    workspace_path: str = ""
    dispatch_mode: str = "active"


class SetWeightRequest(BaseModel):
    weight: float


class RebalanceRequest(BaseModel):
    weights: dict[str, float]


_tui_log_entries: list[dict[str, Any]] = []


def _get_session_factory(app: FastAPI) -> Any:
    return getattr(app.state, "_session_factory", None)


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/projects")
    async def admin_add_project(req: AddProjectRequest) -> dict[str, Any]:
        from general_ludd.daemon import _get_or_create_extended_subsystems
        from general_ludd.projects.manager import (
            materialize_project_workspace,
            persist_project,
        )
        ext = _get_or_create_extended_subsystems(app)
        try:
            project = ext["projects"].add_project(
                name=req.name, weight=req.weight, description=req.description,
                repo_url=req.repo_url, workspace_path=req.workspace_path,
                dispatch_mode=req.dispatch_mode,
            )
            # W3.11 (H13): materialize the workspace from repo_url so a dispatched
            # job has real code to edit. Persist with repo_url so a restart keeps it.
            if req.repo_url:
                materialize_project_workspace(
                    repo_url=req.repo_url,
                    workspace_path=req.workspace_path or project.project_id,
                )
            factory = _get_session_factory(app)
            if factory is not None:
                async with factory() as session:
                    repo = ProjectRepository(session)
                    try:
                        await persist_project(
                            repo,
                            project_id=project.project_id,
                            name=req.name,
                            weight=req.weight,
                            description=req.description,
                            repo_url=req.repo_url,
                            workspace_path=req.workspace_path,
                            dispatch_mode=req.dispatch_mode,
                        )
                        await session.commit()
                    except Exception:
                        pass
            return {
                "project_id": project.project_id,
                "name": project.name, "weight": project.weight,
                "description": project.description,
                "repo_url": project.repo_url,
                "workspace_path": project.workspace_path,
                "dispatch_mode": project.dispatch_mode,
                "active": project.active,
            }
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/admin/projects/{project_id}")
    async def admin_delete_project(project_id: str) -> dict[str, Any]:
        from general_ludd.daemon import _get_or_create_extended_subsystems
        ext = _get_or_create_extended_subsystems(app)
        factory = _get_session_factory(app)
        if factory is not None:
            async with factory() as session:
                repo = ProjectRepository(session)
                await repo.deactivate(project_id)
                await session.commit()
        ext["projects"].remove_project(project_id)
        return {"removed": project_id}

    @app.put("/admin/projects/{project_id}/weight")
    async def admin_set_project_weight(project_id: str, req: SetWeightRequest) -> dict[str, Any]:
        from general_ludd.daemon import _get_or_create_extended_subsystems
        ext = _get_or_create_extended_subsystems(app)
        try:
            ext["projects"].set_weight(project_id, req.weight)
            project = ext["projects"].get_project(project_id)
            return {"project_id": project_id, "weight": project.weight if project else req.weight}
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/admin/projects/rebalance")
    async def admin_rebalance_projects(req: RebalanceRequest) -> dict[str, Any]:
        from general_ludd.daemon import _get_or_create_extended_subsystems
        ext = _get_or_create_extended_subsystems(app)
        try:
            ext["projects"].rebalance(req.weights)
            return {"rebalanced": list(req.weights.keys())}
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/admin/projects")
    async def admin_list_projects() -> dict[str, Any]:
        from general_ludd.daemon import _get_or_create_extended_subsystems
        ext = _get_or_create_extended_subsystems(app)
        factory = _get_session_factory(app)
        db_projects: list[dict[str, Any]] = []
        if factory is not None:
            async with factory() as session:
                repo = ProjectRepository(session)
                active = await repo.list_active()
                db_projects = [
                    {
                        "project_id": p.project_id, "name": p.name,
                        "active": p.active,
                    }
                    for p in active
                ]
        summary = ext["projects"].get_summary()
        if isinstance(summary, dict):
            summary["db_projects"] = db_projects
        return cast("dict[str, Any]", summary)

    @app.post("/admin/projects/skills")
    async def admin_project_skills(req: dict[str, Any]) -> dict[str, Any]:
        project_id = req.get("project_id", "")
        skill_name = req.get("skill_name", "")
        if not project_id or not skill_name:
            raise HTTPException(status_code=422, detail="project_id and skill_name required")
        registry = getattr(app.state, "_skill_registry", None)
        if registry is None:
            registry = SkillRegistry()
            app.state._skill_registry = registry
        skill = registry.get(skill_name)
        if skill is None:
            catalog = getattr(app.state, "_skill_catalog", None)
            if catalog is None:
                catalog = SkillCatalog()
                app.state._skill_catalog = catalog
            entry = catalog.get_skill(skill_name)
            if entry is None:
                raise HTTPException(status_code=404, detail=f"Skill {skill_name} not found")
            config_dir = getattr(app.state, "_config_dir", None) or "/etc/general-ludd"
            path = catalog.install_skill(skill_name, config_dir)
            if path is None:
                raise HTTPException(status_code=404, detail=f"Failed to install skill {skill_name}")
            discovered = discover_skills(os.path.join(config_dir, "skills"))
            for s in discovered:
                registry.register(s)
            skill = registry.get(skill_name)
        if skill is not None:
            registry.register(skill, project_id=project_id)
        return {"status": "ok", "project_id": project_id, "skill": skill_name}

    @app.put("/admin/dispatch/mode")
    async def admin_dispatch_mode(req: dict[str, Any]) -> Any:
        mode = req.get("mode", "active")
        valid = ["active", "passive_external", "worktree_monitor"]
        if mode not in valid:
            return JSONResponse(status_code=400, content={"error": f"Invalid mode: {mode}"})
        cfg = getattr(app.state, "_startup_config", None)
        if cfg is not None and isinstance(cfg, dict):
            cfg["dispatch_mode"] = mode
        return {"dispatch_mode": mode}

    @app.post("/admin/self-improve")
    async def admin_self_improve() -> dict[str, Any]:
        harness = SelfImprovementHarness()
        result = harness.run_full_cycle()
        return {"status": "ok", "findings_count": result["findings_count"],
                "todos_generated": result["todos_generated"],
                "todos_enqueued": result["todos_enqueued"]}

    @app.post("/admin/tui-log")
    async def admin_tui_log(req: dict[str, Any]) -> dict[str, Any]:
        entries = req.get("entries", [])
        _tui_log_entries.extend(entries)
        if len(_tui_log_entries) > 10000:
            del _tui_log_entries[:len(_tui_log_entries) - 10000]
        return {"status": "ok", "stored": len(entries)}

    @app.get("/admin/tui-log")
    async def admin_tui_log_get() -> dict[str, Any]:
        return {"entries": list(_tui_log_entries[-200:])}
