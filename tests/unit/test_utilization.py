"""Unit tests for compute utilization maximizer."""

from __future__ import annotations

from general_ludd.infra.utilization import (
    ComputeEndpoint,
    TaskRouting,
    UtilizationTracker,
)


class TestComputeEndpointProperties:
    def test_utilization_zero_load(self):
        ep = ComputeEndpoint(endpoint_id="e1", url="http://e1", max_concurrent=4)
        assert ep.utilization == 0.0

    def test_utilization_half_load(self):
        ep = ComputeEndpoint(endpoint_id="e1", url="http://e1", max_concurrent=4, current_load=2)
        assert ep.utilization == 0.5

    def test_utilization_full_load(self):
        ep = ComputeEndpoint(endpoint_id="e1", url="http://e1", max_concurrent=4, current_load=4)
        assert ep.utilization == 1.0

    def test_utilization_zero_max_concurrent(self):
        ep = ComputeEndpoint(endpoint_id="e1", url="http://e1", max_concurrent=0)
        assert ep.utilization == 0.0

    def test_cache_hit_rate_zero_requests(self):
        ep = ComputeEndpoint(endpoint_id="e1", url="http://e1")
        assert ep.cache_hit_rate == 0.0

    def test_cache_hit_rate_with_hits(self):
        ep = ComputeEndpoint(endpoint_id="e1", url="http://e1", total_requests=10, cache_hits=3)
        assert ep.cache_hit_rate == 0.3

    def test_is_available_active_with_slots(self):
        ep = ComputeEndpoint(endpoint_id="e1", url="http://e1", active=True, current_load=2, max_concurrent=4)
        assert ep.is_available is True

    def test_is_available_inactive(self):
        ep = ComputeEndpoint(endpoint_id="e1", url="http://e1", active=False, current_load=0, max_concurrent=4)
        assert ep.is_available is False

    def test_is_available_full(self):
        ep = ComputeEndpoint(endpoint_id="e1", url="http://e1", active=True, current_load=4, max_concurrent=4)
        assert ep.is_available is False

    def test_available_slots_normal(self):
        ep = ComputeEndpoint(endpoint_id="e1", url="http://e1", max_concurrent=8, current_load=3)
        assert ep.available_slots == 5

    def test_available_slots_zero(self):
        ep = ComputeEndpoint(endpoint_id="e1", url="http://e1", max_concurrent=4, current_load=4)
        assert ep.available_slots == 0

    def test_available_slots_negative_clamped(self):
        ep = ComputeEndpoint(endpoint_id="e1", url="http://e1", max_concurrent=2, current_load=5)
        assert ep.available_slots == 0


