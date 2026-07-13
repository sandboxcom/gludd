"""Unit tests for ag15_benchmarks/benchmark_harness.py — BenchmarkSuite, data models."""

from __future__ import annotations

import json

from general_ludd.ag15_benchmarks.benchmark_harness import (
    BenchmarkResult,
    BenchmarkSuite,
    BenchmarkSummary,
    BenchmarkTask,
)


class TestBenchmarkTask:
    def test_creation(self):
        task = BenchmarkTask(task_id="t1", description="Solve bug")
        assert task.task_id == "t1"
        assert task.description == "Solve bug"
        assert task.metadata == {}

    def test_with_metadata(self):
        task = BenchmarkTask(
            task_id="t2",
            description="Add feature",
            metadata={"repo": "foo/bar", "base_commit": "abc123"},
        )
        assert task.metadata["repo"] == "foo/bar"


class TestBenchmarkResult:
    def test_creation(self):
        result = BenchmarkResult(
            benchmark="swe-bench",
            task_id="t1",
            score=0.8,
            agent_name="default",
            duration_ms=100.0,
            attempts=1,
            resolved=False,
        )
        assert result.benchmark == "swe-bench"
        assert result.score == 0.8
        assert result.resolved is False
        assert result.error is None

    def test_with_error(self):
        result = BenchmarkResult(
            benchmark="gaia",
            task_id="t1",
            score=0.0,
            agent_name="default",
            duration_ms=50.0,
            attempts=1,
            resolved=False,
            error="Connection timeout",
        )
        assert result.error == "Connection timeout"


class TestBenchmarkSummary:
    def test_resolution_rate_zero_tasks(self):
        summary = BenchmarkSummary(
            benchmark="test",
            agent_name="default",
            total_tasks=0,
            resolved_count=0,
            mean_score=0.0,
            total_duration_ms=0.0,
        )
        assert summary.resolution_rate() == 0.0

    def test_resolution_rate_half(self):
        summary = BenchmarkSummary(
            benchmark="test",
            agent_name="default",
            total_tasks=10,
            resolved_count=5,
            mean_score=0.5,
            total_duration_ms=1000.0,
        )
        assert summary.resolution_rate() == 0.5

    def test_resolution_rate_all(self):
        summary = BenchmarkSummary(
            benchmark="test",
            agent_name="default",
            total_tasks=10,
            resolved_count=10,
            mean_score=1.0,
            total_duration_ms=1000.0,
        )
        assert summary.resolution_rate() == 1.0


class TestBenchmarkSuite:
    def _runner(self):
        return lambda task: f"output for {task.task_id}"

    def _scorer(self):
        return lambda task, output: 1.0 if "t1" in task.task_id else 0.0

    def _failing_runner(self):
        def _fn(task):
            raise RuntimeError("runner error")
        return _fn

    def test_run_benchmark(self):
        suite = BenchmarkSuite(agent_name="test_agent")
        tasks = [
            BenchmarkTask(task_id="t1", description="task 1"),
            BenchmarkTask(task_id="t2", description="task 2"),
        ]
        summary = suite.run_benchmark("swe-bench", tasks, self._scorer(), self._runner())
        assert summary.benchmark == "swe-bench"
        assert summary.agent_name == "test_agent"
        assert summary.total_tasks == 2
        assert summary.resolved_count == 1
        assert summary.results[0].resolved is True
        assert summary.results[1].resolved is False

    def test_run_benchmark_resolved_when_score_gte_1(self):
        suite = BenchmarkSuite()
        tasks = [BenchmarkTask(task_id="t1", description="d")]
        def scorer(task, output):
            return 1.0
        summary = suite.run_benchmark("test", tasks, scorer, self._runner())
        assert summary.resolved_count == 1

    def test_run_benchmark_not_resolved_when_score_lt_1(self):
        suite = BenchmarkSuite()
        tasks = [BenchmarkTask(task_id="t1", description="d")]
        def scorer(task, output):
            return 0.5
        summary = suite.run_benchmark("test", tasks, scorer, self._runner())
        assert summary.resolved_count == 0

    def test_run_single_exception_yields_error(self):
        suite = BenchmarkSuite()
        task = BenchmarkTask(task_id="t1", description="d")
        result = suite._run_single("bench", task, self._failing_runner(), self._scorer())
        assert result.error is not None
        assert "runner error" in result.error
        assert result.score == 0.0
        assert result.resolved is False

    def test_run_single_error_returns_negative_score(self):
        suite = BenchmarkSuite()
        task = BenchmarkTask(task_id="t1", description="d")
        def runner(t):
            return [][0]
        def scorer(task, output):
            return 1.0
        result = suite._run_single("bench", task, runner, scorer)
        assert result.score == 0.0
        assert not result.resolved

    def test_report(self):
        suite = BenchmarkSuite(agent_name="test_agent")
        suite.results = [
            BenchmarkResult(
                benchmark="swe-bench", task_id="t1", score=1.0,
                agent_name="test_agent", duration_ms=100.0, attempts=1, resolved=True,
            ),
            BenchmarkResult(
                benchmark="swe-bench", task_id="t2", score=0.0,
                agent_name="test_agent", duration_ms=50.0, attempts=1, resolved=False,
            ),
        ]
        report = suite.report()
        assert report["agent"] == "test_agent"
        assert "swe-bench" in report["benchmarks"]
        assert report["benchmarks"]["swe-bench"]["total_tasks"] == 2
        assert report["benchmarks"]["swe-bench"]["resolved_count"] == 1
        assert report["benchmarks"]["swe-bench"]["resolution_rate"] == 0.5

    def test_report_with_output_path(self, tmp_path):
        suite = BenchmarkSuite(agent_name="test_agent")
        suite.results = [
            BenchmarkResult(
                benchmark="gaia", task_id="t1", score=0.5,
                agent_name="test_agent", duration_ms=100.0, attempts=1, resolved=False,
            ),
        ]
        out_path = tmp_path / "report.json"
        suite.report(output_path=out_path)
        assert out_path.exists()
        data = json.loads(out_path.read_text())
        assert data["agent"] == "test_agent"
        assert data["benchmarks"]["gaia"]["total_tasks"] == 1
