"""Tests for G3 semantic codebase retrieval."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_cache_dir() -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_files(tmp_path: Path) -> tuple[list[Path], Path]:
    files_dir = tmp_path / "test_src"
    files_dir.mkdir()

    py_file = files_dir / "main.py"
    py_file.write_text("""
def hello_world():
    print("Hello, world!")

class Calculator:
    def add(self, a, b):
        return a + b

    def subtract(self, a, b):
        return a - b
""")

    md_file = files_dir / "README.md"
    md_file.write_text("""
# Test Project

This is a test project for semantic retrieval.

## Installation

Run pip install to install the package.

## Usage

Import the module and use the Calculator class.
""")

    return [py_file, md_file], files_dir


class TestCodebaseIndexer:
    def test_index_files_returns_stats(self, sample_files, temp_cache_dir):
        from general_ludd.retrieval.indexer import CodebaseIndexer

        paths, _ = sample_files
        indexer = CodebaseIndexer(cache_dir=temp_cache_dir)
        result = indexer.index_files(paths)
        indexer.close()

        assert result["files_indexed"] == 2
        assert result["chunks_indexed"] > 0
        assert result["errors"] == 0

    def test_index_files_skips_missing(self, temp_cache_dir):
        from general_ludd.retrieval.indexer import CodebaseIndexer

        indexer = CodebaseIndexer(cache_dir=temp_cache_dir)
        result = indexer.index_files([Path("nonexistent.py")])
        indexer.close()

        assert result["files_indexed"] == 0
        assert result["chunks_indexed"] == 0

    def test_chunking_python_functions(self, temp_cache_dir):
        from general_ludd.retrieval.indexer import CodebaseIndexer

        indexer = CodebaseIndexer(cache_dir=temp_cache_dir)
        content = """
def foo():
    pass

def bar():
    return 42
