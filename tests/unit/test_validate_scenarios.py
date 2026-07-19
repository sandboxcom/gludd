"""Unit tests for validate_scenarios.py — heuristic confidence, HTTP client, error handling."""

from __future__ import annotations

import contextlib
import importlib
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

COLLECTIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "collections/ansible_collections/general_ludd/e2e_test_gen/roles/validate_scenarios/files"
)
sys.path.insert(0, str(COLLECTIONS_DIR))

vs = importlib.import_module("validate_scenarios")

SAMPLE_SCENARIOS = {
    "module": "sample",
    "path": "tests/e2e/test_sample.py",
    "scenarios": [
        {
            "name": "crud_lifecycle",
            "description": "Test full CRUD lifecycle with create, read, update, delete operations",
            "coverage_targets": ["api", "database"],
        },
        {
            "name": "auth_flow",
            "description": "Verify login token auth session flow",
            "coverage_targets": ["auth_service"],
        },
        {
            "name": "unknown_pattern",
            "description": "Some random description without matching keywords",
            "coverage_targets": [],
        },
    ],
}


class TestHeuristicConfidence:

    def test_heuristic_crud_lifecycle_returns_high_confidence(self):
        conf = vs._heuristic_confidence("crud_lifecycle", "create read update delete")
        assert conf > 0.6

    def test_heuristic_unknown_pattern_returns_base(self):
        conf = vs._heuristic_confidence("unknown_pattern", "no matching keywords")
        assert conf == 0.4

    def test_heuristic_no_matching_keywords_in_text_returns_fallback(self):
        conf = vs._heuristic_confidence("crud_lifecycle", "anything unreleated")
        assert conf == 0.3

    def test_heuristic_auth_flow_login_token(self):
        conf = vs._heuristic_confidence("auth_flow", "login token")
        assert conf == pytest.approx(0.85, abs=0.01)

    def test_heuristic_timeout_handling(self):
        conf = vs._heuristic_confidence("timeout_handling", "timeout retry backoff")
        assert conf > 0.7

    def test_heuristic_concurrent_edits(self):
        conf = vs._heuristic_confidence("concurrent_edits", "concurrent transaction lock")
        assert conf > 0.7

    def test_heuristic_daemon_restart(self):
        conf = vs._heuristic_confidence("daemon_restart", "startup shutdown restart")
        assert conf > 0.8


class TestExtractConfidenceFromReport:

    def test_empty_findings_returns_zero(self):
        report = {"findings": [], "confidence_overall": 0.85}
        assert vs._extract_confidence_from_report(report) == 0.0

    def test_valid_overall_returns_value(self):
        report = {
            "findings": [{"claim": "test", "confidence": 0.7}],
            "confidence_overall": 0.75,
        }
        assert vs._extract_confidence_from_report(report) == 0.75

    def test_overall_clamped_to_range(self):
        report = {
            "findings": [{"claim": "test", "confidence": 0.7}],
            "confidence_overall": 1.5,
        }
        assert vs._extract_confidence_from_report(report) == 1.0

    def test_negative_overall_clamped_to_zero(self):
        report = {
            "findings": [{"claim": "test", "confidence": 0.7}],
            "confidence_overall": -0.5,
        }
        assert vs._extract_confidence_from_report(report) == 0.0

    def test_non_numeric_overall_returns_zero(self):
        report = {
            "findings": [{"claim": "test", "confidence": 0.7}],
            "confidence_overall": None,
        }
        assert vs._extract_confidence_from_report(report) == 0.0


