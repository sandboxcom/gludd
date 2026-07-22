"""Episodic memory — structured records of past task executions.

Each Episode captures: what task was attempted, the context, the outcome, and
key takeaways. Records are persisted via MemoryRepository in the "episodic"
namespace and are the substrate for consolidation and cross-task learning.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

EPISODIC_NAMESPACE = "episodic"


@dataclass
class Episode:
    """A single task execution record."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    agent_id: str = ""
    task_type: str = ""
    work_type: str = ""
    priority: str = "medium"
    outcome: str = "unknown"  # success, failure, partial, cancelled
    context: dict[str, object] = field(default_factory=dict)
    tools_used: list[str] = field(default_factory=list)
    takeaway: str = ""
    error_message: str = ""
    duration_seconds: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class EpisodicMemoryRecorder:
    """Records task execution episodes to persistent memory."""

    def __init__(self, memory_repo: Any) -> None:
        self._repo = memory_repo
        self._namespace = EPISODIC_NAMESPACE

    async def record_episode(self, episode: Episode, project_id: str | None = None) -> str:
        serialized = json.dumps(_episode_to_dict(episode), default=str)
        await self._repo.set(
            agent_id=episode.agent_id,
            key=episode.id,
            value=serialized,
            namespace=self._namespace,
            project_id=project_id,
        )
        return episode.id

    async def get_episode(
        self, agent_id: str, episode_id: str, project_id: str | None = None,
    ) -> Episode | None:
        row = await self._repo.get(agent_id, episode_id, namespace=self._namespace, project_id=project_id)
        if row is None:
            return None
        return _dict_to_episode(json.loads(_row_value(row)))

    async def list_episodes(
        self,
        agent_id: str,
        task_type: str | None = None,
        outcome: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[Episode]:
        rows = await self._repo.list_by_namespace(
            agent_id, namespace=self._namespace, project_id=project_id, limit=limit
        )
        episodes = []
        for row in rows:
            try:
                ep = _dict_to_episode(json.loads(_row_value(row)))
                if task_type and ep.task_type != task_type:
                    continue
                if outcome and ep.outcome != outcome:
                    continue
                episodes.append(ep)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return episodes

    async def list_by_outcome(
        self, agent_id: str, outcome: str, project_id: str | None = None, limit: int = 100,
    ) -> list[Episode]:
        return await self.list_episodes(agent_id, outcome=outcome, project_id=project_id, limit=limit)

    async def record_completion(
        self,
        agent_id: str,
        task_type: str,
        work_type: str = "code",
        priority: str = "medium",
        outcome: str = "success",
        context: dict[str, object] | None = None,
        takeaway: str = "",
        error_message: str = "",
        duration_seconds: float = 0.0,
        project_id: str | None = None,
    ) -> str:
        episode = Episode(
            agent_id=agent_id,
            task_type=task_type,
            work_type=work_type,
            priority=priority,
            outcome=outcome,
            context=context or {},
            takeaway=takeaway,
            error_message=error_message,
            duration_seconds=duration_seconds,
        )
        return await self.record_episode(episode, project_id=project_id)


def _row_value(row: Any) -> str:
    """Return serialized memory payload from repository rows or raw store values."""
    if isinstance(row, str):
        return row
    if isinstance(row, bytes):
        return row.decode("utf-8")
    value = row.get("value") if isinstance(row, dict) else getattr(row, "value", None)
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8")
    raise TypeError(f"Unsupported memory row payload: {type(row).__name__}")


def _episode_to_dict(ep: Episode) -> dict[str, object]:
    return {
        "id": ep.id,
        "agent_id": ep.agent_id,
        "task_type": ep.task_type,
        "work_type": ep.work_type,
        "priority": ep.priority,
        "outcome": ep.outcome,
        "context": ep.context,
        "tools_used": ep.tools_used,
        "takeaway": ep.takeaway,
        "error_message": ep.error_message,
        "duration_seconds": ep.duration_seconds,
        "created_at": ep.created_at,
    }


def _dict_to_episode(d: dict[str, Any]) -> Episode:
    return Episode(
        id=d.get("id", ""),
        agent_id=d.get("agent_id", ""),
        task_type=d.get("task_type", ""),
        work_type=d.get("work_type", ""),
        priority=d.get("priority", "medium"),
        outcome=d.get("outcome", "unknown"),
        context=d.get("context", {}),
        tools_used=d.get("tools_used", []),
        takeaway=d.get("takeaway", ""),
        error_message=d.get("error_message", ""),
        duration_seconds=float(d.get("duration_seconds", 0)),
        created_at=d.get("created_at", ""),
    )
