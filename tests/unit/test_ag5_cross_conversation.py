"""Tests for AG.5 CrossConversationStore — LangGraph Store API wrapper."""

from __future__ import annotations

import time

from general_ludd.memory.cross_conversation import CrossConversationStore


class TestPutGetRoundTrip:
    def test_put_get_default_namespace(self) -> None:
        store = CrossConversationStore()
        store.put("greeting", {"msg": "hello", "lang": "en"})
        result = store.get("greeting")
        assert result is not None
        assert result["key"] == "greeting"
        assert result["value"]["msg"] == "hello"
        assert result["value"]["lang"] == "en"

    def test_put_get_explicit_namespace(self) -> None:
        store = CrossConversationStore()
        store.put("config", {"theme": "dark"}, namespace=("ui", "prefs"))
        result = store.get("config", namespace=("ui", "prefs"))
        assert result is not None
        assert result["value"]["theme"] == "dark"

    def test_get_missing_returns_none(self) -> None:
        store = CrossConversationStore()
        assert store.get("nonexistent") is None

    def test_get_wrong_namespace_returns_none(self) -> None:
        store = CrossConversationStore()
        store.put("secret", {"data": "sensitive"}, namespace=("admin",))
        assert store.get("secret", namespace=("user",)) is None

    def test_overwrite_updates_value(self) -> None:
        store = CrossConversationStore()
        store.put("count", {"n": 1})
        store.put("count", {"n": 99})
        result = store.get("count")
        assert result is not None
        assert result["value"]["n"] == 99


class TestNamespaceIsolation:
    def test_same_key_different_namespaces(self) -> None:
        store = CrossConversationStore()
        store.put("x", {"val": "a"}, namespace=("ns1",))
        store.put("x", {"val": "b"}, namespace=("ns2",))
        assert store.get("x", namespace=("ns1",))["value"]["val"] == "a"
        assert store.get("x", namespace=("ns2",))["value"]["val"] == "b"

    def test_string_namespace_normalised_to_tuple(self) -> None:
        store = CrossConversationStore()
        store.put("item", {"v": 1}, namespace="solo")
        result = store.get("item", namespace="solo")
        assert result is not None
        assert result["value"]["v"] == 1

    def test_nested_namespace_isolation(self) -> None:
        store = CrossConversationStore()
        store.put("k", {"v": 1}, namespace=("a", "b"))
        store.put("k", {"v": 2}, namespace=("a", "b", "c"))
        assert store.get("k", namespace=("a", "b"))["value"]["v"] == 1
        assert store.get("k", namespace=("a", "b", "c"))["value"]["v"] == 2


class TestTTLExpiration:
    def test_item_retrievable_before_ttl(self) -> None:
        store = CrossConversationStore()
        store.put("temp", {"data": "transient"}, ttl=60)
        result = store.get("temp")
        assert result is not None
        assert result["value"]["data"] == "transient"

    def test_item_expired_after_ttl(self) -> None:
        store = CrossConversationStore()
        store.put("temp", {"data": "transient"}, ttl=0.001)
        time.sleep(0.01)
        assert store.get("temp") is None

    def test_ttl_only_affects_target_key(self) -> None:
        store = CrossConversationStore()
        store.put("short", {"v": "expired"}, ttl=0.001)
        store.put("long", {"v": "persistent"}, ttl=3600)
        time.sleep(0.01)
        assert store.get("short") is None
        long_result = store.get("long")
        assert long_result is not None
        assert long_result["value"]["v"] == "persistent"

    def test_no_ttl_persists(self) -> None:
        store = CrossConversationStore()
        store.put("forever", {"v": "eternal"})
        assert store.get("forever") is not None

    def test_ttl_across_namespaces_independent(self) -> None:
        store = CrossConversationStore()
        store.put("k", {"v": 1}, namespace=("fast",), ttl=0.001)
        store.put("k", {"v": 2}, namespace=("slow",), ttl=3600)
        time.sleep(0.01)
        assert store.get("k", namespace=("fast",)) is None
        assert store.get("k", namespace=("slow",))["value"]["v"] == 2

    def test_purge_expired_removes_stale(self) -> None:
        store = CrossConversationStore()
        store.put("a", {"v": 1}, ttl=0.001)
        store.put("b", {"v": 2}, ttl=0.001)
        store.put("c", {"v": 3}, ttl=3600)
        time.sleep(0.01)
        purged = store.purge_expired()
        assert purged == 2
        assert store.get("a") is None
        assert store.get("b") is None
        assert store.get("c") is not None


