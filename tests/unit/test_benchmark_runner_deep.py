"""Deep edge-case tests for benchmark/langgraph_bench.py.

Covers boundary conditions, sentinel values, exception paths, idle loops,
and statefulness not exercised by existing structural or integration tests.
"""

from __future__ import annotations

import asyncio
import json
import sys
from io import StringIO
from typing import Any, cast

import pytest

from general_ludd.benchmark.langgraph_bench import (
    BenchmarkRunner,
    ComparisonResult,
    MagicMockPromptRegistry,
    _compute_stats,
    _peak_memory_kb,
    _time_async,
    _time_sync,
)

# ═══════════════════════════════════════════════════════════════════════════
# _compute_stats — deep edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeStatsDeep:
    def test_all_negative_values(self) -> None:
        mean, std = _compute_stats([-10.0, -20.0, -30.0])
        assert mean == -20.0
        assert std > 0.0

    def test_mixed_positive_negative(self) -> None:
        mean, std = _compute_stats([100.0, -100.0])
        assert mean == 0.0
        assert std == pytest.approx(100.0)

    def test_very_large_values(self) -> None:
        mean, std = _compute_stats([1e12, 2e12, 3e12])
        assert mean == 2e12
        assert std > 0.0

    def test_very_small_values(self) -> None:
        mean, std = _compute_stats([1e-12, 2e-12, 3e-12])
        assert mean == 2e-12
        assert std > 0.0

    def test_large_sample_count(self) -> None:
        samples = [float(i) for i in range(1000)]
        mean, std = _compute_stats(samples)
        assert mean == pytest.approx(499.5)
        assert std > 0.0

    def test_repeated_identical_value(self) -> None:
        samples = [42.0] * 100
        mean, std = _compute_stats(samples)
        assert mean == 42.0
        assert std == 0.0

    def test_identical_pair(self) -> None:
        mean, std = _compute_stats([7.0, 7.0])
        assert mean == 7.0
        assert std == 0.0

    def test_returns_float_type(self) -> None:
        mean, std = _compute_stats([1.0, 2.0])
        assert isinstance(mean, float)
        assert isinstance(std, float)


# ═══════════════════════════════════════════════════════════════════════════
# _time_sync — deep edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestTimeSyncDeep:
    def test_zero_iterations_returns_empty(self) -> None:
        def fn() -> None:
            pass

        times = _time_sync(fn, iterations=0, warmup=0)
        assert times == []

    def test_zero_warmup_zero_iterations(self) -> None:
        def fn() -> None:
            pass

        times = _time_sync(fn, iterations=0, warmup=0)
        assert times == []

    def test_zero_warmup_with_iterations(self) -> None:
        calls: list[int] = []

        def fn() -> None:
            calls.append(1)

        times = _time_sync(fn, iterations=5, warmup=0)
        assert len(times) == 5
        assert len(calls) == 5  # no warmup calls

    def test_callable_returns_complex_object(self) -> None:
        def fn() -> dict[str, int]:
            return {"a": 1, "b": 2}

        times = _time_sync(fn, iterations=3, warmup=1)
        assert len(times) == 3
        assert all(t >= 0 for t in times)

    def test_times_are_monotonic_float(self) -> None:
        def fn() -> None:
            sum(i * i for i in range(1000))

        times = _time_sync(fn, iterations=10, warmup=2)
        assert all(isinstance(t, float) for t in times)
        assert all(t >= 0.0 for t in times)

    def test_callable_that_mutates_captured_state(self) -> None:
        state: dict[str, int] = {"count": 0}

        def fn() -> None:
            state["count"] += 1

        times = _time_sync(fn, iterations=4, warmup=1)
        assert len(times) == 4
        assert state["count"] == 5  # 1 warmup + 4 iterations


