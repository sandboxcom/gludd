"""Flakiness-detection tests for MetricsCollector.

Covers the advisor-facing helpers added so a role can prefer a fallback when a
model is flaky:
  - get_recent_failures(model_profile_id, window_calls) -> most-recent-first,
    bounded list of failure descriptions.
  - is_flaky(model_profile_id, threshold, min_calls) -> bool.

Also asserts the pre-existing success_rate / cost / token accounting is
unchanged by the append-on-failure ring buffer.
"""

from __future__ import annotations

import pytest

from general_ludd.metrics import MetricsCollector
from general_ludd.metrics.collector import _FAILURE_RING_MAXLEN


def _record(mc: MetricsCollector, success: bool, error: str | None = None) -> None:
    mc.record_model_call(
        "agent-1", "glm-4.6", 10, 5, success, error=error
    )


class TestGetRecentFailures:
    def test_unknown_model_returns_empty_list(self):
        mc = MetricsCollector()
        assert mc.get_recent_failures("never-seen") == []

    def test_model_with_only_successes_returns_empty_list(self):
        mc = MetricsCollector()
        for _ in range(5):
            _record(mc, True)
        assert mc.get_recent_failures("glm-4.6") == []

    def test_returns_most_recent_first(self):
        mc = MetricsCollector()
        _record(mc, False, "boom-1")
        _record(mc, True)
        _record(mc, False, "boom-2")
        _record(mc, False, "boom-3")
        assert mc.get_recent_failures("glm-4.6") == ["boom-3", "boom-2", "boom-1"]

    def test_window_caps_returned_count(self):
        mc = MetricsCollector()
        for i in range(8):
            _record(mc, False, f"err-{i}")
        recent = mc.get_recent_failures("glm-4.6", window_calls=3)
        assert recent == ["err-7", "err-6", "err-5"]

    def test_non_positive_window_returns_empty(self):
        mc = MetricsCollector()
        _record(mc, False, "boom")
        assert mc.get_recent_failures("glm-4.6", window_calls=0) == []
        assert mc.get_recent_failures("glm-4.6", window_calls=-3) == []

    def test_failure_without_error_records_marker(self):
        mc = MetricsCollector()
        _record(mc, False, None)
        recent = mc.get_recent_failures("glm-4.6")
        assert len(recent) == 1
        assert recent[0]  # non-empty marker string

    def test_ring_is_bounded(self):
        mc = MetricsCollector()
        total = _FAILURE_RING_MAXLEN + 25
        for i in range(total):
            _record(mc, False, f"err-{i}")
        # The full history is bounded; the bound caps retained entries.
        all_recent = mc.get_recent_failures("glm-4.6", window_calls=10_000)
        assert len(all_recent) == _FAILURE_RING_MAXLEN
        # Most-recent first means the very last error is at index 0.
        assert all_recent[0] == f"err-{total - 1}"


class TestIsFlaky:
    def test_unknown_model_is_not_flaky(self):
        mc = MetricsCollector()
        assert mc.is_flaky("never-seen") is False

    def test_below_min_calls_is_not_flaky_even_if_all_fail(self):
        mc = MetricsCollector()
        for _ in range(3):  # min_calls default is 4
            _record(mc, False, "boom")
        assert mc.is_flaky("glm-4.6") is False

    def test_flips_true_at_threshold(self):
        mc = MetricsCollector()
        # 4 calls, 2 failures -> success_rate 0.5 < 0.7 -> flaky
        _record(mc, True)
        _record(mc, False, "boom")
        _record(mc, True)
        _record(mc, False, "boom")
        assert mc.is_flaky("glm-4.6", threshold=0.7) is True

    def test_healthy_model_is_not_flaky(self):
        mc = MetricsCollector()
        # 5 calls, 1 failure -> success_rate 0.8 >= 0.7 -> not flaky
        for _ in range(4):
            _record(mc, True)
        _record(mc, False, "boom")
        assert mc.is_flaky("glm-4.6", threshold=0.7) is False

    def test_threshold_is_strict_less_than(self):
        mc = MetricsCollector()
        # 4 calls, 1 failure -> success_rate exactly 0.75
        for _ in range(3):
            _record(mc, True)
        _record(mc, False, "boom")
        # 0.75 < 0.75 is False -> not flaky at an equal threshold
        assert mc.is_flaky("glm-4.6", threshold=0.75) is False
        # 0.75 < 0.8 is True -> flaky at a higher threshold
        assert mc.is_flaky("glm-4.6", threshold=0.8) is True

    def test_min_calls_override(self):
        mc = MetricsCollector()
        _record(mc, False, "boom")
        _record(mc, False, "boom")
        assert mc.is_flaky("glm-4.6", min_calls=2) is True
        assert mc.is_flaky("glm-4.6", min_calls=5) is False


class TestExistingAccountingUnchanged:
    def test_success_rate_and_counts_unaffected_by_ring(self):
        mc = MetricsCollector()
        mc.record_model_call("a1", "gpt-4", 100, 50, True)
        mc.record_model_call("a1", "gpt-4", 100, 50, False, error="boom")
        usage = mc.get_global_model_usage()["gpt-4"]
        assert usage.total_calls == 2
        assert usage.successful_calls == 1
        assert usage.failed_calls == 1
        assert usage.success_rate == pytest.approx(0.5)
        assert usage.total_input_tokens == 200
        assert usage.total_output_tokens == 100

    def test_cost_accounting_unchanged(self):
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.record_model_call(
            "a1", "gpt-4", 1000, 500, True,
            cost_per_input_token=0.01, cost_per_output_token=0.02,
        )
        agent = mc.get_agent("a1")
        expected = 1000 * 0.01 + 500 * 0.02
        assert agent.total_cost_usd == pytest.approx(expected)

    def test_existing_callers_without_error_kwarg_still_work(self):
        # Pre-existing call style (no error kwarg) must remain valid and
        # still record a failure into the ring.
        mc = MetricsCollector()
        mc.register_agent("a1")
        mc.record_model_call("a1", "gpt-4", 10, 5, False)
        assert mc.get_agent("a1").model_usage["gpt-4"].failed_calls == 1
        assert len(mc.get_recent_failures("gpt-4")) == 1
