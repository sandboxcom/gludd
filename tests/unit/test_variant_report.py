"""Unit tests for VariantMetrics.generate_variant_report() (G6 variant comparison)."""

from __future__ import annotations

import os
import tempfile

from general_ludd.prompts.variant_metrics import VariantMetrics


def _tmp_metrics(min_samples: int = 3) -> VariantMetrics:
    path = os.path.join(tempfile.mkdtemp(), "metrics.json")
    return VariantMetrics(storage_path=path, min_samples_per_variant=min_samples)


class TestGenerateVariantReport:
    def test_empty_metrics_returns_empty_templates(self):
        m = _tmp_metrics()
        report = m.generate_variant_report()
        assert report == {"templates": {}, "template_count": 0}

    def test_insufficient_data_reports_no_winner(self):
        m = _tmp_metrics(min_samples=10)
        m.record_outcome("tpl", "A", success=True, latency_ms=100.0)
        m.record_outcome("tpl", "B", success=True, latency_ms=100.0)

        report = m.generate_variant_report()
        tmpl = report["templates"]["tpl"]

        assert tmpl["winner"] is None
        assert tmpl["sufficient_data"] is False
        assert tmpl["variants"]["A"]["samples"] == 1
        assert tmpl["variants"]["B"]["samples"] == 1
        assert tmpl["promoted"] is None

    def test_sufficient_data_reports_winner_by_success_rate(self):
        m = _tmp_metrics(min_samples=3)
        for _ in range(3):
            m.record_outcome("tpl", "A", success=True, latency_ms=100.0)
        for _ in range(3):
            m.record_outcome("tpl", "B", success=False, latency_ms=200.0)

        report = m.generate_variant_report()
        tmpl = report["templates"]["tpl"]

        assert tmpl["winner"] == "A"
        assert tmpl["sufficient_data"] is True
        assert tmpl["variants"]["A"]["success_rate"] == 1.0
        assert tmpl["variants"]["B"]["success_rate"] == 0.0
        assert tmpl["variants"]["A"]["avg_latency_ms"] == 100.0
        assert tmpl["variants"]["B"]["avg_latency_ms"] == 200.0

    def test_winner_by_latency_tiebreaker_reported(self):
        m = _tmp_metrics(min_samples=3)
        for _ in range(3):
            m.record_outcome("tpl", "A", success=True, latency_ms=200.0)
        for _ in range(3):
            m.record_outcome("tpl", "B", success=True, latency_ms=100.0)

        report = m.generate_variant_report()
        tmpl = report["templates"]["tpl"]

        assert tmpl["winner"] == "B"
        assert tmpl["variants"]["A"]["avg_latency_ms"] == 200.0
        assert tmpl["variants"]["B"]["avg_latency_ms"] == 100.0

    def test_multiple_templates_each_have_own_comparison(self):
        m = _tmp_metrics(min_samples=3)
        for _ in range(3):
            m.record_outcome("dispatch", "A", success=True, latency_ms=50.0)
        for _ in range(3):
            m.record_outcome("dispatch", "B", success=False, latency_ms=500.0)
        for _ in range(3):
            m.record_outcome("review", "A", success=False, latency_ms=200.0)
        for _ in range(3):
            m.record_outcome("review", "B", success=True, latency_ms=30.0)

        report = m.generate_variant_report()
        assert report["template_count"] == 2
        assert report["templates"]["dispatch"]["winner"] == "A"
        assert report["templates"]["review"]["winner"] == "B"

    def test_promoted_variant_shown_in_report(self):
        m = _tmp_metrics(min_samples=3)
        for _ in range(3):
            m.record_outcome("tpl", "A", success=True, latency_ms=50.0)
        for _ in range(3):
            m.record_outcome("tpl", "B", success=False, latency_ms=200.0)
        m.promote_winner("tpl")

        report = m.generate_variant_report()
        tmpl = report["templates"]["tpl"]
        assert tmpl["promoted"] == "A"
        assert tmpl["winner"] == "A"

    def test_margin_computed_with_rate_delta(self):
        m = _tmp_metrics(min_samples=3)
        for _ in range(3):
            m.record_outcome("tpl", "A", success=True, latency_ms=100.0)
        for _ in range(3):
            m.record_outcome("tpl", "B", success=False, latency_ms=100.0)

        report = m.generate_variant_report()
        margin = report["templates"]["tpl"]["margin"]
        assert margin["success_rate_delta"] == 1.0
        assert margin["winning_metric"] == "success_rate"
        assert "A leads B" in margin["description"]

    def test_margin_computed_with_latency_delta(self):
        m = _tmp_metrics(min_samples=3)
        for _ in range(3):
            m.record_outcome("tpl", "A", success=True, latency_ms=300.0)
        for _ in range(3):
            m.record_outcome("tpl", "B", success=True, latency_ms=100.0)

        report = m.generate_variant_report()
        margin = report["templates"]["tpl"]["margin"]
        assert margin["success_rate_delta"] == 0.0
        assert margin["winning_metric"] == "latency"
        assert margin["latency_delta_pct"] is not None
        # B is faster, so A slower => B leads
        assert "B faster" in margin["description"] or "B leads" in margin["description"]

    def test_margin_equal_when_no_winner(self):
        m = _tmp_metrics(min_samples=3)
        for _ in range(3):
            m.record_outcome("tpl", "A", success=True, latency_ms=100.0)
        for _ in range(3):
            m.record_outcome("tpl", "B", success=True, latency_ms=100.0)

        report = m.generate_variant_report()
        margin = report["templates"]["tpl"]["margin"]
        assert margin["success_rate_delta"] == 0.0
        assert margin["winning_metric"] is None
        assert "equal" in margin["description"]
