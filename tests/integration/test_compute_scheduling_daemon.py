"""Integration tests for compute-scheduling hint routing.

Proves that ComputeSchedulingHint routes work-type todos to the correct
GPU endpoint and that unknown work types fall back to least_utilized.
"""

from __future__ import annotations

from general_ludd.infra.utilization import UtilizationTracker
from general_ludd.scheduling.scheduler import ComputeSchedulingHint


class TestComputeSchedulingHint:
    def test_analysis_routes_to_a100(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            endpoint_id="a100-1", url="http://a100:8000",
            model="llama3", gpu_type="a100_80", max_concurrent=4,
        )
        tracker.register_endpoint(
            endpoint_id="t4-1", url="http://t4:8000",
            model="llama3", gpu_type="t4", max_concurrent=4,
        )

        hint = ComputeSchedulingHint.for_work_type("analysis")
        assert hint.preferred_gpu_type == "a100_80"

        routing = tracker.route_task(
            task_id="task-1", model="llama3",
            scheduling_hint=hint,
        )
        assert routing is not None
        assert routing.endpoint_id == "a100-1"
        assert routing.reason == "least_utilized"

    def test_review_routes_to_t4(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            endpoint_id="a100-1", url="http://a100:8000",
            model="llama3", gpu_type="a100_80", max_concurrent=4,
        )
        tracker.register_endpoint(
            endpoint_id="t4-1", url="http://t4:8000",
            model="llama3", gpu_type="t4", max_concurrent=4,
        )

        hint = ComputeSchedulingHint.for_work_type("review")
        assert hint.preferred_gpu_type == "t4"

        routing = tracker.route_task(
            task_id="task-2", model="llama3",
            scheduling_hint=hint,
        )
        assert routing is not None
        assert routing.endpoint_id == "t4-1"

    def test_unknown_work_type_falls_back_to_least_utilized(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            endpoint_id="a100-1", url="http://a100:8000",
            model="llama3", gpu_type="a100_80", max_concurrent=4, current_load=2,
        )
        tracker.register_endpoint(
            endpoint_id="t4-1", url="http://t4:8000",
            model="llama3", gpu_type="t4", max_concurrent=4, current_load=0,
        )

        hint = ComputeSchedulingHint.for_work_type("unknown_type")
        assert hint.preferred_gpu_type is None

        routing = tracker.route_task(
            task_id="task-3", model="llama3",
            scheduling_hint=hint,
        )
        assert routing is not None
        assert routing.endpoint_id == "t4-1"

    def test_no_matching_gpu_falls_back_to_available(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            endpoint_id="t4-1", url="http://t4:8000",
            model="llama3", gpu_type="t4", max_concurrent=4,
        )

        hint = ComputeSchedulingHint.for_work_type("analysis")
        assert hint.preferred_gpu_type == "a100_80"

        routing = tracker.route_task(
            task_id="task-4", model="llama3",
            scheduling_hint=hint,
        )
        assert routing is not None
        assert routing.endpoint_id == "t4-1"

    def test_self_improve_routes_to_h100(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            endpoint_id="h100-1", url="http://h100:8000",
            model="mixtral", gpu_type="h100", max_concurrent=4,
        )
        tracker.register_endpoint(
            endpoint_id="a100-1", url="http://a100:8000",
            model="mixtral", gpu_type="a100_80", max_concurrent=4,
        )

        hint = ComputeSchedulingHint.for_work_type("self_improve")
        assert hint.preferred_gpu_type == "h100"

        routing = tracker.route_task(
            task_id="task-5", model="mixtral",
            scheduling_hint=hint,
        )
        assert routing is not None
        assert routing.endpoint_id == "h100-1"

    def test_hint_with_no_preferred_gpu_is_noop(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint(
            endpoint_id="a100-1", url="http://a100:8000",
            model="llama3", gpu_type="a100_80", max_concurrent=4, current_load=3,
        )
        tracker.register_endpoint(
            endpoint_id="t4-1", url="http://t4:8000",
            model="llama3", gpu_type="t4", max_concurrent=4, current_load=0,
        )

        hint = ComputeSchedulingHint(
            preferred_gpu_type=None, min_vram_gb=0.0,
        )
        routing = tracker.route_task(
            task_id="task-6", model="llama3",
            scheduling_hint=hint,
        )
        assert routing is not None
        assert routing.endpoint_id == "t4-1"
