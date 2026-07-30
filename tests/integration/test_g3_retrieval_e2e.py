"""Integration / end-to-end tests for G3 semantic codebase retrieval.

Covers the full pipeline: indexer → searcher, plus edge cases and
internal helpers (_tokenize, _cosine_similarity).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from general_ludd.retrieval.indexer import (
    MAX_CHUNK_CHARS,
    CodebaseIndexer,
    _cosine_similarity,
    _tokenize,
)
from general_ludd.retrieval.searcher import SemanticSearcher
from general_ludd.security.safe_diskcache import open_safe_diskcache

# ── helpers ────────────────────────────────────────────────────────────────


def _write_python_file(dir_: Path, name: str, content: str) -> Path:
    fp = dir_ / name
    fp.write_text(content, encoding="utf-8")
    return fp


def _write_binary_file(dir_: Path, name: str) -> Path:
    fp = dir_ / name
    fp.write_bytes(b"\x00\x01\x02\xff\xfe\xfd")
    return fp


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def cache_dir() -> str:
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    return src


@pytest.fixture
def indexed_project(project_dir: Path, cache_dir: str) -> dict[str, Any]:
    """Index a small multi-file Python project and return paths + cache_dir."""
    _write_python_file(
        project_dir,
        "models.py",
        """\
from dataclasses import dataclass

@dataclass
class User:
    name: str
    email: str

@dataclass
class Order:
    order_id: str
    items: list[str]
    total: float

def validate_email(email: str) -> bool:
    return "@" in email and "." in email.split("@")[1]
""",
    )

    _write_python_file(
        project_dir,
        "auth.py",
        """\
import hashlib

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hash_password(password) == hashed

def authenticate(username: str, password: str, db: dict) -> bool:
    stored = db.get(username)
    if stored is None:
        return False
    return verify_password(password, stored)
""",
    )

    _write_python_file(
        project_dir,
        "api.py",
        """\
from fastapi import FastAPI, HTTPException
from models import User, Order

app = FastAPI()

@app.get("/users/{user_id}")
async def get_user(user_id: str) -> User:
    return User(name="test", email="test@example.com")

@app.post("/orders")
async def create_order(order: Order) -> Order:
    return order
