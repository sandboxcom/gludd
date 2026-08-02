"""Tests for module_utils/embeddings.py — EmbeddingClient and VectorStore."""

from __future__ import annotations

import pytest
from plugins.module_utils.embeddings import EmbeddingClient, VectorStore


class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        a = [1.0, 0.0, 0.0]
        assert EmbeddingClient.cosine_similarity(a, a) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert EmbeddingClient.cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self) -> None:
        a = [1.0, 0.0, 0.0]
        b = [-1.0, 0.0, 0.0]
        assert EmbeddingClient.cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_partial_similarity(self) -> None:
        a = [1.0, 2.0, 3.0]
        b = [2.0, 4.0, 6.0]
        assert EmbeddingClient.cosine_similarity(a, b) == pytest.approx(1.0)

    def test_zero_vector_returns_zero(self) -> None:
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert EmbeddingClient.cosine_similarity(a, b) == 0.0
        assert EmbeddingClient.cosine_similarity(b, a) == 0.0

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="vector length mismatch"):
            EmbeddingClient.cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_empty_vectors(self) -> None:
        with pytest.raises(ValueError, match="vector length mismatch"):
            EmbeddingClient.cosine_similarity([], [1.0])


class TestEmbeddingClientConstruction:
    def test_default_construction(self) -> None:
        client = EmbeddingClient(model_profile="openai/text-embedding-3-small")
        assert client._profile == "openai/text-embedding-3-small"
        assert client._timeout == 60

    def test_custom_timeout(self) -> None:
        client = EmbeddingClient(model_profile="local/bge-small", timeout=120)
        assert client._timeout == 120


class TestEmbeddingClientEmbed:
    def test_embed_text_returns_vector(self) -> None:
        client = EmbeddingClient()
        result = client.embed_text("hello world")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_embed_text_different_inputs(self) -> None:
        client = EmbeddingClient()
        v1 = client.embed_text("hello")
        v2 = client.embed_text("world")
        assert v1 != v2

    def test_embed_text_same_input(self) -> None:
        client = EmbeddingClient()
        v1 = client.embed_text("hello")
        v2 = client.embed_text("hello")
        assert v1 == v2

    def test_embed_batch_multiple(self) -> None:
        client = EmbeddingClient()
        results = client.embed_batch(["a", "b", "c"])
        assert len(results) == 3
        for r in results:
            assert isinstance(r, list)
            assert len(r) > 0

    def test_embed_batch_empty(self) -> None:
        client = EmbeddingClient()
        results = client.embed_batch([])
        assert results == []

    def test_embed_text_empty(self) -> None:
        client = EmbeddingClient()
        result = client.embed_text("")
        assert isinstance(result, list)
        assert all(v == 0.0 for v in result)


class TestVectorStoreBasic:
    def test_initial_empty(self) -> None:
        store = VectorStore()
        assert len(store) == 0

    def test_add_and_len(self) -> None:
        store = VectorStore()
        store.add("a", [1.0, 2.0])
        assert len(store) == 1
        assert "a" in store
        assert "b" not in store

    def test_add_overwrites(self) -> None:
        store = VectorStore()
        store.add("a", [1.0, 2.0])
        store.add("a", [3.0, 4.0])
        assert store.get("a") == [3.0, 4.0]
        assert len(store) == 1

    def test_remove(self) -> None:
        store = VectorStore()
        store.add("a", [1.0, 2.0])
        store.add("b", [3.0, 4.0])
        store.remove("a")
        assert len(store) == 1
        assert "a" not in store
        assert "b" in store

    def test_remove_nonexistent_noop(self) -> None:
        store = VectorStore()
        store.remove("nope")

    def test_clear(self) -> None:
        store = VectorStore()
        store.add("a", [1.0, 2.0])
        store.add("b", [3.0, 4.0])
        store.clear()
        assert len(store) == 0

    def test_get(self) -> None:
        store = VectorStore()
        store.add("a", [1.0, 2.0, 3.0])
        assert store.get("a") == [1.0, 2.0, 3.0]

    def test_get_raises_keyerror(self) -> None:
        store = VectorStore()
        with pytest.raises(KeyError):
            store.get("missing")

    def test_list_ids(self) -> None:
        store = VectorStore()
        store.add("b", [1.0])
        store.add("a", [2.0])
        ids = store.list_ids()
        assert set(ids) == {"a", "b"}


class TestVectorStoreSearch:
    def test_search_returns_top_k(self) -> None:
        store = VectorStore()
        store.add("first", [1.0, 0.0, 0.0])
        store.add("second", [0.0, 0.0, 1.0])
        store.add("third", [0.0, 1.0, 0.0])

        query = [1.0, 0.0, 0.0]
        results = store.search(query, k=2)
        assert len(results) == 2
        assert results[0][0] == "first"
        assert results[0][1] == pytest.approx(1.0)

    def test_search_empty_store(self) -> None:
        store = VectorStore()
        assert store.search([1.0, 2.0, 3.0]) == []

    def test_search_k_clamped(self) -> None:
        store = VectorStore()
        store.add("x", [1.0, 2.0])
        results = store.search([1.0, 2.0], k=10)
        assert len(results) == 1

    def test_search_k_zero_clamped_to_one(self) -> None:
        store = VectorStore()
        store.add("x", [1.0, 2.0])
        results = store.search([1.0, 2.0], k=0)
        assert len(results) == 1

    def test_search_k_negative_clamped_to_one(self) -> None:
        store = VectorStore()
        store.add("x", [1.0, 2.0])
        results = store.search([1.0, 2.0], k=-5)
        assert len(results) == 1

    def test_search_ordering_descending(self) -> None:
        store = VectorStore()
        store.add("a", [1.0, 0.0, 0.0])
        store.add("b", [0.0, 1.0, 0.0])
        store.add("c", [0.0, 0.0, 1.0])
        query = [1.0, 0.0, 0.0]
        results = store.search(query, k=3)
        assert results[0][0] == "a"
        assert results[0][1] == pytest.approx(1.0)
        assert results[1][1] == pytest.approx(0.0)
        assert results[2][1] == pytest.approx(0.0)


class TestVectorStoreSimilarity:
    def test_similarity_known_item(self) -> None:
        store = VectorStore()
        store.add("a", [1.0, 0.0, 0.0])
        score = store.similarity([1.0, 0.0, 0.0], "a")
        assert score == pytest.approx(1.0)

    def test_similarity_unknown_raises(self) -> None:
        store = VectorStore()
        with pytest.raises(KeyError):
            store.similarity([1.0, 2.0], "no-such-item")
