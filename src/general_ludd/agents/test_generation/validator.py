"""Scenario validation for generated E2E test plans.

The validator corroborates generated scenarios through an injected researcher
dependency when available. Research failures are fail-open: a missing or broken
research backend must not discard generated test coverage.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from .scenario_generator import GeneratedScenario

logger = logging.getLogger(__name__)


class ScenarioValidator:
    """Validate generated scenarios against real-world research evidence."""

    MAX_QUERIES_PER_BATCH = 50

    def __init__(
        self,
        *,
        researcher: Any | None = None,
        confidence_threshold: float = 0.4,
        max_queries_per_batch: int | None = None,
    ) -> None:
        self._researcher = researcher
        self.confidence_threshold = confidence_threshold
        self._max_queries_per_batch = (
            max_queries_per_batch
            if max_queries_per_batch is not None
            else self.MAX_QUERIES_PER_BATCH
        )

    async def validate(
        self,
        scenarios: list[GeneratedScenario],
        *,
        target_module: str,
    ) -> tuple[list[GeneratedScenario], list[GeneratedScenario], list[str]]:
        """Return ``(valid, discarded, research_queries)`` for ``scenarios``.

        If no researcher is configured, or if research raises, every scenario is
        retained. This keeps the generation pipeline usable in offline CI.
        """
        if not scenarios:
            return [], [], []

        if self._researcher is None:
            return list(scenarios), [], []

        queries = [
            self._query_for_scenario(scenario, target_module=target_module)
            for scenario in scenarios[: self._max_queries_per_batch]
        ]

        valid: list[GeneratedScenario] = []
        discarded: list[GeneratedScenario] = []

        try:
            for scenario, query in zip(scenarios, queries, strict=False):
                report = await self._dispatch_research(query)
                confidence = self.compute_confidence(self._hit_count(report))
                if confidence >= self.confidence_threshold:
                    valid.append(scenario)
                else:
                    discarded.append(scenario)
        except Exception:
            logger.exception("scenario research validation failed; keeping scenarios")
            return list(scenarios), [], queries

        if len(scenarios) > len(queries):
            valid.extend(scenarios[len(queries):])

        return valid, discarded, queries

    def compute_confidence(self, hit_count: int) -> float:
        """Map corroborating result count to a clamped confidence score."""
        if hit_count <= 0:
            return 0.0
        return round(min(1.0, max(0.0, hit_count / 5.0)), 2)

    def _query_for_scenario(
        self,
        scenario: GeneratedScenario,
        *,
        target_module: str,
    ) -> str:
        targets = " ".join(scenario.coverage_targets)
        return (
            f'how is "{scenario.name}" tested in production e2e test patterns '
            f"{target_module} {targets}".strip()
        )

    async def _dispatch_research(self, query: str) -> Any:
        researcher = self._researcher
        if researcher is None:
            return None
        if hasattr(researcher, "search"):
            result = researcher.search(query)
        elif hasattr(researcher, "research"):
            result = researcher.research(
                query,
                categories=["general", "it"],
                time_range="year",
                max_results=10,
            )
        else:
            return None

        if inspect.isawaitable(result):
            return await result
        return result

    def _hit_count(self, report: Any) -> int:
        if report is None:
            return 0
        if isinstance(report, dict):
            if isinstance(report.get("results"), list):
                return len(report["results"])
            if isinstance(report.get("findings"), list):
                return len(report["findings"])
            reports = report.get("reports")
            if isinstance(reports, list):
                return sum(self._hit_count(item) for item in reports)

        findings = getattr(report, "findings", None)
        if isinstance(findings, list):
            return len(findings)
        results = getattr(report, "results", None)
        if isinstance(results, list):
            return len(results)
        return 0


__all__ = ["ScenarioValidator"]