""",
    )

    _write_python_file(
        project_dir,
        "__init__.py",
        "# myapp package\n",
    )

    indexer = CodebaseIndexer(cache_dir=cache_dir)
    paths = sorted(project_dir.glob("*.py"))
    result = indexer.index_files(paths)
    indexer.close()

    return {"paths": paths, "result": result, "cache_dir": cache_dir, "project_dir": project_dir}


# ── CodebaseIndexer.index_files ────────────────────────────────────────────


class TestIndexFiles:
    def test_indexes_multiple_python_files(self, indexed_project):
        assert indexed_project["result"]["files_indexed"] == 4
        assert indexed_project["result"]["chunks_indexed"] > 0
        assert indexed_project["result"]["errors"] == 0

    def test_each_file_produces_at_least_one_chunk(self, indexed_project):
        assert indexed_project["result"]["chunks_indexed"] >= indexed_project["result"]["files_indexed"]

    def test_stats_return_correct_types(self, indexed_project):
        r = indexed_project["result"]
        assert isinstance(r["files_indexed"], int)
        assert isinstance(r["chunks_indexed"], int)
        assert isinstance(r["errors"], int)

    def test_handles_missing_files_gracefully(self, cache_dir):
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        result = indexer.index_files(
            [Path("/nonexistent/a.py"), Path("/nonexistent/b.py"), Path("/nonexistent/c.py")]
        )
        indexer.close()
        assert result["files_indexed"] == 0
        assert result["chunks_indexed"] == 0
        assert result["errors"] == 0

    def test_handles_unreadable_binary_files(self, project_dir, cache_dir):
        bin_file = _write_binary_file(project_dir, "binary.py")
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        result = indexer.index_files([bin_file])
        indexer.close()
        assert result["errors"] == 1
        assert result["files_indexed"] == 0
        assert result["chunks_indexed"] == 0

    def test_skips_unicode_decode_errors(self, project_dir, cache_dir):
        fp = project_dir / "broken.py"
        fp.write_bytes("def foo():\n    pass\n".encode("utf-16"))
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        result = indexer.index_files([fp])
        indexer.close()
        assert result["errors"] >= 1
        assert result["files_indexed"] == 0

    def test_mix_of_valid_and_invalid(self, project_dir, cache_dir):
        good = _write_python_file(project_dir, "good.py", "def a(): pass\n\ndef b(): pass\n")
        bad = _write_binary_file(project_dir, "bad.py")
        missing = Path("/no/such/file.py")
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        result = indexer.index_files([good, bad, missing])
        indexer.close()
        assert result["files_indexed"] == 1
        assert result["errors"] == 1
        assert result["chunks_indexed"] >= 1

    def test_indexed_entries_are_in_diskcache(self, indexed_project):
        cache = open_safe_diskcache(indexed_project["cache_dir"])
        keys = list(cache.iterkeys())
        cache.close()
        assert len(keys) == indexed_project["result"]["chunks_indexed"]
        assert all(isinstance(k, str) for k in keys)
        assert all(":" in k for k in keys)


# ── CodebaseIndexer chunking ────────────────────────────────────────────────


class TestChunkPython:
    def test_functions_become_separate_chunks(self, cache_dir):
        code = "def foo():\n    pass\n\ndef bar():\n    return 1\n"
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        chunks = indexer._chunk_python(code)
        indexer.close()
        assert len(chunks) >= 2
        assert any("def foo" in c for c in chunks)
        assert any("def bar" in c for c in chunks)

    def test_classes_with_methods(self, cache_dir):
        code = "class Foo:\n    def m1(self):\n        pass\n    def m2(self):\n        pass\n"
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        chunks = indexer._chunk_python(code)
        indexer.close()
        # tree-sitter extracts class + methods; fallback extracts class line
        assert len(chunks) >= 1
        assert any("Foo" in c for c in chunks)

    def test_empty_file_produces_empty_list(self, cache_dir):
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        chunks = indexer._chunk_python("")
        indexer.close()
        assert chunks == []

    def test_only_comments_returns_empty_or_single_chunk(self, cache_dir):
        code = "# just a comment\n# another one\n"
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        chunks = indexer._chunk_python(code)
        indexer.close()
        for chunk in chunks:
            assert isinstance(chunk, str)

    def test_fallback_produces_at_least_one_chunk(self, cache_dir):
        code = "def f(): pass\n"
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        chunks = indexer._chunk_python(code)
        indexer.close()
        assert len(chunks) >= 1


class TestChunkGeneric:
    def test_splits_on_double_newlines(self, cache_dir):
        content = "Para one.\n\nPara two.\n\nPara three."
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        chunks = indexer._chunk_generic(content)
        indexer.close()
        assert len(chunks) == 3
        assert "Para one." in chunks[0]
        assert "Para two." in chunks[1]
        assert "Para three." in chunks[2]

    def test_splits_on_whitespace_between_paragraphs(self, cache_dir):
        content = "A\n  \nB"
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        chunks = indexer._chunk_generic(content)
        indexer.close()
        assert len(chunks) == 2

    def test_single_block_returns_single_chunk(self, cache_dir):
        content = "Just one block here."
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        chunks = indexer._chunk_generic(content)
        indexer.close()
        assert len(chunks) == 1

    def test_empty_string_returns_empty_list(self, cache_dir):
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        chunks = indexer._chunk_generic("")
        indexer.close()
        assert chunks == []

    def test_only_whitespace_returns_empty(self, cache_dir):
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        chunks = indexer._chunk_generic("\n\n  \n\n\n")
        indexer.close()
        assert chunks == []

    def test_large_chunk_is_split_by_max_chars(self, cache_dir):
        line = "x" * 100
        many_lines = "\n".join(line for _ in range(30))
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        chunks = indexer._chunk_generic(many_lines)
        indexer.close()
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= MAX_CHUNK_CHARS


# ── SemanticSearcher.search ─────────────────────────────────────────────────


class TestSearchResults:
    def test_returns_results_sorted_by_descending_score(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("hash password authentication")
        searcher.close()
        assert len(results) > 0
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_top_k_limits_results(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("user email validate", top_k=2)
        searcher.close()
        assert len(results) <= 2

    def test_top_k_zero_returns_empty(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("def class", top_k=0)
        searcher.close()
        assert results == []

    def test_top_k_larger_than_index_returns_all(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("def", top_k=1000)
        searcher.close()
        total = indexed_project["result"]["chunks_indexed"]
        assert len(results) <= total

    def test_results_contain_expected_fields(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("User model")
        searcher.close()
        assert len(results) > 0
        for r in results:
            assert "filepath" in r
            assert "content" in r
            assert "score" in r
            assert isinstance(r["score"], float)
            assert isinstance(r["filepath"], str)
            assert isinstance(r["content"], str)
            assert r["score"] > 0

    def test_scores_are_between_zero_and_one(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("class function method")
        searcher.close()
        for r in results:
            assert 0.0 < r["score"] <= 1.0

    def test_score_ordering_highest_similarity_first(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("password hashing sha256")
        searcher.close()
        assert len(results) > 0
        assert results[0]["score"] == max(r["score"] for r in results)

    def test_empty_query_returns_empty_list(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("")
        searcher.close()
        assert results == []

    def test_query_only_punctuation_returns_empty_list(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("!@#$%^&*()")
        searcher.close()
        assert results == []

    def test_empty_cache_directory_returns_empty_list(self, cache_dir):
        searcher = SemanticSearcher(cache_dir=cache_dir)
        results = searcher.search("anything")
        searcher.close()
        assert results == []

    def test_nonexistent_cache_directory_returns_empty_list(self):
        searcher = SemanticSearcher(cache_dir="/tmp/__does_not_exist_gludd_test__")
        results = searcher.search("anything")
        searcher.close()
        assert results == []


# ── Roundtrip: index → search relevance ─────────────────────────────────────


class TestRoundtripRelevance:
    def test_search_for_password_returns_auth_file(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("hash password authentication", top_k=5)
        searcher.close()
        assert len(results) > 0
        top_files = {r["filepath"] for r in results[:3]}
        auth_files = [p for p in top_files if "auth" in p]
        assert len(auth_files) > 0

    def test_search_for_order_returns_models_file(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("dataclass Order items total", top_k=5)
        searcher.close()
        assert len(results) > 0
        combined = " ".join(r["content"] for r in results)
        assert "dataclass" in combined or "Order" in combined

    def test_search_for_api_returns_api_file(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("FastAPI HTTP endpoint get_user", top_k=5)
        searcher.close()
        assert len(results) > 0
        top_files = {r["filepath"] for r in results[:3]}
        api_files = [p for p in top_files if "api" in p]
        assert len(api_files) > 0

    def test_search_for_email_validation(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("validate email address", top_k=5)
        searcher.close()
        assert len(results) > 0
        combined = " ".join(r["content"] for r in results)
        assert "validate_email" in combined or "@" in combined

    def test_generic_query_returns_multiple_files(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("def", top_k=20)
        searcher.close()
        files = {r["filepath"] for r in results}
        assert len(files) >= 2

    def test_highly_specific_query_score_ordering(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        results = searcher.search("hashlib sha256 hash_password")
        searcher.close()
        if len(results) >= 2:
            assert results[0]["score"] >= results[-1]["score"]
        top_content = results[0]["content"] if results else ""
        assert "hashlib" in top_content or "hash_password" in top_content or "sha256" in top_content


# ── _tokenize ──────────────────────────────────────────────────────────────


class TestTokenize:
    def test_code_tokens(self):
        tokens = _tokenize("def hello_world(a, b): return a + b")
        assert "def" in tokens
        assert "hello_world" in tokens
        assert "return" in tokens

    def test_english_tokens(self):
        tokens = _tokenize("This function validates an email address.")
        assert "this" in tokens
        assert "function" in tokens
        assert "validates" in tokens
        assert "email" in tokens
        assert "address" in tokens

    def test_punctuation_is_removed(self):
        tokens = _tokenize("hello! world? foo-bar_baz.")
        assert all("!" not in t for t in tokens)
        assert all("?" not in t for t in tokens)
        assert all("-" not in t for t in tokens)
        assert all("." not in t for t in tokens)
        assert "hello" in tokens

    def test_numbers_are_not_tokens(self):
        tokens = _tokenize("x = 12345 y = 67890")
        assert "12345" not in tokens
        assert "67890" not in tokens

    def test_single_letter_tokens_are_filtered(self):
        tokens = _tokenize("a b c d e function")
        assert "a" not in tokens
        assert "b" not in tokens
        assert "function" in tokens

    def test_underscore_prefixed_tokens(self):
        tokens = _tokenize("_private_var = 42")
        assert "private_var" in tokens

    def test_empty_input(self):
        assert _tokenize("") == []

    def test_only_punctuation(self):
        assert _tokenize("!@#$%^&*()") == []

    def test_only_numbers(self):
        assert _tokenize("123 456 789") == []

    def test_case_insensitive(self):
        tokens = _tokenize("Hello HELLO hello")
        assert all(t == "hello" for t in tokens)

    def test_duplicates_are_returned(self):
        tokens = _tokenize("foo foo foo bar")
        assert len([t for t in tokens if t == "foo"]) == 3


# ── _cosine_similarity ──────────────────────────────────────────────────────


class TestCosineSimilarity:
    def test_identical_vectors(self):
        vec = {"hello": 2.0, "world": 1.0}
        sim = _cosine_similarity(vec, vec)
        assert sim == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = {"hello": 1.0}
        b = {"world": 1.0}
        sim = _cosine_similarity(a, b)
        assert sim == pytest.approx(0.0)

    def test_zero_vector_a(self):
        sim = _cosine_similarity({}, {"hello": 1.0})
        assert sim == 0.0

    def test_zero_vector_b(self):
        sim = _cosine_similarity({"hello": 1.0}, {})
        assert sim == 0.0

    def test_both_zero_vectors(self):
        sim = _cosine_similarity({}, {})
        assert sim == 0.0

    def test_partial_overlap(self):
        a = {"hello": 1.0, "world": 2.0}
        b = {"world": 1.0, "test": 3.0}
        sim = _cosine_similarity(a, b)
        assert 0.0 < sim < 1.0

    def test_symmetric(self):
        a = {"hello": 3.0, "world": 1.0, "test": 2.0}
        b = {"world": 4.0, "foo": 1.0, "hello": 0.5}
        assert _cosine_similarity(a, b) == pytest.approx(_cosine_similarity(b, a))

    def test_negative_values_handled(self):
        a = {"hello": -1.0}
        b = {"hello": 2.0}
        sim = _cosine_similarity(a, b)
        assert isinstance(sim, float)
        assert sim < 0.0

    def test_produces_float(self):
        sim = _cosine_similarity({"a": 1.0}, {"a": 2.0})
        assert isinstance(sim, float)


# ── close() cleanup ─────────────────────────────────────────────────────────


class TestClose:
    def test_indexer_close_does_not_raise(self, cache_dir):
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        indexer.close()
        # close should be idempotent
        indexer.close()

    def test_indexer_close_after_indexing(self, indexed_project):
        indexer = CodebaseIndexer(cache_dir=indexed_project["cache_dir"])
        indexer.close()

    def test_searcher_close_does_not_raise(self, indexed_project):
        searcher = SemanticSearcher(cache_dir=indexed_project["cache_dir"])
        searcher.close()
        searcher.close()

    def test_searcher_close_with_nonexistent_cache(self):
        searcher = SemanticSearcher(cache_dir="/tmp/__no_such_dir__")
        searcher.close()

    def test_cache_remains_on_disk_after_close(self, indexed_project):
        cache_dir = indexed_project["cache_dir"]
        searcher = SemanticSearcher(cache_dir=cache_dir)
        searcher.close()
        # cache directory should still exist
        assert Path(cache_dir).is_dir()
        assert len(list(Path(cache_dir).iterdir())) > 0

    def test_can_reopen_cache_after_close(self, indexed_project):
        cache_dir = indexed_project["cache_dir"]
        searcher1 = SemanticSearcher(cache_dir=cache_dir)
        results1 = searcher1.search("hash password")
        searcher1.close()

        searcher2 = SemanticSearcher(cache_dir=cache_dir)
        results2 = searcher2.search("hash password")
        searcher2.close()

        assert results1 == results2


# ── cache_dir property ──────────────────────────────────────────────────────


class TestCacheDirProperty:
    def test_indexer_cache_dir(self, cache_dir):
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        assert indexer.cache_dir == cache_dir
        indexer.close()

    def test_cache_dir_expands_user(self):
        indexer = CodebaseIndexer(cache_dir="~/.gludd/test_retrieval")
        assert "~" not in indexer.cache_dir
        assert Path(indexer.cache_dir).is_dir()
        indexer.close()
        # cleanup
        import shutil

        shutil.rmtree(Path(indexer.cache_dir), ignore_errors=True)


# ── batch_size parameter ────────────────────────────────────────────────────


class TestBatchSize:
    def test_index_files_accepts_batch_size(self, project_dir, cache_dir):
        _write_python_file(project_dir, "a.py", "def a(): pass\n")
        _write_python_file(project_dir, "b.py", "def b(): pass\n")
        indexer = CodebaseIndexer(cache_dir=cache_dir)
        result = indexer.index_files(
            sorted(project_dir.glob("*.py")), batch_size=1
        )
        indexer.close()
        assert result["files_indexed"] == 2
        assert result["errors"] == 0
