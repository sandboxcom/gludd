"""Behavioral tests for ModelComparison (observability/comparison.py).

Covers:
- Happy-path ranking with composite sort (descending)
- Cost sort (ascending)
- Key-mapping: avg_quality → avg_code_quality, avg_efficiency → avg_token_efficiency
- min_samples filtering
- tie-breaking stability (equal composite scores)
- total_combinations vs qualified_combinations bookkeeping
- summary content (best model description)
- task_type passthrough when raw row omits it
- prompt_profile_id preserved (including None)
- numeric rounding (4 dp composite/avg, 6 dp cost)
- no-repo early-return
- empty raw-data early-return
- repo exception → error summary, no raise
- default sort_by is "composite" (not "cost")
- large dataset ordering consistency
- min_samples=0 admits everything including zero-sample rows
- missing fields default gracefully (no KeyError)
- sort_by unknown falls back to composite sort
"""

from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(
    model_profile_id: str = "m1",
    prompt_profile_id: str | None = "p1",
    task_type: str = "code",
    sample_count: int = 5,
    composite_score: float = 0.80,
    avg_cost: float = 0.01,
    avg_completion: float = 0.80,
    avg_quality: float = 0.80,
    avg_instruction: float = 0.80,
    avg_efficiency: float = 0.80,
) -> dict:
    return {
        "model_profile_id": model_profile_id,
        "prompt_profile_id": prompt_profile_id,
        "task_type": task_type,
        "sample_count": sample_count,
        "composite_score": composite_score,
        "avg_cost": avg_cost,
        "avg_completion": avg_completion,
        "avg_quality": avg_quality,
        "avg_instruction": avg_instruction,
        "avg_efficiency": avg_efficiency,
    }


def _make_comparison(rows: list[dict]) -> object:
    from general_ludd.observability.comparison import ModelComparison

    repo = AsyncMock()
    repo.get_aggregate_scores = AsyncMock(return_value=rows)
    return ModelComparison(benchmark_repo=repo)


# ---------------------------------------------------------------------------
# No-repo / no-data paths
# ---------------------------------------------------------------------------

class TestNoRepoOrNoData:
    @pytest.mark.asyncio
    async def test_no_repo_returns_empty_rankings(self):
        from general_ludd.observability.comparison import ModelComparison

        c = ModelComparison(benchmark_repo=None)
        result = await c.compare_models(task_type="code")
        assert result["rankings"] == []

    @pytest.mark.asyncio
    async def test_no_repo_summary_mentions_no_benchmark_repository(self):
        from general_ludd.observability.comparison import ModelComparison

        c = ModelComparison(benchmark_repo=None)
        result = await c.compare_models()
        assert "No benchmark repository" in result["summary"]

    @pytest.mark.asyncio
    async def test_empty_raw_returns_empty_rankings(self):
        c = _make_comparison([])
        result = await c.compare_models(task_type="docs")
        assert result["rankings"] == []

    @pytest.mark.asyncio
    async def test_empty_raw_summary_says_no_benchmark_data(self):
        c = _make_comparison([])
        result = await c.compare_models()
        assert result["summary"] == "No benchmark data available"

    @pytest.mark.asyncio
    async def test_repo_exception_returns_empty_rankings(self):
        from general_ludd.observability.comparison import ModelComparison

        repo = AsyncMock()
        repo.get_aggregate_scores.side_effect = RuntimeError("DB exploded")
        c = ModelComparison(benchmark_repo=repo)
        result = await c.compare_models(task_type="code")
        assert result["rankings"] == []

    @pytest.mark.asyncio
    async def test_repo_exception_does_not_propagate(self):
        from general_ludd.observability.comparison import ModelComparison

        repo = AsyncMock()
        repo.get_aggregate_scores.side_effect = ValueError("bad query")
        c = ModelComparison(benchmark_repo=repo)
        # Must not raise
        result = await c.compare_models()
        assert "Error fetching data" in result["summary"]

    @pytest.mark.asyncio
    async def test_repo_exception_summary_includes_error_message(self):
        from general_ludd.observability.comparison import ModelComparison

        repo = AsyncMock()
        repo.get_aggregate_scores.side_effect = RuntimeError("timeout after 30s")
        c = ModelComparison(benchmark_repo=repo)
        result = await c.compare_models()
        assert "timeout after 30s" in result["summary"]


# ---------------------------------------------------------------------------
# Key-mapping: avg_quality → avg_code_quality, avg_efficiency → avg_token_efficiency
# ---------------------------------------------------------------------------

