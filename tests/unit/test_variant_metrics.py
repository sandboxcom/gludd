"""Unit tests for VariantMetrics and PromptVariantSelector auto-promotion (G6)."""

from __future__ import annotations

import os
import tempfile

from general_ludd.prompts.variant_metrics import VariantMetrics
from general_ludd.prompts.variant_selector import PromptVariantSelector

# ----------------------------------------------------------------------
# VariantMetrics tests
# ----------------------------------------------------------------------


class TestVariantMetrics:
    def test_round_robin_during_warmup_less_than_min_samples(self):
        """get_winner returns None when fewer than 10 samples per variant."""
        m = VariantMetrics(min_samples_per_variant=10)
        for _ in range(5):
            m.record_outcome("tpl", "A", success=True, latency_ms=100.0)
            m.record_outcome("tpl", "B", success=True, latency_ms=100.0)
        assert m.get_winner("tpl") is None
        assert m.total_samples("tpl", "A") == 5
        assert m.total_samples("tpl", "B") == 5

    def test_winner_by_success_rate(self):
        """Higher success rate wins."""
        m = VariantMetrics(min_samples_per_variant=3)
        m.record_outcome("tpl", "A", success=True, latency_ms=100.0)
        m.record_outcome("tpl", "A", success=True, latency_ms=100.0)
        m.record_outcome("tpl", "A", success=True, latency_ms=100.0)
        m.record_outcome("tpl", "B", success=False, latency_ms=100.0)
        m.record_outcome("tpl", "B", success=False, latency_ms=100.0)
        m.record_outcome("tpl", "B", success=False, latency_ms=100.0)
        assert m.get_winner("tpl") == "A"

    def test_winner_by_latency_tiebreaker(self):
        """When success rates are equal, lower latency wins."""
        m = VariantMetrics(min_samples_per_variant=3)
        m.record_outcome("tpl", "A", success=True, latency_ms=200.0)
        m.record_outcome("tpl", "A", success=True, latency_ms=200.0)
        m.record_outcome("tpl", "A", success=True, latency_ms=200.0)
        m.record_outcome("tpl", "B", success=True, latency_ms=100.0)
        m.record_outcome("tpl", "B", success=True, latency_ms=100.0)
        m.record_outcome("tpl", "B", success=True, latency_ms=100.0)
        assert m.get_winner("tpl") == "B"

    def test_no_winner_when_equal(self):
        """When both success and latency are equal, returns None."""
        m = VariantMetrics(min_samples_per_variant=3)
        for _ in range(3):
            m.record_outcome("tpl", "A", success=True, latency_ms=100.0)
            m.record_outcome("tpl", "B", success=True, latency_ms=100.0)
        assert m.get_winner("tpl") is None

    def test_promote_marks_winner(self):
        """promote_winner sets the promoted flag and returns the winner."""
        m = VariantMetrics(min_samples_per_variant=3)
        m.record_outcome("tpl", "A", success=True, latency_ms=100.0)
        m.record_outcome("tpl", "A", success=True, latency_ms=100.0)
        m.record_outcome("tpl", "A", success=True, latency_ms=100.0)
        m.record_outcome("tpl", "B", success=False, latency_ms=100.0)
        m.record_outcome("tpl", "B", success=False, latency_ms=100.0)
        m.record_outcome("tpl", "B", success=False, latency_ms=100.0)
        winner = m.promote_winner("tpl")
        assert winner == "A"
        assert m.is_promoted("tpl") == "A"

    def test_json_persistence_round_trip(self):
        """Metrics survive a save + reload cycle."""
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "metrics.json")
            m1 = VariantMetrics(storage_path=path, min_samples_per_variant=3)
            m1.record_outcome("tpl", "A", success=True, latency_ms=100.0)
            m1.record_outcome("tpl", "A", success=True, latency_ms=100.0)
            m1.record_outcome("tpl", "A", success=True, latency_ms=100.0)
            m1.record_outcome("tpl", "B", success=False, latency_ms=100.0)
            m1.record_outcome("tpl", "B", success=False, latency_ms=100.0)
            m1.record_outcome("tpl", "B", success=False, latency_ms=100.0)
            m1.promote_winner("tpl")

            m2 = VariantMetrics(storage_path=path, min_samples_per_variant=3)
            assert m2.get_winner("tpl") == "A"
            assert m2.is_promoted("tpl") == "A"
            assert m2.total_samples("tpl", "A") == 3
            assert m2.total_samples("tpl", "B") == 3
        finally:
            if os.path.isfile(path):
                os.remove(path)
            os.rmdir(tmp)

    def test_multiple_templates_tracked_independently(self):
        """Each template name has its own stats."""
        m = VariantMetrics(min_samples_per_variant=3)
        m.record_outcome("tpl_a", "A", success=True, latency_ms=100.0)
        m.record_outcome("tpl_a", "A", success=True, latency_ms=100.0)
        m.record_outcome("tpl_a", "A", success=True, latency_ms=100.0)
        m.record_outcome("tpl_a", "B", success=False, latency_ms=100.0)
        m.record_outcome("tpl_a", "B", success=False, latency_ms=100.0)
        m.record_outcome("tpl_a", "B", success=False, latency_ms=100.0)

        m.record_outcome("tpl_b", "A", success=False, latency_ms=100.0)
        m.record_outcome("tpl_b", "A", success=False, latency_ms=100.0)
        m.record_outcome("tpl_b", "A", success=False, latency_ms=100.0)
        m.record_outcome("tpl_b", "B", success=True, latency_ms=100.0)
        m.record_outcome("tpl_b", "B", success=True, latency_ms=100.0)
        m.record_outcome("tpl_b", "B", success=True, latency_ms=100.0)

        assert m.get_winner("tpl_a") == "A"
        assert m.get_winner("tpl_b") == "B"


