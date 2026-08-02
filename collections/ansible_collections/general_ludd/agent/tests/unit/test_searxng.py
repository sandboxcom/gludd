"""Tests for SearXNG Ansible module_utils client."""

from __future__ import annotations

import os
import sys
import types
from typing import Any
from unittest.mock import patch

import pytest

_MODULE_FILE = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "plugins",
        "module_utils",
        "searxng.py",
    )
)


def _import_mod():
    mod = types.ModuleType("searxng_module_utils")
    mod.__file__ = _MODULE_FILE
    sys.modules["searxng_module_utils"] = mod
    try:
        with open(_MODULE_FILE) as f:
            code = compile(f.read(), _MODULE_FILE, "exec")
        exec(code, mod.__dict__)
    finally:
        sys.modules.pop("searxng_module_utils", None)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_raw_result() -> dict:
    return {
        "url": "https://example.com/article",
        "title": "Example Article",
        "content": "This is example content for testing.",
        "engine": "google",
        "score": 0.95,
        "category": "science",
        "img_src": "/thumb.png",
        "thumbnail_src": "/thumb_small.png",
        "publishedDate": "2024-01-15",
    }


@pytest.fixture
def sample_search_response() -> dict:
    return {
        "query": "test query",
        "results": [
            {
                "url": "https://example.com/article",
                "title": "Example Article",
                "content": "This is example content.",
                "engine": "arxiv",
                "score": 0.95,
                "category": "science",
            },
            {
                "url": "https://example.com/article",
                "title": "Duplicate (lower score)",
                "content": "Duplicate content.",
                "engine": "pubmed",
                "score": 0.60,
                "category": "science",
            },
            {
                "url": "https://other.org/paper",
                "title": "Another Paper",
                "content": "Different content.",
                "engine": "semantic_scholar",
                "score": 0.80,
                "category": "science",
            },
        ],
        "number_of_results": 42,
        "suggestions": ["test query example"],
        "answers": ["direct answer"],
        "unresponsive_engines": [["google", "timeout"]],
    }


@pytest.fixture
def client() -> Any:
    mod = _import_mod()
    return mod.SearXNGClient(base_url="http://searx:8080", retries=0)


# ---------------------------------------------------------------------------
# SearxResult
# ---------------------------------------------------------------------------


class TestSearxResult:
    def test_from_raw_full(self, sample_raw_result):
        mod = _import_mod()
        r = mod.SearxResult.from_raw(sample_raw_result)
        assert r.url == "https://example.com/article"
        assert r.title == "Example Article"
        assert r.snippet == "This is example content for testing."
        assert r.engine == "google"
        assert r.score == 0.95
        assert r.category == "science"
        assert r.img_src == "/thumb.png"
        assert r.thumbnail_src == "/thumb_small.png"
        assert r.published_date == "2024-01-15"

    def test_from_raw_minimal(self):
        mod = _import_mod()
        r = mod.SearxResult.from_raw({"url": "https://x.com"})
        assert r.url == "https://x.com"
        assert r.title == ""
        assert r.snippet == ""
        assert r.engine == ""
        assert r.score == 0.0
        assert r.category == ""
        assert r.img_src == ""
        assert r.thumbnail_src == ""
        assert r.published_date is None

    def test_from_raw_score_is_float(self):
        mod = _import_mod()
        r = mod.SearxResult.from_raw({"url": "https://x.com", "score": "3.14"})
        assert isinstance(r.score, float)
        assert r.score == 3.14

    def test_published_date_prefers_publishedDate(self):
        mod = _import_mod()
        r = mod.SearxResult.from_raw({"url": "https://x.com", "publishedDate": "first", "published_date": "second"})
        assert r.published_date == "first"

    def test_published_date_fallback(self):
        mod = _import_mod()
        r = mod.SearxResult.from_raw({"url": "https://x.com", "published_date": "2023-06-01"})
        assert r.published_date == "2023-06-01"


# ---------------------------------------------------------------------------
# SearxResponse properties
# ---------------------------------------------------------------------------