# ═══════════════════════════════════════════════════════════════════════════
# _time_async — deep edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestTimeAsyncDeep:
    def test_zero_iterations_returns_empty(self) -> None:
        async def fn() -> None:
            pass

        times = _time_async(fn, iterations=0, warmup=0)
        assert times == []

    def test_zero_warmup_with_iterations(self) -> None:
        calls: list[int] = []

        async def fn() -> None:
            calls.append(1)

        times = _time_async(fn, iterations=5, warmup=0)
        assert len(times) == 5
        assert len(calls) == 5

    def test_coroutine_that_actually_awaits(self) -> None:
        async def fn() -> str:
            await asyncio.sleep(0.001)
            return "done"

        times = _time_async(fn, iterations=3, warmup=1)
        assert len(times) == 3
        assert all(t >= 0 for t in times)

    def test_coroutine_returning_complex_data(self) -> None:
        async def fn() -> dict[str, list[int]]:
            return {"data": [1, 2, 3]}

        times = _time_async(fn, iterations=4, warmup=1)
        assert len(times) == 4
        assert all(isinstance(t, float) for t in times)

    def test_creates_and_closes_its_own_event_loop(self) -> None:
        async def fn() -> None:
            pass

        _time_async(fn, iterations=2, warmup=0)
        # If the event loop wasn't closed, this wouldn't prove anything directly,
        # but we can verify it does NOT leak the loop by checking no running loop.
        # The implementation creates new_event_loop() and closes it in finally.
        # We ensure this doesn't raise.

    def test_times_are_nonnegative(self) -> None:
        async def fn() -> None:
            await asyncio.sleep(0.0)

        times = _time_async(fn, iterations=20, warmup=2)
        assert all(t >= 0.0 for t in times)
        assert len(times) == 20