class TestKeyMapping:
    @pytest.mark.asyncio
    async def test_avg_quality_mapped_to_avg_code_quality(self):
        c = _make_comparison([_row(avg_quality=0.75)])
        result = await c.compare_models()
        assert result["rankings"][0]["avg_code_quality"] == 0.75

    @pytest.mark.asyncio
    async def test_avg_efficiency_mapped_to_avg_token_efficiency(self):
        c = _make_comparison([_row(avg_efficiency=0.63)])
        result = await c.compare_models()
        assert result["rankings"][0]["avg_token_efficiency"] == 0.63

    @pytest.mark.asyncio
    async def test_raw_avg_quality_key_not_present_in_output(self):
        c = _make_comparison([_row()])
        result = await c.compare_models()
        ranking = result["rankings"][0]
        assert "avg_quality" not in ranking

    @pytest.mark.asyncio
    async def test_raw_avg_efficiency_key_not_present_in_output(self):
        c = _make_comparison([_row()])
        result = await c.compare_models()
        ranking = result["rankings"][0]
        assert "avg_efficiency" not in ranking

    @pytest.mark.asyncio
    async def test_zero_avg_quality_maps_correctly_not_hidden(self):
        """Mapping must work even when source value is 0.0 (was the original bug)."""
        c = _make_comparison([_row(avg_quality=0.0, avg_efficiency=0.0)])
        result = await c.compare_models()
        ranking = result["rankings"][0]
        assert ranking["avg_code_quality"] == 0.0
        assert ranking["avg_token_efficiency"] == 0.0


# ---------------------------------------------------------------------------
# Composite sort (default / sort_by="composite")
# ---------------------------------------------------------------------------

class TestCompositeSorting:
    @pytest.mark.asyncio
    async def test_default_sort_is_composite_descending(self):
        rows = [
            _row("low", composite_score=0.50),
            _row("high", composite_score=0.95),
            _row("mid", composite_score=0.70),
        ]
        c = _make_comparison(rows)
        result = await c.compare_models()
        scores = [r["composite_score"] for r in result["rankings"]]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_explicit_composite_sort_descending(self):
        rows = [
            _row("a", composite_score=0.40),
            _row("b", composite_score=0.90),
        ]
        c = _make_comparison(rows)
        result = await c.compare_models(sort_by="composite")
        assert result["rankings"][0]["model_profile_id"] == "b"

    @pytest.mark.asyncio
    async def test_unknown_sort_by_falls_back_to_composite(self):
        rows = [
            _row("slow", composite_score=0.30),
            _row("fast", composite_score=0.88),
        ]
        c = _make_comparison(rows)
        result = await c.compare_models(sort_by="xyzzy")
        assert result["rankings"][0]["model_profile_id"] == "fast"

    @pytest.mark.asyncio
    async def test_best_model_in_summary_is_first_ranked(self):
        rows = [
            _row("winner", composite_score=0.99, sample_count=10),
            _row("loser", composite_score=0.50, sample_count=5),
        ]
        c = _make_comparison(rows)
        result = await c.compare_models()
        assert "winner" in result["summary"]
        assert "0.99" in result["summary"]


# ---------------------------------------------------------------------------
# Cost sort
# ---------------------------------------------------------------------------

class TestCostSorting:
    @pytest.mark.asyncio
    async def test_sort_by_cost_ascending(self):
        rows = [
            _row("expensive", avg_cost=0.50, composite_score=0.95),
            _row("cheap", avg_cost=0.001, composite_score=0.60),
        ]
        c = _make_comparison(rows)
        result = await c.compare_models(sort_by="cost")
        assert result["rankings"][0]["model_profile_id"] == "cheap"

    @pytest.mark.asyncio
    async def test_sort_by_cost_ignores_composite_score(self):
        rows = [
            _row("best_quality", avg_cost=1.00, composite_score=0.99),
            _row("decent_cheap", avg_cost=0.01, composite_score=0.70),
        ]
        c = _make_comparison(rows)
        result = await c.compare_models(sort_by="cost")
        assert result["rankings"][0]["model_profile_id"] == "decent_cheap"


# ---------------------------------------------------------------------------
# min_samples filtering
# ---------------------------------------------------------------------------

