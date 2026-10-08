"""Tests for SearxModelDiscoverer bridging SearX results into ModelGateway."""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

from general_ludd.infra.model_search import ModelSearchResult
from general_ludd.models.gateway import ModelGateway
from general_ludd.models.searx_discoverer import SearxModelDiscoverer


def _make_result(name: str, desc: str = "") -> ModelSearchResult:
    return ModelSearchResult(name=name, source_url=f"https://huggingface.co/{name}", description=desc)


class TestSearxModelDiscoverer:
    def test_constructor_and_profile_metadata_branches(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        with tempfile.TemporaryDirectory() as tmpdir:
            discoverer = SearxModelDiscoverer(
                gateway,
                searx_url="http://127.0.0.1:8080",
                cache_dir=tmpdir,
                ttl_seconds=0,
            )
            rich = ModelSearchResult(
                name="org__rich",
                description="x" * 600,
                params_count=7.0,
            )
            sparse = _make_result("org__sparse")

            rich_kwargs = discoverer._result_to_profile_kwargs(rich)
            sparse_kwargs = discoverer._result_to_profile_kwargs(sparse)

        assert discoverer._profile_id("model") == "searx-model"
        assert rich_kwargs["model_profile_id"] == "searx-org__rich"
        assert len(str(rich_kwargs["description"])) == 500
        assert rich_kwargs["cost_per_input_token"] == 0.0
        assert "description" not in sparse_kwargs
        assert "cost_per_input_token" not in sparse_kwargs

    def test_add_profile_failure_is_bounded(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        gateway.add_profile.side_effect = RuntimeError("duplicate")
        discoverer = SearxModelDiscoverer.__new__(SearxModelDiscoverer)
        discoverer._gateway = gateway

        assert discoverer._add_profile_from_result(_make_result("duplicate")) is False

    def test_empty_search_rechecks_empty_cache(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        discoverer = SearxModelDiscoverer.__new__(SearxModelDiscoverer)
        discoverer._gateway = gateway
        discoverer._ttl = 0
        discoverer._last_sync = 0.0
        discoverer._searcher = MagicMock()
        discoverer._searcher.search_models.return_value = []
        discoverer._index = MagicMock()
        discoverer._index.list_all.return_value = []
        discoverer._index.size.return_value = 0

        assert discoverer.sync_models(force=True) == 0
        assert discoverer._index.list_all.call_count == 1

    def test_discover_now_counts_only_successfully_added_profiles(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        gateway.add_profile.side_effect = [None, RuntimeError("duplicate")]
        discoverer = SearxModelDiscoverer.__new__(SearxModelDiscoverer)
        discoverer._gateway = gateway
        discoverer._searcher = MagicMock()
        discoverer._searcher.search_models.return_value = [
            _make_result("first"),
            _make_result("second"),
        ]
        discoverer._index = MagicMock()
        discoverer._index.get.return_value = None

        assert discoverer.discover_now("models") == 1
        assert discoverer._index.put.call_count == 2

    def test_searx_returns_results_profiles_added(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        mock_results = [_make_result("test-org__model-a"), _make_result("test-org__model-b")]

        with patch.object(SearxModelDiscoverer, "__init__", return_value=None):
            discoverer = SearxModelDiscoverer.__new__(SearxModelDiscoverer)
            discoverer._gateway = gateway
            discoverer._searx_url = "http://localhost:8080"
            discoverer._ttl = 3600
            discoverer._last_sync = 0.0
            with tempfile.TemporaryDirectory() as tmpdir:
                from general_ludd.infra.model_search import ModelIndex
                discoverer._index = ModelIndex(cache_dir=tmpdir)
                from general_ludd.infra.model_search import SearXModelSearch
                discoverer._searcher = SearXModelSearch(base_url=discoverer._searx_url)

                with patch.object(discoverer._searcher, "search_models", return_value=mock_results):
                    added = discoverer.sync_models(force=True)

        assert added == 2
        assert gateway.add_profile.call_count == 2
        gateway.add_profile.assert_any_call(
            model_id="searx-test-org__model-a",
            provider="searx-discovered",
            model="test-org__model-a",
            enabled=False,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
        )

    def test_searx_unreachable_falls_back_to_index(self) -> None:
        gateway = MagicMock(spec=ModelGateway)

        discoverer = SearxModelDiscoverer.__new__(SearxModelDiscoverer)
        discoverer._gateway = gateway
        discoverer._searx_url = "http://localhost:8080"
        discoverer._ttl = 3600
        discoverer._last_sync = 0.0

        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.infra.model_search import ModelIndex, SearXModelSearch
            discoverer._index = ModelIndex(cache_dir=tmpdir)
            discoverer._index.put(_make_result("cached-model"))
            discoverer._searcher = SearXModelSearch(base_url=discoverer._searx_url)

            with patch.object(discoverer._searcher, "search_models", side_effect=Exception("unreachable")):
                added = discoverer.sync_models(force=True)

        assert added == 1
        gateway.add_profile.assert_called_once_with(
            model_id="searx-cached-model",
            provider="searx-discovered",
            model="cached-model",
            enabled=False,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
        )

    def test_ttl_not_expired_skips_sync(self) -> None:
        gateway = MagicMock(spec=ModelGateway)

        discoverer = SearxModelDiscoverer.__new__(SearxModelDiscoverer)
        discoverer._gateway = gateway
        discoverer._searx_url = "http://localhost:8080"
        discoverer._ttl = 3600

        import time
        discoverer._last_sync = time.monotonic()

        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.infra.model_search import ModelIndex, SearXModelSearch
            discoverer._index = ModelIndex(cache_dir=tmpdir)
            discoverer._searcher = SearXModelSearch(base_url=discoverer._searx_url)

            added = discoverer.sync_models()

        assert added == 0
        gateway.add_profile.assert_not_called()

    def test_force_bypasses_ttl(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        mock_results = [_make_result("fresh-model")]

        discoverer = SearxModelDiscoverer.__new__(SearxModelDiscoverer)
        discoverer._gateway = gateway
        discoverer._searx_url = "http://localhost:8080"
        discoverer._ttl = 3600

        import time
        discoverer._last_sync = time.monotonic()

        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.infra.model_search import ModelIndex, SearXModelSearch
            discoverer._index = ModelIndex(cache_dir=tmpdir)
            discoverer._searcher = SearXModelSearch(base_url=discoverer._searx_url)
            with patch.object(discoverer._searcher, "search_models", return_value=mock_results):
                added = discoverer.sync_models(force=True)

        assert added == 1

    def test_discover_now_with_query(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        mock_results = [_make_result("query-match-model")]

        discoverer = SearxModelDiscoverer.__new__(SearxModelDiscoverer)
        discoverer._gateway = gateway
        discoverer._searx_url = "http://localhost:8080"

        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.infra.model_search import ModelIndex, SearXModelSearch
            discoverer._index = ModelIndex(cache_dir=tmpdir)
            discoverer._searcher = SearXModelSearch(base_url=discoverer._searx_url)
            with patch.object(discoverer._searcher, "search_models", return_value=mock_results):
                added = discoverer.discover_now("specific model query")

        assert added == 1
        gateway.add_profile.assert_called_once()

    def test_discover_now_falls_back_to_index_on_error(self) -> None:
        gateway = MagicMock(spec=ModelGateway)

        discoverer = SearxModelDiscoverer.__new__(SearxModelDiscoverer)
        discoverer._gateway = gateway
        discoverer._searx_url = "http://localhost:8080"

        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.infra.model_search import ModelIndex, SearXModelSearch
            discoverer._index = ModelIndex(cache_dir=tmpdir)
            discoverer._index.put(_make_result("fallback-model", "matches query"))
            discoverer._searcher = SearXModelSearch(base_url=discoverer._searx_url)
            with patch.object(discoverer._searcher, "search_models", side_effect=Exception("unreachable")):
                added = discoverer.discover_now("fallback")

        assert added == 1

    def test_index_size_and_last_sync_tracking(self) -> None:
        gateway = MagicMock(spec=ModelGateway)

        discoverer = SearxModelDiscoverer.__new__(SearxModelDiscoverer)
        discoverer._gateway = gateway
        discoverer._searx_url = "http://localhost:8080"
        discoverer._ttl = 3600
        discoverer._last_sync = 0.0

        assert discoverer.last_sync_time == 0.0
        assert discoverer.ttl_seconds == 3600

        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.infra.model_search import ModelIndex, SearXModelSearch
            discoverer._index = ModelIndex(cache_dir=tmpdir)
            discoverer._searcher = SearXModelSearch(base_url=discoverer._searx_url)
            assert discoverer.index_size == 0

            with patch.object(
                discoverer._searcher, "search_models",
                return_value=[_make_result("a"), _make_result("b")],
            ):
                discoverer.sync_models(force=True)

            assert discoverer.index_size == 2
            assert discoverer.last_sync_time > 0.0

    def test_duplicate_results_deduplicated_by_index(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        result_a = _make_result("model-a")

        discoverer = SearxModelDiscoverer.__new__(SearxModelDiscoverer)
        discoverer._gateway = gateway
        discoverer._searx_url = "http://localhost:8080"
        discoverer._ttl = 3600
        discoverer._last_sync = 0.0

        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.infra.model_search import ModelIndex, SearXModelSearch
            discoverer._index = ModelIndex(cache_dir=tmpdir)
            discoverer._index.put(result_a)
            discoverer._searcher = SearXModelSearch(base_url=discoverer._searx_url)

            with patch.object(discoverer._searcher, "search_models", return_value=[result_a]):
                added = discoverer.sync_models(force=True)

        assert added == 1