class TestSearxResponse:
    def test_creation_defaults(self):
        mod = _import_mod()
        resp = mod.SearxResponse(query="q", results=[])
        assert resp.query == "q"
        assert resp.results == []
        assert resp.number_of_results == 0
        assert resp.suggestions == []
        assert resp.answers == []
        assert resp.unresponsive_engines == []
        assert resp.urls == []
        assert resp.titles == []
        assert resp.snippets == []
        assert resp.engines == []

    def test_creation_full(self):
        mod = _import_mod()
        r1 = mod.SearxResult.from_raw({"url": "https://a.com", "title": "A", "content": "cA", "engine": "g"})
        r2 = mod.SearxResult.from_raw({"url": "https://b.com", "title": "B", "content": "cB", "engine": "g"})
        resp = mod.SearxResponse(
            query="q",
            results=[r1, r2],
            number_of_results=2,
            suggestions=["s1"],
            answers=["a1"],
            unresponsive_engines=[["b", "error"]],
        )
        assert resp.urls == ["https://a.com", "https://b.com"]
        assert resp.titles == ["A", "B"]
        assert resp.snippets == ["cA", "cB"]
        assert resp.engines == ["g"]

    def test_engines_deduplicates(self):
        mod = _import_mod()
        r1 = mod.SearxResult.from_raw({"url": "https://a.com", "engine": "arxiv"})
        r2 = mod.SearxResult.from_raw({"url": "https://b.com", "engine": "arxiv"})
        r3 = mod.SearxResult.from_raw({"url": "https://c.com", "engine": "google"})
        resp = mod.SearxResponse(query="q", results=[r1, r2, r3])
        assert sorted(resp.engines) == ["arxiv", "google"]

    def test_engines_skips_empty(self):
        mod = _import_mod()
        r = mod.SearxResult.from_raw({"url": "https://a.com"})
        resp = mod.SearxResponse(query="q", results=[r])
        assert resp.engines == []


# ---------------------------------------------------------------------------
# SearXNGClient initialization
# ---------------------------------------------------------------------------


class TestSearXNGClientInit:
    def test_default_base_url(self):
        mod = _import_mod()
        c = mod.SearXNGClient()
        assert c.base_url == "http://localhost:8080"

    def test_custom_base_url(self):
        mod = _import_mod()
        c = mod.SearXNGClient(base_url="http://searx:9090")
        assert c.base_url == "http://searx:9090"

    def test_base_url_strips_trailing_slash(self):
        mod = _import_mod()
        c = mod.SearXNGClient(base_url="http://searx:9090/")
        assert c.base_url == "http://searx:9090"

    def test_default_timeout(self):
        mod = _import_mod()
        c = mod.SearXNGClient()
        assert c.timeout == 30

    def test_custom_timeout(self):
        mod = _import_mod()
        c = mod.SearXNGClient(timeout=10)
        assert c.timeout == 10

    def test_default_retries(self):
        mod = _import_mod()
        c = mod.SearXNGClient()
        assert c.retries == 2

    def test_no_indices_at_init(self):
        mod = _import_mod()
        c = mod.SearXNGClient()
        assert c._indices == {}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_basic(self, client, sample_search_response):
        with patch.object(client._connector, "_get", return_value=(200, sample_search_response)):
            resp = client.search("test query")

        assert resp.query == "test query"
        assert len(resp.results) == 2  # 3 raw, 1 deduped by URL
        assert resp.number_of_results == 42
        assert resp.suggestions == ["test query example"]
        assert resp.answers == ["direct answer"]

    def test_search_max_results_caps(self, client, sample_search_response):
        with patch.object(client._connector, "_get", return_value=(200, sample_search_response)):
            resp = client.search("q", max_results=1)

        assert len(resp.results) == 1

    def test_search_invalid_category_raises(self, client):
        with pytest.raises(ValueError, match="Unknown category"):
            client.search("q", categories=["bogus_category"])

    def test_search_unknown_category_message(self, client):
        with pytest.raises(ValueError, match="bogus"):
            client.search("q", categories=["bogus"])

    def test_search_with_engines(self, client, sample_search_response):
        with patch.object(client._connector, "_get", return_value=(200, sample_search_response)) as mock_get:
            client.search("q", engines=["google", "arxiv"])

        params = mock_get.call_args[1]["params"]
        assert params["engines"] == "google,arxiv"

    def test_search_params_in_url(self, client, sample_search_response):
        with patch.object(client._connector, "_get", return_value=(200, sample_search_response)) as mock_get:
            client.search("hello world", language="fr", safe_search=2, page=3)

        params = mock_get.call_args[1]["params"]
        assert params["q"] == "hello world"
        assert params["language"] == "fr"
        assert params["safesearch"] == "2"
        assert params["pageno"] == "3"
        assert params["categories"] == "general"

    def test_search_empty_results(self, client):
        empty = {"query": "q", "results": [], "number_of_results": 0}
        with patch.object(client._connector, "_get", return_value=(200, empty)):
            resp = client.search("q")

        assert resp.results == []
        assert resp.number_of_results == 0


# ---------------------------------------------------------------------------
# Convenience methods
# ---------------------------------------------------------------------------


