"""Memory consolidation — periodic summarization of old episodic entries.

Mirrors Stanford AutoMemory's consolidation concept: old, detailed episodic
memories are condensed into higher-level summaries stored as "semantic"
memory, freeing detail space while preserving the learned insight.
"""

from __future__ import annotations

import json
import logging
import re
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
        project_id: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        from general_ludd.memory.episodic import EpisodicMemoryRecorder

        recorder = EpisodicMemoryRecorder(self._repo)
        all_episodes = await recorder.list_episodes(agent_id, project_id=project_id, limit=1000)

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
                project_id=project_id,
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
                        project_id=project_id,
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
        self, agent_id: str, task_type: str | None = None, project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = await self._repo.list_by_namespace(
            agent_id, namespace=CONSOLIDATED_NAMESPACE, project_id=project_id, limit=100
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


def consolidate_cascade(memories: list[dict[str, Any]], levels: int = 3) -> list[dict[str, Any]]:
    """Multi-level summarization: raw → compressed → abstract → insight.

    Level 1: raw → compressed (keep details) — group by task type,
             aggregate counts and statistics.
    Level 2: compressed → abstract (keep themes) — extract cross-cutting
             themes from the compressed summaries.
    Level 3: abstract → insight (extract lessons) — distill key lessons
             and actionable patterns from themes.
    """
    if levels < 1:
        return []

    cascade: list[dict[str, Any]] = []

    _level1 = _cascade_level_1(memories)
    cascade.append({"level": 1, "label": "compressed", "data": _level1})

    if levels >= 2:
        _level2 = _cascade_level_2(_level1)
        cascade.append({"level": 2, "label": "abstract", "data": _level2})

    if levels >= 3:
        _level3 = _cascade_level_3(_level2 if levels >= 2 else _level1)
        cascade.append({"level": 3, "label": "insight", "data": _level3})

    return cascade


def _cascade_level_1(memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Level 1: raw → compressed — group and aggregate."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in memories:
        tt = m.get("task_type") or "unknown"
        grouped[tt].append(m)

    compressed: list[dict[str, Any]] = []
    for task_type in sorted(grouped):
        items = grouped[task_type]
        outcomes = Counter(item.get("outcome", "unknown") for item in items)
        priorities = Counter(item.get("priority", "medium") for item in items)
        durations = [float(item.get("duration_seconds", 0)) for item in items]

        error_msgs: list[str] = []
        takeaways: list[str] = []
        for item in items:
            em = item.get("error_message", "")
            if em and em not in error_msgs:
                error_msgs.append(em)
            tw = item.get("takeaway", "")
            if tw and tw not in takeaways:
                takeaways.append(tw)

        compressed.append({
            "task_type": task_type,
            "episode_count": len(items),
            "outcomes": dict(outcomes),
            "priorities": dict(priorities),
            "total_duration": sum(durations),
            "avg_duration": sum(durations) / len(durations) if durations else 0.0,
            "error_patterns": error_msgs[:10],
            "key_takeaways": takeaways[:10],
        })

    return compressed


def _extract_keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    stop = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "and", "but", "or", "not", "no", "nor", "so", "yet",
    }
    return {w for w in words if w not in stop and len(w) > 2}


def _cascade_level_2(compressed: list[dict[str, Any]]) -> dict[str, Any]:
    """Level 2: compressed → abstract — extract cross-cutting themes."""
    total_episodes = sum(item.get("episode_count", 0) for item in compressed)
    all_outcomes: Counter[str] = Counter()
    for item in compressed:
        for outcome, count in item.get("outcomes", {}).items():
            all_outcomes[outcome] += count

    success_count = all_outcomes.get("success", 0)
    total = sum(all_outcomes.values())

    all_errors: list[str] = []
    all_takeaways: list[str] = []
    for item in compressed:
        all_errors.extend(item.get("error_patterns", []))
        all_takeaways.extend(item.get("key_takeaways", []))

    all_error_words: set[str] = set()
    for err in all_errors:
        all_error_words |= _extract_keywords(err)

    all_takeaway_words: set[str] = set()
    for tw in all_takeaways:
        all_takeaway_words |= _extract_keywords(tw)

    success_rate = (success_count / total * 100) if total > 0 else 0.0

    return {
        "total_episodes": total_episodes,
        "task_type_count": len(compressed),
        "task_types": [c.get("task_type") for c in compressed],
        "overall_success_rate_pct": round(success_rate, 1),
        "failure_rate_pct": round(100.0 - success_rate, 1),
        "dominant_failure_words": sorted(all_error_words - all_takeaway_words)[:20],
        "dominant_success_words": sorted(all_takeaway_words - all_error_words)[:20],
        "shared_patterns": sorted(all_error_words & all_takeaway_words)[:10],
        "outcome_distribution": dict(all_outcomes),
        "consolidated_at": datetime.now(UTC).isoformat(),
    }


def _cascade_level_3(previous: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    """Level 3: abstract → insight — distill key lessons."""
    if isinstance(previous, list):
        total = sum(item.get("episode_count", 0) for item in previous)
        outcomes: Counter[str] = Counter()
        for item in previous:
            for outcome, count in item.get("outcomes", {}).items():
                outcomes[outcome] += count
        error_patterns: list[str] = []
        for item in previous:
            error_patterns.extend(item.get("error_patterns", []))
        task_types = [item.get("task_type") for item in previous]
        dominant_words: set[str] = set()
        for err in error_patterns:
            dominant_words |= _extract_keywords(err)
    else:
        total = previous.get("total_episodes", 0)
        outcomes = Counter(previous.get("outcome_distribution", {}))
        dominant_words = set(previous.get("dominant_failure_words", []))
        task_types = previous.get("task_types", [])

    lessons: list[str] = []

    success_rate = (outcomes.get("success", 0) / total * 100) if total > 0 else 0.0

    if success_rate < 50 and total > 0:
        lessons.append(f"Overall success rate is low ({success_rate:.0f}%) — review failure patterns")

    if len(dominant_words) > 5:
        lessons.append(f"Recurring failure themes: {', '.join(sorted(dominant_words)[:5])}")

    if len(task_types) > 3:
        lessons.append(f"Broad task diversity ({len(task_types)} types) — consider specializing problem areas")

    if total < 10:
        lessons.append("Insufficient data for robust insights — continue accumulating episodes")

    return {
        "total_episodes_analyzed": total,
        "insights": lessons,
        "recommendation_priority": "high" if success_rate < 50 else "medium" if success_rate < 80 else "low",
        "suggested_focus_areas": sorted(dominant_words)[:5],
        "generated_at": datetime.now(UTC).isoformat(),
    }