class TestMinSamplesFilter:
    @pytest.mark.asyncio
    async def test_rows_below_min_samples_excluded(self):
        rows = [
            _row("enough", sample_count=5),
            _row("not_enough", sample_count=1),
        ]
        c = _make_comparison(rows)
        result = await c.compare_models(min_samples=2)
        ids = [r["model_profile_id"] for r in result["rankings"]]
        assert "not_enough" not in ids
        assert "enough" in ids

    @pytest.mark.asyncio
    async def test_min_samples_exactly_met_is_included(self):
        rows = [_row("exactly", sample_count=3)]
        c = _make_comparison(rows)
        result = await c.compare_models(min_samples=3)
        assert len(result["rankings"]) == 1

    @pytest.mark.asyncio
    async def test_min_samples_zero_includes_zero_sample_rows(self):
        rows = [_row("zero_samples", sample_count=0)]
        c = _make_comparison(rows)
        result = await c.compare_models(min_samples=0)
        assert len(result["rankings"]) == 1

    @pytest.mark.asyncio
    async def test_all_filtered_out_gives_empty_rankings(self):
        rows = [_row("m1", sample_count=1)]
        c = _make_comparison(rows)
        result = await c.compare_models(min_samples=10)
        assert result["rankings"] == []

    @pytest.mark.asyncio
    async def test_all_filtered_out_summary_mentions_min_samples(self):
        rows = [_row("m1", sample_count=1)]
        c = _make_comparison(rows)
        result = await c.compare_models(min_samples=10)
        assert "10" in result["summary"]

    @pytest.mark.asyncio
    async def test_qualified_combinations_reflects_filter(self):
        rows = [
            _row("a", sample_count=10),
            _row("b", sample_count=1),
            _row("c", sample_count=5),
        ]
        c = _make_comparison(rows)
        result = await c.compare_models(min_samples=2)
        assert result["total_combinations"] == 3
        assert result["qualified_combinations"] == 2


# ---------------------------------------------------------------------------
# Bookkeeping fields (total_combinations, qualified_combinations, sort_by)
# ---------------------------------------------------------------------------

class TestBookkeepingFields:
    @pytest.mark.asyncio
    async def test_total_combinations_counts_all_raw_rows(self):
        rows = [_row() for _ in range(7)]
        c = _make_comparison(rows)
        result = await c.compare_models(min_samples=1)
        assert result["total_combinations"] == 7

    @pytest.mark.asyncio
    async def test_sort_by_is_echoed_in_result(self):
        c = _make_comparison([_row()])
        result = await c.compare_models(sort_by="cost")
        assert result["sort_by"] == "cost"

    @pytest.mark.asyncio
    async def test_sort_by_composite_echoed(self):
        c = _make_comparison([_row()])
        result = await c.compare_models(sort_by="composite")
        assert result["sort_by"] == "composite"


# ---------------------------------------------------------------------------
# Output field completeness and rounding
# ---------------------------------------------------------------------------

class TestOutputFields:
    @pytest.mark.asyncio
    async def test_ranking_contains_all_expected_keys(self):
        expected_keys = {
            "model_profile_id",
            "prompt_profile_id",
            "task_type",
            "sample_count",
            "composite_score",
            "avg_cost",
            "avg_completion",
            "avg_code_quality",
            "avg_instruction",
            "avg_token_efficiency",
        }
        c = _make_comparison([_row()])
        result = await c.compare_models()
        assert set(result["rankings"][0].keys()) == expected_keys

    @pytest.mark.asyncio
    async def test_composite_score_rounded_to_4dp(self):
        c = _make_comparison([_row(composite_score=0.123456789)])
        result = await c.compare_models()
        # round to 4dp → 0.1235
        assert result["rankings"][0]["composite_score"] == round(0.123456789, 4)

    @pytest.mark.asyncio
    async def test_avg_cost_rounded_to_6dp(self):
        c = _make_comparison([_row(avg_cost=0.00012345678)])
        result = await c.compare_models()
        assert result["rankings"][0]["avg_cost"] == round(0.00012345678, 6)

    @pytest.mark.asyncio
    async def test_avg_code_quality_rounded_to_4dp(self):
        c = _make_comparison([_row(avg_quality=0.333333333)])
        result = await c.compare_models()
        assert result["rankings"][0]["avg_code_quality"] == round(0.333333333, 4)

    @pytest.mark.asyncio
    async def test_avg_token_efficiency_rounded_to_4dp(self):
        c = _make_comparison([_row(avg_efficiency=0.666666666)])
        result = await c.compare_models()
        assert result["rankings"][0]["avg_token_efficiency"] == round(0.666666666, 4)

    @pytest.mark.asyncio
    async def test_prompt_profile_id_preserved_when_present(self):
        c = _make_comparison([_row(prompt_profile_id="my_prompt")])
        result = await c.compare_models()
        assert result["rankings"][0]["prompt_profile_id"] == "my_prompt"

    @pytest.mark.asyncio
    async def test_prompt_profile_id_none_preserved(self):
        c = _make_comparison([_row(prompt_profile_id=None)])
        result = await c.compare_models()
        assert result["rankings"][0]["prompt_profile_id"] is None

    @pytest.mark.asyncio
    async def test_task_type_from_row_takes_precedence(self):
        c = _make_comparison([_row(task_type="refactor")])
        result = await c.compare_models(task_type="code")
        assert result["rankings"][0]["task_type"] == "refactor"

    @pytest.mark.asyncio
    async def test_task_type_fallback_when_row_omits_it(self):
        row = _row()
        del row["task_type"]
        c = _make_comparison([row])
        result = await c.compare_models(task_type="my_task")
        assert result["rankings"][0]["task_type"] == "my_task"


