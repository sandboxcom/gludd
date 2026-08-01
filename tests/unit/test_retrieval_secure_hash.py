"""Security and compatibility tests for deterministic retrieval vectors."""

from __future__ import annotations

import hashlib
import math
from unittest.mock import patch

import pytest

from general_ludd.ai_ml.retrieval import RetrievalResult, RetrievalService


def test_dense_vector_uses_versioned_blake2b_mapping() -> None:
    """The dense stub must not retain the collision-prone legacy MD5 map."""
    word = "retrieval"
    digest = hashlib.blake2b(
        word.encode("utf-8"),
        digest_size=32,
        person=b"gludd-retrieval",
    ).digest()
    expected = [0.0] * 64
    expected[int.from_bytes(digest, byteorder="big") % len(expected)] = 1.0

    with patch(
        "general_ludd.ai_ml.retrieval.hashlib.md5",
        side_effect=AssertionError("legacy MD5 mapping was called"),
    ):
        vector = RetrievalService._hash_vec(word)

    assert vector == expected


@pytest.mark.parametrize("text", ["", "   ", "Caf\u00e9 \u6771\u4eac", "word " * 1_000])
def test_dense_vector_is_deterministic_normalized_and_bounded(text: str) -> None:
    first = RetrievalService._hash_vec(text)
    second = RetrievalService._hash_vec(text)

    assert first == second
    assert len(first) == 64
    assert all(math.isfinite(value) and value >= 0.0 for value in first)
    norm = math.sqrt(sum(value * value for value in first))
    assert norm == pytest.approx(0.0 if not text.split() else 1.0)


def test_search_records_dense_vector_mapping_version() -> None:
    service = RetrievalService()
    service.index("source-1", "deterministic secure retrieval")

    result = service.search("secure retrieval", k=1)

    assert result.dense_vector_version == "blake2b-256-v2"


def test_result_rejects_empty_dense_vector_version() -> None:
    with pytest.raises(ValueError, match="dense_vector_version"):
        RetrievalResult(
            query="query",
            query_rewrite="query",
            index_version="index-v1",
            filter_policy="default",
            passages=(),
            reranker_version="reranker-v1",
            retrieved_source_ids=(),
            latency_ms=0.0,
            dense_vector_version="",
        )
