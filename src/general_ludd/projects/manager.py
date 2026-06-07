from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

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
            created_at=time.time(),
        )
        self._projects[project_id] = project
        logger.info("Added project %s (%s) at %.1f%%", name, project_id, weight)
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

        active = self.list_projects(active_only=True)
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
            "total_weight": self.total_weight(),
            "unallocated": 100.0 - self.total_weight(),
            "projects": [
                {
                    "project_id": p.project_id,
                    "name": p.name,
                    "weight": p.weight,
                    "active": p.active,
                    "description": p.description,
                }
                for p in projects
            ],
        }
