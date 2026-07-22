"""Focused tests for generated-scenario research validation."""

from __future__ import annotations

import pytest

from general_ludd.agents.test_generation.scenario_generator import (
    GeneratedScenario,
    ScenarioStep,
)
from general_ludd.agents.test_generation.validator import ScenarioValidator


def _scenario(name: str, target: str = "create_user") -> GeneratedScenario:
    return GeneratedScenario(
        name=name,
        description=f"{name} validation scenario",
        steps=[ScenarioStep("GET", "/", "200 OK", ["response.status_code == 200"])],
        coverage_targets=[target],
    )


@pytest.mark.asyncio
async def test_validator_keeps_all_scenarios_without_researcher() -> None:
    validator = ScenarioValidator(researcher=None)
    scenarios = [_scenario("crud_lifecycle")]

    valid, discarded, queries = await validator.validate(scenarios, target_module="app.py")

    assert valid == scenarios
    assert discarded == []
    assert queries == []


@pytest.mark.asyncio
async def test_validator_prunes_low_confidence_reports() -> None:
    class Researcher:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, query: str) -> dict[str, object]:
            self.queries.append(query)
            if "well_evidenced" in query:
                return {"results": [{"title": "a"}, {"title": "b"}, {"title": "c"}]}
            return {"results": []}

    researcher = Researcher()
    validator = ScenarioValidator(researcher=researcher, confidence_threshold=0.4)
    scenarios = [_scenario("well_evidenced"), _scenario("thin_evidence")]

    valid, discarded, queries = await validator.validate(scenarios, target_module="service.py")

    assert [scenario.name for scenario in valid] == ["well_evidenced"]
    assert [scenario.name for scenario in discarded] == ["thin_evidence"]
    assert queries == researcher.queries
    assert "service.py create_user" in queries[0]


@pytest.mark.asyncio
async def test_validator_respects_batch_limit_and_keeps_unqueried_tail() -> None:
    class Researcher:
        def __init__(self) -> None:
            self.count = 0

        async def research(self, query: str, **_: object) -> dict[str, object]:
            self.count += 1
            return {"findings": [{"url": query}, {"url": "second hit"}]}

    researcher = Researcher()
    validator = ScenarioValidator(researcher=researcher, max_queries_per_batch=1)
    scenarios = [_scenario("first"), _scenario("unqueried_tail")]

    valid, discarded, queries = await validator.validate(scenarios, target_module="api.py")

    assert valid == scenarios
    assert discarded == []
    assert len(queries) == 1
    assert researcher.count == 1


def test_validator_confidence_is_clamped_and_rounded() -> None:
    validator = ScenarioValidator()

    assert validator.compute_confidence(-1) == 0.0
    assert validator.compute_confidence(2) == 0.4
    assert validator.compute_confidence(100) == 1.0