class TestWebSearch:
    def test_web_search(self, client, sample_search_response):
        with patch.object(client._connector, "_get", return_value=(200, sample_search_response)) as mock_get:
            client.web_search("ansible")

        params = mock_get.call_args[1]["params"]
        assert params["categories"] == "general"

    def test_web_search_max_results(self, client, sample_search_response):
        with patch.object(client._connector, "_get", return_value=(200, sample_search_response)):
            resp = client.web_search("q", max_results=5)

        assert len(resp.results) <= 5


class TestNewsSearch:
    def test_news_search(self, client, sample_search_response):
        with patch.object(client._connector, "_get", return_value=(200, sample_search_response)) as mock_get:
            client.news_search("breaking news")

        params = mock_get.call_args[1]["params"]
        assert params["categories"] == "news"


class TestImageSearch:
    def test_image_search(self, client, sample_search_response):
        with patch.object(client._connector, "_get", return_value=(200, sample_search_response)) as mock_get:
            client.image_search("cats")

        params = mock_get.call_args[1]["params"]
        assert params["categories"] == "images"


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------


class TestCreateIndex:
    def test_create_index(self, client):
        result = client.create_index("my_index", ["google", "arxiv"])
        assert result["name"] == "my_index"
        assert result["engines"] == ["google", "arxiv"]
        assert result["created"] is True

    def test_create_index_empty_name_raises(self, client):
        with pytest.raises(ValueError, match="non-empty"):
            client.create_index("", ["google"])

    def test_create_index_empty_engines_raises(self, client):
        with pytest.raises(ValueError, match="least one engine"):
            client.create_index("idx", [])

    def test_create_index_stores_internally(self, client):
        client.create_index("idx", ["google"])
        assert client._indices["idx"] == ["google"]

    def test_create_index_overwrites(self, client):
        client.create_index("idx", ["google"])
        client.create_index("idx", ["arxiv", "pubmed"])
        assert client._indices["idx"] == ["arxiv", "pubmed"]


class TestIndexStatus:
    def test_index_status_not_found(self, client):
        result = client.index_status("missing")
        assert result["exists"] is False
        assert "not found" in result["error"]

    def test_index_status_healthy(self, client, sample_search_response):
        client.create_index("idx", ["arxiv", "google"])

        with patch.object(client._connector, "_get", return_value=(200, sample_search_response)):
            result = client.index_status("idx")

        assert result["exists"] is True
        assert result["name"] == "idx"
        assert result["engines_total"] == 2
        assert result["healthy"] is True

    def test_index_status_with_unresponsive(self, client):
        client.create_index("idx", ["arxiv", "google"])
        response = {
            "query": "health check",
            "results": [],
            "unresponsive_engines": [["google", "timeout"]],
        }

        with patch.object(client._connector, "_get", return_value=(200, response)):
            result = client.index_status("idx")

        assert result["engines_responsive"] == 1
        assert result["engines_unresponsive"] == 1
        assert result["responsive"] == ["arxiv"]
        assert result["unresponsive"] == ["google"]
        assert result["healthy"] is True  # at least one engine responsive

    def test_index_status_network_error(self, client):
        client.create_index("idx", ["google"])

        with patch.object(client._connector, "_get", side_effect=Exception("refused")):
            result = client.index_status("idx")

        assert result["exists"] is True
        assert result["healthy"] is False
        assert "refused" in result["error"]


class TestSearchWithIndex:
    def test_search_with_index(self, client, sample_search_response):
        client.create_index("my_idx", ["google", "arxiv"])

        with patch.object(client._connector, "_get", return_value=(200, sample_search_response)) as mock_get:
            client.search_with_index("query", "my_idx")

        params = mock_get.call_args[1]["params"]
        assert params["engines"] == "google,arxiv"

    def test_search_with_index_not_found(self, client):
        with pytest.raises(ValueError, match="not found"):
            client.search_with_index("q", "nonexistent")

    def test_search_with_index_max_results(self, client, sample_search_response):
        client.create_index("idx", ["google"])

        with patch.object(client._connector, "_get", return_value=(200, sample_search_response)):
            resp = client.search_with_index("q", "idx", max_results=1)

        assert len(resp.results) == 1


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_ok(self, client):
        with patch.object(client._connector, "health", return_value={"ok": True}):
            result = client.health()

        assert result["ok"] is True
        assert "reachable" in result["detail"]
        assert result["base_url"] == "http://searx:8080"

    def test_health_unreachable(self, client):
        with patch.object(client._connector, "health", side_effect=Exception("refused")):
            result = client.health()

        assert result["ok"] is False
        assert "refused" in result["detail"]

    def test_health_http_error(self, client):
        with patch.object(client._connector, "health", side_effect=Exception("timeout")):
            result = client.health()

        assert result["ok"] is False


