"""Procedural memory — "how to do things" patterns and action sequences.

Stores reusable procedures: command sequences, tool invocation patterns,
error-recovery workflows, and configuration templates that the agent has
learned and can replay across tasks.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

PROCEDURAL_NAMESPACE = "procedural"


@dataclass
class Procedure:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    description: str = ""
    trigger: str = ""
    steps: list[dict[str, str]] = field(default_factory=list)
    expected_outcome: str = ""
    tool_chain: list[str] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    last_used_at: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0


class ProceduralMemoryStore:
    """Persistent store for learned procedures (how-to knowledge)."""

    def __init__(self, memory_repo: Any) -> None:
        self._repo = memory_repo

    async def store_procedure(
        self,
        procedure: Procedure,
        project_id: str | None = None,
    ) -> str:
        serialized = json.dumps(_procedure_to_dict(procedure), default=str)
        await self._repo.set(
            agent_id="system",
            key=procedure.id,
            value=serialized,
            namespace=PROCEDURAL_NAMESPACE,
            project_id=project_id,
        )
        return procedure.id

    async def get_procedure(
        self,
        procedure_id: str,
        project_id: str | None = None,
    ) -> Procedure | None:
        row = await self._repo.get(
            "system",
            procedure_id,
            namespace=PROCEDURAL_NAMESPACE,
            project_id=project_id,
        )
        if row is None:
            return None
        data = json.loads(row.value) if isinstance(row.value, str) else row.value
        return _dict_to_procedure(data)

    async def list_procedures(
        self,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[Procedure]:
        rows = await self._repo.list_by_namespace(
            "system",
            namespace=PROCEDURAL_NAMESPACE,
            project_id=project_id,
            limit=limit,
        )
        results: list[Procedure] = []
        for row in rows:
            try:
                data = json.loads(row.value)
                results.append(_dict_to_procedure(data))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return results

    async def find_by_trigger(
        self,
        trigger_text: str,
        project_id: str | None = None,
        limit: int = 10,
    ) -> list[Procedure]:
        all_procs = await self.list_procedures(project_id=project_id, limit=200)
        trigger_lower = trigger_text.lower()
        scored: list[tuple[Procedure, float]] = []
        for proc in all_procs:
            score = 0.0
            if trigger_lower in proc.trigger.lower():
                score += 0.5
            if trigger_lower in proc.name.lower() or trigger_lower in proc.description.lower():
                score += 0.2
            for tag in proc.tags:
                if tag.lower() in trigger_lower:
                    score += 0.15
            score += proc.success_rate * 0.15
            if score > 0:
                scored.append((proc, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return [proc for proc, _score in scored[:limit]]

    async def record_success(
        self,
        procedure_id: str,
        project_id: str | None = None,
    ) -> None:
        proc = await self.get_procedure(procedure_id, project_id=project_id)
        if proc is None:
            return
        proc.success_count += 1
        proc.last_used_at = datetime.now(UTC).isoformat()
        await self.store_procedure(proc, project_id=project_id)

    async def record_failure(
        self,
        procedure_id: str,
        project_id: str | None = None,
    ) -> None:
        proc = await self.get_procedure(procedure_id, project_id=project_id)
        if proc is None:
            return
        proc.failure_count += 1
        proc.last_used_at = datetime.now(UTC).isoformat()
        await self.store_procedure(proc, project_id=project_id)

    async def consolidate_from_episodes(
        self,
        episodic_recorder: Any,
        agent_id: str,
        project_id: str | None = None,
        min_success_count: int = 2,
    ) -> int:
        episodes = await episodic_recorder.list_episodes(
            agent_id,
            project_id=project_id,
            limit=500,
        )
        success_eps = [ep for ep in episodes if ep.outcome == "success" and ep.takeaway]
        grouped: dict[str, list[Any]] = {}
        for ep in success_eps:
            key = ep.task_type or "unknown"
            grouped.setdefault(key, []).append(ep)

        created = 0
        for task_type, eps in grouped.items():
            if len(eps) < min_success_count:
                continue
            steps: list[dict[str, str]] = []
            for ep in eps:
                for tool in ep.tools_used or []:
                    steps.append({"tool": tool, "task_type": task_type})
                if ep.takeaway:
                    steps.append({"action": ep.takeaway, "task_type": task_type})

            proc = Procedure(
                name=f"{task_type}_procedure",
                description=f"Learned procedure for {task_type} tasks",
                trigger=task_type,
                steps=steps[:20],
                expected_outcome=eps[0].outcome if eps else "success",
                success_count=len(eps),
                tags=[task_type],
            )
            await self.store_procedure(proc, project_id=project_id)
            created += 1

        return created


def _procedure_to_dict(proc: Procedure) -> dict[str, object]:
    return {
        "id": proc.id,
        "name": proc.name,
        "description": proc.description,
        "trigger": proc.trigger,
        "steps": proc.steps,
        "expected_outcome": proc.expected_outcome,
        "tool_chain": proc.tool_chain,
        "success_count": proc.success_count,
        "failure_count": proc.failure_count,
        "last_used_at": proc.last_used_at,
        "tags": proc.tags,
        "created_at": proc.created_at,
    }


def _dict_to_procedure(data: dict[str, Any]) -> Procedure:
    return Procedure(
        id=str(data.get("id", "")),
        name=str(data.get("name", "")),
        description=str(data.get("description", "")),
        trigger=str(data.get("trigger", "")),
        steps=list(data.get("steps", [])),
        expected_outcome=str(data.get("expected_outcome", "")),
        tool_chain=list(data.get("tool_chain", [])),
        success_count=int(data.get("success_count", 0)),
        failure_count=int(data.get("failure_count", 0)),
        last_used_at=str(data.get("last_used_at", "")),
        tags=list(data.get("tags", [])),
        created_at=str(data.get("created_at", datetime.now(UTC).isoformat())),
    )
