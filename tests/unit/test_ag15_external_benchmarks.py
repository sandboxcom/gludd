"""Unit tests for AG.16: External benchmarks (SWE-bench, GAIA, WebArena).

Tests the benchmark harness, task loading, scoring, and summary aggregation.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from general_ludd.ag15_benchmarks.benchmark_harness import (
    BenchmarkResult,
    BenchmarkSuite,
    BenchmarkSummary,
    BenchmarkTask,
)
from general_ludd.ag15_benchmarks.gaia import (
    _normalize,
)
from general_ludd.ag15_benchmarks.gaia import (
    load_tasks as gaia_load_tasks,
)
from general_ludd.ag15_benchmarks.gaia import (
    score_result as gaia_score,
)
from general_ludd.ag15_benchmarks.swe_bench import (
    load_tasks as swe_load_tasks,
)
from general_ludd.ag15_benchmarks.swe_bench import (
    score_result as swe_score,
)


class TestBenchmarkTask:
    def test_minimal_construction(self):
        t = BenchmarkTask(task_id="swe-001", description="Fix null pointer")
        assert t.task_id == "swe-001"
        assert t.description == "Fix null pointer"
        assert t.metadata == {}

    def test_full_construction(self):
        t = BenchmarkTask(
            task_id="gaia-001",
            description="What is the capital of France?",
            metadata={"level": "1", "ground_truth": "Paris"},
        )
        assert t.metadata["level"] == "1"
        assert t.metadata["ground_truth"] == "Paris"


class TestBenchmarkResult:
    def test_success_result(self):
        r = BenchmarkResult(
            benchmark="swe-bench",
            task_id="swe-001",
            score=1.0,
            agent_name="default",
            duration_ms=1500.0,
            attempts=1,
            resolved=True,
        )
        assert r.resolved is True
        assert r.score == 1.0
        assert r.error is None

    def test_failure_result(self):
        r = BenchmarkResult(
            benchmark="gaia",
            task_id="gaia-001",
            score=0.0,
            agent_name="default",
            duration_ms=800.0,
            attempts=1,
            resolved=False,
            error="timeout",
        )
        assert r.resolved is False
        assert r.error == "timeout"


class TestBenchmarkSummary:
    def test_resolution_rate_empty(self):
        s = BenchmarkSummary(
            benchmark="swe-bench", agent_name="default",
            total_tasks=0, resolved_count=0,
            mean_score=0.0, total_duration_ms=0.0,
        )
        assert s.resolution_rate() == 0.0

    def test_resolution_rate_half(self):
        s = BenchmarkSummary(
            benchmark="swe-bench", agent_name="default",
            total_tasks=10, resolved_count=5,
            mean_score=0.5, total_duration_ms=5000.0,
        )
        assert s.resolution_rate() == 0.5

    def test_resolution_rate_all(self):
        s = BenchmarkSummary(
            benchmark="gaia", agent_name="default",
            total_tasks=20, resolved_count=20,
            mean_score=1.0, total_duration_ms=10000.0,
        )
        assert s.resolution_rate() == 1.0


class TestBenchmarkSuite:
    def test_run_benchmark_no_tasks(self):
        suite = BenchmarkSuite(agent_name="test-agent")

        def noop_runner(t: BenchmarkTask) -> str:
            return ""

        def noop_scorer(t: BenchmarkTask, output: str) -> float:
            return 1.0

        summary = suite.run_benchmark("swe-bench", [], noop_scorer, noop_runner)
        assert summary.total_tasks == 0
        assert summary.resolved_count == 0
        assert summary.resolution_rate() == 0.0

    def test_run_benchmark_with_tasks(self):
        suite = BenchmarkSuite(agent_name="test-agent")
        tasks = [
            BenchmarkTask(task_id=f"task-{i}", description=f"Task {i}")
            for i in range(5)
        ]

        def runner(t: BenchmarkTask) -> str:
            return f"output-{t.task_id}"

        def scorer(t: BenchmarkTask, output: str) -> float:
            idx = int(t.task_id.split("-")[1])
            return 1.0 if idx % 2 == 0 else 0.0

        summary = suite.run_benchmark("gaia", tasks, scorer, runner)
        assert summary.total_tasks == 5
        assert summary.resolved_count == 3
        assert summary.mean_score == pytest.approx(0.6)
        assert len(suite.results) == 5

    def test_run_benchmark_runner_error(self):
        suite = BenchmarkSuite(agent_name="test-agent")
        tasks = [BenchmarkTask(task_id="fail-1", description="Will fail")]

        def failing_runner(t: BenchmarkTask) -> str:
            raise RuntimeError("simulated failure")

        def scorer(t: BenchmarkTask, output: str) -> float:
            return 1.0

        summary = suite.run_benchmark("swe-bench", tasks, scorer, failing_runner)
        assert summary.total_tasks == 1
        assert summary.resolved_count == 0
        assert summary.mean_score == 0.0
        assert len(suite.results) == 1
        assert "simulated failure" in suite.results[0].error

    def test_report_json_output(self):
        suite = BenchmarkSuite(agent_name="reporter")
        tasks = [BenchmarkTask(task_id="r-1", description="Report test")]

        def runner(t: BenchmarkTask) -> str:
            return "ok"

        def scorer(t: BenchmarkTask, output: str) -> float:
            return 1.0

        suite.run_benchmark("gaia", tasks, scorer, runner)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "reports" / "benchmarks" / "test.json"
            suite.report(output_path=out)
            assert out.exists()
            data = json.loads(out.read_text())
            assert data["agent"] == "reporter"
            assert "gaia" in data["benchmarks"]
            assert data["benchmarks"]["gaia"]["total_tasks"] == 1


class TestSWEBench:
    def test_load_tasks_empty_dir(self):
        tasks = swe_load_tasks(cache_dir=Path("/nonexistent/path"))
        assert tasks == []

    def test_load_tasks_with_data(self, tmp_path):
        dataset = tmp_path / "swe-bench"
        dataset.mkdir(parents=True)
        (dataset / "swe-bench_Verified.jsonl").write_text(
            json.dumps({
                "instance_id": "swe-001",
                "repo": "owner/repo",
                "problem_statement": "Fix bug",
                "base_commit": "abc123",
                "FAIL_TO_PASS": ["test_a", "test_b"],
                "PASS_TO_PASS": ["test_c"],
            }) + "\n" + json.dumps({
                "instance_id": "swe-002",
                "repo": "owner/repo2",
                "issue": "Fix other bug",
                "base_commit": "def456",
                "FAIL_TO_PASS": ["test_x"],
                "PASS_TO_PASS": [],
            }) + "\n"
        )
        tasks = swe_load_tasks(cache_dir=tmp_path)
        assert len(tasks) == 2
        assert tasks[0].task_id == "swe-001"
        assert tasks[1].task_id == "swe-002"
        assert tasks[0].metadata["repo"] == "owner/repo"

    def test_score_all_pass(self):
        task = BenchmarkTask(
            task_id="swe-001",
            description="Fix bug",
            metadata={"fail_to_pass": ["test_a", "test_b"]},
        )
        result = "test_a\nok\ntest_b\nok"
        assert swe_score(result, task) == 1.0

    def test_score_partial_pass(self):
        task = BenchmarkTask(
            task_id="swe-002",
            description="Fix bug",
            metadata={"fail_to_pass": ["test_x", "test_y", "test_z"]},
        )
        result = "test_x\nok"
        assert swe_score(result, task) == pytest.approx(1.0 / 3.0)

    def test_score_empty_tests(self):
        task = BenchmarkTask(
            task_id="swe-003",
            description="No tests",
            metadata={"fail_to_pass": []},
        )
        assert swe_score("anything", task) == 0.0


class TestGAIA:
    def test_load_tasks_empty_dir(self):
        tasks = gaia_load_tasks(cache_dir=Path("/nonexistent"))
        assert tasks == []

    def test_load_tasks_with_data(self, tmp_path):
        dataset = tmp_path / "gaia"
        dataset.mkdir(parents=True)
        (dataset / "gaia_validation.jsonl").write_text(
            json.dumps({
                "task_id": "gaia-001",
                "question": "What is the capital of France?",
                "Level": "1",
                "Final answer": "Paris",
            }) + "\n" + json.dumps({
                "task_id": "gaia-002",
                "question": "How many moons does Mars have?",
                "Level": "1",
                "Final answer": "2",
            }) + "\n"
        )
        tasks = gaia_load_tasks(cache_dir=tmp_path)
        assert len(tasks) == 2
        assert tasks[0].task_id == "gaia-001"
        assert tasks[0].description == "What is the capital of France?"

    def test_score_exact_match(self):
        task = BenchmarkTask(
            task_id="gaia-001",
            description="Capital?",
            metadata={"ground_truth": "Paris"},
        )
        assert gaia_score("Paris", task) == 1.0

    def test_score_partial_match(self):
        task = BenchmarkTask(
            task_id="gaia-002",
            description="Capital?",
            metadata={"ground_truth": "Paris"},
        )
        assert gaia_score("Paris, France", task) == 0.5

    def test_score_no_match(self):
        task = BenchmarkTask(
            task_id="gaia-003",
            description="Capital?",
            metadata={"ground_truth": "Paris"},
        )
        assert gaia_score("London", task) == 0.0

    def test_score_case_insensitive(self):
        task = BenchmarkTask(
            task_id="gaia-004",
            description="Capital?",
            metadata={"ground_truth": "Paris"},
        )
        assert gaia_score("paris", task) == 1.0

    def test_score_trailing_period(self):
        task = BenchmarkTask(
            task_id="gaia-005",
            description="Capital?",
            metadata={"ground_truth": "Paris."},
        )
        assert gaia_score("Paris", task) == 1.0

    def test_normalize(self):
        assert _normalize("  Paris  ") == "paris"
        assert _normalize("Paris.") == "paris"
        assert _normalize("Hello World.") == "hello world"