class TestUtilizationTrackerRegister:
    def test_register_endpoint(self):
        tracker = UtilizationTracker()
        ep = tracker.register_endpoint("e1", "http://e1", model="llama3")
        assert ep.endpoint_id == "e1"
        assert ep.url == "http://e1"
        assert ep.model == "llama3"
        assert ep.active is True

    def test_register_endpoint_with_kwargs(self):
        tracker = UtilizationTracker()
        ep = tracker.register_endpoint("e1", "http://e1", gpu_type="h100", gpu_count=4)
        assert ep.gpu_type == "h100"
        assert ep.gpu_count == 4

    def test_register_duplicate_overwrites(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://old")
        ep = tracker.register_endpoint("e1", "http://new")
        assert ep.url == "http://new"
        assert len(tracker.list_endpoints(active_only=False)) == 1

    def test_unregister_endpoint(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1")
        tracker.unregister_endpoint("e1")
        assert tracker.get_endpoint("e1") is None

    def test_unregister_nonexistent_is_noop(self):
        tracker = UtilizationTracker()
        tracker.unregister_endpoint("nonexistent")

    def test_get_endpoint(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1")
        ep = tracker.get_endpoint("e1")
        assert ep is not None
        assert ep.endpoint_id == "e1"

    def test_get_endpoint_missing_returns_none(self):
        tracker = UtilizationTracker()
        assert tracker.get_endpoint("missing") is None

    def test_list_endpoints_active_only(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1")
        tracker.register_endpoint("e2", "http://e2")
        tracker.unregister_endpoint("e2")
        active = tracker.list_endpoints(active_only=True)
        assert len(active) == 1
        assert active[0].endpoint_id == "e1"

    def test_list_endpoints_all(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1")
        tracker.register_endpoint("e2", "http://e2")
        all_eps = tracker.list_endpoints(active_only=False)
        assert len(all_eps) == 2


class TestUtilizationTrackerRouting:
    def test_route_task_to_least_utilized(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", model="llama3", max_concurrent=4, current_load=3)
        tracker.register_endpoint("e2", "http://e2", model="llama3", max_concurrent=4, current_load=1)
        result = tracker.route_task("t1")
        assert result is not None
        assert result.endpoint_id == "e2"
        assert result.reason == "least_utilized"
        assert result.cache_hit is False

    def test_route_task_prefers_model_match(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", model="llama3", max_concurrent=4)
        tracker.register_endpoint("e2", "http://e2", model="mistral", max_concurrent=4)
        result = tracker.route_task("t1", model="llama3", prefer_model=True)
        assert result is not None
        assert result.endpoint_id == "e1"

    def test_route_task_no_model_fallback(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", model="llama3", max_concurrent=4, current_load=3)
        tracker.register_endpoint("e2", "http://e2", model="mistral", max_concurrent=4, current_load=0)
        result = tracker.route_task("t1", model="gpt4", prefer_model=True)
        assert result is not None
        assert result.endpoint_id == "e2"

    def test_route_task_no_available_endpoints(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=2, current_load=2)
        result = tracker.route_task("t1")
        assert result is None

    def test_route_task_zero_endpoints(self):
        tracker = UtilizationTracker()
        result = tracker.route_task("t1")
        assert result is None

    def test_route_task_increments_load(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4)
        tracker.route_task("t1")
        ep = tracker.get_endpoint("e1")
        assert ep is not None
        assert ep.current_load == 1
        assert ep.total_requests == 1

    def test_route_task_updates_last_used(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4)
        tracker.route_task("t1")
        ep = tracker.get_endpoint("e1")
        assert ep is not None
        assert ep.last_used > 0.0

    def test_release_task_decrements_load(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4)
        tracker.route_task("t1")
        tracker.release_task("t1")
        ep = tracker.get_endpoint("e1")
        assert ep is not None
        assert ep.current_load == 0

    def test_release_unknown_task_is_noop(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4)
        tracker.release_task("nonexistent")
        ep = tracker.get_endpoint("e1")
        assert ep is not None
        assert ep.current_load == 0

    def test_max_concurrent_enforcement(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=2)
        r1 = tracker.route_task("t1")
        r2 = tracker.route_task("t2")
        r3 = tracker.route_task("t3")
        assert r1 is not None
        assert r2 is not None
        assert r3 is None


class TestUtilizationTrackerBalancing:
    def test_balances_across_multiple_endpoints(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4)
        tracker.register_endpoint("e2", "http://e2", max_concurrent=4)
        r1 = tracker.route_task("t1")
        r2 = tracker.route_task("t2")
        assert r1 is not None
        assert r2 is not None
        assert r1.endpoint_id != r2.endpoint_id

    def test_fills_least_loaded_first(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4, current_load=0)
        tracker.register_endpoint("e2", "http://e2", max_concurrent=4, current_load=2)
        result = tracker.route_task("t1")
        assert result is not None
        assert result.endpoint_id == "e1"

    def test_single_endpoint_routes_until_full(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=3)
        r1 = tracker.route_task("t1")
        r2 = tracker.route_task("t2")
        r3 = tracker.route_task("t3")
        r4 = tracker.route_task("t4")
        assert r1 is not None
        assert r2 is not None
        assert r3 is not None
        assert r4 is None


class TestUtilizationTrackerReport:
    def test_utilization_report_empty(self):
        tracker = UtilizationTracker()
        report = tracker.get_utilization_report()
        assert report["overall_utilization_pct"] == 0.0
        assert report["total_capacity"] == 0
        assert report["total_load"] == 0
        assert len(report["endpoints"]) == 0

    def test_utilization_report_with_endpoints(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", model="llama3", max_concurrent=4)
        tracker.route_task("t1")
        report = tracker.get_utilization_report()
        assert report["overall_utilization_pct"] == 25.0
        assert report["total_capacity"] == 4
        assert report["total_load"] == 1
        assert len(report["endpoints"]) == 1
        ep_report = report["endpoints"][0]
        assert ep_report["endpoint_id"] == "e1"
        assert ep_report["utilization_pct"] == 25.0
        assert ep_report["cache_hit_rate"] == 0.0
        assert ep_report["active"] is True

    def test_utilization_report_excludes_inactive_from_totals(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4)
        tracker.register_endpoint("e2", "http://e2", max_concurrent=8)
        tracker.unregister_endpoint("e2")
        report = tracker.get_utilization_report()
        assert report["total_capacity"] == 4


class TestUtilizationTrackerUnderutilized:
    def test_find_underutilized(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4, current_load=1)
        tracker.register_endpoint("e2", "http://e2", max_concurrent=4, current_load=4)
        result = tracker.find_underutilized(threshold=0.5)
        assert len(result) == 1
        assert result[0].endpoint_id == "e1"

    def test_find_underutilized_none(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4, current_load=4)
        result = tracker.find_underutilized(threshold=0.5)
        assert len(result) == 0

    def test_find_underutilized_excludes_inactive(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4, current_load=0)
        tracker.unregister_endpoint("e1")
        result = tracker.find_underutilized(threshold=0.5)
        assert len(result) == 0


class TestUtilizationTrackerSuggestions:
    def test_suggest_task_assignment(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", model="llama3", max_concurrent=4)
        tracker.register_endpoint("e2", "http://e2", model="mistral", max_concurrent=4, current_load=3)
        suggestions = tracker.suggest_task_assignment(count=1)
        assert len(suggestions) == 1
        assert suggestions[0].endpoint_id == "e1"
        assert suggestions[0].reason == "suggested_for_utilization"

    def test_suggest_task_assignment_multiple(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4)
        tracker.register_endpoint("e2", "http://e2", max_concurrent=4)
        suggestions = tracker.suggest_task_assignment(count=2)
        assert len(suggestions) == 2

    def test_suggest_task_assignment_none_available(self):
        tracker = UtilizationTracker()
        suggestions = tracker.suggest_task_assignment(count=3)
        assert len(suggestions) == 0

    def test_suggest_task_assignment_limited_by_available(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4)
        suggestions = tracker.suggest_task_assignment(count=5)
        assert len(suggestions) == 5


class TestUtilizationTrackerTokens:
    def test_record_tokens(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4)
        tracker.record_tokens("e1", 100)
        ep = tracker.get_endpoint("e1")
        assert ep is not None
        assert ep.total_tokens == 100

    def test_record_tokens_accumulates(self):
        tracker = UtilizationTracker()
        tracker.register_endpoint("e1", "http://e1", max_concurrent=4)
        tracker.record_tokens("e1", 100)
        tracker.record_tokens("e1", 200)
        ep = tracker.get_endpoint("e1")
        assert ep is not None
        assert ep.total_tokens == 300

    def test_record_tokens_unknown_endpoint_is_noop(self):
        tracker = UtilizationTracker()
        tracker.record_tokens("nonexistent", 100)


class TestTaskRoutingDataclass:
    def test_task_routing_defaults(self):
        tr = TaskRouting(task_id="t1", endpoint_id="e1")
        assert tr.task_id == "t1"
        assert tr.endpoint_id == "e1"
        assert tr.model == ""
        assert tr.cache_hit is False
        assert tr.reason == ""

    def test_task_routing_with_values(self):
        tr = TaskRouting(task_id="t1", endpoint_id="e1", model="llama3", cache_hit=True, reason="cache_hit")
        assert tr.model == "llama3"
        assert tr.cache_hit is True
        assert tr.reason == "cache_hit"