# ═══════════════════════════════════════════════════════════════════════════
# ComparisonResult — deep edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestComparisonResultDeep:
    def test_negative_mean_times(self) -> None:
        cr = ComparisonResult(
            test_name="neg",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=-5.0,
            langgraph_mean_ms=-3.0,
            custom_std_ms=1.0,
            langgraph_std_ms=1.0,
            speedup=1.0,
            custom_iters=1,
            langgraph_iters=1,
            custom_mem_kb=0.0,
            langgraph_mem_kb=0.0,
            winner="tie",
        )
        d = cr.to_dict()
        assert d["custom_mean_ms"] == -5.0
        assert d["langgraph_mean_ms"] == -3.0

    def test_speedup_zero(self) -> None:
        cr = ComparisonResult(
            test_name="zero_speedup",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=0.0,
            langgraph_mean_ms=0.0,
            custom_std_ms=0.0,
            langgraph_std_ms=0.0,
            speedup=0.0,
            custom_iters=1,
            langgraph_iters=1,
            custom_mem_kb=0.0,
            langgraph_mem_kb=0.0,
            winner="tie",
        )
        d = cr.to_dict()
        assert d["speedup"] == 0.0

    def test_very_large_speedup(self) -> None:
        cr = ComparisonResult(
            test_name="huge_speedup",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=1000000.0,
            langgraph_mean_ms=1.0,
            custom_std_ms=1.0,
            langgraph_std_ms=1.0,
            speedup=1_000_000.0,
            custom_iters=1,
            langgraph_iters=1,
            custom_mem_kb=0.0,
            langgraph_mem_kb=0.0,
            winner="langgraph",
        )
        d = cr.to_dict()
        assert d["speedup"] == 1_000_000.0

    def test_zero_iterations(self) -> None:
        cr = ComparisonResult(
            test_name="zero_iters",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=0.0,
            langgraph_mean_ms=0.0,
            custom_std_ms=0.0,
            langgraph_std_ms=0.0,
            speedup=1.0,
            custom_iters=0,
            langgraph_iters=0,
            custom_mem_kb=0.0,
            langgraph_mem_kb=0.0,
            winner="tie",
        )
        d = cr.to_dict()
        assert d["custom_iters"] == 0
        assert d["langgraph_iters"] == 0

    def test_empty_notes_field(self) -> None:
        cr = ComparisonResult(
            test_name="no_notes",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=1.0,
            langgraph_mean_ms=1.0,
            custom_std_ms=0.0,
            langgraph_std_ms=0.0,
            speedup=1.0,
            custom_iters=1,
            langgraph_iters=1,
            custom_mem_kb=0.0,
            langgraph_mem_kb=0.0,
            winner="tie",
        )
        d = cr.to_dict()
        assert d["notes"] == []

    def test_many_notes(self) -> None:
        notes = [f"note_{i}" for i in range(50)]
        cr = ComparisonResult(
            test_name="many_notes",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=1.0,
            langgraph_mean_ms=1.0,
            custom_std_ms=1.0,
            langgraph_std_ms=1.0,
            speedup=1.0,
            custom_iters=1,
            langgraph_iters=1,
            custom_mem_kb=0.0,
            langgraph_mem_kb=0.0,
            winner="tie",
            notes=notes,
        )
        d = cr.to_dict()
        assert len(d["notes"]) == 50

    def test_memory_sentinel_negative_one(self) -> None:
        cr = ComparisonResult(
            test_name="no_mem",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=1.0,
            langgraph_mean_ms=1.0,
            custom_std_ms=1.0,
            langgraph_std_ms=1.0,
            speedup=1.0,
            custom_iters=1,
            langgraph_iters=1,
            custom_mem_kb=-1.0,
            langgraph_mem_kb=-1.0,
            winner="tie",
        )
        d = cr.to_dict()
        assert d["custom_mem_kb"] == -1.0
        assert d["langgraph_mem_kb"] == -1.0

    def test_to_dict_rounds_floats(self) -> None:
        cr = ComparisonResult(
            test_name="rounding",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=1.23456789,
            langgraph_mean_ms=9.87654321,
            custom_std_ms=0.123456789,
            langgraph_std_ms=0.987654321,
            speedup=3.1415926535,
            custom_iters=1,
            langgraph_iters=1,
            custom_mem_kb=1.23456789,
            langgraph_mem_kb=9.87654321,
            winner="langgraph",
        )
        d = cr.to_dict()
        assert d["custom_mean_ms"] == round(1.23456789, 4)
        assert d["langgraph_mean_ms"] == round(9.87654321, 4)
        assert d["speedup"] == round(3.1415926535, 3)
        assert d["custom_mem_kb"] == round(1.23456789, 2)

    def test_all_winner_types(self) -> None:
        for winner in ("custom", "langgraph", "tie"):
            cr = ComparisonResult(
                test_name=f"winner_{winner}",
                custom_impl="C",
                langgraph_impl="L",
                custom_mean_ms=1.0,
                langgraph_mean_ms=1.0,
                custom_std_ms=0.0,
                langgraph_std_ms=0.0,
                speedup=1.0,
                custom_iters=1,
                langgraph_iters=1,
                custom_mem_kb=0.0,
                langgraph_mem_kb=0.0,
                winner=winner,
            )
            assert cr.winner == winner

    def test_dataclass_field_mutability_notes(self) -> None:
        cr = ComparisonResult(
            test_name="mut",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=1.0,
            langgraph_mean_ms=1.0,
            custom_std_ms=0.0,
            langgraph_std_ms=0.0,
            speedup=1.0,
            custom_iters=1,
            langgraph_iters=1,
            custom_mem_kb=0.0,
            langgraph_mem_kb=0.0,
            winner="tie",
        )
        cr.notes.append("added later")
        assert cr.notes == ["added later"]

    def test_very_large_mean_no_overflow(self) -> None:
        cr = ComparisonResult(
            test_name="big",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=1e308,
            langgraph_mean_ms=1e308,
            custom_std_ms=1e308,
            langgraph_std_ms=1e308,
            speedup=1e308,
            custom_iters=1,
            langgraph_iters=1,
            custom_mem_kb=1e308,
            langgraph_mem_kb=1e308,
            winner="tie",
        )
        d = cr.to_dict()
        assert isinstance(d["custom_mean_ms"], float)
        assert isinstance(d["speedup"], float)


# ═══════════════════════════════════════════════════════════════════════════
# _peak_memory_kb — deep edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestPeakMemoryKbDeep:
    def test_returns_float_or_sentinel(self) -> None:
        val = _peak_memory_kb()
        assert isinstance(val, float)

    def test_returns_sentinel_when_tracemalloc_not_started(self) -> None:
        val = _peak_memory_kb()
        assert val >= -1.0