class TestSearch:
    def test_search_finds_items_in_namespace(self) -> None:
        store = CrossConversationStore()
        store.put("k1", {"val": 1}, namespace=("searchable",))
        store.put("k2", {"val": 2}, namespace=("searchable",))
        store.put("k3", {"val": 3}, namespace=("hidden",))
        results = store.search(namespace_prefix=("searchable",))
        assert len(results) == 2
        result_keys = {r["key"] for r in results}
        assert result_keys == {"k1", "k2"}

    def test_search_with_filter(self) -> None:
        store = CrossConversationStore()
        store.put("u1", {"role": "admin", "active": "true"})
        store.put("u2", {"role": "user", "active": "true"})
        store.put("u3", {"role": "admin", "active": "false"})
        results = store.search(filter={"role": "admin"})
        assert len(results) == 2
        for r in results:
            assert r["value"]["role"] == "admin"

    def test_search_with_limit(self) -> None:
        store = CrossConversationStore()
        for i in range(20):
            store.put(f"item_{i}", {"idx": i})
        results = store.search(limit=5)
        assert len(results) == 5

    def test_search_excludes_expired(self) -> None:
        store = CrossConversationStore()
        store.put("live", {"v": 1})
        store.put("dead", {"v": 2}, ttl=0.001)
        time.sleep(0.01)
        results = store.search()
        assert len(results) == 1
        assert results[0]["key"] == "live"

    def test_search_namespace_prefix_matches_prefix(self) -> None:
        store = CrossConversationStore()
        store.put("a", {"v": 1}, namespace=("app", "config"))
        store.put("b", {"v": 2}, namespace=("app", "data"))
        store.put("c", {"v": 3}, namespace=("other",))
        results = store.search(namespace_prefix=("app",))
        assert len(results) == 2
        result_keys = {r["key"] for r in results}
        assert result_keys == {"a", "b"}


class TestDelete:
    def test_delete_removes_item(self) -> None:
        store = CrossConversationStore()
        store.put("removable", {"val": 1})
        assert store.get("removable") is not None
        deleted = store.delete("removable")
        assert deleted is True
        assert store.get("removable") is None

    def test_delete_nonexistent_returns_false(self) -> None:
        store = CrossConversationStore()
        assert store.delete("ghost") is False

    def test_delete_respects_namespace(self) -> None:
        store = CrossConversationStore()
        store.put("k", {"v": 1}, namespace=("a",))
        store.put("k", {"v": 2}, namespace=("b",))
        store.delete("k", namespace=("a",))
        assert store.get("k", namespace=("a",)) is None
        assert store.get("k", namespace=("b",)) is not None


class TestGracefulDegradation:
    def test_works_without_langgraph_store(self) -> None:
        store = CrossConversationStore()
        store.put("standalone", {"msg": "no langgraph needed"})
        assert store.get("standalone") is not None

    def test_accepts_explicit_store(self) -> None:
        from langgraph.store.memory import InMemoryStore
        backend = InMemoryStore()
        store = CrossConversationStore(store=backend)
        store.put("explicit", {"msg": "via backend"})
        result = backend.get(("default",), "explicit")
        assert result is not None
        assert result.value["msg"] == "via backend"


class TestReturnFieldShape:
    def test_get_result_has_required_fields(self) -> None:
        store = CrossConversationStore()
        store.put("shape", {"val": 1})
        result = store.get("shape")
        assert result is not None
        for field in ("key", "value", "namespace", "created_at", "updated_at"):
            assert field in result, f"missing field: {field}"

    def test_search_result_has_required_fields(self) -> None:
        store = CrossConversationStore()
        store.put("s1", {"val": 1})
        store.put("s2", {"val": 2})
        results = store.search()
        assert len(results) >= 1
        for field in ("key", "value", "namespace", "created_at", "updated_at"):
            assert field in results[0], f"missing field: {field}"
