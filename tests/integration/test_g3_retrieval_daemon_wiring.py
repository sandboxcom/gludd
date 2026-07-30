"""Integration tests for G3 semantic retrieval (CodebaseIndexer + SemanticSearcher).

Cross-subsystem pattern: constructs classes directly (no daemon needed).
Proves index → search round-trip with diskcache-based storage, scored
and ordered results.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from general_ludd.retrieval.indexer import CodebaseIndexer
from general_ludd.retrieval.searcher import SemanticSearcher
from general_ludd.security.safe_diskcache import open_safe_diskcache


def _write_file(dir_: Path, name: str, content: str) -> Path:
    fp = dir_ / name
    fp.write_text(content, encoding="utf-8")
    return fp


def _make_indexed_project(cache_dir: str, project_dir: Path) -> dict:
    _write_file(
        project_dir,
        "parser.py",
        "def parse_json(text: str) -> dict:\n    import json\n    return json.loads(text)\n\n"
        "def parse_xml(text: str) -> str:\n    return text.strip()\n",
    )
    _write_file(
        project_dir,
        "database.py",
        "import sqlite3\n\ndef connect_db(path: str) -> sqlite3.Connection:\n"
        "    return sqlite3.connect(path)\n\ndef query_db(conn, sql: str):\n"
        "    return conn.execute(sql).fetchall()\n",
    )
    _write_file(
        project_dir,
        "network.py",
        "import requests\n\ndef fetch_url(url: str) -> str:\n"
        "    return requests.get(url).text\n\ndef post_data(url: str, data: dict):\n"
        "    return requests.post(url, json=data).json()\n",
    )

    indexer = CodebaseIndexer(cache_dir=cache_dir)
    paths = sorted(project_dir.glob("*.py"))
    result = indexer.index_files(paths)
    indexer.close()

    return {"paths": paths, "result": result, "cache_dir": cache_dir, "project_dir": project_dir}


@pytest.fixture
def scratch_dir():
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def code_dir(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    return src


@pytest.fixture
def indexed(scratch_dir: str, code_dir: Path) -> dict:
    return _make_indexed_project(scratch_dir, code_dir)


class TestIndexerDaemonWiring:
    def test_indexer_can_be_instantiated(self, scratch_dir):
        indexer = CodebaseIndexer(cache_dir=scratch_dir)
        try:
            assert indexer is not None
            assert indexer.cache_dir == scratch_dir
            assert os.path.isdir(indexer.cache_dir)
        finally:
            indexer.close()

    def test_index_add_works(self, scratch_dir, code_dir):
        _write_file(code_dir, "mod.py", "def add(a, b):\n    return a + b\n")
        indexer = CodebaseIndexer(cache_dir=scratch_dir)
        try:
            result = indexer.index_files([code_dir / "mod.py"])
            assert result["files_indexed"] == 1
            assert result["chunks_indexed"] >= 1
            assert result["errors"] == 0
        finally:
            indexer.close()

    def test_indexer_result_stats_include_chunks(self, scratch_dir, code_dir):
        content = "def greet(name):\n    return f'Hello {name}'\n\ndef main():\n    print('hi')\n"
        _write_file(code_dir, "hello.py", content)
        idx = CodebaseIndexer(cache_dir=scratch_dir)
        try:
            result = idx.index_files([code_dir / "hello.py"])
            assert result["files_indexed"] == 1
            assert result["chunks_indexed"] >= 1
            assert result["errors"] == 0
        finally:
            idx.close()

    def test_diskcache_persistence_across_instances(self, scratch_dir, code_dir):
        _write_file(code_dir, "mod.py", "def f(): pass\n")
        idx1 = CodebaseIndexer(cache_dir=scratch_dir)
        idx1.index_files([code_dir / "mod.py"])
        idx1.close()

        idx2 = CodebaseIndexer(cache_dir=scratch_dir)
        try:
            cache = open_safe_diskcache(scratch_dir)
            keys = list(cache.iterkeys())
            cache.close()
            assert len(keys) > 0
        finally:
            idx2.close()


class TestSearcherDaemonWiring:
    def test_searcher_can_search_indexed_content(self, indexed):
        searcher = SemanticSearcher(cache_dir=indexed["cache_dir"])
        try:
            results = searcher.search("json parse")
            assert len(results) > 0
            top_files = {r["filepath"] for r in results[:3]}
            has_parser = any("parser" in f for f in top_files)
            assert has_parser, f"Expected parser.py in top results, got: {top_files}"
        finally:
            searcher.close()

    def test_search_results_are_scored_and_ordered(self, scratch_dir, code_dir):
        _write_file(code_dir, "db.py", "import sqlite3\n\ndef connect():\n    return sqlite3.connect(':memory:')\n")
        idx = CodebaseIndexer(cache_dir=scratch_dir)
        idx.index_files([code_dir / "db.py"])
        idx.close()

        searcher = SemanticSearcher(cache_dir=scratch_dir)
        try:
            results = searcher.search("sqlite3 database connect")
            assert len(results) > 0

            for r in results:
                assert "score" in r
                assert isinstance(r["score"], float)
                assert r["score"] > 0.0

            scores = [r["score"] for r in results]
            assert scores == sorted(scores, reverse=True)
        finally:
            searcher.close()

    def test_search_top_k_limits(self, indexed):
        searcher = SemanticSearcher(cache_dir=indexed["cache_dir"])
        try:
            results = searcher.search("def", top_k=2)
            assert len(results) <= 2
        finally:
            searcher.close()

    def test_searcher_empty_cache_returns_empty(self, scratch_dir):
        searcher = SemanticSearcher(cache_dir=scratch_dir)
        try:
            results = searcher.search("anything")
            assert results == []
        finally:
            searcher.close()
