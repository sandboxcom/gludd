"""Tests for embeddings router — importability, request models, and helpers."""

from __future__ import annotations

import pytest


class TestEmbeddingsImports:
    def test_module_importable(self) -> None:
        from general_ludd.routers import embeddings

        assert embeddings is not None

    def test_register_function_exists(self) -> None:
        from general_ludd.routers.embeddings import register

        assert callable(register)


class TestEmbeddingRequestModels:
    def test_similar_request_model_constructs(self) -> None:
        from general_ludd.routers.embeddings import EmbeddingSimilarRequest

        req = EmbeddingSimilarRequest(text="test query", top_k=5)
        assert req.text == "test query"
        assert req.top_k == 5

    def test_similar_request_default_top_k(self) -> None:
        from general_ludd.routers.embeddings import EmbeddingSimilarRequest

        req = EmbeddingSimilarRequest(text="test")
        assert req.top_k == 5

    def test_compare_request_model(self) -> None:
        from general_ludd.routers.embeddings import EmbeddingCompareRequest

        req = EmbeddingCompareRequest(text_a="hello", text_b="world")
        assert req.text_a == "hello"
        assert req.text_b == "world"

    def test_search_request_model(self) -> None:
        from general_ludd.routers.embeddings import EmbeddingSearchRequest

        req = EmbeddingSearchRequest(text="find me", corpus="skills", top_k=3)
        assert req.text == "find me"
        assert req.corpus == "skills"
        assert req.top_k == 3

    def test_search_request_invalid_corpus_rejected(self) -> None:
        from general_ludd.routers.embeddings import EmbeddingSearchRequest

        with pytest.raises(ValueError):
            EmbeddingSearchRequest(text="x", corpus="invalid_corpus")
        assert True

    def test_multi_corpus_request_model(self) -> None:
        from general_ludd.routers.embeddings import MultiCorpusSearchRequest

        req = MultiCorpusSearchRequest(text="test", corpora=["skills", "task_types"])
        assert req.text == "test"
        assert req.corpora == ["skills", "task_types"]

    def test_similar_task_result_model(self) -> None:
        from general_ludd.routers.embeddings import SimilarTaskResult

        result = SimilarTaskResult(
            task_type="generation",
            similarity_score=0.95,
            canonical_text="Generate text",
            embedding_dim=1536,
        )
        assert result.task_type == "generation"
        assert result.similarity_score == 0.95

    def test_search_result_item_model(self) -> None:
        from general_ludd.routers.embeddings import SearchResultItem

        item = SearchResultItem(
            rank=1,
            name="test-skill",
            source_text="some text",
            similarity_score=0.88,
        )
        assert item.rank == 1
        assert item.name == "test-skill"
        assert item.similarity_score == 0.88


class TestParseJsonListHelper:
    def test_parses_valid_json_array(self) -> None:
        from general_ludd.routers.embeddings import _parse_json_list

        result = _parse_json_list('["a", "b", "c"]')
        assert result == ["a", "b", "c"]

    def test_non_list_json_returns_empty(self) -> None:
        from general_ludd.routers.embeddings import _parse_json_list

        result = _parse_json_list('{"key": "value"}')
        assert result == []

    def test_non_string_returns_empty(self) -> None:
        from general_ludd.routers.embeddings import _parse_json_list

        result = _parse_json_list(42)
        assert result == []

    def test_invalid_json_returns_empty(self) -> None:
        from general_ludd.routers.embeddings import _parse_json_list

        result = _parse_json_list("not valid json")
        assert result == []

    def test_none_returns_empty(self) -> None:
        from general_ludd.routers.embeddings import _parse_json_list

        result = _parse_json_list(None)
        assert result == []


class TestEmbeddingMethodHelper:
    def test_returns_string_for_store(self) -> None:
        from general_ludd.routers.embeddings import _embedding_method

        store = object()
        result = _embedding_method(store)
        assert isinstance(result, str)
        assert len(result) > 0


class TestCompareHelper:
    def test_compare_identical_strings(self) -> None:
        from general_ludd.routers.embeddings import EmbeddingCompareRequest, _compare

        req = EmbeddingCompareRequest(text_a="hello world", text_b="hello world")
        result = _compare(req)
        assert result.similarity is not None
        assert result.similarity >= 0.0
        assert result.similarity <= 1.0
