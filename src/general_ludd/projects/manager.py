from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from general_ludd.db.repository import ProjectRepository

logger = logging.getLogger(__name__)


@dataclass
class ProjectWeight:
    project_id: str
    name: str
    weight: float
    description: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    workspace_path: str = ""
    repo_url: str = ""
    dispatch_mode: str = "active"
    created_at: float = 0.0
    active: bool = True


class ProjectAllocationError(Exception):
    pass


class ProjectManager:
    def __init__(self) -> None:
        self._projects: dict[str, ProjectWeight] = {}

    def add_project(
        self,
        name: str,
        weight: float,
        description: str = "",
        workspace_path: str = "",
        repo_url: str = "",
        dispatch_mode: str = "active",
        **config: Any,
    ) -> ProjectWeight:
        total = sum(p.weight for p in self._projects.values() if p.active)
        if total + weight > 100.0:
            available = 100.0 - total
            raise ProjectAllocationError(
                f"Cannot allocate {weight}% to '{name}': only {available}% available (current total: {total}%)"
            )
        project_id = f"proj-{uuid.uuid4().hex[:8]}"
        import time

        project = ProjectWeight(
            project_id=project_id,
            name=name,
            weight=weight,
            description=description,
            config=config,
            workspace_path=workspace_path,
            repo_url=repo_url,
            dispatch_mode=dispatch_mode,
            created_at=time.time(),
        )
        self._projects[project_id] = project
        logger.info("Added project %s (%s) at %.1f%% mode=%s", name, project_id, weight, dispatch_mode)
        return project

    def remove_project(self, project_id: str) -> None:
        project = self._projects.get(project_id)
        if project is None:
            return
        project.active = False
        logger.info("Removed project %s (%s)", project.name, project_id)

    def set_weight(self, project_id: str, new_weight: float) -> None:
        project = self._projects.get(project_id)
        if project is None:
            raise ProjectAllocationError(f"Project '{project_id}' not found")
        if not project.active:
            raise ProjectAllocationError(f"Project '{project_id}' is not active")
        if new_weight < 0 or new_weight > 100:
            raise ProjectAllocationError(f"Weight must be between 0 and 100, got {new_weight}")
        others_total = sum(p.weight for p in self._projects.values() if p.active and p.project_id != project_id)
        if others_total + new_weight > 100.0:
            available = 100.0 - others_total
            raise ProjectAllocationError(
                f"Cannot set weight to {new_weight}%: only {available}% available for this project"
            )
        old_weight = project.weight
        project.weight = new_weight
        logger.info("Changed project %s weight from %.1f%% to %.1f%%", project.name, old_weight, new_weight)

    def rebalance(self, weights: dict[str, float]) -> None:
        total = sum(weights.values())
        if abs(total - 100.0) > 0.01:
            raise ProjectAllocationError(f"Weights must sum to 100%, got {total:.1f}%")
        for pid, _w in weights.items():
            project = self._projects.get(pid)
            if project is None:
                raise ProjectAllocationError(f"Project '{pid}' not found")
            if not project.active:
                raise ProjectAllocationError(f"Project '{pid}' is not active")
        for pid, w in weights.items():
            self._projects[pid].weight = w
        logger.info("Rebalanced %d projects", len(weights))

    def get_project(self, project_id: str) -> ProjectWeight | None:
        return self._projects.get(project_id)

    def list_projects(self, active_only: bool = True) -> list[ProjectWeight]:
        projects = list(self._projects.values())
        if active_only:
            projects = [p for p in projects if p.active]
        return projects

    def list_active(self) -> list[ProjectWeight]:
        return self.list_projects(active_only=True)

    def total_weight(self) -> float:
        return sum(p.weight for p in self._projects.values() if p.active)

    def get_allocation(self) -> dict[str, float]:
        return {p.project_id: p.weight for p in self._projects.values() if p.active}

    def select_project(self) -> ProjectWeight | None:
        import random

        active = [p for p in self.list_projects(active_only=True)
                  if p.dispatch_mode == "active"]
        if not active:
            return None
        weights = [p.weight for p in active]
        total = sum(weights)
        if total <= 0:
            return active[0]
        r = random.random() * total
        cumulative = 0.0
        for project in active:
            cumulative += project.weight
            if r <= cumulative:
                return project
        return active[-1]

    def get_summary(self) -> dict[str, Any]:
        projects = self.list_projects(active_only=False)
        active = [p for p in projects if p.active]
        return {
            "total_projects": len(projects),
            "active_projects": len(active),
            "total_weight": sum(p.weight for p in active),
            "unallocated": 100.0 - sum(p.weight for p in active),
            "projects": [
                {
                    "project_id": p.project_id,
                    "name": p.name,
                    "weight": p.weight,
                    "description": p.description,
                    "dispatch_mode": p.dispatch_mode,
                    "repo_url": p.repo_url,
                    "workspace_path": p.workspace_path,
                    "active": p.active,
                }
                for p in projects
            ],
        }


