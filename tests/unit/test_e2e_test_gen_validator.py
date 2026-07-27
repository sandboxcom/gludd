"""Unit tests for ScenarioValidator — async validation of generated E2E scenarios."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from general_ludd.agents.test_generation.scenario_generator import GeneratedScenario, ScenarioStep
from general_ludd.agents.test_generation.validator import ScenarioValidator


def _make_scenario(
    name: str = "crud_lifecycle",
    description: str = "test scenario",
    coverage_targets: list[str] | None = None,
    step_count: int = 3,
) -> GeneratedScenario:
    steps = [
        ScenarioStep(
            action=f"step_{i}",
            target=f"/api/{name}",
            expected_result=f"expected {i}",
            assertions=[f"assert_{i}"],
        )
        for i in range(step_count)
    ]
    return GeneratedScenario(
        name=name,
        description=description,
        steps=steps,
        coverage_targets=coverage_targets or [f"fn_{i}" for i in range(step_count)],
    )


async def _validate_sync_helper(
    validator: ScenarioValidator,
    scenarios: list[GeneratedScenario],
    module: str,
) -> tuple[list[GeneratedScenario], list[GeneratedScenario], list[str]]:
    return await validator.validate(scenarios, target_module=module)


def _run(
    validator: ScenarioValidator,
    scenarios: list[GeneratedScenario],
    module: str = "test_module",
) -> tuple[list[GeneratedScenario], list[GeneratedScenario], list[str]]:
    return asyncio.run(_validate_sync_helper(validator, scenarios, module))


class TestScenarioValidatorBasics:
    def test_no_researcher_returns_all_scenarios(self):
        validator = ScenarioValidator(researcher=None)
        scenarios = [_make_scenario("crud_lifecycle"), _make_scenario("auth_flow")]
        valid, discarded, queries = _run(validator, scenarios)
        assert len(valid) == len(scenarios) and all(s in scenarios for s in valid)
        assert discarded == []
        assert queries == []

    def test_no_scenarios_returns_empty(self):
        validator = ScenarioValidator(researcher=None)
        valid, discarded, queries = _run(validator, [])
        assert valid == []
        assert discarded == []
        assert queries == []

    def test_researcher_validate_returns_all_when_exception(self):
        class _BogusResearcher:
            def research(self, query, categories=None, time_range=None, max_results=None):
                raise RuntimeError("research broken")

        bogus = _BogusResearcher()
        validator = ScenarioValidator(researcher=bogus)
        scenarios = [_make_scenario("crud")]
        valid, discarded, queries = _run(validator, scenarios)
        assert valid == scenarios
        assert discarded == []
        assert len(queries) == 1


class TestComputeConfidence:
    def test_zero_hit_count(self):
        assert ScenarioValidator().compute_confidence(0) == 0.0

    def test_negative_hit_count(self):
        assert ScenarioValidator().compute_confidence(-1) == 0.0

    def test_one_hit(self):
        assert ScenarioValidator().compute_confidence(1) == 0.2

    def test_five_hits_equals_one(self):
        assert ScenarioValidator().compute_confidence(5) == 1.0

    def test_ten_hits_clamped(self):
        assert ScenarioValidator().compute_confidence(10) == 1.0

    def test_three_hits_intermediate(self):
        assert ScenarioValidator().compute_confidence(3) == 0.6


class TestQueryForScenario:
    def test_query_includes_scenario_name_and_targets(self):
        validator = ScenarioValidator()
        scenario = _make_scenario(name="crud_lifecycle", coverage_targets=["create_resource", "delete_resource"])
        query = validator._query_for_scenario(scenario, target_module="foo.py")
        assert "crud_lifecycle" in query
        assert "create_resource" in query
        assert "delete_resource" in query
        assert "foo.py" in query

    def test_query_includes_e2e_pattern(self):
        validator = ScenarioValidator()
        scenario = _make_scenario(name="auth_flow", coverage_targets=["login"])
        query = validator._query_for_scenario(scenario, target_module="bar.py")
        assert "e2e test patterns" in query
        assert "auth_flow" in query
        assert "bar.py" in query


class TestHitCount:
    def test_none_is_zero(self):
        assert ScenarioValidator()._hit_count(None) == 0

    def test_empty_dict_is_zero(self):
        assert ScenarioValidator()._hit_count({}) == 0

    def test_results_list_length(self):
        assert ScenarioValidator()._hit_count({"results": [1, 2, 3]}) == 3

    def test_findings_list_length(self):
        assert ScenarioValidator()._hit_count({"findings": [{"a": 1}, {"b": 2}]}) == 2

    def test_reports_total(self):
        report = {"reports": [{"findings": [1, 2]}, {"findings": [3, 4, 5]}]}
        assert ScenarioValidator()._hit_count(report) == 5

    def test_object_with_findings_attr(self):
        obj = type("Research", (), {"findings": [1, 2, 3, 4]})()
        assert ScenarioValidator()._hit_count(obj) == 4

    def test_object_with_results_attr(self):
        obj = type("Research", (), {"results": [1, 2]})()
        assert ScenarioValidator()._hit_count(obj) == 2


class TestDispatchResearch:
    def test_search_method_dispatched(self):
        mock = MagicMock()
        mock.search.return_value = {"results": [1, 2]}
        validator = ScenarioValidator(researcher=mock)
        result = asyncio.run(validator._dispatch_research("test query"))
        assert result == {"results": [1, 2]}

    def test_research_method_dispatched(self):
        class _ResearchOnly:
            def research(self, query, categories=None, time_range=None, max_results=None):
                return {"findings": [1, 2, 3]}

        mock = _ResearchOnly()
        validator = ScenarioValidator(researcher=mock)
        result = asyncio.run(validator._dispatch_research("test query"))
        assert result == {"findings": [1, 2, 3]}

    def test_no_research_method_returns_none(self):
        mock = MagicMock(spec=[])
        validator = ScenarioValidator(researcher=mock)
        result = asyncio.run(validator._dispatch_research("test query"))
        assert result is None

    def test_awaits_async_result(self):
        mock = MagicMock()

        async def _async_search(query):
            return {"results": [1]}

        mock.search = _async_search
        validator = ScenarioValidator(researcher=mock)
        result = asyncio.run(validator._dispatch_research("test query"))
        assert result == {"results": [1]}

    def test_research_null_guard(self):
        validator = ScenarioValidator(researcher=None)
        result = asyncio.run(validator._dispatch_research("test query"))
        assert result is None


class TestMaxQueriesPerBatch:
    def test_default_limit(self):
        validator = ScenarioValidator()
        assert validator._max_queries_per_batch == 50

    def test_custom_limit(self):
        validator = ScenarioValidator(max_queries_per_batch=5)
        assert validator._max_queries_per_batch == 5

    def test_batch_caps_queries_when_researcher_present(self):
        researcher = MagicMock()
        researcher.search.return_value = {"findings": [1, 2, 3, 4, 5, 6]}  # 6 findings → hit_count=6 → confidence=1.0
        validator = ScenarioValidator(researcher=researcher, max_queries_per_batch=2)
        scenarios = [_make_scenario(f"scenario_{i}") for i in range(5)]
        valid, _discarded, queries = _run(validator, scenarios)
        # only first 2 get queries; remaining 3 appended without checking
        assert len(valid) >= 2
        assert len(queries) == 2


class TestConfidenceThreshold:
    def test_default_threshold_is_0_4(self):
        validator = ScenarioValidator()
        assert validator.confidence_threshold == 0.4

    def test_custom_threshold(self):
        validator = ScenarioValidator(confidence_threshold=0.8)
        assert validator.confidence_threshold == 0.8

    def test_low_confidence_below_threshold_filtered(self):
        researcher = MagicMock()

        def _search(query):
            if "crud" in query:
                return {"findings": [1]}  # hit_count=1 → confidence=0.2
            return {"findings": [1, 2, 3, 4, 5]}  # hit_count=5 → confidence=1.0

        researcher.search = _search
        validator = ScenarioValidator(researcher=researcher, confidence_threshold=0.6)
        scenarios = [
            _make_scenario("crud_lifecycle"),
            _make_scenario("auth_flow"),
        ]
        valid, discarded, _queries = _run(validator, scenarios)
        assert len(valid) == 1
        assert valid[0].name == "auth_flow"
        assert len(discarded) == 1
        assert discarded[0].name == "crud_lifecycle"