# ---------------------------------------------------------------------------
# Error handling — SearXNGClient delegates transport to SearXConnector
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_non_200_returns_empty_response(self, client):
        with patch.object(client._connector, "_get", return_value=(500, None)):
            resp = client.search("q")

        assert resp.results == []
        assert resp.query == "q"

    def test_non_dict_body_returns_empty_response(self, client):
        with patch.object(client._connector, "_get", return_value=(200, "not a dict")):
            resp = client.search("q")

        assert resp.results == []
        assert resp.query == "q"

    def test_get_exception_propagates(self, client):
        with (
            patch.object(client._connector, "_get", side_effect=Exception("boom")),
            pytest.raises(Exception, match="boom"),
        ):
            client.search("q")

    def test_non_2xx_client_error_returns_empty(self, client):
        with patch.object(client._connector, "_get", return_value=(404, None)):
            resp = client.search("q")

        assert resp.results == []
        assert resp.query == "q"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_removes_duplicate_urls(self):
        mod = _import_mod()
        r1 = mod.SearxResult(url="https://a.com", score=0.9)
        r2 = mod.SearxResult(url="https://a.com", score=0.5)
        r3 = mod.SearxResult(url="https://b.com", score=0.7)
        deduped = mod._deduplicate([r1, r2, r3])
        assert len(deduped) == 2
        a_vals = [r for r in deduped if r.url == "https://a.com"]
        assert len(a_vals) == 1
        assert a_vals[0].score == 0.9

    def test_sorted_by_score_desc(self):
        mod = _import_mod()
        r1 = mod.SearxResult(url="https://a.com", score=0.5)
        r2 = mod.SearxResult(url="https://b.com", score=0.9)
        r3 = mod.SearxResult(url="https://c.com", score=0.7)
        deduped = mod._deduplicate([r1, r2, r3])
        assert [r.score for r in deduped] == [0.9, 0.7, 0.5]

    def test_skips_empty_urls(self):
        mod = _import_mod()
        r1 = mod.SearxResult(url="", score=0.9)
        r2 = mod.SearxResult(url="https://a.com", score=0.7)
        deduped = mod._deduplicate([r1, r2])
        assert len(deduped) == 1
        assert deduped[0].url == "https://a.com"

    def test_empty_list(self):
        mod = _import_mod()
        assert mod._deduplicate([]) == []


# ---------------------------------------------------------------------------
# Module-level parsers
# ---------------------------------------------------------------------------


class TestParseUrls:
    def test_parse_urls(self):
        mod = _import_mod()
        raw = [
            {"url": "https://a.com", "title": "A"},
            {"url": "https://b.com", "title": "B"},
            {"title": "No URL"},
        ]
        urls = mod.parse_urls(raw)
        assert urls == ["https://a.com", "https://b.com"]

    def test_parse_urls_empty(self):
        mod = _import_mod()
        assert mod.parse_urls([]) == []


class TestParseTitles:
    def test_parse_titles(self):
        mod = _import_mod()
        raw = [
            {"title": "Title A"},
            {"title": "Title B"},
            {},
        ]
        titles = mod.parse_titles(raw)
        assert titles == ["Title A", "Title B"]

    def test_parse_titles_empty(self):
        mod = _import_mod()
        assert mod.parse_titles([]) == []


class TestParseSnippets:
    def test_parse_snippets(self):
        mod = _import_mod()
        raw = [
            {"content": "Snippet A"},
            {"content": "Snippet B"},
            {},
        ]
        snippets = mod.parse_snippets(raw)
        assert snippets == ["Snippet A", "Snippet B"]

    def test_parse_snippets_empty(self):
        mod = _import_mod()
        assert mod.parse_snippets([]) == []


class TestParseEngines:
    def test_parse_engines(self):
        mod = _import_mod()
        raw = [
            {"engine": "google"},
            {"engine": "arxiv"},
            {"engine": "google"},
        ]
        engines = mod.parse_engines(raw)
        assert engines == ["arxiv", "google"]

    def test_parse_engines_skips_missing(self):
        mod = _import_mod()
        raw = [{"title": "A"}, {"engine": "google"}]
        engines = mod.parse_engines(raw)
        assert engines == ["google"]

    def test_parse_engines_empty(self):
        mod = _import_mod()
        assert mod.parse_engines([]) == []
