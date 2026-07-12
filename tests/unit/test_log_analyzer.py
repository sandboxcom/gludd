"""Unit tests for the log analysis Python module.

Covers log discovery, line parsing, error clustering, COT logging, and report generation.
"""

from __future__ import annotations

from pathlib import Path

from general_ludd.log_analyzer import (
    _is_error_line,
    _parse_category,
    _parse_severity,
    _parse_timestamp,
    analyze,
    cluster_errors,
    discover_logs,
    generate_reports,
    parse_log_lines,
    write_cot_log,
)


class TestLogDiscovery:
    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        assert discover_logs(str(tmp_path), "*.log") == []

    def test_matches_log_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.log").write_text("")
        (tmp_path / "b.log").write_text("")
        (tmp_path / "c.txt").write_text("")
        found = discover_logs(str(tmp_path), "*.log")
        names = {f.name for f in found}
        assert names == {"a.log", "b.log"}

    def test_no_match_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("")
        assert discover_logs(str(tmp_path), "*.log") == []

    def test_nonexistent_dir_returns_empty(self) -> None:
        assert discover_logs("/nonexistent/path/xyz", "*.log") == []


class TestLineParsing:
    def test_severity_detection(self) -> None:
        assert _parse_severity("2024-01-01 ERROR something") == "ERROR"
        assert _parse_severity("2024-01-01 CRITICAL: disk full") == "CRITICAL"
        assert _parse_severity("2024-01-01 Exception in thread") == "EXCEPTION"
        assert _parse_severity("2024-01-01 just a normal message") == "UNKNOWN"

    def test_timestamp_iso(self) -> None:
        line = "2024-06-15 10:30:45 ERROR timeout"
        assert _parse_timestamp(line) == "2024-06-15 10:30:45"

    def test_timestamp_slashed(self) -> None:
        line = "15/06/2024 10:30:45 INFO started"
        assert _parse_timestamp(line) == "15/06/2024 10:30:45"

    def test_timestamp_bracketed(self) -> None:
        line = "[10:30:45.123] WARNING disk near capacity"
        assert _parse_timestamp(line) == "10:30:45.123"

    def test_timestamp_missing(self) -> None:
        assert _parse_timestamp("no timestamp here") == ""

    def test_category_bracketed(self) -> None:
        assert _parse_category("[plugin] something") == "plugin"

    def test_category_component_keyword(self) -> None:
        assert _parse_category("from worker.thread something") == "worker.thread"

    def test_category_missing(self) -> None:
        assert _parse_category("no category info") == ""

    def test_is_error_line_true(self) -> None:
        assert _is_error_line("ERROR: connection refused")
        assert _is_error_line("CRITICAL: out of memory")
        assert _is_error_line("Traceback (most recent call last):")

    def test_is_error_line_false(self) -> None:
        assert not _is_error_line("INFO: service started")
        assert not _is_error_line("DEBUG: request latency 5ms")

    def test_parse_log_lines_full(self) -> None:
        content = (
            "2024-06-15 10:30:45 ERROR [db] connection timeout\n"
            "2024-06-15 10:30:46 INFO [api] request served\n"
            "\n"
        )
        entries = parse_log_lines(content)
        assert len(entries) == 2
        assert entries[0]["severity"] == "ERROR"
        assert entries[0]["category"] == "db"
        assert entries[0]["is_error"] is True
        assert entries[1]["severity"] == "INFO"
        assert entries[1]["category"] == "api"
        assert entries[1]["is_error"] is False

    def test_empty_content(self) -> None:
        assert parse_log_lines("") == []

    def test_only_blank_lines(self) -> None:
        assert parse_log_lines("\n\n  \n") == []


class TestErrorClustering:
    def test_below_min_size_returns_empty(self) -> None:
        entries = parse_log_lines("ERROR [db] timeout\n")
        clusters = cluster_errors(entries, min_size=2)
        assert clusters == []

    def test_clusters_by_severity_and_category(self) -> None:
        content = (
            "ERROR [db] timeout\n"
            "ERROR [db] connection lost\n"
            "ERROR [db] deadlock\n"
            "ERROR [api] slow response\n"
            "ERROR [api] high latency\n"
        )
        entries = parse_log_lines(content)
        clusters = cluster_errors(entries, min_size=2)
        assert len(clusters) == 2
        cat_cluster = next(c for c in clusters if c["category"] == "db")
        assert cat_cluster["severity"] == "ERROR"
        assert cat_cluster["count"] == 3
        assert len(cat_cluster["sample_lines"]) == 3

    def test_respects_windowing(self) -> None:
        entries = [{"is_error": True, "severity": "ERROR", "category": "x", "raw": "msg"} for _ in range(5)]
        clusters = cluster_errors(entries, window_seconds=60, min_size=3)
        assert len(clusters) == 1
        assert clusters[0]["window_seconds_applied"] == 60


