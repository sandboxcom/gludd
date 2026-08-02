"""Unit tests for travel searxng_index module and searxng_client module_utils."""

from __future__ import annotations

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

    def test_manager_query_returns_results(self):
        mgr = TravelIndexManager()
        mgr.create("travel-meta")
        results = mgr.query("travel-meta", "flights NYC to Paris")
        assert isinstance(results, list)
        assert len(results) > 0
        assert "title" in results[0]

    def test_manager_query_on_nonexistent_raises(self):
        import pytest

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

    def test_query_index_returns_results(self):
        create_index("travel-meta")
        results = query_index("travel-meta", "hotels in Tokyo")
        assert isinstance(results, list)
        assert len(results) > 0

    def test_query_index_includes_engine_info(self):
        create_index("travel-meta")
        results = query_index("travel-meta", "NYC hotels")
        assert len(results) > 0
        for r in results:
            assert "engine" in r or "source" in r

    def test_query_index_empty_query_returns_empty(self):
        create_index("travel-meta")
        results = query_index("travel-meta", "")
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
