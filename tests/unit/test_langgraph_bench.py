"""Structural tests for benchmark/langgraph_bench.py."""

from __future__ import annotations

import json

from general_ludd.benchmark.langgraph_bench import (
    BenchmarkRunner,
    ComparisonResult,
    MagicMockPromptRegistry,
    _compute_stats,
    _time_sync,
)


def test_comparison_result_dataclass_defaults():
    result = ComparisonResult(
        test_name="test",
        custom_impl="CustomImpl",
        langgraph_impl="LGImpl",
        custom_mean_ms=10.0,
        langgraph_mean_ms=8.0,
        custom_std_ms=1.0,
        langgraph_std_ms=0.5,
        speedup=1.25,
        custom_iters=50,
        langgraph_iters=50,
        custom_mem_kb=1024.0,
        langgraph_mem_kb=512.0,
        winner="langgraph",
    )
    assert result.test_name == "test"
    assert result.custom_mean_ms == 10.0
    assert result.langgraph_mean_ms == 8.0
    assert result.speedup == 1.25
    assert result.winner == "langgraph"
    assert result.notes == []


def test_comparison_result_to_dict():
    result = ComparisonResult(
        test_name="agent_loop",
        custom_impl="TC",
        langgraph_impl="LG",
        custom_mean_ms=100.0,
        langgraph_mean_ms=80.0,
        custom_std_ms=5.0,
        langgraph_std_ms=4.0,
        speedup=1.25,
        custom_iters=1,
        langgraph_iters=1,
        custom_mem_kb=-1.0,
        langgraph_mem_kb=-1.0,
        winner="langgraph",
        notes=["test note"],
    )
    d = result.to_dict()
    assert d["test_name"] == "agent_loop"
    assert d["speedup"] == 1.25
    assert d["winner"] == "langgraph"
    assert d["notes"] == ["test note"]


def test_compute_stats_empty():
    mean, std = _compute_stats([])
    assert mean == 0.0
    assert std == 0.0


def test_compute_stats_single_value():
    mean, std = _compute_stats([5.0])
    assert mean == 5.0
    assert std == 0.0


def test_compute_stats_multiple_values():
    mean, std = _compute_stats([1.0, 2.0, 3.0])
    assert mean == 2.0
    assert std > 0.0


def test_time_sync_runs_correct_iterations():
    call_count = [0]

    def work():
        call_count[0] += 1

    _time_sync(work, iterations=3, warmup=0)
    assert call_count[0] == 3


def test_benchmark_runner_initialization():
    runner = BenchmarkRunner(warmup=3, iterations=10)
    assert runner._warmup == 3
    assert runner._iterations == 10
    assert runner.results == []


def test_benchmark_runner_run_all():
    runner = BenchmarkRunner(warmup=1, iterations=2)
    results = runner.run_all()
    assert isinstance(results, list)
    assert len(results) == 3
    test_names = {r.test_name for r in results}
    assert "agent_loop_plain_path" in test_names
    assert "consensus_engine_5_agents_3_rounds" in test_names
    assert "reviewer_accuracy" in test_names


def test_magic_mock_prompt_registry():
    registry = MagicMockPromptRegistry()
    output = registry.render("test_template", key="value")
    assert "test_template" in output
    assert "value" in output


def test_benchmark_runner_report_json():
    runner = BenchmarkRunner(warmup=1, iterations=2)
    runner.run_all()
    report = json.dumps(
        {"benchmarks": [r.to_dict() for r in runner.results]},
    )
    data = json.loads(report)
    assert len(data["benchmarks"]) == 3
    for b in data["benchmarks"]:
        assert "test_name" in b
        assert "winner" in b
        assert "speedup" in b
