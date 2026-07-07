"""Memory consolidation — periodic summarization of old episodic entries.

Mirrors Stanford AutoMemory's consolidation concept: old, detailed episodic
memories are condensed into higher-level summaries stored as "semantic"
memory, freeing detail space while preserving the learned insight.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

CONSOLIDATED_NAMESPACE = "consolidated"
CONSOLIDATION_KEY_PREFIX = "summary_"


class MemoryConsolidator:
    """Periodically summarizes old episodic memories into consolidated form.

    Consolidation runs on a configurable schedule (default: every 50 episodes
    or every 6 hours, whichever comes first). Old episodes are grouped by task
    type and summarized: key patterns, common failure modes, effective
    strategies, and frequency statistics.
    """

    def __init__(
        self,
        memory_repo: Any,
        model_gateway: Any | None = None,
        min_episodes_to_consolidate: int = 10,
        max_episode_age_hours: float = 24.0,
    ) -> None:
        self._repo = memory_repo
        self._model_gateway = model_gateway
        self._min_episodes = min_episodes_to_consolidate
        self._max_age_hours = max_episode_age_hours

    async def consolidate(
        self,
        agent_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        from general_ludd.memory.episodic import EpisodicMemoryRecorder

        recorder = EpisodicMemoryRecorder(self._repo)
        all_episodes = await recorder.list_episodes(agent_id, limit=1000)

        if len(all_episodes) < self._min_episodes and not force:
            return {"consolidated": 0, "reason": "insufficient episodes", "total": len(all_episodes)}

        now = datetime.now(UTC)

        old_episodes = []
        recent_episodes = []
        for ep in all_episodes:
            try:
                created = datetime.fromisoformat(ep.created_at)
                age_h = (now - created).total_seconds() / 3600
                if age_h >= self._max_age_hours:
                    old_episodes.append(ep)
                else:
                    recent_episodes.append(ep)
            except (ValueError, TypeError):
                recent_episodes.append(ep)

        if len(old_episodes) < self._min_episodes and not force:
            return {
                "consolidated": 0,
                "reason": "insufficient old episodes",
                "old_count": len(old_episodes),
                "total": len(all_episodes),
            }

        grouped: dict[str, list[Any]] = defaultdict(list)
        for ep in old_episodes:
            grouped[ep.task_type or "unknown"].append(ep)

        summaries: dict[str, dict[str, object]] = {}
        for task_type, eps in grouped.items():
            summary = self._summarize_group(task_type, eps)
            summaries[task_type] = summary

        consolidated_count = 0
        for task_type, summary in summaries.items():
            key = f"{CONSOLIDATION_KEY_PREFIX}{_safe_key(task_type)}"
            value = json.dumps(summary, default=str)
            await self._repo.set(
                agent_id=agent_id,
                key=key,
                value=value,
                namespace=CONSOLIDATED_NAMESPACE,
            )
            consolidated_count += 1

        if self._model_gateway is not None and consolidated_count > 0:
            try:
                model_summary = await self._model_consolidate(summaries)
                if model_summary:
                    await self._repo.set(
                        agent_id=agent_id,
                        key="model_insight",
                        value=model_summary,
                        namespace=CONSOLIDATED_NAMESPACE,
                    )
                    consolidated_count += 1
            except Exception as exc:
                logger.warning("Model consolidation failed: %s", exc)

        return {
            "consolidated": consolidated_count,
            "task_types": list(summaries.keys()),
            "episodes_consolidated": len(old_episodes),
        }

    def _summarize_group(
        self, task_type: str, episodes: list[Any]
    ) -> dict[str, object]:
        outcomes = Counter(ep.outcome for ep in episodes)
        priorities = Counter(ep.priority for ep in episodes)
        total_duration = sum(ep.duration_seconds for ep in episodes)

        failures = [ep for ep in episodes if ep.outcome == "failure"]
        successes = [ep for ep in episodes if ep.outcome == "success"]

        error_patterns: list[str] = []
        for ep in failures:
            if ep.error_message and ep.error_message not in error_patterns:
                error_patterns.append(ep.error_message)

        takeaways: list[str] = []
        for ep in successes:
            if ep.takeaway and ep.takeaway not in takeaways:
                takeaways.append(ep.takeaway)

        return {
            "task_type": task_type,
            "episode_count": len(episodes),
            "outcomes": dict(outcomes),
            "priorities": dict(priorities),
            "total_duration_seconds": total_duration,
            "avg_duration_seconds": total_duration / len(episodes) if episodes else 0,
            "error_patterns": error_patterns[:10],
            "key_takeaways": takeaways[:10],
            "consolidated_at": datetime.now(UTC).isoformat(),
        }

    async def _model_consolidate(
        self, summaries: dict[str, dict[str, object]]
    ) -> str | None:
        gw = self._model_gateway
        if gw is None:
            return None

        summary_text = json.dumps(summaries, default=str, indent=2)
        prompt = (
            "You are analyzing execution history for a coding agent. "
            "Below are consolidated summaries of past task executions grouped "
            "by task type. Identify the top 3 patterns: what the agent does "
            "well, where it fails repeatedly, and one concrete recommendation "
            "for improvement.\n\n"
            f"Summaries:\n{summary_text}\n\n"
            "Return a concise JSON object with keys: strengths (list), "
            "weaknesses (list), recommendation (string). Return ONLY the JSON."
        )

        try:
            if hasattr(gw, "call_model"):
                response = gw.call_model(
                    "default",
                    [{"role": "user", "content": prompt}],
                    work_type="memory_consolidation",
                )
            else:
                response = gw.complete(prompt)

            content = str(response.content).strip()
            if content.startswith("```"):
                lines = content.splitlines()
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            return content
        except Exception as exc:
            logger.warning("Model consolidation call failed: %s", exc)
            return None

    async def get_consolidated(
        self, agent_id: str, task_type: str | None = None
    ) -> list[dict[str, Any]]:
        rows = await self._repo.list_by_namespace(
            agent_id, namespace=CONSOLIDATED_NAMESPACE, limit=100
        )
        results = []
        for row in rows:
            try:
                data = json.loads(row.value)
                if task_type and data.get("task_type") != task_type:
                    continue
                results.append(data)
            except (json.JSONDecodeError, KeyError):
                continue
        return results


def _safe_key(task_type: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in task_type.lower())[:64]
