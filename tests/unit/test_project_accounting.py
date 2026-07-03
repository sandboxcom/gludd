"""Per-project accounting: cost, time, and LoC aggregation.

Validates MetricsCollector.get_time_by_project, get_loc_by_project, and
get_project_accounting_summary against seeded records.
"""

from __future__ import annotations

import threading

from general_ludd.metrics.collector import MetricsCollector


class TestGetTimeByProject:
    def test_aggregates_task_execution_time_per_project(self):
        mc = MetricsCollector()
        mc.record_task_time("proj-a", 12.5)
        mc.record_task_time("proj-a", 7.3)
        mc.record_task_time("proj-b", 4.0)
        mc.record_task_time("proj-a", 0.2)

        result = mc.get_time_by_project()
        assert result == {"proj-a": 20.0, "proj-b": 4.0}

    def test_empty_when_no_time_recorded(self):
        mc = MetricsCollector()
        assert mc.get_time_by_project() == {}

    def test_ignores_empty_project_id(self):
        mc = MetricsCollector()
        mc.record_task_time("", 5.0)
        mc.record_task_time("proj-a", 3.0)
        assert mc.get_time_by_project() == {"proj-a": 3.0}


class TestGetLocByProject:
    def test_aggregates_loc_per_project(self):
        mc = MetricsCollector()
        mc.record_task_loc("proj-x", 150)
        mc.record_task_loc("proj-x", 42)
        mc.record_task_loc("proj-y", 12)

        result = mc.get_loc_by_project()
        assert result == {"proj-x": 192, "proj-y": 12}

    def test_empty_when_no_loc_recorded(self):
        mc = MetricsCollector()
        assert mc.get_loc_by_project() == {}

    def test_ignores_empty_project_id(self):
        mc = MetricsCollector()
        mc.record_task_loc("", 999)
        mc.record_task_loc("proj-x", 10)
        assert mc.get_loc_by_project() == {"proj-x": 10}


class TestGetProjectAccountingSummary:
    _agent_counter = 0

    def _make_agent_cost(self, mc: MetricsCollector, project: str, cost: float) -> None:
        TestGetProjectAccountingSummary._agent_counter += 1
        cid = TestGetProjectAccountingSummary._agent_counter
        agent = mc.register_agent(f"agent-{project}-{cid}", "test", project)
        agent.get_or_create_usage(
            "m1",
            cost_per_input_token=cost,
            cost_per_output_token=0.0,
        )
        mc.record_model_call(
            f"agent-{project}-{cid}",
            "m1",
            input_tokens=1,
            output_tokens=0,
            success=True,
        )

    def test_combines_cost_time_loc_per_project(self):
        mc = MetricsCollector()
        self._make_agent_cost(mc, "proj-1", 0.05)
        self._make_agent_cost(mc, "proj-1", 0.03)
        self._make_agent_cost(mc, "proj-2", 0.10)
        mc.record_task_time("proj-1", 30.0)
        mc.record_task_time("proj-1", 15.0)
        mc.record_task_time("proj-2", 5.0)
        mc.record_task_loc("proj-1", 200)
        mc.record_task_loc("proj-2", 50)

        summary = mc.get_project_accounting_summary()
        assert summary["proj-1"] == {
            "cost_usd": 0.08,
            "elapsed_seconds": 45.0,
            "loc_changed": 200,
        }
        assert summary["proj-2"] == {
            "cost_usd": 0.10,
            "elapsed_seconds": 5.0,
            "loc_changed": 50,
        }

    def test_project_with_only_time_shows_zero_cost_and_loc(self):
        mc = MetricsCollector()
        mc.record_task_time("lonely", 60.0)

        summary = mc.get_project_accounting_summary()
        assert summary["lonely"] == {
            "cost_usd": 0.0,
            "elapsed_seconds": 60.0,
            "loc_changed": 0,
        }

    def test_project_with_only_cost_shows_zero_time_and_loc(self):
        mc = MetricsCollector()
        self._make_agent_cost(mc, "costly", 0.25)

        summary = mc.get_project_accounting_summary()
        assert summary["costly"] == {
            "cost_usd": 0.25,
            "elapsed_seconds": 0.0,
            "loc_changed": 0,
        }

    def test_project_with_only_loc_shows_zero_cost_and_time(self):
        mc = MetricsCollector()
        mc.record_task_loc("verbose", 500)

        summary = mc.get_project_accounting_summary()
        assert summary["verbose"] == {
            "cost_usd": 0.0,
            "elapsed_seconds": 0.0,
            "loc_changed": 500,
        }

    def test_empty_collector_returns_empty_dict(self):
        mc = MetricsCollector()
        assert mc.get_project_accounting_summary() == {}


class TestConcurrentAccounting:
    def test_concurrent_record_task_time_does_not_lose_data(self):
        mc = MetricsCollector()
        _THREADS = 8
        _RECORDS_PER_THREAD = 500
        barrier = threading.Barrier(_THREADS)
        errors: list[BaseException] = []

        def worker(tid: int) -> None:
            try:
                barrier.wait()
                for i in range(_RECORDS_PER_THREAD):
                    mc.record_task_time("p", float(tid * 1000 + i) * 0.01)
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(t,)) for t in range(_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"writer threads raised: {errors!r}"
        mc.get_time_by_project()
        # 8 threads * 500 records = 4000 entries for project "p"
        assert len(mc._task_times.get("p", [])) == _THREADS * _RECORDS_PER_THREAD, (
            f"expected {_THREADS * _RECORDS_PER_THREAD} entries, "
            f"got {len(mc._task_times.get('p', []))}"
        )

    def test_concurrent_record_task_loc_does_not_lose_data(self):
        mc = MetricsCollector()
        _THREADS = 8
        _RECORDS_PER_THREAD = 500
        barrier = threading.Barrier(_THREADS)
        errors: list[BaseException] = []

        def worker(tid: int) -> None:
            try:
                barrier.wait()
                for i in range(_RECORDS_PER_THREAD):
                    mc.record_task_loc("q", tid * 100 + i)
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(t,)) for t in range(_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"writer threads raised: {errors!r}"
        assert len(mc._task_loc.get("q", [])) == _THREADS * _RECORDS_PER_THREAD, (
            f"expected {_THREADS * _RECORDS_PER_THREAD} entries, "
            f"got {len(mc._task_loc.get('q', []))}"
        )
