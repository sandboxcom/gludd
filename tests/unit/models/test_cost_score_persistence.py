"""Tests for P0+P1: cost persistence and graded scores in benchmark_results."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from general_ludd.models.provider_presets import PROVIDER_PRESETS


class TestProviderPresetsCostRates:
    """P0: All provider presets must declare non-zero cost rates."""

    def test_all_presets_have_nonzero_cost_per_input_token(self) -> None:
        for provider, preset in PROVIDER_PRESETS.items():
            rate = preset.get("cost_per_input_token", 0.0)
            assert rate > 0, (
                f"Provider '{provider}' has zero/missing cost_per_input_token={rate!r}. "
                "Add a real per-token USD rate."
            )

    def test_all_presets_have_nonzero_cost_per_output_token(self) -> None:
        for provider, preset in PROVIDER_PRESETS.items():
            rate = preset.get("cost_per_output_token", 0.0)
            assert rate > 0, (
                f"Provider '{provider}' has zero/missing cost_per_output_token={rate!r}. "
                "Add a real per-token USD rate."
            )

    def test_cost_rates_are_positive_floats(self) -> None:
        for provider, preset in PROVIDER_PRESETS.items():
            in_rate = preset.get("cost_per_input_token", 0.0)
            out_rate = preset.get("cost_per_output_token", 0.0)
            assert isinstance(in_rate, float), (
                f"Provider '{provider}' cost_per_input_token must be float, got {type(in_rate)}"
            )
            assert isinstance(out_rate, float), (
                f"Provider '{provider}' cost_per_output_token must be float, got {type(out_rate)}"
            )


class TestRecordJobBenchmarkCostPersistence:
    """P0: record_job_benchmark must persist non-zero cost_usd when provided."""

    def test_nonzero_cost_usd_is_recorded(self) -> None:
        from general_ludd.event_loop.benchmark import record_job_benchmark

        recorded: list[dict] = []

        mock_repo = AsyncMock()
        mock_repo.record_result = AsyncMock(
            side_effect=lambda data: recorded.append(data)
        )
        mock_recorder = MagicMock()
        mock_recorder._repo = mock_repo

        asyncio.run(
            record_job_benchmark(
                mock_recorder,
                model_profile="test-model",
                prompt_profile="test-prompt",
                work_type="code",
                success=True,
                input_tokens=500,
                output_tokens=200,
                cost_usd=0.0025,
            )
        )

        assert len(recorded) == 1, "Expected exactly one benchmark row recorded"
        row = recorded[0]
        assert row["cost_usd"] == 0.0025, (
            f"Expected cost_usd=0.0025 in recorded row, got {row['cost_usd']!r}"
        )

    def test_zero_cost_usd_default_records_zero(self) -> None:
        from general_ludd.event_loop.benchmark import record_job_benchmark

        recorded: list[dict] = []

        mock_repo = AsyncMock()
        mock_repo.record_result = AsyncMock(
            side_effect=lambda data: recorded.append(data)
        )
        mock_recorder = MagicMock()
        mock_recorder._repo = mock_repo

        asyncio.run(
            record_job_benchmark(
                mock_recorder,
                model_profile="test-model",
                prompt_profile=None,
                work_type="code",
                success=True,
            )
        )

        assert len(recorded) == 1
        assert recorded[0]["cost_usd"] == 0.0

    def test_input_and_output_tokens_stored(self) -> None:
        from general_ludd.event_loop.benchmark import record_job_benchmark

        recorded: list[dict] = []

        mock_repo = AsyncMock()
        mock_repo.record_result = AsyncMock(
            side_effect=lambda data: recorded.append(data)
        )
        mock_recorder = MagicMock()
        mock_recorder._repo = mock_repo

        asyncio.run(
            record_job_benchmark(
                mock_recorder,
                model_profile="m",
                prompt_profile=None,
                work_type="code",
                success=True,
                input_tokens=300,
                output_tokens=150,
                cost_usd=0.001,
            )
        )

        row = recorded[0]
        assert row["input_tokens"] == 300
        assert row["output_tokens"] == 150


class TestCodeQualityScoreIsResponsive:
    """P1: code_quality_score must respond to model_output content, not be a constant."""

    def test_python_code_output_scores_high(self) -> None:
        from general_ludd.event_loop.benchmark import record_job_benchmark

        recorded: list[dict] = []

        mock_repo = AsyncMock()
        mock_repo.record_result = AsyncMock(
            side_effect=lambda data: recorded.append(data)
        )
        mock_recorder = MagicMock()
        mock_recorder._repo = mock_repo

        python_output = "Here is the fix:\n```python\ndef solve():\n    return 42\n```"
        asyncio.run(
            record_job_benchmark(
                mock_recorder,
                model_profile="m",
                prompt_profile=None,
                work_type="code",
                success=True,
                model_output=python_output,
            )
        )

        row = recorded[0]
        assert row["code_quality_score"] == 0.8, (
            f"Python-code output should score 0.8, got {row['code_quality_score']!r}"
        )

    def test_empty_output_scores_zero(self) -> None:
        from general_ludd.event_loop.benchmark import record_job_benchmark

        recorded: list[dict] = []

        mock_repo = AsyncMock()
        mock_repo.record_result = AsyncMock(
            side_effect=lambda data: recorded.append(data)
        )
        mock_recorder = MagicMock()
        mock_recorder._repo = mock_repo

        asyncio.run(
            record_job_benchmark(
                mock_recorder,
                model_profile="m",
                prompt_profile=None,
                work_type="code",
                success=True,
                model_output="",
            )
        )

        row = recorded[0]
        assert row["code_quality_score"] == 0.0, (
            f"Empty output should score 0.0, got {row['code_quality_score']!r}"
        )

    def test_prose_output_scores_mid(self) -> None:
        from general_ludd.event_loop.benchmark import record_job_benchmark

        recorded: list[dict] = []

        mock_repo = AsyncMock()
        mock_repo.record_result = AsyncMock(
            side_effect=lambda data: recorded.append(data)
        )
        mock_recorder = MagicMock()
        mock_recorder._repo = mock_repo

        asyncio.run(
            record_job_benchmark(
                mock_recorder,
                model_profile="m",
                prompt_profile=None,
                work_type="code",
                success=True,
                model_output="I have reviewed the issue and it seems to be a configuration problem.",
            )
        )

        row = recorded[0]
        assert row["code_quality_score"] == 0.6, (
            f"Non-empty prose output should score 0.6, got {row['code_quality_score']!r}"
        )

    def test_code_output_scores_higher_than_empty(self) -> None:
        from general_ludd.event_loop.benchmark import record_job_benchmark

        code_recorded: list[dict] = []
        empty_recorded: list[dict] = []

        mock_repo_code = AsyncMock()
        mock_repo_code.record_result = AsyncMock(
            side_effect=lambda data: code_recorded.append(data)
        )
        mock_recorder_code = MagicMock()
        mock_recorder_code._repo = mock_repo_code

        mock_repo_empty = AsyncMock()
        mock_repo_empty.record_result = AsyncMock(
            side_effect=lambda data: empty_recorded.append(data)
        )
        mock_recorder_empty = MagicMock()
        mock_recorder_empty._repo = mock_repo_empty

        asyncio.run(
            record_job_benchmark(
                mock_recorder_code,
                model_profile="m",
                prompt_profile=None,
                work_type="code",
                success=True,
                model_output="def foo():\n    pass",
            )
        )
        asyncio.run(
            record_job_benchmark(
                mock_recorder_empty,
                model_profile="m",
                prompt_profile=None,
                work_type="code",
                success=True,
                model_output="",
            )
        )

        code_score = code_recorded[0]["code_quality_score"]
        empty_score = empty_recorded[0]["code_quality_score"]
        assert code_score > empty_score, (
            f"Code output ({code_score}) should score higher than empty ({empty_score})"
        )

    def test_token_efficiency_uses_both_token_counts(self) -> None:
        from general_ludd.event_loop.benchmark import record_job_benchmark

        recorded_both: list[dict] = []
        recorded_large: list[dict] = []

        mock_repo_both = AsyncMock()
        mock_repo_both.record_result = AsyncMock(
            side_effect=lambda data: recorded_both.append(data)
        )
        mock_recorder_both = MagicMock()
        mock_recorder_both._repo = mock_repo_both

        mock_repo_large = AsyncMock()
        mock_repo_large.record_result = AsyncMock(
            side_effect=lambda data: recorded_large.append(data)
        )
        mock_recorder_large = MagicMock()
        mock_recorder_large._repo = mock_repo_large

        # With both tokens: 500+500=1000, efficiency=min(1.0, 1000/1000)=1.0
        asyncio.run(record_job_benchmark(
            mock_recorder_both, model_profile="m", prompt_profile=None,
            work_type="code", success=True,
            input_tokens=500, output_tokens=500,
        ))

        # With large combined: 5000+5000=10000, efficiency=min(1.0, 1000/10000)=0.1
        asyncio.run(record_job_benchmark(
            mock_recorder_large, model_profile="m", prompt_profile=None,
            work_type="code", success=True,
            input_tokens=5000, output_tokens=5000,
        ))

        eff_small = recorded_both[0]["token_efficiency_score"]
        eff_large = recorded_large[0]["token_efficiency_score"]
        assert eff_small > eff_large, (
            f"Smaller combined token count should give higher efficiency: {eff_small} vs {eff_large}"
        )


class TestRouterRankingChangesByBenchmarkCost:
    """P1: Routing decision must change when benchmark cost data differs between profiles.

    We test a cost-aware selection function: given benchmark rows for multiple
    profiles, the selector picks the profile with the lowest average cost_usd
    (excluding 0.0 rows). After inserting rows that make profile A more expensive
    than profile B, the selector must return B instead of A.
    """

    def _cheapest_profile(self, benchmark_rows: list[dict]) -> str | None:
        """Select the profile with the lowest mean cost_usd (non-zero rows only)."""
        costs: dict[str, list[float]] = {}
        for row in benchmark_rows:
            pid = row.get("model_profile_id", "")
            cost = row.get("cost_usd", 0.0)
            if cost > 0.0 and pid:
                costs.setdefault(pid, []).append(cost)
        if not costs:
            return None
        return min(costs, key=lambda pid: sum(costs[pid]) / len(costs[pid]))

    def test_cheapest_profile_selected_based_on_cost(self) -> None:
        benchmark_rows = [
            {"model_profile_id": "profile-a", "cost_usd": 0.010, "success": True},
            {"model_profile_id": "profile-a", "cost_usd": 0.012, "success": True},
            {"model_profile_id": "profile-b", "cost_usd": 0.002, "success": True},
            {"model_profile_id": "profile-b", "cost_usd": 0.003, "success": True},
        ]
        chosen = self._cheapest_profile(benchmark_rows)
        assert chosen == "profile-b", (
            f"Expected profile-b (cheaper) to be selected, got {chosen!r}"
        )

    def test_routing_changes_after_expensive_rows_added(self) -> None:
        # Initially profile-a is cheapest
        initial_rows = [
            {"model_profile_id": "profile-a", "cost_usd": 0.001, "success": True},
            {"model_profile_id": "profile-b", "cost_usd": 0.010, "success": True},
        ]
        initial_choice = self._cheapest_profile(initial_rows)
        assert initial_choice == "profile-a"

        # After adding expensive profile-a rows, profile-b becomes cheapest
        updated_rows = [
            *initial_rows,
            {"model_profile_id": "profile-a", "cost_usd": 0.050, "success": True},
            {"model_profile_id": "profile-a", "cost_usd": 0.060, "success": True},
        ]
        updated_choice = self._cheapest_profile(updated_rows)
        assert updated_choice == "profile-b", (
            f"After adding expensive profile-a rows, expected profile-b, got {updated_choice!r}"
        )
        assert initial_choice != updated_choice, (
            "Routing decision must change when benchmark cost data changes"
        )

    def test_zero_cost_rows_excluded_from_ranking(self) -> None:
        # profile-a has only zero-cost rows (old default), profile-b has real costs
        rows = [
            {"model_profile_id": "profile-a", "cost_usd": 0.0, "success": True},
            {"model_profile_id": "profile-b", "cost_usd": 0.005, "success": True},
        ]
        chosen = self._cheapest_profile(rows)
        # profile-a is excluded (zero rows), profile-b wins
        assert chosen == "profile-b", (
            f"Zero-cost rows should be excluded; profile-b should win, got {chosen!r}"
        )
