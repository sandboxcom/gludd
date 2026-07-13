"""Unit tests for ag15_benchmarks/swe_bench.py — SWE-bench loader, runner, scorer."""

from __future__ import annotations

import json

from general_ludd.ag15_benchmarks.benchmark_harness import BenchmarkTask
from general_ludd.ag15_benchmarks.swe_bench import (
    _simulated_runner,
    load_tasks,
    score_result,
)


class TestLoadTasks:
    def test_no_dataset_returns_empty(self, tmp_path):
        tasks = load_tasks(cache_dir=tmp_path)
        assert tasks == []

    def test_loads_tasks_from_jsonl(self, tmp_path):
        swe_dir = tmp_path / "swe-bench"
        swe_dir.mkdir(parents=True)
        dataset = swe_dir / "swe-bench_Verified.jsonl"
        dataset.write_text(json.dumps({
            "instance_id": "django__1234",
            "problem_statement": "Fix the bug in auth module",
            "repo": "django/django",
            "base_commit": "abc123",
            "FAIL_TO_PASS": ["test_auth_login", "test_auth_logout"],
            "PASS_TO_PASS": ["test_other"],
        }) + "\n")

        tasks = load_tasks(cache_dir=tmp_path)
        assert len(tasks) == 1
        assert tasks[0].task_id == "django__1234"
        assert tasks[0].description == "Fix the bug in auth module"
        assert tasks[0].metadata["repo"] == "django/django"
        assert tasks[0].metadata["base_commit"] == "abc123"
        assert len(tasks[0].metadata["fail_to_pass"]) == 2

    def test_falls_back_to_issue_field(self, tmp_path):
        swe_dir = tmp_path / "swe-bench"
        swe_dir.mkdir(parents=True)
        dataset = swe_dir / "swe-bench_Verified.jsonl"
        dataset.write_text(json.dumps({
            "instance_id": "django__1234",
            "issue": "Bug description from issue",
        }) + "\n")

        tasks = load_tasks(cache_dir=tmp_path)
        assert len(tasks) == 1
        assert tasks[0].description == "Bug description from issue"

    def test_skips_empty_lines(self, tmp_path):
        swe_dir = tmp_path / "swe-bench"
        swe_dir.mkdir(parents=True)
        dataset = swe_dir / "swe-bench_Verified.jsonl"
        dataset.write_text(
            json.dumps({"instance_id": "a"}) + "\n\n" +
            json.dumps({"instance_id": "b"}) + "\n"
        )

        tasks = load_tasks(cache_dir=tmp_path)
        assert len(tasks) == 2

    def test_empty_metadata_defaults(self, tmp_path):
        swe_dir = tmp_path / "swe-bench"
        swe_dir.mkdir(parents=True)
        dataset = swe_dir / "swe-bench_Verified.jsonl"
        dataset.write_text(json.dumps({"instance_id": "x"}) + "\n")

        tasks = load_tasks(cache_dir=tmp_path)
        assert len(tasks) == 1
        assert tasks[0].metadata["repo"] == ""
        assert tasks[0].metadata["fail_to_pass"] == []


class TestSimulatedRunner:
    def test_returns_placeholder(self):
        task = BenchmarkTask(task_id="t1", description="d")
        result = _simulated_runner(task)
        assert "placeholder" in result


class TestScoreResult:
    def test_all_tests_pass(self):
        task = BenchmarkTask(
            task_id="t1", description="d",
            metadata={"fail_to_pass": ["test_a", "test_b"]},
        )
        result = "test_a passed\ntest_b passed"
        assert score_result(result, task) == 1.0

    def test_partial_pass(self):
        task = BenchmarkTask(
            task_id="t1", description="d",
            metadata={"fail_to_pass": ["test_a", "test_b"]},
        )
        result = "test_a passed"
        assert score_result(result, task) == 0.5

    def test_no_pass(self):
        task = BenchmarkTask(
            task_id="t1", description="d",
            metadata={"fail_to_pass": ["test_a", "test_b"]},
        )
        result = "nothing relevant"
        assert score_result(result, task) == 0.0

    def test_empty_fail_to_pass(self):
        task = BenchmarkTask(
            task_id="t1", description="d",
            metadata={"fail_to_pass": []},
        )
        assert score_result("anything", task) == 0.0

    def test_missing_fail_to_pass_key(self):
        task = BenchmarkTask(task_id="t1", description="d", metadata={})
        assert score_result("anything", task) == 0.0
