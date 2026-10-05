"""Structural tests for retrieval/searcher.py module shape."""

from __future__ import annotations

from tempfile import TemporaryDirectory

from general_ludd.retrieval.searcher import SemanticSearcher


class TestSemanticSearcherStructural:
    def test_init_with_nonexistent_dir(self):
        searcher = SemanticSearcher(cache_dir="/nonexistent/path")
        assert searcher._cache is None

    def test_init_with_existing_dir(self):
        with TemporaryDirectory() as tmpdir:
            searcher = SemanticSearcher(cache_dir=tmpdir)
            assert searcher._cache is not None
            searcher.close()

    def test_search_with_none_cache(self):
        searcher = SemanticSearcher(cache_dir="/nonexistent")
        results = searcher.search("query")
        assert results == []

    def test_search_with_empty_cache(self):
        with TemporaryDirectory() as tmpdir:
            searcher = SemanticSearcher(cache_dir=tmpdir)
            results = searcher.search("query")
            assert results == []
            searcher.close()

    def test_search_empty_query_with_cache(self):
        with TemporaryDirectory() as tmpdir:
            searcher = SemanticSearcher(cache_dir=tmpdir)
            results = searcher.search("")
            assert results == []
            searcher.close()

    def test_close_with_none_cache(self):
        searcher = SemanticSearcher(cache_dir="/nonexistent")
        searcher.close()

    def test_close_twice(self):
        with TemporaryDirectory() as tmpdir:
            searcher = SemanticSearcher(cache_dir=tmpdir)
            searcher.close()
            searcher.close()

    def test_context_manager_closes_cache(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with SemanticSearcher(cache_dir=tmpdir) as searcher:
                assert searcher._cache is not None

            assert searcher._cache is None
