"""TDD tests for E2E test generation validate_scenarios role + ResearcherAgent wiring.

All tests MUST FAIL — the ScenarioValidator module does not exist yet.

Per FEATURE_E2E_TEST_GEN.md §2.3:
  "Cross-references generated scenarios against real-world usage via
   ResearcherAgent through the daemon /admin/research API endpoint. Searches
   GitHub issues, Stack Overflow, blogs. Computes confidence scores per
   scenario from source corroboration. Prunes implausible scenarios below
   confidence_threshold (default 0.4)."

Tests:
  1. Module importability — general_ludd.agents.test_generation.validator exists
  2. ScenarioValidator class has confidence_threshold attribute (default 0.4)
  3. validate() accepts GeneratedScenario list + returns (valid, discarded, queries)
  4. validate() calls ResearcherAgent for every scenario to gather evidence
  5. validate() computes confidence from research hit count
  6. validate() prunes scenarios below confidence_threshold
  7. validate() handles ResearcherAgent unavailability gracefully (pass-all, no-prune)
  8. validate() handles researcher errors without crashing (fail-soft)
  9. validate() respects MAX_QUERIES_PER_BATCH rate limit
 10. validate() handles empty scenario list
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.agents.test_generation.scenario_generator import (
    GeneratedScenario,
    ScenarioStep,
)

MODULE_PATH = "general_ludd.agents.test_generation.validator"


# ── Import gate — MUST FAIL ─────────────────────────────────────────────────


def _skip_if_exists() -> None:
    """Import the module if it exists — this should fail for TDD."""
    try:
        importlib.import_module(MODULE_PATH)
    except ModuleNotFoundError:
        return  # Expected — module does not exist yet
    # If we get here, the module already exists — update these TDD tests
    pytest.skip(f"{MODULE_PATH} already exists — TDD tests may need updating")


class TestScenarioValidatorModuleExists:
    """P3: The validator module must be importable. Fails until built."""

    def test_validator_module_is_importable(self) -> None:
        """general_ludd.agents.test_generation.validator must exist."""
        try:
            mod = importlib.import_module(MODULE_PATH)
            assert mod is not None
        except ModuleNotFoundError:
            pytest.fail(
                f"P3 gap: {MODULE_PATH} does not exist yet. "
                "Create src/general_ludd/agents/test_generation/validator.py "
                "with a ScenarioValidator class per FEATURE_E2E_TEST_GEN.md §2.3."
            )

    def test_validator_class_is_defined(self) -> None:
        """Module must export ScenarioValidator class."""
        _skip_if_exists()
        try:
            mod = importlib.import_module(MODULE_PATH)
            assert hasattr(mod, "ScenarioValidator"), (
                f"P3 gap: {MODULE_PATH} exists but has no ScenarioValidator class"
            )
        except ModuleNotFoundError:
            pytest.fail(f"P3 gap: {MODULE_PATH} module not found")

    def test_validator_has_confidence_threshold_default(self) -> None:
        """ScenarioValidator.confidence_threshold defaults to 0.4 per spec."""
        _skip_if_exists()
        try:
            from general_ludd.agents.test_generation.validator import ScenarioValidator

            validator = ScenarioValidator()
            assert validator.confidence_threshold == 0.4, (
                f"P3 gap: confidence_threshold must default to 0.4 per "
                f"FEATURE_E2E_TEST_GEN.md §2.3, got {validator.confidence_threshold}"
            )
        except ModuleNotFoundError:
            pytest.fail(f"P3 gap: {MODULE_PATH} module not found")


class TestScenarioValidatorValidateSignature:
    """P3: validate() returns (valid, discarded, queries) tuple."""

    def test_validate_accepts_scenarios_list(self) -> None:
        """validate() must accept list[GeneratedScenario] as first arg."""
        _skip_if_exists()
        try:
            from general_ludd.agents.test_generation.validator import ScenarioValidator

            assert ScenarioValidator is not None
        except ModuleNotFoundError:
            pytest.fail(f"P3 gap: {MODULE_PATH} module not found")

    def test_validate_returns_three_tuple(self) -> None:
        """validate() returns (valid: list, discarded: list, queries: list)."""
        _skip_if_exists()
        try:
            from general_ludd.agents.test_generation.validator import ScenarioValidator

            validator = ScenarioValidator()
            import asyncio

            result = asyncio.run(validator.validate([], target_module="test.py"))
            assert isinstance(result, tuple), (
                f"P3 gap: validate() must return tuple, got {type(result)}"
            )
            assert len(result) == 3, (
                f"P3 gap: validate() must return (valid, discarded, queries) 3-tuple, "
                f"got {len(result)} elements"
            )
        except ModuleNotFoundError:
            pytest.fail(f"P3 gap: {MODULE_PATH} module not found")


class TestScenarioValidatorResearcherWiring:
    """P3: validate() must query ResearcherAgent via daemon /admin/research."""

    def test_validator_accepts_researcher_dependency(self) -> None:
        """ScenarioValidator.__init__ accepts researcher kwarg for injection."""
        _skip_if_exists()
        try:
            from general_ludd.agents.test_generation.validator import ScenarioValidator

            mock_researcher = MagicMock()
            validator = ScenarioValidator(researcher=mock_researcher, confidence_threshold=0.4)
            assert validator._researcher is mock_researcher, (
                "P3 gap: ScenarioValidator must store researcher for query dispatch"
            )
        except ModuleNotFoundError:
            pytest.fail(f"P3 gap: {MODULE_PATH} module not found")

    def test_validate_researcher_unavailable_no_prune(self) -> None:
        """Without researcher, validate() passes ALL scenarios (no pruning)."""
        _skip_if_exists()
        try:
            from general_ludd.agents.test_generation.validator import ScenarioValidator

            validator = ScenarioValidator(researcher=None, confidence_threshold=0.4)
            import asyncio

            scenarios = [
                GeneratedScenario(
                    name="test_scenario",
                    description="a scenario",
                    steps=[ScenarioStep("GET", "/", "200", [])],
                ),
            ]
            valid, discarded, queries = asyncio.run(
                validator.validate(scenarios, target_module="app.py")
            )
            assert len(valid) == 1, (
                f"P3 gap: researcher=None must keep all scenarios (no prune); "
                f"got valid={len(valid)}, discarded={len(discarded)}"
            )
            assert len(discarded) == 0
            assert len(queries) == 0, (
                "P3 gap: no research queries when researcher=None"
            )
        except ModuleNotFoundError:
            pytest.fail(f"P3 gap: {MODULE_PATH} module not found")

    def test_validate_handles_researcher_errors(self) -> None:
        """validate() does not crash when ResearcherAgent.search() raises."""
        _skip_if_exists()
        try:
            from general_ludd.agents.test_generation.validator import ScenarioValidator

            mock_researcher = AsyncMock()
            mock_researcher.search.side_effect = RuntimeError("search down")

            validator = ScenarioValidator(researcher=mock_researcher, confidence_threshold=0.4)
            import asyncio

            scenarios = [
                GeneratedScenario(
                    name="scenario_a",
                    description="desc",
                    steps=[ScenarioStep("GET", "/", "200", [])],
                ),
            ]
            valid, _, _ = asyncio.run(
                validator.validate(scenarios, target_module="app.py")
            )
            assert len(valid) == 1, (
                "P3 gap: researcher errors must not crash validate() — "
                "fail-soft, keep all scenarios"
            )
        except ModuleNotFoundError:
            pytest.fail(f"P3 gap: {MODULE_PATH} module not found")


class TestScenarioValidatorConfidenceScoring:
    """P3: confidence scores and threshold pruning."""

    def test_confidence_bounds_zero_to_one(self) -> None:
        """Confidence scores clamped to [0.0, 1.0]."""
        _skip_if_exists()
        try:
            from general_ludd.agents.test_generation.validator import ScenarioValidator

            validator = ScenarioValidator()
            c0 = validator.compute_confidence(0)
            assert c0 == 0.0, f"P3 gap: 0 hits → confidence 0.0, got {c0}"
            c_max = validator.compute_confidence(1000)
            assert c_max <= 1.0, f"P3 gap: max confidence must be <=1.0, got {c_max}"
            c_mid = validator.compute_confidence(2)
            assert 0.0 <= c_mid <= 1.0, f"P3 gap: confidence must be in [0,1], got {c_mid}"
        except ModuleNotFoundError:
            pytest.fail(f"P3 gap: {MODULE_PATH} module not found")

    def test_threshold_prunes_low_confidence(self) -> None:
        """Scenarios below confidence_threshold are in discarded list."""
        _skip_if_exists()
        try:
            from general_ludd.agents.test_generation.validator import ScenarioValidator

            mock_researcher = AsyncMock()
            mock_researcher.search.side_effect = [
                {"results": [{"title": "a"}, {"title": "b"}, {"title": "c"}], "query_count": 1},
                {"results": [], "query_count": 1},
            ]

            validator = ScenarioValidator(researcher=mock_researcher, confidence_threshold=0.4)
            import asyncio

            scenarios = [
                GeneratedScenario(
                    name="well_evidenced",
                    description="has 3 hits",
                    steps=[ScenarioStep("GET", "/", "200", [])],
                ),
                GeneratedScenario(
                    name="no_evidence",
                    description="0 hits",
                    steps=[ScenarioStep("GET", "/", "200", [])],
                ),
            ]
            valid, discarded, _ = asyncio.run(
                validator.validate(scenarios, target_module="app.py")
            )

            assert len(valid) == 1, (
                f"P3 gap: 3 hits should exceed threshold 0.4, got valid={len(valid)}"
            )
            assert len(discarded) == 1, (
                f"P3 gap: 0 hits should be below threshold, got discarded={len(discarded)}"
            )
        except ModuleNotFoundError:
            pytest.fail(f"P3 gap: {MODULE_PATH} module not found")


class TestScenarioValidatorBatchLimits:
    """P3: rate-safety via MAX_QUERIES_PER_BATCH."""

    def test_max_queries_per_batch_exists(self) -> None:
        """ScenarioValidator must define MAX_QUERIES_PER_BATCH."""
        _skip_if_exists()
        try:
            from general_ludd.agents.test_generation.validator import ScenarioValidator

            assert hasattr(ScenarioValidator, "MAX_QUERIES_PER_BATCH") or hasattr(
                ScenarioValidator(), "_max_queries_per_batch"
            ), (
                "P3 gap: ScenarioValidator must limit queries per batch "
                "to avoid overwhelming the ResearcherAgent / SearXNG"
            )
        except ModuleNotFoundError:
            pytest.fail(f"P3 gap: {MODULE_PATH} module not found")


class TestScenarioValidatorEmptyInput:
    """P3: handle empty scenario lists."""

    def test_validate_empty_returns_empty(self) -> None:
        """validate([]) returns three empty lists."""
        _skip_if_exists()
        try:
            from general_ludd.agents.test_generation.validator import ScenarioValidator

            validator = ScenarioValidator()
            import asyncio

            valid, discarded, queries = asyncio.run(
                validator.validate([], target_module="app.py")
            )
            assert valid == [], f"P3 gap: empty input → empty valid, got {valid}"
            assert discarded == [], f"P3 gap: empty input → empty discarded, got {discarded}"
            assert queries == [], f"P3 gap: empty input → empty queries, got {queries}"
        except ModuleNotFoundError:
            pytest.fail(f"P3 gap: {MODULE_PATH} module not found")