class TestSourceUrlsFromReport:

    def test_empty_findings_returns_empty_list(self):
        report = {"findings": []}
        assert vs._source_urls_from_report(report) == []

    def test_single_finding_with_citation(self):
        report = {
            "findings": [
                {
                    "citations": [
                        {"url": "https://example.com/a", "title": "A"},
                        {"url": "https://example.com/b", "title": "B"},
                    ]
                }
            ]
        }
        urls = vs._source_urls_from_report(report)
        assert urls == ["https://example.com/a", "https://example.com/b"]

    def test_deduplicates_duplicate_urls(self):
        report = {
            "findings": [
                {"citations": [{"url": "https://example.com/x"}]},
                {"citations": [{"url": "https://example.com/x"}]},
            ]
        }
        urls = vs._source_urls_from_report(report)
        assert urls == ["https://example.com/x"]

    def test_skips_empty_urls(self):
        report = {
            "findings": [
                {"citations": [{"url": ""}, {"url": "https://example.com/y"}]}
            ]
        }
        urls = vs._source_urls_from_report(report)
        assert urls == ["https://example.com/y"]


class TestDaemonResearchClient:

    def test_init_stores_base_url_stripped(self):
        client = vs.DaemonResearchClient(daemon_url="http://localhost:8000/")
        assert client._base == "http://localhost:8000"

    def test_headers_with_psk(self):
        client = vs.DaemonResearchClient(psk="secret")
        h = client._headers()
        assert h["X-PSK"] == "secret"
        assert h["Content-Type"] == "application/json"

    def test_headers_without_psk(self):
        client = vs.DaemonResearchClient()
        h = client._headers()
        assert "X-PSK" not in h

    def test_validate_queries_success(self):
        response_json = json.dumps({
            "reports": [
                {
                    "report_id": "abc",
                    "query": "test query",
                    "findings": [
                        {
                            "finding_id": "f1",
                            "claim": "test claim",
                            "confidence": 0.75,
                            "citations": [{"url": "https://example.com"}],
                        }
                    ],
                    "sources_consulted": 1,
                    "sources_used": 1,
                    "search_engines_used": ["google"],
                    "elapsed_seconds": 0.5,
                    "generated_at": "2025-01-01T00:00:00Z",
                    "summary": "test",
                    "confidence_overall": 0.75,
                }
            ],
            "query_count": 1,
            "findings_count": 1,
            "searx_available": True,
        }).encode("utf-8")

        mock_urlopen = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = response_json
        mock_urlopen.return_value = mock_resp
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)

        client = vs.DaemonResearchClient()
        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            result = client.validate_queries(["test query"])

        assert result["query_count"] == 1
        assert result["findings_count"] == 1
        assert result["searx_available"] is True
        assert len(result["reports"]) == 1
        assert result["reports"][0]["confidence_overall"] == 0.75

    def test_validate_queries_daemon_unreachable_returns_empty(self):
        client = vs.DaemonResearchClient()
        with mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = client.validate_queries(["test query"])

        assert result["reports"] == []
        assert result["query_count"] == 0
        assert result["findings_count"] == 0

    def test_validate_queries_non_200_returns_empty(self):
        mock_resp = mock.MagicMock()
        mock_resp.status = 503
        mock_resp.read.return_value = b"Service Unavailable"
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)

        client = vs.DaemonResearchClient()
        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            result = client.validate_queries(["test query"])

        assert result["reports"] == []
        assert result["query_count"] == 0


