"""Unit tests for the service-discovery pipeline and its compatibility shim."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import general_ludd.service_discovery.pipeline as pipeline_module
from general_ludd.connectors.searx import SearXResult
from general_ludd.infra.service_catalog import DiscoveredService, ServiceCatalog
from general_ludd.infra.service_discovery_pipeline import (
    DEFAULT_SEARCH_TERMS,
    DiscoveryReport,
    ServiceDiscoveryPipeline,
)


class TestServiceDiscoveryPipelineExports:
    def test_classes_importable(self) -> None:
        assert isinstance(DEFAULT_SEARCH_TERMS, (list, tuple))
        assert callable(ServiceDiscoveryPipeline)
        assert callable(DiscoveryReport)


@pytest.mark.parametrize("scalar_terms", ["service api", ""])
def test_pipeline_rejects_scalar_search_terms_before_connector_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scalar_terms: str,
) -> None:
    """A lone string must not become one outbound search per character."""
    connector_factory = MagicMock()
    monkeypatch.setattr(pipeline_module, "SearXConnector", connector_factory)

    with pytest.raises(TypeError, match="search_terms must be a sequence of entries"):
        ServiceDiscoveryPipeline(
            searx_url="http://localhost:8080",
            catalog_path=str(tmp_path / "service_catalog.yml"),
            search_terms=scalar_terms,
        )

    connector_factory.assert_not_called()


def test_pipeline_empty_search_term_sequence_uses_defaults(tmp_path: Path) -> None:
    """An empty sequence retains the documented default-query fallback."""
    pipeline = ServiceDiscoveryPipeline(
        searx_url="http://localhost:8080",
        catalog_path=str(tmp_path / "service_catalog.yml"),
        search_terms=[],
    )

    assert pipeline._search_terms == [query for _, query in DEFAULT_SEARCH_TERMS]


def test_empty_successful_search_preserves_existing_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transiently empty search must not retire every known service."""
    catalog_path = tmp_path / "service_catalog.yml"
    catalog = ServiceCatalog(path=str(catalog_path))
    catalog.add(
        DiscoveredService(
            name="Existing API",
            url="https://existing.example/api",
            status="active",
        )
    )
    catalog.save()

    pipeline = ServiceDiscoveryPipeline(
        searx_url="http://localhost:8080",
        catalog_path=str(catalog_path),
        search_terms=["service api"],
    )
    monkeypatch.setattr(pipeline._searx, "search", MagicMock(return_value=[]))

    report = pipeline.run_discovery_pipeline()

    persisted = ServiceCatalog(path=str(catalog_path))
    existing = persisted.get("Existing API")
    assert report.retired_services == []
    assert report.total_discovered == 1
    assert existing is not None
    assert existing.status == "active"


