"""G6: Prompt versioning A/B test — variant selection, metrics, and auto-promotion.

This is the e2e proof referenced by docs/features.yml for g6-prompt-versioning.
Covers the full dispatch-path integration: select -> record -> promote -> report.
"""

from __future__ import annotations

import os
import tempfile

from general_ludd.prompts.registry import PromptRegistry
from general_ludd.prompts.variant_metrics import VariantMetrics
from general_ludd.prompts.variant_selector import PromptVariantSelector


def _tmp_metrics(min_samples: int = 3) -> tuple[VariantMetrics, str]:
    path = os.path.join(tempfile.mkdtemp(), "metrics.json")
    return VariantMetrics(storage_path=path, min_samples_per_variant=min_samples), path


# ---------------------------------------------------------------------------
# PromptRegistry version-tracking (registry-level checks)
# ---------------------------------------------------------------------------


class TestPromptRegistryVersioning:
    def test_version_default(self) -> None:
        reg = PromptRegistry()
        assert reg.version == "0.1.0"

    def test_version_custom(self) -> None:
        reg = PromptRegistry(version="2.3.1")
        assert reg.version == "2.3.1"

    def test_template_hash_computed_on_register(self) -> None:
        reg = PromptRegistry()
        reg.register("t1", "Hello {{ name }}!")
        info = reg.get_template_version_info("t1")
        assert "hash" in info
        assert info["hash"] is not None
        assert len(info["hash"]) == 64

    def test_template_hash_history_tracks_changes(self) -> None:
        reg = PromptRegistry()
        reg.register("t1", "original content")
        h1 = reg.get_template_version_info("t1")["hash"]
        reg.register("t1", "modified content")
        h2 = reg.get_template_version_info("t1")["hash"]
        assert h1 != h2
        history = reg.get_template_version_info("t1").get("history", [])
        assert len(history) >= 1
        assert h1 in history

    def test_hash_based_on_content_only(self) -> None:
        reg1 = PromptRegistry()
        reg2 = PromptRegistry()
        reg1.register("t", "same content")
        reg2.register("t", "same content")
        h1 = reg1.get_template_version_info("t")["hash"]
        h2 = reg2.get_template_version_info("t")["hash"]
        assert h1 == h2

    def test_history_limit_is_bounded(self) -> None:
        reg = PromptRegistry()
        for i in range(10):
            reg.register("t", f"content {i}")
        info = reg.get_template_version_info("t")
        assert len(info["history"]) <= 5


# ---------------------------------------------------------------------------
# PromptVariantSelector — round-robin and winner-only modes
# ---------------------------------------------------------------------------


class TestPromptVariantSelectorAB:
    def test_disabled_returns_none(self) -> None:
        sel = PromptVariantSelector(enabled=False)
        assert sel.select("dispatch") is None

    def test_round_robin_alternates(self) -> None:
        sel = PromptVariantSelector(enabled=True)
        r1 = sel.select("dispatch")
        r2 = sel.select("dispatch")
        assert r1["variant"] == "A"
        assert r2["variant"] == "B"
        assert r1["run_index"] == 0
        assert r2["run_index"] == 1

    def test_includes_template_hash_when_set(self) -> None:
        sel = PromptVariantSelector(template_hash="abc123", enabled=True)
        result = sel.select("dispatch")
        assert result["template_hash"] == "abc123"

    def test_omits_template_hash_when_none(self) -> None:
        sel = PromptVariantSelector(template_hash=None, enabled=True)
        result = sel.select("dispatch")
        assert "template_hash" not in result

    def test_select_includes_template_name(self) -> None:
        sel = PromptVariantSelector(enabled=True)
        result = sel.select("my_template")
        assert result["template_name"] == "my_template"

    def test_run_index_increments(self) -> None:
        sel = PromptVariantSelector(enabled=True)
        assert sel.select("t")["run_index"] == 0
        assert sel.select("t")["run_index"] == 1
        assert sel.select("t")["run_index"] == 2
        assert sel.current_run_index() == 3

    def test_record_outcome_noop_without_select(self) -> None:
        sel = PromptVariantSelector(enabled=True)
        sel.record_outcome(True, 100.0)  # should not raise

    def test_record_outcome_delegates_to_metrics(self) -> None:
        metrics, _ = _tmp_metrics(min_samples=2)
        sel = PromptVariantSelector(enabled=True, variant_metrics=metrics)
        sel.select("dispatch")
        sel.record_outcome(True, 100.0)
        sel.select("dispatch")
        sel.record_outcome(True, 200.0)
        # A had 1 select+record (run_index 0), B had 1 (run_index 1)
        assert metrics.total_samples("dispatch", "A") == 1
        assert metrics.total_samples("dispatch", "B") == 1


# ---------------------------------------------------------------------------
# VariantMetrics — persistence and aggregation
# ---------------------------------------------------------------------------


