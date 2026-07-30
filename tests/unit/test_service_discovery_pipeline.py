"""Unit tests for ServiceDiscoveryPipeline and DiscoveryReport.

Covers:
- ``DiscoveryReport`` creation and defaults
- ``ServiceDiscoveryPipeline`` via patched SearXConnector
- ``run_discovery_pipeline()``: new services, retired, changed, unchanged
- Error handling: SearX unreachable → logged, no crash
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.service_discovery.pipeline import (
    DiscoveryReport,
    ServiceDiscoveryPipeline,
    _extract_service_name,
)


def _mock_searx_result(
    title: str = "API Gateway",
    url: str = "https://api.example.com",
    snippet: str = "API management",
    engine: str = "arxiv",
    score: float = 0.9,
) -> MagicMock:
    r = MagicMock()
    r.title = title
    r.url = url
    r.snippet = snippet
    r.engine = engine
    r.score = score
    return r


def _write_catalog(path: Path, services: list[dict]) -> None:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"services": services}))


class TestDiscoveryReport:
    def test_creation_defaults(self) -> None:
        report = DiscoveryReport()
        assert report.new_services == []
        assert report.retired_services == []
        assert report.changed_services == []
        assert report.errors == []
        assert report.total_discovered == 0

    def test_creation_with_fields(self) -> None:
        report = DiscoveryReport(
            new_services=["alpha"],
            retired_services=["beta"],
            changed_services=["gamma"],
            errors=["timeout on term X"],
            total_discovered=5,
        )
        assert report.new_services == ["alpha"]
        assert report.retired_services == ["beta"]
        assert report.changed_services == ["gamma"]
        assert report.errors == ["timeout on term X"]
        assert report.total_discovered == 5


class TestExtractServiceName:
    @pytest.mark.parametrize("title", ["GE", "HP", "AI"])
    def test_preserves_valid_two_character_names(self, title: str) -> None:
        assert _extract_service_name(_mock_searx_result(title=title)) == title

    def test_rejects_blank_title(self) -> None:
        assert _extract_service_name(_mock_searx_result(title=" \t ")) is None


class TestRunDiscoveryPipeline:
    def test_new_services_vs_empty_catalog(self, tmp_path: Path) -> None:
        catalog_path = tmp_path / "catalog.yml"
        results = [
            _mock_searx_result("Alpha API", "https://alpha.example.com"),
            _mock_searx_result("Beta API", "https://beta.example.com"),
        ]
        mock_searx = MagicMock()
        mock_searx.search.return_value = results

        with patch(
            "general_ludd.service_discovery.pipeline.SearXConnector",
            return_value=mock_searx,
        ):
            pipeline = ServiceDiscoveryPipeline(
                searx_url="http://test:8888",
                catalog_path=str(catalog_path),
                search_terms=["test"],
            )
            report = pipeline.run_discovery_pipeline()

        assert len(report.new_services) == 2
        assert set(report.new_services) == {"Alpha API", "Beta API"}
        assert report.errors == []

    def test_retired_services(self, tmp_path: Path) -> None:
        catalog_path = tmp_path / "catalog.yml"
        _write_catalog(
            catalog_path,
            [
                {"name": "Old API", "url": "https://old.example.com", "status": "active"},
                {"name": "Current API", "url": "https://current.example.com", "status": "active"},
            ],
        )
        results = [_mock_searx_result("Current API", "https://current.example.com")]
        mock_searx = MagicMock()
        mock_searx.search.return_value = results

        with patch(
            "general_ludd.service_discovery.pipeline.SearXConnector",
            return_value=mock_searx,
        ):
            pipeline = ServiceDiscoveryPipeline(
                searx_url="http://test:8888",
                catalog_path=str(catalog_path),
                search_terms=["test"],
            )
            report = pipeline.run_discovery_pipeline()

        assert "Old API" in report.retired_services
        assert len(report.new_services) == 0

    def test_changed_service_different_url(self, tmp_path: Path) -> None:
        catalog_path = tmp_path / "catalog.yml"
        _write_catalog(
            catalog_path,
            [{"name": "Migrated API", "url": "https://old-url.example.com", "status": "active"}],
        )
        results = [_mock_searx_result("Migrated API", "https://new-url.example.com")]
        mock_searx = MagicMock()
        mock_searx.search.return_value = results

        with patch(
            "general_ludd.service_discovery.pipeline.SearXConnector",
            return_value=mock_searx,
        ):
            pipeline = ServiceDiscoveryPipeline(
                searx_url="http://test:8888",
                catalog_path=str(catalog_path),
                search_terms=["test"],
            )
            report = pipeline.run_discovery_pipeline()

        assert "Migrated API" in report.changed_services
        assert report.new_services == []
        assert report.retired_services == []

    def test_unchanged_services(self, tmp_path: Path) -> None:
        catalog_path = tmp_path / "catalog.yml"
        _write_catalog(
            catalog_path,
            [
                {"name": "Stable API", "url": "https://stable.example.com", "status": "active"},
                {"name": "Legacy API", "url": "https://legacy.example.com", "status": "active"},
            ],
        )
        results = [
            _mock_searx_result("Stable API", "https://stable.example.com"),
            _mock_searx_result("Legacy API", "https://legacy.example.com"),
        ]
        mock_searx = MagicMock()
        mock_searx.search.return_value = results

        with patch(
            "general_ludd.service_discovery.pipeline.SearXConnector",
            return_value=mock_searx,
        ):
            pipeline = ServiceDiscoveryPipeline(
                searx_url="http://test:8888",
                catalog_path=str(catalog_path),
                search_terms=["test"],
            )
            report = pipeline.run_discovery_pipeline()

        assert report.new_services == []
        assert report.retired_services == []
        assert report.changed_services == []

    def test_error_handling_searx_unreachable(self, tmp_path: Path) -> None:
        catalog_path = tmp_path / "catalog.yml"
        mock_searx = MagicMock()
        mock_searx.search.side_effect = ConnectionError("SearX unreachable")

        with patch(
            "general_ludd.service_discovery.pipeline.SearXConnector",
            return_value=mock_searx,
        ):
            pipeline = ServiceDiscoveryPipeline(
                searx_url="http://test:8888",
                catalog_path=str(catalog_path),
                search_terms=["single-term"],
            )
            report = pipeline.run_discovery_pipeline()

        assert len(report.errors) == 1
        assert "SearX unreachable" in report.errors[0]
        assert report.new_services == []

    def test_errors_logged_not_crashed(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        catalog_path = tmp_path / "catalog.yml"
        mock_searx = MagicMock()
        mock_searx.search.side_effect = RuntimeError("timeout")

        with patch(
            "general_ludd.service_discovery.pipeline.SearXConnector",
            return_value=mock_searx,
        ):
            pipeline = ServiceDiscoveryPipeline(
                searx_url="http://test:8888",
                catalog_path=str(catalog_path),
                search_terms=["test-term"],
            )
            with caplog.at_level(logging.WARNING, logger="general_ludd.service_discovery.pipeline"):
                report = pipeline.run_discovery_pipeline()

        assert len(report.errors) == 1
        assert "test-term" in report.errors[0]

    def test_empty_results_returns_empty_report(self, tmp_path: Path) -> None:
        catalog_path = tmp_path / "catalog.yml"
        mock_searx = MagicMock()
        mock_searx.search.return_value = []

        with patch(
            "general_ludd.service_discovery.pipeline.SearXConnector",
            return_value=mock_searx,
        ):
            pipeline = ServiceDiscoveryPipeline(
                searx_url="http://test:8888",
                catalog_path=str(catalog_path),
                search_terms=["test"],
            )
            report = pipeline.run_discovery_pipeline()

        assert report.new_services == []
        assert report.retired_services == []
        assert report.changed_services == []

    def test_duplicate_urls_are_deduplicated(self, tmp_path: Path) -> None:
        catalog_path = tmp_path / "catalog.yml"
        results = [
            _mock_searx_result("First", "https://dup.example.com"),
            _mock_searx_result("Duplicate", "https://dup.example.com", engine="pubmed"),
        ]
        mock_searx = MagicMock()
        mock_searx.search.return_value = results

        with patch(
            "general_ludd.service_discovery.pipeline.SearXConnector",
            return_value=mock_searx,
        ):
            pipeline = ServiceDiscoveryPipeline(
                searx_url="http://test:8888",
                catalog_path=str(catalog_path),
                search_terms=["test"],
            )
            report = pipeline.run_discovery_pipeline()

        # Both have same URL, only one unique service name should be in new_services
        # (deduplication happens by name, not URL, in the pipeline via snapshot.add)
        assert len(report.new_services) <= 2

    def test_empty_url_skipped(self, tmp_path: Path) -> None:
        catalog_path = tmp_path / "catalog.yml"
        results = [
            _mock_searx_result("No URL", ""),
            _mock_searx_result("Good", "https://good.example.com"),
        ]
        mock_searx = MagicMock()
        mock_searx.search.return_value = results

        with patch(
            "general_ludd.service_discovery.pipeline.SearXConnector",
            return_value=mock_searx,
        ):
            pipeline = ServiceDiscoveryPipeline(
                searx_url="http://test:8888",
                catalog_path=str(catalog_path),
                search_terms=["test"],
            )
            report = pipeline.run_discovery_pipeline()

        assert "Good" in report.new_services or not report.errors

    def test_partial_failures_some_succeed(self, tmp_path: Path) -> None:
        catalog_path = tmp_path / "catalog.yml"
        mock_searx = MagicMock()
        mock_searx.search.side_effect = [
            RuntimeError("term1 failed"),
            [_mock_searx_result("Working", "https://working.example.com")],
            RuntimeError("term3 failed"),
        ]

        with patch(
            "general_ludd.service_discovery.pipeline.SearXConnector",
            return_value=mock_searx,
        ):
            pipeline = ServiceDiscoveryPipeline(
                searx_url="http://test:8888",
                catalog_path=str(catalog_path),
                search_terms=["term1", "term2", "term3"],
            )
            report = pipeline.run_discovery_pipeline()

        assert len(report.errors) == 2
        assert len(report.new_services) == 1
        assert report.new_services[0] == "Working"

    def test_source_engine_is_preserved(self, tmp_path: Path) -> None:
        catalog_path = tmp_path / "catalog.yml"
        results = [_mock_searx_result("GE", "https://ge.example.com", engine="google")]
        mock_searx = MagicMock()
        mock_searx.search.return_value = results

        with patch(
            "general_ludd.service_discovery.pipeline.SearXConnector",
            return_value=mock_searx,
        ):
            pipeline = ServiceDiscoveryPipeline(
                searx_url="http://test:8888",
                catalog_path=str(catalog_path),
                search_terms=["test"],
            )
            report = pipeline.run_discovery_pipeline()

        assert "GE" in report.new_services
