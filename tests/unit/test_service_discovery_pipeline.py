"""Unit tests for the service-discovery pipeline and its compatibility shim."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
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


def test_pipeline_rejects_blank_project_namespace_before_connector_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blank ownership boundary must fail before any connector is built."""
    connector_factory = MagicMock()
    monkeypatch.setattr(pipeline_module, "SearXConnector", connector_factory)

    with pytest.raises(ValueError, match="project_namespace must not be blank"):
        ServiceDiscoveryPipeline(
            searx_url="http://localhost:8080",
            project_namespace="   ",
        )

    connector_factory.assert_not_called()


def test_concurrent_project_namespaces_isolate_health_socket_catalogs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One namespace's reconcile cannot discover or retire another's socket."""
    monkeypatch.chdir(tmp_path)
    rendezvous = Barrier(2)

    def build_pipeline(namespace: str) -> ServiceDiscoveryPipeline:
        pipeline = ServiceDiscoveryPipeline(
            searx_url="http://localhost:8080",
            search_terms=["local health socket"],
            project_namespace=namespace,
        )

        def discover(_term: str) -> list[SearXResult]:
            rendezvous.wait(timeout=2)
            return [
                SearXResult(
                    title=f"{namespace.title()} Health Socket - local readiness",
                    url=f"unix:///run/gludd/{namespace}/health.sock",
                    snippet=f"{namespace} readiness endpoint",
                    engine="local-harness",
                )
            ]

        monkeypatch.setattr(pipeline._searx, "search", discover)
        return pipeline

    alpha = build_pipeline("project-alpha")
    beta = build_pipeline("project-beta")

    with ThreadPoolExecutor(max_workers=2) as executor:
        reports = list(
            executor.map(
                lambda pipeline: pipeline.run_discovery_pipeline(),
                (alpha, beta),
            )
        )

    alpha_catalog_path = Path(alpha._catalog.path)
    beta_catalog_path = Path(beta._catalog.path)
    assert alpha_catalog_path != beta_catalog_path
    assert [report.new_services for report in reports] == [
        ["Project-Alpha Health Socket"],
        ["Project-Beta Health Socket"],
    ]
    assert set(ServiceCatalog(path=str(alpha_catalog_path)).services) == {
        "Project-Alpha Health Socket"
    }
    assert set(ServiceCatalog(path=str(beta_catalog_path)).services) == {
        "Project-Beta Health Socket"
    }

    beta_before = beta_catalog_path.read_bytes()
    monkeypatch.setattr(
        alpha._searx,
        "search",
        MagicMock(
            return_value=[
                SearXResult(
                    title="Project-Alpha Replacement - local readiness",
                    url="unix:///run/gludd/project-alpha/replacement.sock",
                    snippet="replacement readiness endpoint",
                    engine="local-harness",
                )
            ]
        ),
    )

    alpha_report = alpha.run_discovery_pipeline()

    assert alpha_report.retired_services == ["Project-Alpha Health Socket"]
    assert beta_catalog_path.read_bytes() == beta_before
    beta_service = ServiceCatalog(path=str(beta_catalog_path)).get(
        "Project-Beta Health Socket"
    )
    assert beta_service is not None
    assert beta_service.status == "active"