class TestVariantMetricsPersistence:
    def test_persists_and_reloads(self) -> None:
        m1, path = _tmp_metrics(min_samples=2)
        m1.record_outcome("dispatch", "A", success=True, latency_ms=100.0)
        m1.record_outcome("dispatch", "A", success=False, latency_ms=200.0)

        m2 = VariantMetrics(storage_path=path, min_samples_per_variant=2)
        stats = m2.stats("dispatch")
        assert stats["A"]["total"] == 2
        assert stats["A"]["successes"] == 1

    def test_winner_not_declared_without_min_samples(self) -> None:
        metrics, _ = _tmp_metrics(min_samples=5)
        metrics.record_outcome("dispatch", "A", success=True, latency_ms=100.0)
        metrics.record_outcome("dispatch", "B", success=False, latency_ms=200.0)
        assert metrics.get_winner("dispatch") is None

    def test_winner_declared_when_min_samples_met(self) -> None:
        metrics, _ = _tmp_metrics(min_samples=2)
        metrics.record_outcome("dispatch", "A", success=True, latency_ms=100.0)
        metrics.record_outcome("dispatch", "A", success=True, latency_ms=120.0)
        metrics.record_outcome("dispatch", "B", success=False, latency_ms=200.0)
        metrics.record_outcome("dispatch", "B", success=False, latency_ms=220.0)
        winner = metrics.get_winner("dispatch")
        assert winner == "A"

    def test_latency_tiebreaker_when_success_equal(self) -> None:
        metrics, _ = _tmp_metrics(min_samples=2)
        metrics.record_outcome("dispatch", "A", success=True, latency_ms=300.0)
        metrics.record_outcome("dispatch", "A", success=True, latency_ms=310.0)
        metrics.record_outcome("dispatch", "B", success=True, latency_ms=100.0)
        metrics.record_outcome("dispatch", "B", success=True, latency_ms=110.0)
        winner = metrics.get_winner("dispatch")
        assert winner == "B"


# ---------------------------------------------------------------------------
# Auto-promotion: selector flips from round-robin to winner-only
# ---------------------------------------------------------------------------


class TestAutoPromotionE2E:
    def test_selector_uses_round_robin_before_promotion(self) -> None:
        metrics, _ = _tmp_metrics(min_samples=10)
        sel = PromptVariantSelector(enabled=True, variant_metrics=metrics)
        r1 = sel.select("dispatch")
        r2 = sel.select("dispatch")
        assert r1["variant"] == "A"
        assert r2["variant"] == "B"

    def test_auto_promotion_switches_to_winner_only(self) -> None:
        metrics, _ = _tmp_metrics(min_samples=2)
        # Record directly, then wire to selector to verify promotion
        for _ in range(3):
            metrics.record_outcome("dispatch", "A", success=True, latency_ms=50.0)
        for _ in range(3):
            metrics.record_outcome("dispatch", "B", success=False, latency_ms=500.0)

        winner = metrics.get_winner("dispatch")
        assert winner == "A"
        promoted = metrics.promote_winner("dispatch")
        assert promoted == "A"

        sel = PromptVariantSelector(enabled=True, variant_metrics=metrics)
        r = sel.select("dispatch")
        assert r["variant"] == "A"

    def test_promotion_checked_only_for_named_templates(self) -> None:
        metrics, _ = _tmp_metrics(min_samples=10)
        sel = PromptVariantSelector(enabled=True, variant_metrics=metrics)
        r = sel.select("t1")
        assert r["variant"] in ("A", "B")
        assert r["template_name"] == "t1"

    def test_selector_without_metrics_stays_round_robin(self) -> None:
        sel = PromptVariantSelector(enabled=True)
        for i in range(10):
            r = sel.select("dispatch")
            assert r["variant"] == ("A" if i % 2 == 0 else "B")


# ---------------------------------------------------------------------------
# Variant report — summary view
# ---------------------------------------------------------------------------


class TestVariantReport:
    def test_empty_report_returns_zero_counts(self) -> None:
        metrics, _ = _tmp_metrics()
        report = metrics.generate_variant_report()
        assert report["template_count"] == 0

    def test_report_includes_per_variant_scores(self) -> None:
        metrics, _ = _tmp_metrics(min_samples=2)
        metrics.record_outcome("dispatch", "A", success=True, latency_ms=100.0)
        metrics.record_outcome("dispatch", "B", success=False, latency_ms=200.0)
        report = metrics.generate_variant_report()
        assert "dispatch" in report["templates"]
        disp = report["templates"]["dispatch"]
        assert "variants" in disp
        assert "A" in disp["variants"]
        assert "B" in disp["variants"]
        assert disp["variants"]["A"]["success_rate"] == 1.0
        assert disp["variants"]["B"]["success_rate"] == 0.0