# ----------------------------------------------------------------------
# PromptVariantSelector + VariantMetrics integration tests
# ----------------------------------------------------------------------


class TestVariantSelectorWithMetrics:
    def test_round_robin_without_metrics(self):
        """Without metrics, selector always alternates."""
        selector = PromptVariantSelector(enabled=True)
        results = [selector.select("tpl")["variant"] for _ in range(12)]
        expected = ["A", "B"] * 6
        assert results == expected

    def test_winner_only_after_promotion(self):
        """After promotion, select() always returns the winner variant."""
        metrics = VariantMetrics(min_samples_per_variant=3)
        metrics.record_outcome("tpl", "A", success=True, latency_ms=50.0)
        metrics.record_outcome("tpl", "A", success=True, latency_ms=50.0)
        metrics.record_outcome("tpl", "A", success=True, latency_ms=50.0)
        metrics.record_outcome("tpl", "B", success=False, latency_ms=200.0)
        metrics.record_outcome("tpl", "B", success=False, latency_ms=200.0)
        metrics.record_outcome("tpl", "B", success=False, latency_ms=200.0)
        metrics.promote_winner("tpl")

        selector = PromptVariantSelector(enabled=True, variant_metrics=metrics)
        results = [selector.select("tpl")["variant"] for _ in range(10)]
        assert all(v == "A" for v in results)

    def test_record_outcome_noop_without_metrics(self):
        """record_outcome is a no-op when no variant_metrics is wired."""
        selector = PromptVariantSelector(enabled=True)
        selector.select("tpl")
        selector.record_outcome(success=True, latency_ms=100.0)  # should not raise

    def test_record_outcome_delegates_to_metrics(self):
        """record_outcome actually records in the metrics instance."""
        metrics = VariantMetrics(min_samples_per_variant=5)
        selector = PromptVariantSelector(enabled=True, variant_metrics=metrics)
        selector.select("tpl")
        selector.record_outcome(success=True, latency_ms=50.0)
        assert metrics.total_samples("tpl", "A") == 1
