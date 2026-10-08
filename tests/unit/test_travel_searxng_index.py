"""Unit tests for travel searxng_index module and searxng_client module_utils."""

from __future__ import annotations

from typing import Any

import pytest
from ansible_collections.general_ludd.travel.plugins.module_utils.searxng_client import (
    TRAVEL_INDEX_ENGINES,
    SearXNGCreateIndexError,
    SearXNGIndex,
    SearXNGIndexNotFoundError,
    TravelIndexManager,
)
from ansible_collections.general_ludd.travel.plugins.modules.searxng_index import (
    create_index,
    delete_index,
    index_exists,
    query_index,
)


class TestSearXNGIndex:
    def test_index_defaults(self):
        idx = SearXNGIndex(name="travel-meta")
        assert idx.name == "travel-meta"
        assert idx.created_at is not None
        assert "google_flights" in idx.engines
        assert "kayak" in idx.engines

    def test_index_default_engines_from_travel_set(self):
        idx = SearXNGIndex(name="travel-meta")
        expected = TRAVEL_INDEX_ENGINES
        assert set(idx.engines) == set(expected)

    def test_index_custom_engines(self):
        idx = SearXNGIndex(name="custom", engines=["google", "bing"])
        assert idx.engines == ["google", "bing"]

    def test_index_serialisation_roundtrip(self):
        idx = SearXNGIndex(name="travel-meta", engines=["google_flights", "kayak"])
        data = idx.serialise()
        restored = SearXNGIndex.from_dict(data)
        assert restored.name == idx.name
        assert restored.engines == idx.engines

    def test_index_engine_display(self):
        idx = SearXNGIndex(name="test", engines=["google_flights", "booking", "expedia"])
        display = idx.engine_display()
        assert "google_flights" in display
        assert "booking" in display
        assert "expedia" in display

    def test_index_immutable_engines_defensive_copy(self):
        engines = ["google_flights"]
        idx = SearXNGIndex(name="test", engines=engines)
        engines.append("kayak")
        assert idx.engines == ["google_flights"]

    @pytest.mark.parametrize(
        ("name", "engines", "message"),
        [
            ("../escape", None, "index name"),
            ("valid", [], "between 1 and 20"),
            ("valid", ["../engine"], "invalid name"),
            ("valid", [f"engine-{index}" for index in range(21)], "between 1 and 20"),
        ],
    )
    def test_index_rejects_unbounded_names_and_engines(
        self,
        name: str,
        engines: list[str] | None,
        message: str,
    ) -> None:
        with pytest.raises(ValueError, match=message):
            SearXNGIndex(name=name, engines=engines)

    def test_index_from_dict_without_timestamp_uses_current_time(self) -> None:
        restored = SearXNGIndex.from_dict({"name": "fresh", "engines": ["booking"]})

        assert restored.name == "fresh"
        assert restored.created_at is not None


class TestTravelIndexManager:
    def test_manager_creates_index_dict(self):
        mgr = TravelIndexManager()
        result = mgr.create("travel-meta")
        assert result["name"] == "travel-meta"
        assert result["engines"] == TRAVEL_INDEX_ENGINES
        assert "created_at" in result

    def test_manager_creates_with_already_exists_returns_existing(self):
        mgr = TravelIndexManager()
        mgr.create("travel-meta")
        result = mgr.create("travel-meta")
        assert result["name"] == "travel-meta"

    def test_manager_get_existing_index(self):
        mgr = TravelIndexManager()
        mgr.create("primary")
        result = mgr.get("primary")
        assert result["name"] == "primary"
        assert result["engines"] == TRAVEL_INDEX_ENGINES

    def test_manager_get_missing_raises(self):
        import pytest

        mgr = TravelIndexManager()
        with pytest.raises(SearXNGIndexNotFoundError, match="nonexistent"):
            mgr.get("nonexistent")

    def test_manager_delete_removes_index(self):
        import pytest

        mgr = TravelIndexManager()
        mgr.create("temporary")
        mgr.delete("temporary")
        with pytest.raises(SearXNGIndexNotFoundError):
            mgr.get("temporary")

    def test_manager_delete_nonexistent_raises(self):
        import pytest

        mgr = TravelIndexManager()
        with pytest.raises(SearXNGIndexNotFoundError):
            mgr.delete("no-such-index")

    def test_manager_list_all(self):
        mgr = TravelIndexManager()
        mgr.create("a")
        mgr.create("b")
        names = mgr.list_all()
        assert "a" in names
        assert "b" in names
        assert len(names) >= 2

    def test_manager_eager_load_indices(self):
        mgr = TravelIndexManager()
        mgr.create("eager-test")
        assert "eager-test" in mgr.indices

    def test_manager_empty_initial_list(self):
        mgr = TravelIndexManager()
        assert mgr.list_all() == []

    def test_manager_query_returns_exact_backend_results(self):
        expected = [
            {
                "title": "Exact backend result",
                "url": "https://travel.test/offers/42",
                "engine": "booking",
                "score": 0.91,
                "content": "backend-owned content",
                "category": "travel",
            }
        ]
        calls: list[dict[str, Any]] = []

        def backend(query: str, **kwargs: Any) -> list[dict[str, Any]]:
            calls.append({"query": query, **kwargs})
            return expected

        mgr = TravelIndexManager(search_backend=backend)
        mgr.create("travel-meta")
        results = mgr.query("travel-meta", "flights NYC to Paris")
        assert results == expected
        assert results is not expected
        assert calls == [
            {
                "query": "flights NYC to Paris",
                "engines": TRAVEL_INDEX_ENGINES,
                "max_results": 10,
            }
        ]

    def test_manager_query_without_backend_fails_closed(self):
        mgr = TravelIndexManager()
        mgr.create("travel-meta")

        with pytest.raises(SearXNGCreateIndexError, match="search backend"):
            mgr.query("travel-meta", "flights NYC to Paris")

    def test_manager_query_on_nonexistent_raises(self):
        mgr = TravelIndexManager()
        with pytest.raises(SearXNGIndexNotFoundError):
            mgr.query("ghost", "flights")

    def test_manager_repr(self):
        mgr = TravelIndexManager()
        mgr.create("primary")
        r = repr(mgr)
        assert "primary" in r

    def test_manager_has_existing(self):
        mgr = TravelIndexManager()
        mgr.create("check-me")
        assert mgr.has("check-me") is True
        assert mgr.has("not-here") is False

    def test_manager_enforces_registry_capacity(self) -> None:
        mgr = TravelIndexManager()
        for index in range(32):
            mgr.create(f"index-{index}")

        with pytest.raises(SearXNGCreateIndexError, match="limited to 32"):
            mgr.create("overflow")

    @pytest.mark.parametrize(
        ("query", "max_results", "message"),
        [
            ("contains\x00nul", 1, "without NUL"),
            ("valid", True, "between 1 and 100"),
            ("valid", 0, "between 1 and 100"),
            ("valid", 101, "between 1 and 100"),
        ],
    )
    def test_manager_rejects_unbounded_query_inputs(
        self,
        query: str,
        max_results: int,
        message: str,
    ) -> None:
        mgr = TravelIndexManager(search_backend=lambda *_args, **_kwargs: [])
        mgr.create("travel-meta")

        with pytest.raises(ValueError, match=message):
            mgr.query("travel-meta", query, max_results=max_results)

    @pytest.mark.parametrize("payload", [{"not": "a list"}, ["not-a-dict"]])
    def test_manager_rejects_invalid_backend_result_schema(self, payload: object) -> None:
        def backend(*_args: Any, **_kwargs: Any) -> Any:
            return payload

        mgr = TravelIndexManager(search_backend=backend)
        mgr.create("travel-meta")

        with pytest.raises(SearXNGCreateIndexError, match="invalid result list"):
            mgr.query("travel-meta", "valid")


