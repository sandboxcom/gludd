"""Cross-task learning — applying lessons from one task to another.

Extracts patterns across all episodic memories and consolidated summaries
to generate actionable insights: which strategies succeed, which failure
patterns recur, and what approach to recommend for a given task type.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)


class CrossTaskLearner:
    """Extracts cross-task patterns and generates recommendations.

    Uses both raw episodic records and consolidated summaries to derive
    insights that span multiple task types.
    """

    def __init__(
        self,
        memory_repo: Any,
        model_gateway: Any | None = None,
    ) -> None:
        self._repo = memory_repo
        self._model_gateway = model_gateway

    async def learn_patterns(self, agent_id: str) -> dict[str, Any]:
        from general_ludd.memory.episodic import EpisodicMemoryRecorder

        recorder = EpisodicMemoryRecorder(self._repo)
        all_episodes = await recorder.list_episodes(agent_id, limit=1000)

        if not all_episodes:
            return {"patterns_found": 0, "total_episodes": 0, "message": "no episodes to learn from"}

        outcomes = Counter(ep.outcome for ep in all_episodes)
        task_types = Counter(ep.task_type for ep in all_episodes)
        total = len(all_episodes)

        success_rate = (outcomes.get("success", 0) / total) * 100 if total > 0 else 0
        failure_rate = (outcomes.get("failure", 0) / total) * 100 if total > 0 else 0

        # Group episodes by task type for per-type analysis
        by_type: dict[str, list[Any]] = {}
        for ep in all_episodes:
            by_type.setdefault(ep.task_type or "unknown", []).append(ep)

        per_type = {}
        for tt, eps in by_type.items():
            t_outcomes = Counter(ep.outcome for ep in eps)
            t_total = len(eps)
            t_success = (t_outcomes.get("success", 0) / t_total) * 100 if t_total > 0 else 0
            t_failures = [ep for ep in eps if ep.outcome == "failure"]
            t_successes = [ep for ep in eps if ep.outcome == "success"]
            per_type[tt] = {
                "total": t_total,
                "success_rate_pct": round(t_success, 1),
                "top_errors": [ep.error_message for ep in t_failures if ep.error_message][:5],
                "best_takeaways": [ep.takeaway for ep in t_successes if ep.takeaway][:5],
            }

        # Cross-type patterns
        all_errors = Counter(
            ep.error_message for ep in all_episodes if ep.error_message and ep.outcome == "failure"
        )
        recurring_errors = [
            {"error": msg, "count": cnt}
            for msg, cnt in all_errors.most_common(10)
        ]

        all_takeaways = Counter(
            ep.takeaway for ep in all_episodes if ep.takeaway and ep.outcome == "success"
        )
        effective_strategies = [
            {"strategy": msg, "count": cnt}
            for msg, cnt in all_takeaways.most_common(10)
        ]

        return {
            "patterns_found": len(recurring_errors) + len(effective_strategies),
            "total_episodes": total,
            "overall_success_rate_pct": round(success_rate, 1),
            "overall_failure_rate_pct": round(failure_rate, 1),
            "outcome_distribution": dict(outcomes),
            "task_type_distribution": dict(task_types),
            "per_type_analysis": per_type,
            "recurring_errors": recurring_errors,
            "effective_strategies": effective_strategies,
        }

    async def recommend_for_task(
        self,
        agent_id: str,
        task_type: str,
        query_context: str = "",
    ) -> dict[str, Any]:
        from general_ludd.memory.retrieval import MemoryRetriever

        retriever = MemoryRetriever(self._repo)
        query = f"{task_type} {query_context}"
        results = await retriever.query(
            agent_id,
            query_text=query,
            task_type=task_type,
            top_k=5,
        )

        successes = [r for r in results if r.episode.outcome == "success"]
        failures = [r for r in results if r.episode.outcome == "failure"]

        recommendations: list[str] = []
        warnings: list[str] = []

        for s in successes[:3]:
            if s.episode.takeaway:
                recommendations.append(s.episode.takeaway)

        for f in failures[:3]:
            if f.episode.error_message:
                warnings.append(f"Previously failed: {f.episode.error_message}")

        # Check consolidated summaries for higher-level insight
        from general_ludd.memory.consolidation import MemoryConsolidator

        consolidator = MemoryConsolidator(self._repo)
        consolidated = await consolidator.get_consolidated(agent_id, task_type=task_type)
        for summary in consolidated:
            error_patterns = summary.get("error_patterns", [])
            for pat in error_patterns[:3]:
                if pat not in warnings:
                    warnings.append(f"Historical pattern: {pat}")
            takeaways = summary.get("key_takeaways", [])
            for t in takeaways[:3]:
                if t not in recommendations:
                    recommendations.append(t)

        return {
            "task_type": task_type,
            "relevant_episodes": len(results),
            "similar_successes": len(successes),
            "similar_failures": len(failures),
            "recommendations": recommendations[:5],
            "warnings": warnings[:5],
            "top_match": results[0].episode.takeaway if results else "",
            "top_match_score": results[0].score if results else 0.0,
        }

    async def generate_improvement_report(self, agent_id: str) -> dict[str, Any]:
        patterns = await self.learn_patterns(agent_id)

        if patterns["total_episodes"] == 0:
            return patterns

        improvements: list[dict[str, Any]] = []

        per_type = patterns.get("per_type_analysis", {})
        for tt, analysis in per_type.items():
            if analysis["success_rate_pct"] < 50 and analysis["total"] >= 3:
                improvements.append({
                    "task_type": tt,
                    "issue": f"Low success rate ({analysis['success_rate_pct']}%)",
                    "evidence": analysis["top_errors"][:3],
                    "suggested_action": "Review failure patterns and strengthen tooling/pre-conditions",
                })

        recurring = patterns.get("recurring_errors", [])
        for err_entry in recurring[:5]:
            if err_entry["count"] >= 2:
                improvements.append({
                    "type": "recurring_error",
                    "error": err_entry["error"],
                    "count": err_entry["count"],
                    "suggested_action": "Add guardrail or pre-check to prevent this error class",
                })

        patterns["improvements_needed"] = improvements
        return patterns
