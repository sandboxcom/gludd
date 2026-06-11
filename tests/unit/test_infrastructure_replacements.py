"""Tests for bugfixes: replace custom infrastructure with mature OSS tools."""
from __future__ import annotations


class TestMetricsExporterUsesPrometheusClient:
    def test_uses_prometheus_client_counter(self):
        from general_ludd.observability.metrics_exporter import get_metrics_exporter

        exporter = get_metrics_exporter()
        exporter.counter_inc("test_counter_total", {"method": "GET"}, value=5)
        exporter.counter_inc("test_counter_total", {"method": "GET"}, value=2)

        rendered = exporter.render_prometheus()
        assert "test_counter_total" in rendered

    def test_uses_prometheus_client_gauge(self):
        from general_ludd.observability.metrics_exporter import get_metrics_exporter

        exporter = get_metrics_exporter()
        exporter.gauge_set("test_gauge", value=42.5)

        rendered = exporter.render_prometheus()
        assert "test_gauge" in rendered

    def test_uses_prometheus_client_histogram(self):
        from general_ludd.observability.metrics_exporter import get_metrics_exporter

        exporter = get_metrics_exporter()
        exporter.histogram_observe("test_histogram_seconds", value=0.5, labels={"status": "200"})
        exporter.histogram_observe("test_histogram_seconds", value=1.2, labels={"status": "200"})

        rendered = exporter.render_prometheus()
        assert "test_histogram_seconds" in rendered

    def test_get_json_returns_metrics(self):
        from general_ludd.observability.metrics_exporter import get_metrics_exporter

        exporter = get_metrics_exporter()
        exporter.counter_inc("test_counter_total", {"method": "POST"})
        result = exporter.get_json()
        assert "metrics" in result
        assert "uptime_seconds" in result

    def test_get_counters_returns_dict(self):
        from general_ludd.observability.metrics_exporter import get_metrics_exporter

        exporter = get_metrics_exporter()
        exporter.counter_inc("test_counter_total", {"method": "GET"})
        counters = exporter.get_counters()
        assert isinstance(counters, dict)

    def test_get_gauges_returns_dict(self):
        from general_ludd.observability.metrics_exporter import get_metrics_exporter

        exporter = get_metrics_exporter()
        exporter.gauge_set("test_gauge", value=10.0)
        gauges = exporter.get_gauges()
        assert isinstance(gauges, dict)


class TestLogAuditorDomainChecks:
    def test_auditor_creates_report(self):
        from general_ludd.validation.log_auditor import LogAuditor

        auditor = LogAuditor()
        entries = [{"event": "test", "correlation_id": "abc123"}]
        report = auditor.audit_logs(entries)
        assert report.total_findings == 0

    def test_auditor_finds_missing_correlation_id(self):
        from general_ludd.validation.log_auditor import LogAuditor

        auditor = LogAuditor()
        entries = [{"event": "test"}]
        report = auditor.audit_logs(entries)
        assert report.total_findings >= 1
        assert any(f.category == "missing_correlation_id" for f in report.findings)

    def test_auditor_detects_stuck_todo(self):
        from general_ludd.validation.log_auditor import LogAuditor

        auditor = LogAuditor()
        entries = [{
            "correlation_id": "abc",
            "todo_id": "todo-1",
            "attempt": 6,
            "from_status": "active",
            "to_status": "active",
        }]
        report = auditor.audit_logs(entries)
        assert report.total_findings >= 1
        assert any(f.category == "stuck_todo" for f in report.findings)