class TestModuleFunctions:
    def test_create_index_returns_dict(self):
        result = create_index("travel-meta")
        assert result["name"] == "travel-meta"
        assert result["engines"] == TRAVEL_INDEX_ENGINES
        assert result["existed"] is False

    def test_create_index_already_exists(self):
        create_index("travel-meta")
        result = create_index("travel-meta")
        assert result["existed"] is True

    def test_index_exists_true(self):
        create_index("exists-test")
        assert index_exists("exists-test") is True

    def test_index_exists_false(self):
        assert index_exists("completely-missing") is False

    def test_query_index_returns_exact_injected_results(self):
        create_index("travel-meta")
        expected = [{"title": "Tokyo", "url": "https://travel.test/tokyo"}]
        results = query_index(
            "travel-meta",
            "hotels in Tokyo",
            search_backend=lambda _query, **_kwargs: expected,
        )
        assert results == expected

    def test_query_index_passes_engine_selection_to_backend(self):
        create_index("travel-meta")
        received: dict[str, Any] = {}

        def backend(query: str, **kwargs: Any) -> list[dict[str, Any]]:
            received.update({"query": query, **kwargs})
            return []

        assert query_index("travel-meta", "NYC hotels", search_backend=backend) == []
        assert received["engines"] == TRAVEL_INDEX_ENGINES

    def test_query_index_empty_query_returns_empty(self):
        create_index("travel-meta")
        results = query_index(
            "travel-meta",
            "",
            search_backend=lambda _query, **_kwargs: [],
        )
        assert isinstance(results, list)

    def test_delete_index_removes(self):
        import pytest

        create_index("kill-me")
        delete_index("kill-me")
        with pytest.raises(SearXNGIndexNotFoundError):
            query_index("kill-me", "anything")

    def test_delete_nonexistent_raises(self):
        import pytest

        with pytest.raises(SearXNGIndexNotFoundError):
            delete_index("never-made")

    def test_create_index_defaults(self):
        result = create_index("travel-meta", engines=None)
        assert result["engines"] == TRAVEL_INDEX_ENGINES

    def test_create_index_custom_name(self):
        result = create_index("custom-travel", engines=["google_flights", "kayak"])
        assert result["name"] == "custom-travel"
        assert result["engines"] == ["google_flights", "kayak"]

    def test_travel_index_engines_constant(self):
        assert isinstance(TRAVEL_INDEX_ENGINES, list)
        assert len(TRAVEL_INDEX_ENGINES) >= 6
        required = {"google_flights", "kayak", "skyscanner", "booking", "tripadvisor", "expedia"}
        assert required.issubset(set(TRAVEL_INDEX_ENGINES))


class TestSearXNGIndexErrors:
    def test_not_found_error_is_exception(self):
        err = SearXNGIndexNotFoundError("index 'missing' not found")
        assert isinstance(err, Exception)
        assert "missing" in str(err)

    def test_create_index_error_is_exception(self):
        err = SearXNGCreateIndexError("unable to create")
        assert isinstance(err, Exception)
        assert "create" in str(err)
