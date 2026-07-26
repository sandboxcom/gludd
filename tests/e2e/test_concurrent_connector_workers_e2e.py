"""E2E stress coverage for connector workers across isolated projects.

The scenario models the workload Gludd must handle in production: several
projects query different connector kinds at once while workers of the same
kind share a bounded admission slot.  It uses only the public dispatcher and
observability APIs, and keeps each project's source registry namespaced.
"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from pathlib import Path

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType
from general_ludd.connectors.base import Observability, SourceRegistry
from general_ludd.projects.manager import ProjectManager
from general_ludd.projects.workspace import ProjectWorkspace

CONNECTOR_KINDS = ("logs", "metrics", "traces", "pipeline")
PROJECT_COUNT = 4
COPIES_PER_CONNECTOR = 3
MAX_PER_KIND = 2


class _ProjectConnector:
    """Small public ``Source`` implementation used as a deterministic backend."""

    def __init__(self, project_id: str, kind: str, *, fail: bool = False) -> None:
        self.name = f"{project_id}:{kind}"
        self.KIND = kind
        self.project_id = project_id
        self._fail = fail
        self._calls = 0
        self._lock = threading.Lock()

    @property
    def calls(self) -> int:
        with self._lock:
            return self._calls

    def health(self) -> dict[str, object]:
        return {"status": "ok", "project_id": self.project_id, "kind": self.KIND}

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        if spec.get("project_id") != self.project_id:
            raise AssertionError("cross-project connector query")
        if self._fail:
            raise RuntimeError("simulated connector outage")
        with self._lock:
            self._calls += 1
            sequence = self._calls
        return [
            {
                "ts": float(sequence),
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": "ok",
                "message": f"{self.project_id}:{self.KIND}:{sequence}",
                "value": None,
                "labels": {
                    "project_id": self.project_id,
                    "connector_kind": self.KIND,
                },
                "raw": None,
            }
        ]


def _registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        AgentConfig(
            name="orchestrator",
            description="connector workload orchestrator",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_read=True,
                can_dispatch_subagents=True,
                allowed_subagents=["*"],
            ),
        )
    )
    for kind in CONNECTOR_KINDS:
        registry.register(
            AgentConfig(
                name=kind,
                description=f"bounded {kind} connector worker",
                type=AgentType.SUBAGENT,
                permissions=AgentPermission(can_read=True),
                max_concurrent=MAX_PER_KIND,
            )
        )
    return registry


def _projects_and_observability(
    tmp_path: Path,
) -> tuple[list[str], dict[str, ProjectWorkspace], dict[str, Observability], dict[str, _ProjectConnector]]:
    manager = ProjectManager()
    weight = 100.0 / PROJECT_COUNT
    projects = [
        manager.add_project(f"connector-project-{i}", weight=weight)
        for i in range(PROJECT_COUNT)
    ]
    workspaces = {
        project.project_id: ProjectWorkspace(project.project_id, base_dir=str(tmp_path))
        for project in projects
    }
    observability: dict[str, Observability] = {}
    sources: dict[str, _ProjectConnector] = {}
    for project in projects:
        workspace = workspaces[project.project_id]
        workspace.ensure_dirs()
        source_registry = SourceRegistry()
        for kind in CONNECTOR_KINDS:
            source = _ProjectConnector(project.project_id, kind)
            source_registry.register(source)
            sources[f"{project.project_id}:{kind}"] = source
        observability[project.project_id] = Observability(source_registry)
    return [project.project_id for project in projects], workspaces, observability, sources


def _tasks(project_ids: list[str]) -> list[AgentTask]:
    return [
        AgentTask(
            task_id=f"{project_id}-{kind}-{copy}",
            agent_name=kind,
            description=f"{kind} connector workload for {project_id}",
            prompt=f"query {kind}",
            invoker_name="orchestrator",
            project_id=project_id,
        )
        for kind in CONNECTOR_KINDS
        for copy in range(COPIES_PER_CONNECTOR)
        for project_id in project_ids
    ]


async def test_connector_workers_are_bounded_and_project_isolated(tmp_path: Path) -> None:
    """All projects overlap, while each connector kind stays within its bound."""

    project_ids, workspaces, observability, sources = _projects_and_observability(tmp_path)
    tasks = _tasks(project_ids)
    state_lock = asyncio.Lock()
    ready_by_kind = {kind: asyncio.Event() for kind in CONNECTOR_KINDS}
    active_by_kind: defaultdict[str, int] = defaultdict(int)
    peak_by_kind: defaultdict[str, int] = defaultdict(int)
    active_projects: set[str] = set()
    peak_projects = 0

    async def execute(task: AgentTask) -> str:
        nonlocal peak_projects
        assert task.project_id is not None
        project_id = task.project_id
        kind = task.agent_name
        async with state_lock:
            active_by_kind[kind] += 1
            peak_by_kind[kind] = max(peak_by_kind[kind], active_by_kind[kind])
            active_projects.add(project_id)
            peak_projects = max(peak_projects, len(active_projects))
            if active_by_kind[kind] >= MAX_PER_KIND:
                ready_by_kind[kind].set()
        try:
            await asyncio.wait_for(ready_by_kind[kind].wait(), timeout=1.0)
            records = await asyncio.to_thread(
                observability[project_id].find,
                {"project_id": project_id, "connector_kind": kind},
                [kind],
            )
            assert len(records) == 1
            record = records[0]
            assert record["source"] == f"{project_id}:{kind}"
            assert record["labels"]["project_id"] == project_id
            artifact = workspaces[project_id].job_artifact_dir(task.task_id) / "result.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(f"{project_id}:{kind}\n", encoding="utf-8")
            await asyncio.sleep(0)
            return f"completed:{project_id}:{kind}"
        finally:
            async with state_lock:
                active_by_kind[kind] -= 1
                if active_by_kind[kind] == 0:
                    active_projects.discard(project_id)

    results = await AgentDispatcher(_registry(), executor=execute).dispatch_many(
        tasks, timeout=5.0
    )

    assert len(results) == len(tasks)
    assert all(result.status == "completed" for result in results)
    assert peak_projects >= 2
    assert all(peak_by_kind[kind] <= MAX_PER_KIND for kind in CONNECTOR_KINDS)
    for project_id in project_ids:
        for kind in CONNECTOR_KINDS:
            assert sources[f"{project_id}:{kind}"].calls == COPIES_PER_CONNECTOR
        artifacts = sorted(workspaces[project_id].artifacts_dir.glob("*/result.txt"))
        assert len(artifacts) == len(CONNECTOR_KINDS) * COPIES_PER_CONNECTOR
        assert all(project_id in artifact.read_text(encoding="utf-8") for artifact in artifacts)


async def test_connector_failure_is_contained_to_one_project(tmp_path: Path) -> None:
    """A failed connector produces an error record without blocking other projects."""

    project_ids, _workspaces, observability, sources = _projects_and_observability(tmp_path)
    failed_project = project_ids[0]
    failed_source = sources[f"{failed_project}:logs"]
    failed_source._fail = True

    failed = observability[failed_project].find(
        {"project_id": failed_project, "connector_kind": "logs"}, ["logs"]
    )
    healthy = observability[project_ids[1]].find(
        {"project_id": project_ids[1], "connector_kind": "logs"}, ["logs"]
    )

    assert len(failed) == 1
    assert failed[0]["level_or_status"] == "error"
    assert failed[0]["source"] == failed_source.name
    assert len(healthy) == 1
    assert healthy[0]["source"] == f"{project_ids[1]}:logs"
