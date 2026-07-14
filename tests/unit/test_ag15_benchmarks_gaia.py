"""Unit tests for ag15_benchmarks/gaia.py — GAIA loader and scorer."""

from __future__ import annotations

import json

from general_ludd.ag15_benchmarks.benchmark_harness import BenchmarkTask
from general_ludd.ag15_benchmarks.gaia import _normalize, load_tasks, score_result


class TestNormalize:
    def test_lowercase(self):
        assert _normalize("Hello World") == "hello world"

    def test_strip_whitespace(self):
        assert _normalize("  answer  ") == "answer"

    def test_strip_trailing_dot(self):
        assert _normalize("42.") == "42"

    def test_multiple_trailing_dots(self):
        assert _normalize("ok...") == "ok"

    def test_empty_string(self):
        assert _normalize("") == ""


class TestLoadTasks:
    def test_no_dataset_returns_empty(self, tmp_path):
        tasks = load_tasks(cache_dir=tmp_path)
        assert tasks == []

    def test_loads_tasks_from_jsonl(self, tmp_path):
        gaia_dir = tmp_path / "gaia"
        gaia_dir.mkdir(parents=True)
        dataset = gaia_dir / "gaia_validation.jsonl"
        dataset.write_text(json.dumps({
            "task_id": "g1",
            "question": "What is 2+2?",
            "Level": "1",
            "Final answer": "4",
            "Annotator Metadata": {"steps": "simple"},
        }) + "\n")

        tasks = load_tasks(cache_dir=tmp_path)
        assert len(tasks) == 1
        assert tasks[0].task_id == "g1"
        assert tasks[0].description == "What is 2+2?"
        assert tasks[0].metadata["level"] == "1"
        assert tasks[0].metadata["ground_truth"] == "4"

    def test_skips_empty_lines(self, tmp_path):
        gaia_dir = tmp_path / "gaia"
        gaia_dir.mkdir(parents=True)
        dataset = gaia_dir / "gaia_validation.jsonl"
        dataset.write_text(
            json.dumps({"task_id": "g1", "question": "Q1"}) + "\n\n" +
            json.dumps({"task_id": "g2", "question": "Q2"}) + "\n"
        )

        tasks = load_tasks(cache_dir=tmp_path)
        assert len(tasks) == 2

    def test_missing_question_uses_task_id_prefix(self, tmp_path):
        gaia_dir = tmp_path / "gaia"
        gaia_dir.mkdir(parents=True)
        dataset = gaia_dir / "gaia_validation.jsonl"
        dataset.write_text(json.dumps({"task_id": "g1"}) + "\n")

        tasks = load_tasks(cache_dir=tmp_path)
        assert len(tasks) == 1
        assert tasks[0].description == ""


class TestScoreResult:
    def test_exact_match(self):
        task = BenchmarkTask(task_id="g1", description="Q", metadata={"ground_truth": "42"})
        assert score_result("42", task) == 1.0

    def test_case_insensitive_match(self):
        task = BenchmarkTask(task_id="g1", description="Q", metadata={"ground_truth": "Hello"})
        assert score_result("hello", task) == 1.0

    def test_partial_match_contains(self):
        task = BenchmarkTask(task_id="g1", description="Q", metadata={"ground_truth": "42"})
        assert score_result("the answer is 42", task) == 0.5

    def test_partial_match_contained(self):
        task = BenchmarkTask(task_id="g1", description="Q", metadata={"ground_truth": "the answer is 42"})
        assert score_result("42", task) == 0.5

    def test_no_match(self):
        task = BenchmarkTask(task_id="g1", description="Q", metadata={"ground_truth": "42"})
        assert score_result("99", task) == 0.0

    def test_empty_ground_truth(self):
        task = BenchmarkTask(task_id="g1", description="Q", metadata={"ground_truth": ""})
        assert score_result("anything", task) == 0.0

    def test_missing_ground_truth_key(self):
        task = BenchmarkTask(task_id="g1", description="Q", metadata={})
        assert score_result("anything", task) == 0.0

    def test_trailing_dot_normalized(self):
        task = BenchmarkTask(task_id="g1", description="Q", metadata={"ground_truth": "42."})
        assert score_result("42", task) == 1.0