def test_pipeline_reconciles_results_and_isolates_query_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful query results reconcile even when another query fails."""
    catalog_path = tmp_path / "service_catalog.yml"
    catalog = ServiceCatalog(path=str(catalog_path))
    catalog.add(
        DiscoveredService(
            name="Removed API",
            url="https://removed.example/api",
            status="active",
        )
    )
    catalog.add(
        DiscoveredService(
            name="Changed API",
            url="https://old.example/api",
            status="active",
        )
    )
    catalog.save()

    pipeline = ServiceDiscoveryPipeline(
        searx_url="http://localhost:8080",
        catalog_path=str(catalog_path),
        search_terms=["working query", "broken query"],
    )
    monkeypatch.setattr(
        pipeline._searx,
        "search",
        MagicMock(
            side_effect=[
                [
                    SearXResult(
                        title="New API - Developer portal",
                        url="https://new.example/api",
                        snippet="n" * 700,
                        engine="engine-a",
                    ),
                    SearXResult(
                        title="Changed API | Documentation",
                        url="https://new-location.example/api",
                        snippet="updated",
                        engine="engine-b",
                    ),
                    SearXResult(
                        title=" ",
                        url="https://unnamed.example/api",
                        snippet="ignored",
                        engine="engine-c",
                    ),
                ],
                RuntimeError("search offline"),
            ]
        ),
    )

    report = pipeline.run_discovery_pipeline()

    persisted = ServiceCatalog(path=str(catalog_path))
    removed = persisted.get("Removed API")
    changed = persisted.get("Changed API")
    added = persisted.get("New API")
    assert report.new_services == ["New API"]
    assert report.changed_services == ["Changed API"]
    assert report.retired_services == ["Removed API"]
    assert report.total_discovered == 3
    assert len(report.errors) == 1
    assert "broken query" in report.errors[0]
    assert removed is not None
    assert removed.status == "inactive"
    assert changed is not None
    assert changed.url == "https://new-location.example/api"
    assert added is not None
    assert added.description is not None
    assert len(added.description) == 500


def test_pipeline_isolates_result_parse_and_catalog_save_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One malformed result and a failed save are both reported safely."""
    pipeline = ServiceDiscoveryPipeline(
        searx_url="http://localhost:8080",
        catalog_path=str(tmp_path / "service_catalog.yml"),
        search_terms=["query"],
    )
    monkeypatch.setattr(
        pipeline._searx,
        "search",
        MagicMock(
            return_value=[
                SearXResult(
                    title="Broken API",
                    url="https://broken.example/api",
                    snippet="broken",
                    engine="engine",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "_extract_service_name",
        MagicMock(side_effect=ValueError("invalid title")),
    )
    monkeypatch.setattr(
        ServiceCatalog,
        "save",
        MagicMock(side_effect=OSError("disk unavailable")),
    )

    report = pipeline.run_discovery_pipeline()

    assert report.total_discovered == 0
    assert len(report.errors) == 2
    assert "Failed to parse result" in report.errors[0]
    assert "Failed to save catalog" in report.errors[1]


def test_auto_retire_only_marks_stale_active_services(tmp_path: Path) -> None:
    """The inactivity window preserves fresh and already-inactive services."""
    pipeline = ServiceDiscoveryPipeline(
        searx_url="http://localhost:8080",
        catalog_path=str(tmp_path / "service_catalog.yml"),
        search_terms=["query"],
    )
    now = datetime(2026, 9, 27, tzinfo=UTC)
    stale = DiscoveredService(
        name="Stale API",
        url="https://stale.example/api",
        status="active",
        last_seen=now - timedelta(days=31),
    )
    fresh = DiscoveredService(
        name="Fresh API",
        url="https://fresh.example/api",
        status="active",
        last_seen=now - timedelta(days=29),
    )
    inactive = DiscoveredService(
        name="Inactive API",
        url="https://inactive.example/api",
        status="inactive",
        last_seen=now - timedelta(days=60),
    )
    catalog = ServiceCatalog()
    catalog.services = {svc.name: svc for svc in (stale, fresh, inactive)}

    pipeline._auto_retire_stale(catalog, now)

    assert stale.status == "inactive"
    assert stale.last_seen == now
    assert fresh.status == "active"
    assert inactive.last_seen == now - timedelta(days=60)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Alpha - Docs", "Alpha"),
        ("Beta | Docs", "Beta"),
        ("Gamma · Docs", "Gamma"),
        ("Delta :: Docs", "Delta"),
        ("Epsilon — Docs", "Epsilon"),
        ("Unbroken service title", "Unbroken service title"),
        ("X", None),
        ("   ", None),
        ("L" * 100, "L" * 80),
    ],
)
def test_extract_service_name_handles_supported_title_shapes(
    title: str, expected: str | None
) -> None:
    result = SearXResult(
        title=title,
        url="https://service.example/api",
        snippet="",
        engine="engine",
    )

    assert pipeline_module._extract_service_name(result) == expected


def test_normalize_search_terms_accepts_legacy_and_labeled_entries() -> None:
    assert pipeline_module._normalize_search_terms(
        ["legacy query", ("stable-id", "labeled query")]
    ) == ["legacy query", "labeled query"]
    assert pipeline_module._normalize_search_terms(DEFAULT_SEARCH_TERMS) == [
        query for _, query in DEFAULT_SEARCH_TERMS
    ]


@pytest.mark.parametrize(
    ("terms", "expected_exception"),
    [
        ([tuple()], ValueError),
        ([("one", "two", "three")], ValueError),
        ([("label", 3)], TypeError),
        ([object()], TypeError),
        (["   "], ValueError),
        ([("label", " ")], ValueError),
    ],
)
def test_normalize_search_terms_rejects_invalid_entries(
    terms: list[object], expected_exception: type[Exception]
) -> None:
    with pytest.raises(expected_exception):
        pipeline_module._normalize_search_terms(terms)
