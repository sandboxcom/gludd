"""Tests for G3 semantic codebase retrieval."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestCodebaseIndexer:
    def test_index_files_not_implemented(self):
        from general_ludd.retrieval.indexer import CodebaseIndexer

        indexer = CodebaseIndexer()
        with pytest.raises(NotImplementedError):
            indexer.index_files([Path("test.py")])

    def test_indexer_instantiation(self):
        from general_ludd.retrieval.indexer import CodebaseIndexer

        indexer = CodebaseIndexer()
        assert indexer is not None


class TestSemanticSearcher:
    def test_search_not_implemented(self):
        from general_ludd.retrieval.searcher import SemanticSearcher

        searcher = SemanticSearcher()
        with pytest.raises(NotImplementedError):
            searcher.search("find the main function")

    def test_searcher_instantiation(self):
        from general_ludd.retrieval.searcher import SemanticSearcher

        searcher = SemanticSearcher()
        assert searcher is not None
