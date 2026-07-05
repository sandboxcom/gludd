"""Integration tests for G6 prompt-variant end-to-end pipeline.

Covers the full loop: select variant → dispatch job with variant →
record outcome → check winner → auto-promotion → variant report.
"""

from __future__ import annotations

import json
import os
import tempfile

from general_ludd.prompts.variant_metrics import VariantMetrics
from general_ludd.prompts.variant_selector import PromptVariantSelector


def _tmp_metrics(min_samples: int = 3) -> VariantMetrics:
    path = os.path.join(tempfile.mkdtemp(), "metrics.json")
    return VariantMetrics(storage_path=path, min_samples_per_variant=min_samples)


def _read_metrics_json(metrics: VariantMetrics) -> dict:
    with open(metrics._storage_path, encoding="utf-8") as fh:
        return json.load(fh)


class TestVariantPipelineE2E:
    def test_full_pipeline_select_dispatch_record_winner(self):
        """
        Simulate the event loop dispatch flow:
          1. select() → variant
          2. simulate dispatch with that variant
          3. record_outcome(success, latency)
          4. after min_samples, get_winner() returns the better variant
        """
        metrics = _tmp_metrics(min_samples=5)
        selector = PromptVariantSelector(enabled=True, variant_metrics=metrics)

        template = "dispatch_started"

        for _ in range(5):
            r_a = selector.select(template)
            assert r_a["variant"] == "A"
            assert r_a["run_index"] is not None
            assert r_a["template_name"] == template
            selector.record_outcome(success=True, latency_ms=100.0)

            r_b = selector.select(template)
            assert r_b["variant"] == "B"
            selector.record_outcome(success=False, latency_ms=500.0)

        winner = metrics.get_winner(template)
        assert winner == "A"
        assert metrics.total_samples(template, "A") == 5
        assert metrics.total_samples(template, "B") == 5

    def test_multiple_jobs_different_outcomes_correct_winner(self):
        """
        Variant A: 5 successes, 0 failures (100%)
        Variant B: 1 success, 4 failures (20%)
        Variant A should win.
        """
        metrics = _tmp_metrics(min_samples=5)
        selector = PromptVariantSelector(enabled=True, variant_metrics=metrics)
        template = "code_generation"

        outcomes_a = [(True, 100.0), (True, 110.0), (True, 95.0), (True, 105.0), (True, 100.0)]
        outcomes_b = [(True, 200.0), (False, 300.0), (False, 250.0), (False, 280.0), (False, 260.0)]

        all_outcomes = []
        for a, b in zip(outcomes_a, outcomes_b, strict=True):
            all_outcomes.append(("A", a))
            all_outcomes.append(("B", b))

        for expected_variant, (success, latency) in all_outcomes:
            result = selector.select(template)
            variant = result["variant"]
            assert variant == expected_variant

            # Simulate dispatch: use the pre-determined outcome for this variant
            selector.record_outcome(success=success, latency_ms=latency)

        winner = metrics.get_winner(template)
        assert winner == "A"

        stats = metrics.stats(template)
        assert stats["A"]["total"] == 5
        assert stats["A"]["successes"] == 5
        assert stats["B"]["total"] == 5
        assert stats["B"]["successes"] == 1

    def test_auto_promotion_triggers_after_min_samples(self):
        """
        Pre-load metrics with enough data for a winner, then wire a fresh
        selector. The very first select() call should detect the winner and
        auto-promote, switching from round-robin to winner-only.
        """
        metrics = _tmp_metrics(min_samples=3)
        template = "dispatch_started"

        # Pre-load: A wins (3/3 success), B loses (0/3 success)
        for _ in range(3):
            metrics.record_outcome(template, "A", success=True, latency_ms=50.0)
        for _ in range(3):
            metrics.record_outcome(template, "B", success=False, latency_ms=200.0)

        assert metrics.get_winner(template) == "A"
        assert metrics.is_promoted(template) is None

        selector = PromptVariantSelector(enabled=True, variant_metrics=metrics)

        # First select should trigger auto-promotion (get_winner + promote_winner)
        result = selector.select(template)
        assert result["variant"] == "A"
        assert metrics.is_promoted(template) == "A"

        # Subsequent selects should all return the promoted winner
        for _ in range(5):
            result = selector.select(template)
            assert result["variant"] == "A"

    def test_round_robin_during_warmup_winner_only_after_promotion(self):
        """
        Without enough samples, round-robin alternates between A and B.
        Once enough data exists and a winner is promoted, all subsequent
        selects return the winner.
        """
        metrics = _tmp_metrics(min_samples=5)
        template = "dispatch_started"
        selector = PromptVariantSelector(enabled=True, variant_metrics=metrics)

        # Warmup phase: 4 samples each, not enough for a winner
        for _ in range(4):
            r_a = selector.select(template)
            assert r_a["variant"] == "A"
            selector.record_outcome(success=True, latency_ms=50.0)

            r_b = selector.select(template)
            assert r_b["variant"] == "B"
            selector.record_outcome(success=False, latency_ms=200.0)

        assert metrics.get_winner(template) is None
        assert metrics.is_promoted(template) is None

        # 5th sample for each hits min_samples → winner detected on select()
        r_a = selector.select(template)
        assert r_a["variant"] == "A"
        selector.record_outcome(success=True, latency_ms=50.0)

        r_b = selector.select(template)
        assert r_b["variant"] == "B"
        selector.record_outcome(success=False, latency_ms=200.0)

        # Now min_samples met, next select should auto-promote
        r_next = selector.select(template)
        assert r_next["variant"] == "A"
        assert metrics.is_promoted(template) == "A"

        # All subsequent selects return the winner
        for _ in range(10):
            result = selector.select(template)
            assert result["variant"] == "A"

    def test_variant_report_reflects_real_data(self):
        """
        generate_variant_report() should accurately reflect recorded outcomes,
        including sample counts, success rates, winner, promoted status, and
        sufficient_data flag.
        """
        metrics = _tmp_metrics(min_samples=3)
        template = "code_generation"

        for _ in range(3):
            metrics.record_outcome(template, "A", success=True, latency_ms=100.0)
        for _ in range(3):
            metrics.record_outcome(template, "B", success=False, latency_ms=500.0)

        metrics.promote_winner(template)

        report = metrics.generate_variant_report()
        assert report["template_count"] == 1

        tmpl = report["templates"][template]
        assert tmpl["sufficient_data"] is True
        assert tmpl["winner"] == "A"
        assert tmpl["promoted"] == "A"

        va = tmpl["variants"]["A"]
        vb = tmpl["variants"]["B"]

        assert va["samples"] == 3
        assert va["successes"] == 3
        assert va["success_rate"] == 1.0
        assert va["avg_latency_ms"] == 100.0

        assert vb["samples"] == 3
        assert vb["successes"] == 0
        assert vb["success_rate"] == 0.0
        assert vb["avg_latency_ms"] == 500.0

        assert tmpl["margin"]["success_rate_delta"] == 1.0
        assert tmpl["margin"]["winning_metric"] == "success_rate"
        assert "A leads B" in tmpl["margin"]["description"]

    def test_persistence_survives_selector_reload(self):
        """
        After an initial run with outcomes and promotion, a fresh metrics
        instance loaded from the same JSON file should retain all data,
        and a new selector should immediately use the promoted winner.
        """
        template = "dispatch_started"
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "metrics.json")

        # First session: record outcomes and promote
        m1 = VariantMetrics(storage_path=path, min_samples_per_variant=3)
        for _ in range(3):
            m1.record_outcome(template, "A", success=True, latency_ms=50.0)
        for _ in range(3):
            m1.record_outcome(template, "B", success=False, latency_ms=200.0)
        m1.promote_winner(template)
        assert m1.is_promoted(template) == "A"

        # Second session: load from disk, wire new selector
        m2 = VariantMetrics(storage_path=path, min_samples_per_variant=3)
        assert m2.get_winner(template) == "A"
        assert m2.is_promoted(template) == "A"

        selector2 = PromptVariantSelector(enabled=True, variant_metrics=m2)
        results = [selector2.select(template)["variant"] for _ in range(5)]
        assert all(v == "A" for v in results)

        # Cleanup
        os.remove(path)
        os.rmdir(tmpdir)

    def test_latency_tiebreaker_when_success_rates_equal(self):
        """
        When both variants have identical success rates, the one with lower
        average latency wins.
        """
        metrics = _tmp_metrics(min_samples=3)
        template = "dispatch_started"
        selector = PromptVariantSelector(enabled=True, variant_metrics=metrics)

        for _ in range(3):
            result = selector.select(template)
            assert result["variant"] == "A"
            selector.record_outcome(success=True, latency_ms=300.0)

            result = selector.select(template)
            assert result["variant"] == "B"
            selector.record_outcome(success=True, latency_ms=100.0)

        winner = metrics.get_winner(template)
        assert winner == "B"

        metrics.promote_winner(template)
        report = metrics.generate_variant_report()
        tmpl = report["templates"][template]
        assert tmpl["winner"] == "B"
        assert tmpl["margin"]["winning_metric"] == "latency"

    def test_multiple_templates_independent_in_pipeline(self):
        """
        Two templates track independently. One can be promoted while the
        other remains in round-robin.
        """
        metrics = _tmp_metrics(min_samples=3)

        # Template "alpha": A dominates, gets promoted
        for _ in range(3):
            metrics.record_outcome("alpha", "A", success=True, latency_ms=50.0)
        for _ in range(3):
            metrics.record_outcome("alpha", "B", success=False, latency_ms=200.0)
        metrics.promote_winner("alpha")
        assert metrics.is_promoted("alpha") == "A"

        # Template "beta": only 1 sample each, still in warmup
        metrics.record_outcome("beta", "A", success=True, latency_ms=50.0)
        metrics.record_outcome("beta", "B", success=True, latency_ms=50.0)

        selector = PromptVariantSelector(enabled=True, variant_metrics=metrics)

        # Alpha always picks A (promoted)
        for _ in range(3):
            assert selector.select("alpha")["variant"] == "A"

        # Beta alternates (not enough data)
        b1 = selector.select("beta")["variant"]
        b2 = selector.select("beta")["variant"]
        assert b1 != b2

        report = metrics.generate_variant_report()
        assert report["template_count"] == 2
        assert report["templates"]["alpha"]["promoted"] == "A"
        assert report["templates"]["beta"]["promoted"] is None

    def test_json_on_disk_matches_in_memory_state(self):
        """
        Verify that the JSON written to disk matches the in-memory state
        after every outcome recording and promotion.
        """
        metrics = _tmp_metrics(min_samples=3)
        selector = PromptVariantSelector(enabled=True, variant_metrics=metrics)
        template = "dispatch_started"

        for i in range(3):
            selector.select(template)
            selector.record_outcome(success=True, latency_ms=50.0)
            selector.select(template)
            selector.record_outcome(success=False, latency_ms=200.0)

            on_disk = _read_metrics_json(metrics)
            expected_a_samples = i + 1
            expected_b_samples = i + 1
            assert on_disk[template]["A"]["total"] == expected_a_samples
            assert on_disk[template]["B"]["total"] == expected_b_samples
            assert on_disk[template]["A"]["successes"] == expected_a_samples
            assert on_disk[template]["B"]["successes"] == 0

        selector.select(template)  # triggers auto-promotion
        on_disk = _read_metrics_json(metrics)
        assert on_disk[template]["promoted"] == "A"