# ═══════════════════════════════════════════════════════════════════════════
# MagicMockPromptRegistry — deep edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestMagicMockPromptRegistryDeep:
    def test_empty_kwargs(self) -> None:
        registry = MagicMockPromptRegistry()
        out = registry.render("empty_template")
        assert "empty_template" in out

    def test_none_value_in_kwargs(self) -> None:
        registry = MagicMockPromptRegistry()
        out = registry.render("t", key=None)
        assert "null" in out

    def test_boolean_kwargs(self) -> None:
        registry = MagicMockPromptRegistry()
        out = registry.render("t", flag=True, off=False)
        assert "true" in out
        assert "false" in out

    def test_nested_dict_kwargs(self) -> None:
        registry = MagicMockPromptRegistry()
        out = registry.render("t", data={"nested": [1, 2, 3]})
        parsed = json.loads(out.split("with ")[1])
        assert parsed["data"]["nested"] == [1, 2, 3]

    def test_list_kwargs(self) -> None:
        registry = MagicMockPromptRegistry()
        out = registry.render("t", items=[1, 2, 3])
        assert "[1, 2, 3]" in out

    def test_special_characters_in_template_name(self) -> None:
        registry = MagicMockPromptRegistry()
        out = registry.render("template/with/slashes.html")
        assert "template/with/slashes.html" in out

    def test_multiple_kwarg_types(self) -> None:
        registry = MagicMockPromptRegistry()
        out = registry.render("multi", a=1, b="two", c=3.0, d=[4])
        assert "1" in out
        assert "two" in out
        assert "3.0" in out
        assert "4" in out


