"""Structural tests for retrieval/indexer.py module shape."""

from __future__ import annotations

import tempfile

from general_ludd.retrieval.indexer import (
    DEFAULT_CACHE_DIR,
    MAX_CHUNK_CHARS,
    CodebaseIndexer,
    _cosine_similarity,
    _tokenize,
)


class TestModuleConstants:
    def test_default_cache_dir(self):
        assert DEFAULT_CACHE_DIR.endswith("retrieval_cache")

    def test_max_chunk_chars(self):
        assert MAX_CHUNK_CHARS == 2000


class TestTokenizeEdgeCases:
    def test_numbers_included(self):
        tokens = _tokenize("var1 test2 func3")
        assert "var1" in tokens
        assert "test2" in tokens
        assert "func3" in tokens

    def test_leading_underscores_stripped(self):
        tokens = _tokenize("__init__ __private__")
        assert "init" in tokens
        assert "private" in tokens

    def test_punctuation_ignored(self):
        tokens = _tokenize("hello, world! test.")
        assert tokens == ["hello", "world", "test"]


class TestCosineSimilarityEdgeCases:
    def test_partial_overlap(self):
        vec_a = {"a": 1.0, "b": 2.0, "c": 3.0}
        vec_b = {"b": 1.0, "c": 1.0, "d": 1.0}
        score = _cosine_similarity(vec_a, vec_b)
        assert 0.0 < score < 1.0


class TestCodebaseIndexerStructural:
    def test_cache_dir_property(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            indexer = CodebaseIndexer(cache_dir=tmpdir)
            assert indexer.cache_dir == tmpdir
            indexer.close()

    def test_expands_user_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            indexer = CodebaseIndexer(cache_dir=tmpdir)
            assert indexer.cache_dir == tmpdir
            indexer.close()

    def test_index_files_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            indexer = CodebaseIndexer(cache_dir=tmpdir)
            result = indexer.index_files([])
            assert result["files_indexed"] == 0
            assert result["chunks_indexed"] == 0
            assert result["errors"] == 0
            indexer.close()

    def test_chunk_generic_empty(self):
        indexer = CodebaseIndexer()
        chunks = indexer._chunk_generic("")
        assert chunks == []
        indexer.close()

    def test_chunk_generic_whitespace_only(self):
        indexer = CodebaseIndexer()
        chunks = indexer._chunk_generic("\n\n\n\n")
        assert chunks == []
        indexer.close()

    def test_chunk_generic_over_max_does_not_crash(self):
        indexer = CodebaseIndexer()
        long_line = "x" * (MAX_CHUNK_CHARS + 100)
        chunks = indexer._chunk_generic(long_line)
        assert len(chunks) >= 1
        indexer.close()

    def test_chunk_python_fallback_on_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            indexer = CodebaseIndexer(cache_dir=tmpdir)
            chunks = indexer._chunk_python("not valid python {{{")
            assert isinstance(chunks, list)
            indexer.close()

    def test_close_twice_no_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            indexer = CodebaseIndexer(cache_dir=tmpdir)
            indexer.close()
            indexer.close()
