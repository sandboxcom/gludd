"""Structural tests for ag15_benchmarks/benchmark_harness.py — BenchmarkSuite."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from general_ludd.ag15_benchmarks.benchmark_harness import (
    BenchmarkResult,
    BenchmarkSuite,
    BenchmarkSummary,
    BenchmarkTask,
)


class TestBenchmarkTask:
    def test_minimal_construction(self):
        task = BenchmarkTask(task_id="t1", description="test task")
        assert task.task_id == "t1"
        assert task.description == "test task"
        assert task.metadata == {}

    def test_with_metadata(self):
        task = BenchmarkTask(task_id="t2", description="desc", metadata={"key": "val"})
        assert task.metadata == {"key": "val"}


class TestBenchmarkResult:
    def test_resolved_result(self):
        r = BenchmarkResult(
            benchmark="b1", task_id="t1", score=1.0,
            agent_name="a1", duration_ms=100, attempts=1, resolved=True,
        )
        assert r.resolved is True
        assert r.score == 1.0

    def test_failed_result(self):
        r = BenchmarkResult(
            benchmark="b1", task_id="t1", score=0.0,
            agent_name="a1", duration_ms=100, attempts=1,
            resolved=False, error="crash",
        )
        assert r.resolved is False
        assert r.error == "crash"


class TestBenchmarkSummary:
    def test_resolution_rate_normal(self):
        s = BenchmarkSummary(benchmark="b", agent_name="a", total_tasks=10, resolved_count=3, mean_score=0.5, total_duration_ms=1000)
        assert s.resolution_rate() == 0.3

    def test_resolution_rate_zero_tasks(self):
        s = BenchmarkSummary(benchmark="b", agent_name="a", total_tasks=0, resolved_count=0, mean_score=0.0, total_duration_ms=0)
        assert s.resolution_rate() == 0.0


class TestBenchmarkSuite:
    def test_creates_suite_with_results_list(self):
        suite = BenchmarkSuite(agent_name="test-agent")
        assert suite.agent_name == "test-agent"
        assert suite.results == []

    def test_run_single_resolved(self):
        suite = BenchmarkSuite()
        task = BenchmarkTask(task_id="t1", description="test")
        runner = MagicMock(return_value="output")
        scorer = MagicMock(return_value=1.0)
        result = suite._run_single("bench", task, runner, scorer)
        assert result.resolved is True
        assert result.score == 1.0
        assert result.benchmark == "bench"

    def test_run_single_failed(self):
        suite = BenchmarkSuite()
        task = BenchmarkTask(task_id="t1", description="test")
        runner = MagicMock(side_effect=RuntimeError("fail"))
        scorer = MagicMock()
        result = suite._run_single("bench", task, runner, scorer)
        assert result.resolved is False
        assert result.score == 0.0
        assert result.error == "fail"

    def test_run_single_score_lt_1_not_resolved(self):
        suite = BenchmarkSuite()
        task = BenchmarkTask(task_id="t1", description="test")
        runner = MagicMock(return_value="partial")
        scorer = MagicMock(return_value=0.5)
        result = suite._run_single("bench", task, runner, scorer)
        assert result.resolved is False
        assert result.score == 0.5

    def test_run_benchmark_aggregates_results(self):
        suite = BenchmarkSuite()
        tasks = [
            BenchmarkTask(task_id="t1", description="d1"),
            BenchmarkTask(task_id="t2", description="d2"),
            BenchmarkTask(task_id="t3", description="d3"),
        ]
        def runner(task):
            return task.task_id
        def scorer(task, output):
            return 1.0 if output in ("t1", "t3") else 0.0
        summary = suite.run_benchmark("bench", tasks, scorer, runner)
        assert summary.total_tasks == 3
        assert summary.resolved_count == 2
        assert summary.benchmark == "bench"
        assert len(suite.results) == 3

    def test_report_empty(self):
        suite = BenchmarkSuite(agent_name="agent1")
        report = suite.report()
        assert report["agent"] == "agent1"
        assert report["benchmarks"] == {}

    def test_report_with_results(self):
        suite = BenchmarkSuite(agent_name="agent1")
        suite.results = [
            BenchmarkResult(benchmark="b1", task_id="t1", score=1.0, agent_name="agent1", duration_ms=10, attempts=1, resolved=True),
            BenchmarkResult(benchmark="b1", task_id="t2", score=0.0, agent_name="agent1", duration_ms=20, attempts=1, resolved=False),
            BenchmarkResult(benchmark="b2", task_id="t3", score=1.0, agent_name="agent1", duration_ms=30, attempts=1, resolved=True),
        ]
        report = suite.report()
        assert report["benchmarks"]["b1"]["total_tasks"] == 2
        assert report["benchmarks"]["b1"]["resolved_count"] == 1
        assert report["benchmarks"]["b2"]["total_tasks"] == 1
        assert report["benchmarks"]["b2"]["resolved_count"] == 1

    def test_report_writes_file(self, tmp_path: Path):
        suite = BenchmarkSuite(agent_name="agent1")
        suite.results = [
            BenchmarkResult(benchmark="b1", task_id="t1", score=1.0, agent_name="agent1", duration_ms=10, attempts=1, resolved=True),
        ]
        out = tmp_path / "report.json"
        suite.report(output_path=out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["agent"] == "agent1"
        assert "b1" in data["benchmarks"]

    def test_mean_score_handles_empty_scores(self):
        suite = BenchmarkSuite()
        tasks = [BenchmarkTask(task_id="t1", description="d1")]
        runner = MagicMock(side_effect=RuntimeError("fail"))
        scorer = MagicMock()
        summary = suite.run_benchmark("bench", tasks, scorer, runner)
        assert summary.mean_score == 0.0
