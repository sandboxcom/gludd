"""Prove MetricsCollector tracks per-task-type results.

Per the game audit recommendation #7: MetricsCollector must track
per-task metrics (tokens, success/failure, latency, failure modes)
so operators can answer "how many code-generation tasks succeeded
this week?" without grepping logs.
"""

from __future__ import annotations

import pytest

from general_ludd.metrics.collector import MetricsCollector


class TestRecordTaskResult:
    """record_task_result aggregates per-task-type counters."""

    def test_first_result_creates_entry(self):
        mc = MetricsCollector()
        mc.record_task_result(
            task_type="code_generation",
            success=True,
            tokens_in=100,
            tokens_out=200,
            latency_ms=3500.0,
        )
        summary = mc.get_task_type_summary()
        assert "code_generation" in summary
        assert summary["code_generation"]["total"] == 1
        assert summary["code_generation"]["successes"] == 1
        assert summary["code_generation"]["failures"] == 0

    def test_multiple_results_accumulate_correctly(self):
        mc = MetricsCollector()
        mc.record_task_result("code_generation", True, 100, 200, 3500.0)
        mc.record_task_result("code_generation", True, 150, 250, 4200.0)
        mc.record_task_result("code_generation", False, 80, 0, 800.0)
        mc.record_task_result("review", True, 50, 100, 500.0)

        summary = mc.get_task_type_summary()
        cg = summary["code_generation"]
        assert cg["total"] == 3
        assert cg["successes"] == 2
        assert cg["failures"] == 1

        rv = summary["review"]
        assert rv["total"] == 1
        assert rv["successes"] == 1

    def test_token_accumulation(self):
        mc = MetricsCollector()
        mc.record_task_result("code_generation", True, 100, 200, 3500.0)
        mc.record_task_result("code_generation", True, 300, 500, 8000.0)

        summary = mc.get_task_type_summary()
        cg = summary["code_generation"]
        assert cg["tokens_in"] == 400
        assert cg["tokens_out"] == 700

    def test_latency_stats(self):
        mc = MetricsCollector()
        mc.record_task_result("code_generation", True, 100, 200, 1000.0)
        mc.record_task_result("code_generation", True, 100, 200, 3000.0)
        mc.record_task_result("code_generation", True, 100, 200, 5000.0)

        summary = mc.get_task_type_summary()
        cg = summary["code_generation"]
        assert cg["latency_total_ms"] == 9000.0
        assert cg["latency_avg_ms"] == 3000.0
        assert cg["latency_min_ms"] == 1000.0
        assert cg["latency_max_ms"] == 5000.0

    def test_success_rate_calculation(self):
        mc = MetricsCollector()
        mc.record_task_result("code_generation", True, 100, 200, 1000.0)
        mc.record_task_result("code_generation", False, 100, 200, 2000.0)
        mc.record_task_result("code_generation", True, 100, 200, 3000.0)

        summary = mc.get_task_type_summary()
        cg = summary["code_generation"]
        assert cg["success_rate"] == pytest.approx(2.0 / 3.0)

    def test_failure_modes_captured(self):
        mc = MetricsCollector()
        mc.record_task_result(
            "code_generation", False, 100, 200, 1000.0,
            error="ImportError: No module named 'pygame'",
        )
        mc.record_task_result(
            "code_generation", False, 100, 200, 1000.0,
            error="SyntaxError: invalid syntax",
        )
        mc.record_task_result(
            "code_generation", False, 100, 200, 1000.0,
            error="ImportError: No module named 'pygame'",
        )

        summary = mc.get_task_type_summary()
        cg = summary["code_generation"]
        modes = cg["failure_modes"]
        assert "ImportError" in modes
        assert modes["ImportError"] == 2
        assert "SyntaxError" in modes
        assert modes["SyntaxError"] == 1

    def test_successful_tasks_do_not_count_as_failures(self):
        mc = MetricsCollector()
        mc.record_task_result("code_generation", True, 100, 200, 1000.0)
        summary = mc.get_task_type_summary()
        cg = summary["code_generation"]
        assert cg["failures"] == 0
        assert cg["failure_modes"] == {}

    def test_defaults_for_success_without_error(self):
        mc = MetricsCollector()
        mc.record_task_result("code_generation", True, 0, 0, 0.0)
        summary = mc.get_task_type_summary()
        cg = summary["code_generation"]
        assert cg["tokens_in"] == 0
        assert cg["success_rate"] == 1.0

    def test_empty_summary_for_no_data(self):
        mc = MetricsCollector()
        assert mc.get_task_type_summary() == {}

    def test_thread_safe_under_concurrent_calls(self):
        """record_task_result uses the same _lock as the rest of MetricsCollector."""
        import threading

        mc = MetricsCollector()

        def record_n(n: int) -> None:
            for _ in range(n):
                mc.record_task_result("code_generation", True, 10, 20, 100.0)

        threads = [threading.Thread(target=record_n, args=(25,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        summary = mc.get_task_type_summary()
        cg = summary["code_generation"]
        assert cg["total"] == 100
        assert cg["tokens_in"] == 1000

    def test_record_task_result_with_phase_breakdown(self):
        """Per-phase latency breakdown (model_call, extract_code, etc.)."""
        mc = MetricsCollector()
        mc.record_task_result(
            "code_generation", True, 100, 200, 3500.0,
            phase_latency_ms={
                "model_call": 3000.0,
                "extract_code": 1.0,
                "ast_parse": 2.0,
                "game_verify": 100.0,
            },
        )
        mc.record_task_result(
            "code_generation", True, 100, 200, 4200.0,
            phase_latency_ms={
                "model_call": 3800.0,
                "extract_code": 1.0,
                "ast_parse": 2.0,
                "game_verify": 100.0,
            },
        )
        summary = mc.get_task_type_summary()
        cg = summary["code_generation"]
        phases = cg["phase_avg_ms"]
        assert phases["model_call"] == 3400.0
        assert phases["extract_code"] == 1.0


class TestTaskMetricExport:
    """get_task_type_summary returns serialisable data."""

    def test_json_serialisable(self):
        import json

        mc = MetricsCollector()
        mc.record_task_result("code_generation", True, 100, 200, 3500.0)
        mc.record_task_result("code_generation", False, 100, 200, 800.0,
                              error="some error")

        summary = mc.get_task_type_summary()
        dumped = json.dumps(summary)
        assert "code_generation" in dumped
        assert "tokens_in" in dumped

    def test_reset_clears_all_task_metrics(self):
        mc = MetricsCollector()
        mc.record_task_result("code_generation", True, 100, 200, 3500.0)
        assert mc.get_task_type_summary() != {}
        mc.reset_task_metrics()
        assert mc.get_task_type_summary() == {}
