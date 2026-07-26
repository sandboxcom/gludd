"""E2E stress coverage for concurrent, isolated project worker workloads.

The harness runs gate, test, and audit workers for several projects through the
public project/workspace, file-claim, and agent-dispatch APIs.  It deliberately
uses deterministic barriers and bounded worker semaphores so a global lock or a
cross-project workspace collision fails quickly instead of becoming a flaky
timing assertion.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType
from general_ludd.coordination.file_claims import FileClaimRegistry
from general_ludd.projects.manager import ProjectManager
from general_ludd.projects.workspace import ProjectWorkspace

WORKER_KINDS = ("gate", "test", "audit")


def _worker_config(name: str, *, max_concurrent: int = 2) -> AgentConfig:
    return AgentConfig(
        name=name,
        description=f"bounded {name} worker",
        type=AgentType.SUBAGENT,
        permissions=AgentPermission(can_read=True),
        max_concurrent=max_concurrent,
    )


def _registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        AgentConfig(
            name="orchestrator",
            description="project workload orchestrator",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_edit=True,
                can_bash=True,
                can_read=True,
                can_dispatch_subagents=True,
                allowed_subagents=["*"],
            ),
        )
    )
    for kind in WORKER_KINDS:
        registry.register(_worker_config(kind))
    return registry


def _projects_and_workspaces(
    tmp_path: Path, count: int
) -> tuple[list[str], dict[str, ProjectWorkspace]]:
    manager = ProjectManager()
    weight = 100.0 / count
    projects = [
        manager.add_project(f"project-{index}", weight=weight)
        for index in range(count)
    ]
    workspaces = {
        project.project_id: ProjectWorkspace(
            project.project_id, base_dir=str(tmp_path)
        )
        for project in projects
    }
    for workspace in workspaces.values():
        workspace.ensure_dirs()
    return [project.project_id for project in projects], workspaces


def _tasks(project_ids: list[str], copies: int = 1) -> list[AgentTask]:
    return [
        AgentTask(
            task_id=f"{project_id}-{kind}-{copy}",
            agent_name=kind,
            description=f"{kind} workload for {project_id}",
            prompt=f"run {kind}",
            invoker_name="orchestrator",
            project_id=project_id,
        )
        for kind in WORKER_KINDS
        for copy in range(copies)
        for project_id in project_ids
    ]


def _artifact_path(workspace: ProjectWorkspace, task: AgentTask) -> Path:
    artifact = workspace.job_artifact_dir(task.task_id)
    artifact.mkdir(parents=True, exist_ok=True)
    return artifact / "result.txt"


async def test_two_projects_overlap_gate_test_audit_without_cross_project_claims(
    tmp_path: Path,
) -> None:
    """All three worker kinds for two projects overlap behind isolated claims."""

    project_ids, workspaces = _projects_and_workspaces(tmp_path, count=2)
    tasks = _tasks(project_ids)
    claims = FileClaimRegistry()
    started = asyncio.Event()
    state_lock = asyncio.Lock()
    started_count = 0
    active_projects: set[str] = set()
    peak_projects = 0
    claim_results: dict[str, bool] = {}

    async def execute(task: AgentTask) -> str:
        nonlocal started_count, peak_projects
        assert task.project_id is not None
        project_id = task.project_id
        workspace = workspaces[project_id]
        claim_owner = f"{project_id}:{task.task_id}"
        claim_path = workspace.repo_dir / f"{task.agent_name}.lock"
        claimed = await asyncio.to_thread(
            claims.claim_or_conflict, claim_owner, [str(claim_path)]
        )
        claim_results[task.task_id] = claimed
        assert claimed, f"cross-project claim collision for {task.task_id}"
        async with state_lock:
            started_count += 1
            active_projects.add(project_id)
            peak_projects = max(peak_projects, len(active_projects))
            if started_count == len(tasks):
                started.set()
        try:
            await asyncio.wait_for(started.wait(), timeout=1.0)
            _artifact_path(workspace, task).write_text(
                f"{project_id}:{task.agent_name}\n", encoding="utf-8"
            )
            await asyncio.sleep(0)
            return f"completed:{project_id}:{task.agent_name}"
        finally:
            claims.release(claim_owner)
            async with state_lock:
                active_projects.discard(project_id)

    dispatcher = AgentDispatcher(_registry(), executor=execute)
    results = await dispatcher.dispatch_many(tasks, timeout=5.0)

    assert len(results) == len(tasks)
    assert all(result.status == "completed" for result in results)
    assert started_count == len(tasks)
    assert peak_projects == len(project_ids)
    assert all(claim_results.values())
    assert claims.all_claims() == {}
    for project_id, workspace in workspaces.items():
        artifacts = sorted(workspace.artifacts_dir.glob("*/result.txt"))
        assert len(artifacts) == len(WORKER_KINDS)
        assert all(project_id in artifact.read_text(encoding="utf-8") for artifact in artifacts)


async def test_four_project_worker_stress_respects_per_kind_bounds(
    tmp_path: Path,
) -> None:
    """A larger workload never exceeds worker bounds or mixes project artifacts."""

    project_ids, workspaces = _projects_and_workspaces(tmp_path, count=4)
    tasks = _tasks(project_ids, copies=2)
    claims = FileClaimRegistry()
    state_lock = asyncio.Lock()
    active_by_kind: defaultdict[str, int] = defaultdict(int)
    peak_by_kind: defaultdict[str, int] = defaultdict(int)
    active_by_project: defaultdict[str, int] = defaultdict(int)
    active_projects: set[str] = set()
    peak_projects = 0
    overlap_ready = asyncio.Event()

    async def execute(task: AgentTask) -> str:
        nonlocal peak_projects
        assert task.project_id is not None
        project_id = task.project_id
        workspace = workspaces[project_id]
        claim_owner = f"{project_id}:{task.task_id}"
        claim_path = workspace.repo_dir / f"{task.task_id}.lock"
        assert await asyncio.to_thread(
            claims.claim_or_conflict, claim_owner, [str(claim_path)]
        )
        async with state_lock:
            active_by_kind[task.agent_name] += 1
            peak_by_kind[task.agent_name] = max(
                peak_by_kind[task.agent_name], active_by_kind[task.agent_name]
            )
            active_by_project[project_id] += 1
            active_projects.add(project_id)
            peak_projects = max(peak_projects, len(active_projects))
            if len(active_projects) >= 2:
                overlap_ready.set()
        try:
            await asyncio.wait_for(overlap_ready.wait(), timeout=1.0)
            await asyncio.sleep(0.002)
            _artifact_path(workspace, task).write_text(
                f"{project_id}:{task.agent_name}\n", encoding="utf-8"
            )
            return f"completed:{project_id}:{task.agent_name}"
        finally:
            claims.release(claim_owner)
            async with state_lock:
                active_by_kind[task.agent_name] -= 1
                active_by_project[project_id] -= 1
                if active_by_project[project_id] == 0:
                    active_projects.discard(project_id)

    dispatcher = AgentDispatcher(_registry(), executor=execute)
    results = await dispatcher.dispatch_many(tasks, timeout=5.0)

    assert len(results) == len(tasks)
    assert all(result.status == "completed" for result in results)
    assert peak_projects >= 2
    assert all(peak_by_kind[kind] <= 2 for kind in WORKER_KINDS)
    assert claims.all_claims() == {}
    for project_id, workspace in workspaces.items():
        artifacts = sorted(workspace.artifacts_dir.glob("*/result.txt"))
        assert len(artifacts) == len(WORKER_KINDS) * 2
        assert all(project_id in artifact.read_text(encoding="utf-8") for artifact in artifacts)