class TestEndToEndScript:

    def _run_script(self, args: list[str], scenarios: dict, tmp_path: Path) -> dict:
        scenarios_file = tmp_path / "scenarios.json"
        output_file = tmp_path / "validated.json"
        scenarios_file.write_text(json.dumps(scenarios))
        sys.argv = [
            "validate_scenarios.py", *args,
            "--scenarios-file", str(scenarios_file),
            "--output", str(output_file),
        ]
        with contextlib.suppress(SystemExit):
            vs.main()
        with open(output_file) as f:
            return json.load(f)

    def test_mock_mode_produces_validated_output(self, tmp_path):
        result = self._run_script(["--mock"], SAMPLE_SCENARIOS, tmp_path)
        assert result["status"] == "completed"
        assert result["valid_count"] >= 1
        assert result["discarded_count"] >= 0
        assert len(result["research_queries"]) == 3

    def test_confidence_threshold_filters_discarded(self, tmp_path):
        result = self._run_script(
            ["--mock", "--confidence-threshold", "0.9"], SAMPLE_SCENARIOS, tmp_path
        )
        assert result["discarded_count"] >= 1  # at least one below 0.9

    def test_each_valid_entry_has_confidence(self, tmp_path):
        result = self._run_script(["--mock"], SAMPLE_SCENARIOS, tmp_path)
        for entry in result["valid"]:
            assert "confidence" in entry
            assert isinstance(entry["confidence"], (int, float))
            assert 0.0 <= entry["confidence"] <= 1.0

    def test_non_mock_daemon_unreachable_falls_back_to_heuristic(self, tmp_path):
        with mock.patch.object(
            vs.DaemonResearchClient, "validate_queries",
            return_value={"reports": [], "query_count": 0, "findings_count": 0, "searx_available": False},
        ):
            result = self._run_script(
                ["--daemon-url", "http://localhost:9999"],
                SAMPLE_SCENARIOS,
                tmp_path,
            )
        assert result["status"] == "completed"
        assert result["valid_count"] >= 1
        for entry in result["valid"]:
            assert entry["confidence"] > 0.0

    def test_non_mock_with_daemon_report_uses_research_confidence(self, tmp_path):
        research_report = {
            "findings": [
                {
                    "claim": "CRUD lifecycle is a standard test pattern",
                    "confidence": 0.92,
                    "citations": [
                        {"url": "https://example.com/crud-testing", "title": "CRUD Testing"},
                    ],
                }
            ],
            "confidence_overall": 0.92,
        }
        empty_report = {
            "findings": [],
            "confidence_overall": 0.0,
        }
        daemon_response = {
            "reports": [research_report, empty_report, empty_report],
            "query_count": 3,
            "findings_count": 1,
            "searx_available": True,
        }

        with mock.patch.object(
            vs.DaemonResearchClient, "validate_queries",
            return_value=daemon_response,
        ):
            result = self._run_script(
                ["--daemon-url", "http://localhost:8000"],
                SAMPLE_SCENARIOS,
                tmp_path,
            )

        assert result["status"] == "completed"
        assert result["valid_count"] >= 1
        crud = next(e for e in result["valid"] if e["name"] == "crud_lifecycle")
        assert crud["confidence"] == 0.92
        assert "https://example.com/crud-testing" in crud["source_urls"]

    def test_non_mock_empty_report_falls_back_to_heuristic_per_scenario(self, tmp_path):
        daemon_response = {
            "reports": [
                {"findings": [], "confidence_overall": 0.0},
                {"findings": [], "confidence_overall": 0.0},
                {"findings": [], "confidence_overall": 0.0},
            ],
            "query_count": 3,
            "findings_count": 0,
            "searx_available": True,
        }

        with mock.patch.object(
            vs.DaemonResearchClient, "validate_queries",
            return_value=daemon_response,
        ):
            result = self._run_script(
                ["--daemon-url", "http://localhost:8000"],
                SAMPLE_SCENARIOS,
                tmp_path,
            )

        assert result["status"] == "completed"
        crud = next(e for e in result["valid"] if e["name"] == "crud_lifecycle")
        assert crud["confidence"] > 0.5  # heuristic, not 0.0
        assert crud["source_urls"] == []

    def test_research_categories_parsed_correctly(self, tmp_path):
        categories_seen: list[list[str]] = []

        def _capture_categories(queries, **kwargs):
            categories_seen.append(kwargs.get("categories", []))
            return {"reports": [], "query_count": 0, "findings_count": 0, "searx_available": False}

        with mock.patch.object(
            vs.DaemonResearchClient, "validate_queries", side_effect=_capture_categories,
        ):
            self._run_script(
                ["--daemon-url", "http://localhost:8000",
                 "--research-categories", "science,it,news"],
                SAMPLE_SCENARIOS,
                tmp_path,
            )

        assert categories_seen == [["science", "it", "news"]]