class TestCOTLogWriting:
    def test_writes_cot_log(self, tmp_path: Path) -> None:
        result = {"files_analysed": 2, "total_lines": 100, "error_lines": 10,
                   "error_density": 0.1, "error_threshold": 0.05,
                   "cluster_count": 1, "verdict": "anomalies_detected",
                   "error_clusters": [
                       {"cluster_id": 1, "severity": "ERROR", "category": "db",
                        "count": 10, "sample_lines": ["ERROR db timeout"]}
                   ]}
        cot = write_cot_log(str(tmp_path), result)
        assert cot.exists()
        content = cot.read_text()
        assert "Log Analysis Chain of Thought" in content
        assert "Cluster #1" in content


class TestReportGeneration:
    def test_generates_json_and_md(self, tmp_path: Path) -> None:
        entries = parse_log_lines("ERROR [db] timeout\nINFO all good\n")
        clusters = [{"cluster_id": 1, "severity": "ERROR", "category": "db",
                     "count": 1, "sample_lines": ["ERROR [db] timeout"],
                     "window_seconds_applied": 300}]
        result = generate_reports(entries, clusters, 2, 1, 0.1, str(tmp_path))
        assert result["verdict"] == "anomalies_detected"
        assert (tmp_path / "log_analysis_result.json").exists()
        assert (tmp_path / "log_analysis_report.md").exists()

    def test_clean_verdict_when_below_threshold(self, tmp_path: Path) -> None:
        entries = parse_log_lines("INFO ok\nINFO ok\nINFO ok\nINFO ok\nERROR x\n")
        result = generate_reports(entries, [], 5, 1, 0.3, str(tmp_path))
        assert result["verdict"] == "clean"

    def test_anomaly_verdict_when_above_threshold(self, tmp_path: Path) -> None:
        entries = parse_log_lines("ERROR x\nERROR y\nINFO z\n")
        result = generate_reports(entries, [], 3, 1, 0.1, str(tmp_path))
        assert result["verdict"] == "anomalies_detected"

    def test_anomaly_verdict_when_clusters_present(self, tmp_path: Path) -> None:
        entries = parse_log_lines("INFO x\nINFO y\n")
        clusters = [{"cluster_id": 1, "severity": "ERROR", "category": "db",
                     "count": 1, "sample_lines": []}]
        result = generate_reports(entries, clusters, 2, 1, 0.5, str(tmp_path))
        assert result["verdict"] == "anomalies_detected"

    def test_md_report_no_clusters(self, tmp_path: Path) -> None:
        generate_reports([], [], 0, 0, 0.1, str(tmp_path))
        md = (tmp_path / "log_analysis_report.md").read_text()
        assert "No error clusters detected" in md

    def test_md_report_with_clusters(self, tmp_path: Path) -> None:
        entries = parse_log_lines("ERROR [db] timeout\nERROR [db] deadlock\n")
        clusters = cluster_errors(entries, min_size=2)
        generate_reports(entries, clusters, 2, 1, 0.1, str(tmp_path))
        md = (tmp_path / "log_analysis_report.md").read_text()
        assert "2 occurrences" in md


class TestAnalyzeEndToEnd:
    def test_empty_logs_handled_gracefully(self, tmp_path: Path) -> None:
        (tmp_path / "input.log").write_text("")
        out = tmp_path / "output"
        out.mkdir()
        result = analyze(str(tmp_path), "*.log", str(out))
        assert result["verdict"] == "clean"
        assert result["files_analysed"] == 1
        assert result["total_lines"] == 1
        assert result["error_lines"] == 0
        assert (out / "log_analysis_cot.log").exists()
        assert (out / "log_analysis_result.json").exists()

    def test_malformed_log_handled(self, tmp_path: Path) -> None:
        (tmp_path / "corrupt.log").write_text("\x00\x01\x02garbage\n")
        out = tmp_path / "output"
        out.mkdir()
        result = analyze(str(tmp_path), "*.log", str(out))
        assert result["verdict"] == "clean"

    def test_with_repeated_errors(self, tmp_path: Path) -> None:
        lines = ["ERROR [db] connection timeout\n" for _ in range(20)]
        (tmp_path / "errors.log").write_text("".join(lines))
        out = tmp_path / "output"
        out.mkdir()
        result = analyze(str(tmp_path), "*.log", str(out), error_threshold=0.0, min_cluster_size=3)
        assert result["verdict"] == "anomalies_detected"
        assert result["cluster_count"] >= 1
        clusters = result["error_clusters"]
        assert any(c["category"] == "db" for c in clusters)

    def test_multiple_log_files(self, tmp_path: Path) -> None:
        (tmp_path / "app.log").write_text("ERROR [api] timeout\nINFO ok\n")
        (tmp_path / "db.log").write_text("CRITICAL [db] corrupt\nWARNING slow\n")
        out = tmp_path / "output"
        out.mkdir()
        result = analyze(str(tmp_path), "*.log", str(out))
        assert result["files_analysed"] == 2
        assert result["total_lines"] == 4

    def test_no_matching_files(self, tmp_path: Path) -> None:
        out = tmp_path / "output"
        out.mkdir()
        result = analyze(str(tmp_path), "*.log", str(out))
        assert result["files_analysed"] == 0
        assert result["verdict"] == "clean"