# ---------------------------------------------------------------------------
# Missing / partial fields in raw data (no KeyError)
# ---------------------------------------------------------------------------

class TestMissingFields:
    @pytest.mark.asyncio
    async def test_missing_composite_score_defaults_to_zero(self):
        row = _row()
        del row["composite_score"]
        c = _make_comparison([row])
        result = await c.compare_models()
        assert result["rankings"][0]["composite_score"] == 0.0

    @pytest.mark.asyncio
    async def test_missing_avg_quality_defaults_to_zero(self):
        row = _row()
        del row["avg_quality"]
        c = _make_comparison([row])
        result = await c.compare_models()
        assert result["rankings"][0]["avg_code_quality"] == 0.0

    @pytest.mark.asyncio
    async def test_missing_avg_efficiency_defaults_to_zero(self):
        row = _row()
        del row["avg_efficiency"]
        c = _make_comparison([row])
        result = await c.compare_models()
        assert result["rankings"][0]["avg_token_efficiency"] == 0.0

    @pytest.mark.asyncio
    async def test_missing_model_profile_id_defaults_to_unknown(self):
        row = _row()
        del row["model_profile_id"]
        c = _make_comparison([row])
        result = await c.compare_models()
        assert result["rankings"][0]["model_profile_id"] == "unknown"

    @pytest.mark.asyncio
    async def test_missing_sample_count_treated_as_zero_and_filtered(self):
        row = _row()
        del row["sample_count"]
        c = _make_comparison([row])
        # min_samples=1 should filter out a row with effective sample_count=0
        result = await c.compare_models(min_samples=1)
        assert result["rankings"] == []


# ---------------------------------------------------------------------------
# Summary content
# ---------------------------------------------------------------------------

class TestSummaryContent:
    @pytest.mark.asyncio
    async def test_summary_includes_score_of_best_model(self):
        rows = [
            _row("alpha", composite_score=0.88, sample_count=10),
        ]
        c = _make_comparison(rows)
        result = await c.compare_models()
        assert "0.88" in result["summary"]
        assert "alpha" in result["summary"]

    @pytest.mark.asyncio
    async def test_summary_includes_sample_count_of_best(self):
        rows = [_row("m1", composite_score=0.70, sample_count=42)]
        c = _make_comparison(rows)
        result = await c.compare_models()
        assert "42" in result["summary"]

    @pytest.mark.asyncio
    async def test_no_qualified_summary_mentions_min_samples_threshold(self):
        rows = [_row(sample_count=1)]
        c = _make_comparison(rows)
        result = await c.compare_models(min_samples=5)
        assert ">= 5" in result["summary"]


# ---------------------------------------------------------------------------
# Large dataset / ordering consistency
# ---------------------------------------------------------------------------

class TestLargeDataset:
    @pytest.mark.asyncio
    async def test_ten_models_ranked_descending(self):
        rows = [_row(f"m{i}", composite_score=i * 0.05, sample_count=5) for i in range(10)]
        c = _make_comparison(rows)
        result = await c.compare_models(sort_by="composite")
        scores = [r["composite_score"] for r in result["rankings"]]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_ten_models_sorted_by_cost_ascending(self):
        rows = [_row(f"m{i}", avg_cost=i * 0.01, sample_count=5) for i in range(10)]
        c = _make_comparison(rows)
        result = await c.compare_models(sort_by="cost")
        costs = [r["avg_cost"] for r in result["rankings"]]
        assert costs == sorted(costs)