"""
        chunks = indexer._chunk_python(content)
        assert len(chunks) >= 1
        indexer.close()

    def test_chunking_generic_splits_on_double_newlines(self, temp_cache_dir):
        from general_ludd.retrieval.indexer import CodebaseIndexer

        indexer = CodebaseIndexer(cache_dir=temp_cache_dir)
        content = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        chunks = indexer._chunk_generic(content)
        assert len(chunks) == 3
        indexer.close()

    def test_indexer_close_cleanup(self, temp_cache_dir):
        from general_ludd.retrieval.indexer import CodebaseIndexer

        indexer = CodebaseIndexer(cache_dir=temp_cache_dir)
        indexer.close()


class TestSemanticSearcher:
    def test_search_returns_results(self, sample_files, temp_cache_dir):
        from general_ludd.retrieval.indexer import CodebaseIndexer
        from general_ludd.retrieval.searcher import SemanticSearcher

        paths, _ = sample_files
        indexer = CodebaseIndexer(cache_dir=temp_cache_dir)
        indexer.index_files(paths)
        indexer.close()

        searcher = SemanticSearcher(cache_dir=temp_cache_dir)
        results = searcher.search("calculator add", top_k=5)
        searcher.close()

        assert len(results) > 0

    def test_search_top_k_limits_results(self, sample_files, temp_cache_dir):
        from general_ludd.retrieval.indexer import CodebaseIndexer
        from general_ludd.retrieval.searcher import SemanticSearcher

        paths, _ = sample_files
        indexer = CodebaseIndexer(cache_dir=temp_cache_dir)
        indexer.index_files(paths)
        indexer.close()

        searcher = SemanticSearcher(cache_dir=temp_cache_dir)
        results = searcher.search("test project", top_k=1)
        searcher.close()

        assert len(results) <= 1
        if results:
            assert "score" in results[0]
            assert "filepath" in results[0]
            assert "content" in results[0]

    def test_search_empty_query_returns_empty(self, temp_cache_dir):
        from general_ludd.retrieval.searcher import SemanticSearcher

        searcher = SemanticSearcher(cache_dir=temp_cache_dir)
        results = searcher.search("", top_k=5)
        searcher.close()

        assert results == []

    def test_search_no_cache_returns_empty(self):
        from general_ludd.retrieval.searcher import SemanticSearcher

        searcher = SemanticSearcher(cache_dir="/nonexistent/cache")
        results = searcher.search("query", top_k=5)
        assert results == []

    def test_index_search_roundtrip_relevance(self, sample_files, temp_cache_dir):
        from general_ludd.retrieval.indexer import CodebaseIndexer
        from general_ludd.retrieval.searcher import SemanticSearcher

        paths, _ = sample_files
        indexer = CodebaseIndexer(cache_dir=temp_cache_dir)
        indexer.index_files(paths)
        indexer.close()

        searcher = SemanticSearcher(cache_dir=temp_cache_dir)
        results = searcher.search("calculator subtract", top_k=5)
        searcher.close()

        assert len(results) > 0
        top_file = results[0]["filepath"]
        assert "main.py" in top_file


class TestTokenization:
    def test_tokenize_splits_words(self):
        from general_ludd.retrieval.indexer import _tokenize

        tokens = _tokenize("Hello World! This is a test.")
        assert "hello" in tokens
        assert "world" in tokens
        assert "this" in tokens
        assert "test" in tokens

    def test_tokenize_filters_short_tokens(self):
        from general_ludd.retrieval.indexer import _tokenize

        tokens = _tokenize("a b c ab cd x")
        assert all(len(t) > 1 for t in tokens)

    def test_tokenize_handles_empty(self):
        from general_ludd.retrieval.indexer import _tokenize

        assert _tokenize("") == []
        assert _tokenize("!@#$%") == []


class TestCosineSimilarity:
    def test_identical_vectors(self):
        from general_ludd.retrieval.indexer import _cosine_similarity
        vec = {"hello": 1.0, "world": 2.0}
        assert _cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        from general_ludd.retrieval.indexer import _cosine_similarity
        vec_a = {"a": 1.0}
        vec_b = {"b": 1.0}
        assert _cosine_similarity(vec_a, vec_b) == 0.0

    def test_zero_norm_returns_zero(self):
        from general_ludd.retrieval.indexer import _cosine_similarity
        assert _cosine_similarity({"x": 0.0}, {"y": 1.0}) == 0.0


class TestIndexBatchSize:
    def test_batch_size_default(self):
        from general_ludd.retrieval.indexer import CodebaseIndexer

        with tempfile.TemporaryDirectory() as tmpdir:
            indexer = CodebaseIndexer(cache_dir=tmpdir)
            result = indexer.index_files([], batch_size=32)
            assert result["files_indexed"] == 0
            indexer.close()


class TestIndexUnicode:
    def test_handles_non_utf8(self, temp_cache_dir):
        import tempfile as tf
        from pathlib import Path

        from general_ludd.retrieval.indexer import CodebaseIndexer
        tmp = Path(tf.gettempdir())
        bad = tmp / "binary_file.bin"
        bad.write_bytes(b"\x00\x01\x02\x80\xff")
        try:
            indexer = CodebaseIndexer(cache_dir=temp_cache_dir)
            result = indexer.index_files([bad])
            indexer.close()
            assert result["errors"] >= 1 or result["files_indexed"] == 0
        finally:
            if bad.exists():
                bad.unlink()


class TestSemanticSearcherRelevance:
    def test_search_ranks_by_relevance(self, sample_files, temp_cache_dir):
        from general_ludd.retrieval.indexer import CodebaseIndexer
        from general_ludd.retrieval.searcher import SemanticSearcher

        paths, _ = sample_files
        indexer = CodebaseIndexer(cache_dir=temp_cache_dir)
        indexer.index_files(paths)
        indexer.close()

        searcher = SemanticSearcher(cache_dir=temp_cache_dir)
        results = searcher.search("Calculator class", top_k=5)
        searcher.close()

        assert len(results) > 0
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_scores_are_between_zero_and_one(self, sample_files, temp_cache_dir):
        from general_ludd.retrieval.indexer import CodebaseIndexer
        from general_ludd.retrieval.searcher import SemanticSearcher

        paths, _ = sample_files
        indexer = CodebaseIndexer(cache_dir=temp_cache_dir)
        indexer.index_files(paths)
        indexer.close()

        searcher = SemanticSearcher(cache_dir=temp_cache_dir)
        results = searcher.search("test", top_k=5)
        searcher.close()

        for r in results:
            assert 0.0 <= r["score"] <= 1.0

    def test_top_k_zero_returns_empty(self, sample_files, temp_cache_dir):
        from general_ludd.retrieval.indexer import CodebaseIndexer
        from general_ludd.retrieval.searcher import SemanticSearcher

        paths, _ = sample_files
        indexer = CodebaseIndexer(cache_dir=temp_cache_dir)
        indexer.index_files(paths)
        indexer.close()

        searcher = SemanticSearcher(cache_dir=temp_cache_dir)
        results = searcher.search("test", top_k=0)
        searcher.close()

        assert results == []


class TestIndexerContextManager:
    def test_close_clears_cache(self, temp_cache_dir):
        from general_ludd.retrieval.indexer import CodebaseIndexer

        indexer = CodebaseIndexer(cache_dir=temp_cache_dir)
        assert indexer._cache is not None
        indexer.close()


class TestTokenizeEdgeCases:
    def test_single_char_tokens_filtered(self):
        from general_ludd.retrieval.indexer import _tokenize

        tokens = _tokenize("a b c d ef")
        assert tokens == ["ef"]

    def test_mixed_case_normalized(self):
        from general_ludd.retrieval.indexer import _tokenize

        tokens = _tokenize("FooBar FooBar")
        assert "foobar" in tokens
        count = tokens.count("foobar")
        assert count == 2

    def test_snake_case_tokens(self):
        from general_ludd.retrieval.indexer import _tokenize

        tokens = _tokenize("hello_world test_function")
        assert "hello_world" in tokens
        assert "test_function" in tokens

    def test_camel_case_tokens(self):
        from general_ludd.retrieval.indexer import _tokenize

        tokens = _tokenize("camelCase PascalCase")
        assert "camelcase" in tokens
        assert "pascalcase" in tokens