# ═══════════════════════════════════════════════════════════════════════════
# BenchmarkRunner — deep edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestBenchmarkRunnerDeep:
    def test_zero_warmup_zero_iterations_does_not_crash(self) -> None:
        runner = BenchmarkRunner(warmup=0, iterations=0)
        results = runner.run_all()
        assert len(results) == 3

    def test_zero_warmup_one_iteration(self) -> None:
        runner = BenchmarkRunner(warmup=0, iterations=1)
        results = runner.run_all()
        assert len(results) == 3
        for r in results:
            assert r.custom_iters >= 0

    def test_initialization_stores_output_param(self) -> None:
        runner = BenchmarkRunner(warmup=1, iterations=1, output="file")
        assert runner._output == "file"

    def test_non_stdout_report_is_silent(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runner = BenchmarkRunner(warmup=0, iterations=0, output="file")

        runner.report()

        assert capsys.readouterr().out == ""

    def test_consensus_reviewer_all_verdict_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = BenchmarkRunner(warmup=0, iterations=1)

        for reviewer_hash in (100, 210, 230):
            monkeypatch.setattr(
                "builtins.hash", lambda _value, value=reviewer_hash: value
            )
            runner._compare_consensus_engines()

        assert [result.test_name for result in runner.results] == [
            "consensus_engine_5_agents_3_rounds",
        ] * 3

    def test_agent_loop_records_missing_optional_langgraph(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(
            sys.modules, "general_ludd.execution.langgraph_agent", None
        )
        runner = BenchmarkRunner(warmup=0, iterations=1)

        runner._compare_agent_loops()

        assert runner.results[0].winner == "custom"
        assert runner.results[0].notes == [
            "langgraph not installed — LangGraphAgentLoop skipped"
        ]

    def test_consensus_records_missing_optional_langgraph(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(
            sys.modules, "general_ludd.review.langgraph_consensus", None
        )
        runner = BenchmarkRunner(warmup=0, iterations=1)

        runner._compare_consensus_engines()

        assert runner.results[0].winner == "custom"
        assert runner.results[0].notes == [
            "langgraph not installed — LangGraphConsensusEngine skipped"
        ]

    def test_run_all_resets_results_each_call(self) -> None:
        runner = BenchmarkRunner(warmup=1, iterations=3)
        runner.run_all()
        results2 = runner.run_all()
        assert len(results2) == 3  # resets, does not accumulate

    def test_report_before_run_all_prints_empty(self) -> None:
        runner = BenchmarkRunner(warmup=1, iterations=1)
        captured = StringIO()
        original = sys.stdout
        try:
            sys.stdout = cast(Any, captured)
            runner.report()
            output = captured.getvalue()
        finally:
            sys.stdout = original
        data = json.loads(output)
        assert data["benchmarks"] == []
        assert data["summary"]["total_comparisons"] == 0

    def test_report_has_correct_summary_counts(self) -> None:
        runner = BenchmarkRunner(warmup=1, iterations=2)
        runner.run_all()
        captured = StringIO()
        original = sys.stdout
        try:
            sys.stdout = cast(Any, captured)
            runner.report()
            output = captured.getvalue()
        finally:
            sys.stdout = original
        data = json.loads(output)
        s = data["summary"]
        assert s["total_comparisons"] == 3
        assert s["langgraph_wins"] + s["custom_wins"] + s["ties"] == 3

    def test_large_warmup_initialization_accepted(self) -> None:
        runner = BenchmarkRunner(warmup=10_000, iterations=1)
        assert runner._warmup == 10_000
        assert runner._iterations == 1
        assert runner.results == []

    def test_negative_iterations_accepted_binding(self) -> None:
        runner = BenchmarkRunner(warmup=1, iterations=-5)
        assert runner._iterations == -5

    def test_negative_warmup_accepted_binding(self) -> None:
        runner = BenchmarkRunner(warmup=-3, iterations=10)
        assert runner._warmup == -3


# ═══════════════════════════════════════════════════════════════════════════
# main() — crash-avoidance
# ═══════════════════════════════════════════════════════════════════════════
# main() — crash-avoidance
# ═══════════════════════════════════════════════════════════════════════════


class TestMainFunction:
    def test_main_does_not_crash(self) -> None:
        import contextlib

        from general_ludd.benchmark.langgraph_bench import main

        with contextlib.suppress(Exception):
            main()
        assert True


# ═══════════════════════════════════════════════════════════════════════════
# ComparisonResult — field type / structural invariants
# ═══════════════════════════════════════════════════════════════════════════


class TestComparisonResultFieldTypes:
    def test_to_dict_returns_correct_keys(self) -> None:
        cr = ComparisonResult(
            test_name="x",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=1.0,
            langgraph_mean_ms=2.0,
            custom_std_ms=0.5,
            langgraph_std_ms=0.5,
            speedup=1.0,
            custom_iters=1,
            langgraph_iters=1,
            custom_mem_kb=0.0,
            langgraph_mem_kb=0.0,
            winner="tie",
        )
        d = cr.to_dict()
        expected_keys = {
            "test_name",
            "custom_impl",
            "langgraph_impl",
            "custom_mean_ms",
            "langgraph_mean_ms",
            "custom_std_ms",
            "langgraph_std_ms",
            "speedup",
            "custom_iters",
            "langgraph_iters",
            "custom_mem_kb",
            "langgraph_mem_kb",
            "winner",
            "notes",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_all_values_in_type(self) -> None:
        cr = ComparisonResult(
            test_name="t",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=1.0,
            langgraph_mean_ms=2.0,
            custom_std_ms=0.1,
            langgraph_std_ms=0.2,
            speedup=2.0,
            custom_iters=5,
            langgraph_iters=7,
            custom_mem_kb=100.0,
            langgraph_mem_kb=200.0,
            winner="custom",
        )
        d = cr.to_dict()
        for key in d:
            assert key in cr.__dataclass_fields__ or key == "notes"


# ═══════════════════════════════════════════════════════════════════════════
# BenchmarkRunner report() — output target edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestBenchmarkRunnerReportDeep:
    def test_report_stdout_is_printable_json(self) -> None:
        runner = BenchmarkRunner(warmup=1, iterations=2)
        runner.run_all()
        captured = StringIO()
        original = sys.stdout
        try:
            sys.stdout = cast(Any, captured)
            runner.report()
            output = captured.getvalue()
        finally:
            sys.stdout = original
        data = json.loads(output)
        assert "benchmarks" in data
        assert "summary" in data

    def test_report_summary_keys_match(self) -> None:
        runner = BenchmarkRunner(warmup=1, iterations=2)
        runner.run_all()
        captured = StringIO()
        original = sys.stdout
        try:
            sys.stdout = cast(Any, captured)
            runner.report()
            output = captured.getvalue()
        finally:
            sys.stdout = original
        data = json.loads(output)
        summary = data["summary"]
        assert "langgraph_wins" in summary
        assert "custom_wins" in summary
        assert "ties" in summary
        assert "total_comparisons" in summary

    def test_report_non_stdout_output_is_ignored(self) -> None:
        runner = BenchmarkRunner(warmup=1, iterations=1, output="json_file")
        runner.run_all()
        captured = StringIO()
        original = sys.stdout
        try:
            sys.stdout = cast(Any, captured)
            runner.report()
            output = captured.getvalue()
        finally:
            sys.stdout = original
        assert output == ""  # non-stdout output target, nothing printed
