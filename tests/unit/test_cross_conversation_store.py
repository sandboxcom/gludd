"""Structural tests for memory/cross_conversation.py — CrossConversationStore."""

from __future__ import annotations

import time

from general_ludd.memory.cross_conversation import CrossConversationStore, _now


class TestCrossConversationStoreInit:
    def test_default_init_creates_ephemeral(self) -> None:
        store = CrossConversationStore()
        assert store.available is True

    def test_init_with_explicit_store(self) -> None:
        store = CrossConversationStore(store=None)
        assert store._store is None

    def test_store_key_generation(self) -> None:
        result = CrossConversationStore._store_key(("ns1", "ns2"), "mykey")
        assert result == "ns1:ns2:mykey"

    def test_store_key_single_namespace(self) -> None:
        result = CrossConversationStore._store_key(("default",), "key1")
        assert result == "default:key1"

    def test_normalise_namespace_string(self) -> None:
        result = CrossConversationStore._normalise_namespace("myns")
        assert result == ("myns",)

    def test_normalise_namespace_tuple_unchanged(self) -> None:
        result = CrossConversationStore._normalise_namespace(("a", "b"))
        assert result == ("a", "b")

    def test_normalise_namespace_empty_tuple(self) -> None:
        result = CrossConversationStore._normalise_namespace(())
        assert result == ()

    def test_now_returns_float(self) -> None:
        t = _now()
        assert isinstance(t, float)
        assert t > 0


class TestCrossConversationPutGet:
    def test_put_and_get_default_namespace(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"val": 42})
        result = store.get("k1")
        assert result is not None
        assert result["key"] == "k1"
        assert result["value"] == {"val": 42}

    def test_put_and_get_custom_namespace(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"x": 1}, namespace=("custom", "sub"))
        result = store.get("k1", namespace=("custom", "sub"))
        assert result is not None
        assert result["value"] == {"x": 1}

    def test_get_missing_key_returns_none(self) -> None:
        store = CrossConversationStore()
        assert store.get("nonexistent") is None

    def test_get_wrong_namespace_returns_none(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"a": 1}, namespace=("ns_a",))
        assert store.get("k1", namespace=("ns_b",)) is None

    def test_put_overwrites_existing(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1})
        store.put("k1", {"v": 2})
        result = store.get("k1")
        assert result is not None
        assert result["value"] == {"v": 2}

    def test_put_with_string_namespace(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, namespace="string_ns")
        result = store.get("k1", namespace="string_ns")
        assert result is not None

    def test_namespace_isolation(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": "a"}, namespace=("ns_a",))
        store.put("k1", {"v": "b"}, namespace=("ns_b",))
        assert store.get("k1", namespace=("ns_a",))["value"] == {"v": "a"}
        assert store.get("k1", namespace=("ns_b",))["value"] == {"v": "b"}


class TestCrossConversationTTL:
    def test_ttl_expired_returns_none(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, ttl=0.001)
        time.sleep(0.01)
        assert store.get("k1") is None

    def test_ttl_not_expired_returns_value(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, ttl=3600)
        result = store.get("k1")
        assert result is not None

    def test_put_without_ttl_clears_previous_ttl(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, ttl=3600)
        store.put("k1", {"v": 2})
        assert store._ttl_registry == {} or "k1" not in store._ttl_registry

    def test_is_expired_no_deadline(self) -> None:
        store = CrossConversationStore()
        assert store._is_expired("nonexistent_key") is False

    def test_is_expired_past_deadline(self) -> None:
        store = CrossConversationStore()
        store._ttl_registry["test_key"] = 0
        assert store._is_expired("test_key") is True

    def test_evict_removes_from_ephemeral_and_ttl(self) -> None:
        store = CrossConversationStore()
        store.put("ek", {"v": 1})
        store._evict("default:ek", ("default",), "ek")
        assert store.get("ek") is None


class TestCrossConversationDelete:
    def test_delete_returns_true_when_existed(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1})
        assert store.delete("k1") is True

    def test_delete_returns_false_when_absent(self) -> None:
        store = CrossConversationStore()
        assert store.delete("nonexistent") is False

    def test_delete_removes_from_ephemeral(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1})
        store.delete("k1")
        assert store.get("k1") is None

    def test_delete_with_namespace(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, namespace=("ns",))
        store.delete("k1", namespace=("ns",))
        assert store.get("k1", namespace=("ns",)) is None


class TestCrossConversationSearch:
    def test_search_finds_matching_namespace(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, namespace=("ns_a",))
        store.put("k2", {"v": 2}, namespace=("ns_a",))
        results = store.search(namespace_prefix=("ns_a",))
        assert len(results) == 2

    def test_search_excludes_wrong_namespace(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, namespace=("ns_a",))
        store.put("k2", {"v": 2}, namespace=("ns_b",))
        results = store.search(namespace_prefix=("ns_a",))
        assert len(results) == 1
        assert results[0]["key"] == "k1"

    def test_search_with_filter(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"type": "task"}, namespace=("default",))
        store.put("k2", {"type": "event"}, namespace=("default",))
        results = store.search(filter={"type": "task"})
        assert len(results) == 1
        assert results[0]["key"] == "k1"

    def test_search_with_limit(self) -> None:
        store = CrossConversationStore()
        for i in range(5):
            store.put(f"k{i}", {"v": i}, namespace=("default",))
        results = store.search(limit=3)
        assert len(results) <= 3

    def test_search_excludes_expired(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, ttl=0.001)
        store.put("k2", {"v": 2}, ttl=3600)
        time.sleep(0.01)
        results = store.search()
        assert len(results) == 1
        assert results[0]["key"] == "k2"

    def test_search_empty_store_returns_empty(self) -> None:
        store = CrossConversationStore()
        results = store.search()
        assert results == []


class TestCrossConversationPurgeExpired:
    def test_purge_removes_expired_entries(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, ttl=0.001)
        store.put("k2", {"v": 2}, ttl=3600)
        time.sleep(0.01)
        purged = store.purge_expired()
        assert purged >= 1
        assert store.get("k1") is None
        assert store.get("k2") is not None

    def test_purge_empty_store_returns_zero(self) -> None:
        store = CrossConversationStore()
        assert store.purge_expired() == 0


class TestCrossConversationResultShape:
    def test_get_result_has_expected_keys(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"name": "test"})
        result = store.get("k1")
        assert result is not None
        for key in ("key", "value", "namespace", "created_at", "updated_at"):
            assert key in result, f"Missing key: {key}"

    def test_search_result_has_expected_keys(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"name": "test"})
        results = store.search()
        assert len(results) == 1
        for key in ("key", "value", "namespace", "created_at", "updated_at"):
            assert key in results[0], f"Missing key: {key}"

    def test_search_result_namespace_is_list(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"v": 1}, namespace=("a", "b"))
        results = store.search(namespace_prefix=("a",))
        assert isinstance(results[0]["namespace"], list)
        assert results[0]["namespace"] == ["a", "b"]