def materialize_project_workspace(
    repo_url: str,
    workspace_path: str,
    base_dir: str = "/tmp/gludd-workspaces",
) -> str | None:
    """Clone ``repo_url`` into the project's workspace ``repo`` directory.

    W3.11 (H13): a dispatched job has no code to edit unless the project's
    repo_url is actually checked out. This uses the real ``GitAutomation.clone``
    (idempotent) — no ad-hoc shelling out.

    Returns the path to the materialized repo checkout, or ``None`` when there is
    no repo_url to clone. On clone failure returns ``None`` and logs a warning
    (the project still exists; it simply has no checkout yet).
    """
    if not repo_url:
        return None

    from general_ludd.git_automation.repo import GitAutomation
    from general_ludd.projects.workspace import ProjectWorkspace

    # Derive the workspace; an explicit workspace_path wins, else base_dir/<derived>.
    ws_pid = workspace_path or "default"
    ws = ProjectWorkspace(
        project_id=ws_pid,
        base_dir=base_dir,
        workspace_path=workspace_path or None,
    )
    ws.ensure_dirs()
    repo_dir = str(ws.repo_dir)

    git = GitAutomation()
    result = git.clone(repo_url, repo_dir)
    if not result.success:
        logger.warning(
            "Failed to materialize workspace for %s: %s", repo_url, result.message
        )
        return None
    if result.already_present:
        logger.info("Workspace already materialized at %s", repo_dir)
    else:
        logger.info("Cloned %s into %s", repo_url, repo_dir)
    return repo_dir


async def persist_project(
    repo: ProjectRepository,
    *,
    project_id: str,
    name: str,
    weight: float,
    description: str = "",
    repo_url: str = "",
    workspace_path: str = "",
    dispatch_mode: str = "active",
) -> None:
    """Persist a project through ProjectRepository so restarts keep it.

    ProjectModel has no dedicated repo_url/weight/dispatch_mode columns, so those
    spine-relevant fields live in the JSON ``config`` column. ``rebuild_manager_from_db``
    reads them back. Idempotent: an existing project_id is updated, not duplicated.
    """
    cfg = {
        "repo_url": repo_url,
        "weight": weight,
        "dispatch_mode": dispatch_mode,
    }
    existing = await repo.get_by_id(project_id)
    if existing is not None:
        existing.name = name
        existing.description = description
        existing.workspace_path = workspace_path
        existing.config = json.dumps(cfg)
        existing.active = True
        return
    await repo.create(
        data={
            "project_id": project_id,
            "name": name,
            "description": description,
            "workspace_path": workspace_path,
            "config": json.dumps(cfg),
            "active": True,
        }
    )


async def rebuild_manager_from_db(repo: ProjectRepository) -> ProjectManager:
    """Build a fresh ProjectManager from the persisted (active) projects.

    Used on daemon startup so DB-persisted projects survive a restart instead of
    only living in config. repo_url/weight/dispatch_mode are read back from the
    JSON ``config`` column.
    """
    mgr = ProjectManager()
    import time

    for row in await repo.list_active():
        try:
            cfg = json.loads(row.config) if row.config else {}
        except (ValueError, TypeError):
            cfg = {}
        weight = float(cfg.get("weight", 0.0) or 0.0)
        project = ProjectWeight(
            project_id=row.project_id,
            name=row.name,
            weight=weight,
            description=row.description or "",
            workspace_path=row.workspace_path or "",
            repo_url=str(cfg.get("repo_url", "") or ""),
            dispatch_mode=str(cfg.get("dispatch_mode", "active") or "active"),
            created_at=time.time(),
            active=True,
        )
        mgr._projects[project.project_id] = project
    logger.info("Rebuilt ProjectManager from DB: %d active project(s)", len(mgr._projects))
    return mgr


def seed_from_config(config: dict[str, Any]) -> ProjectManager:
    mgr = ProjectManager()
    projects_list = config.get("projects", [])
    if not isinstance(projects_list, list):
        return mgr
    for pcfg in projects_list:
        if not isinstance(pcfg, dict):
            continue
        try:
            mgr.add_project(
                name=pcfg.get("name", "unnamed"),
                weight=float(pcfg.get("weight", 10)),
                description=pcfg.get("description", ""),
                workspace_path=pcfg.get("workspace_path", ""),
                repo_url=pcfg.get("repo_url", ""),
                dispatch_mode=pcfg.get("dispatch_mode", "active"),
            )
        except ProjectAllocationError:
            logger.warning("Skipping project '%s': allocation would exceed 100%%", pcfg.get("name", "?"))
    return mgr
