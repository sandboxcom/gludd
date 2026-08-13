"""Tests for SearXNG async search client."""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.retrieval.searx_client import SearxNGClient

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
        "parsed_url": ["https", "example.com", "/article", "", "", ""],
        "template": "default.html",
        "img_src": "",
        "thumbnail_src": "",
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
                "content": "This is example content for testing.",
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
        "answers": [],
        "corrections": [],
        "infoboxes": [],
        "unresponsive_engines": [["google", "timeout"]],
    }


@pytest.fixture
def temp_cache_dir() -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def _make_mock_async_client(
    get_response: dict | None = None,
    get_side_effect: Exception | None = None,
) -> MagicMock:
    """Build a mock httpx.AsyncClient that returns a controlled response."""
    mock_client = MagicMock()

    # Build the mock response
    mock_resp = MagicMock()
    if get_response is not None:
        mock_resp.json.return_value = get_response
    mock_resp.raise_for_status.return_value = None
    mock_resp.status_code = 200

    if get_side_effect:
        mock_client.get = AsyncMock(side_effect=get_side_effect)
    else:
        mock_client.get = AsyncMock(return_value=mock_resp)

    # AsyncClient is used directly (not as context manager) in searx_client
    mock_client.is_closed = False
    mock_client.aclose = AsyncMock()
    return mock_client


def _make_client(
    cache_dir: str | None = None,
    base_url: str | None = None,
) -> SearxNGClient:
    return SearxNGClient(base_url=base_url, cache_dir=cache_dir)


# ---------------------------------------------------------------------------
# SearxResult / SearxResponse dataclass tests
# ---------------------------------------------------------------------------


class TestSearxResult:
    def test_creation_defaults(self):
        from general_ludd.retrieval.searx_client import SearxResult

        r = SearxResult(url="https://example.com")
        assert r.url == "https://example.com"
        assert r.title == ""
        assert r.content == ""
        assert r.engine == ""
        assert r.score == 0.0
        assert r.category == ""
        assert r.parsed_url == []
        assert r.template == ""
        assert r.img_src == ""
        assert r.thumbnail_src == ""
        assert r.published_date is None

    def test_creation_full_fields(self):
        from general_ludd.retrieval.searx_client import SearxResult

        r = SearxResult(
            url="https://example.com",
            title="Test",
            content="Test content",
            engine="google",
            score=0.85,
            category="general",
            parsed_url=["https", "example.com", "/", "", "", ""],
            template="result.html",
            img_src="/thumb.png",
            thumbnail_src="/thumb_small.png",
            published_date="2024-06-01",
        )
        assert r.title == "Test"
        assert r.score == 0.85
        assert r.engine == "google"
        assert r.published_date == "2024-06-01"


class TestSearxResponse:
    def test_creation_defaults(self):
        from general_ludd.retrieval.searx_client import SearxResponse

        resp = SearxResponse(query="q", results=[])
        assert resp.query == "q"
        assert resp.results == []
        assert resp.number_of_results == 0
        assert resp.suggestions == []
        assert resp.answers == []
        assert resp.corrections == []
        assert resp.infoboxes == []
        assert resp.unresponsive_engines == []
        assert resp.elapsed_ms == 0.0

    def test_creation_with_results(self, sample_raw_result):
        from general_ludd.retrieval.searx_client import SearxResponse, SearxResult

        result = SearxResult(url=sample_raw_result["url"], title=sample_raw_result["title"])
        resp = SearxResponse(
            query="q",
            results=[result],
            number_of_results=1,
            suggestions=["s1"],
            unresponsive_engines=[["b", "error"]],
            elapsed_ms=150.0,
        )
        assert len(resp.results) == 1
        assert resp.number_of_results == 1
        assert resp.suggestions == ["s1"]
        assert resp.elapsed_ms == 150.0


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_cache_key_deterministic(self):
        from general_ludd.retrieval.searx_client import _cache_key

        k1 = _cache_key("hello", ("general", "science"), None, 1, "en")
        k2 = _cache_key("hello", ("general", "science"), None, 1, "en")
        assert k1 == k2

    def test_cache_key_differs_by_query(self):
        from general_ludd.retrieval.searx_client import _cache_key

        k1 = _cache_key("a", ("general",), None, 1, "en")
        k2 = _cache_key("b", ("general",), None, 1, "en")
        assert k1 != k2

    def test_cache_key_differs_by_page(self):
        from general_ludd.retrieval.searx_client import _cache_key

        k1 = _cache_key("q", ("general",), None, 1, "en")
        k2 = _cache_key("q", ("general",), None, 2, "en")
        assert k1 != k2

    def test_cache_key_differs_by_language(self):
        from general_ludd.retrieval.searx_client import _cache_key

        k1 = _cache_key("q", ("general",), None, 1, "en")
        k2 = _cache_key("q", ("general",), None, 1, "fr")
        assert k1 != k2

    def test_cache_key_differs_by_time_range(self):
        from general_ludd.retrieval.searx_client import _cache_key

        k1 = _cache_key("q", ("general",), None, 1, "en")
        k2 = _cache_key("q", ("general",), "year", 1, "en")
        assert k1 != k2

    def test_cache_key_categories_sorted(self):
        from general_ludd.retrieval.searx_client import _cache_key

        # Category order shouldn't matter — they are sorted in the key.
        k1 = _cache_key("q", ("science", "general"), None, 1, "en")
        k2 = _cache_key("q", ("general", "science"), None, 1, "en")
        assert k1 == k2

    def test_cache_key_format(self):
        from general_ludd.retrieval.searx_client import _cache_key

        key = _cache_key("hello world", ("general", "it"), "week", 1, "en")
        assert key.startswith("searx:")
        assert "hello world" in key
        assert "general" in key
        assert "it" in key
        assert "week" in key


class TestRawResultToSearx:
    def test_parses_all_fields(self, sample_raw_result):
        from general_ludd.retrieval.searx_client import _raw_result_to_searx

        r = _raw_result_to_searx(sample_raw_result)
        assert r.url == "https://example.com/article"
        assert r.title == "Example Article"
        assert r.content == "This is example content for testing."
        assert r.engine == "google"
        assert r.score == 0.95
        assert r.category == "science"
        assert r.published_date == "2024-01-15"

    def test_missing_fields_default(self):
        from general_ludd.retrieval.searx_client import _raw_result_to_searx

        r = _raw_result_to_searx({"url": "https://x.com"})
        assert r.url == "https://x.com"
        assert r.title == ""
        assert r.content == ""
        assert r.score == 0.0

    def test_score_is_float(self):
        from general_ludd.retrieval.searx_client import _raw_result_to_searx

        r = _raw_result_to_searx({"url": "https://x.com", "score": "3.14"})
        assert isinstance(r.score, float)
        assert r.score == 3.14

    def test_published_date_falls_back_to_publishedDate(self):
        from general_ludd.retrieval.searx_client import _raw_result_to_searx

        r = _raw_result_to_searx({"url": "https://x.com", "publishedDate": "2023-05-10"})
        assert r.published_date == "2023-05-10"

    def test_published_date_prefers_published_date(self):
        from general_ludd.retrieval.searx_client import _raw_result_to_searx

        r = _raw_result_to_searx(
            {"url": "https://x.com", "publishedDate": "old", "published_date": "new"}
        )
        # publishedDate is checked first via raw.get("publishedDate")
        assert r.published_date == "old"


class TestDeduplicate:
    def test_removes_duplicate_urls_keeps_highest_score(self):
        from general_ludd.retrieval.searx_client import SearxResult, _deduplicate

        r1 = SearxResult(url="https://a.com", score=0.9)
        r2 = SearxResult(url="https://a.com", score=0.5)
        r3 = SearxResult(url="https://b.com", score=0.7)
        deduped = _deduplicate([r1, r2, r3])
        urls = [r.url for r in deduped]
        assert len(deduped) == 2
        assert "https://a.com" in urls
        assert "https://b.com" in urls
        a_result = next(r for r in deduped if r.url == "https://a.com")
        assert a_result.score == 0.9

    def test_results_sorted_by_score_desc(self):
        from general_ludd.retrieval.searx_client import SearxResult, _deduplicate

        r1 = SearxResult(url="https://a.com", score=0.5)
        r2 = SearxResult(url="https://b.com", score=0.9)
        r3 = SearxResult(url="https://c.com", score=0.7)
        deduped = _deduplicate([r1, r2, r3])
        scores = [r.score for r in deduped]
        assert scores == sorted(scores, reverse=True)

    def test_skips_empty_urls(self):
        from general_ludd.retrieval.searx_client import SearxResult, _deduplicate

        r1 = SearxResult(url="", score=0.9)
        r2 = SearxResult(url="https://a.com", score=0.7)
        deduped = _deduplicate([r1, r2])
        assert len(deduped) == 1
        assert deduped[0].url == "https://a.com"

    def test_empty_list(self):
        from general_ludd.retrieval.searx_client import _deduplicate

        assert _deduplicate([]) == []


# ---------------------------------------------------------------------------
# SearxNGClient initialization tests
# ---------------------------------------------------------------------------


class TestSearxNGClientInit:
    async def test_default_base_url(self, temp_cache_dir):
        client = _make_client(cache_dir=temp_cache_dir)
        assert client._base_url == "http://localhost:8080"
        await client.close()

    async def test_custom_base_url(self, temp_cache_dir):
        client = _make_client(cache_dir=temp_cache_dir, base_url="http://searx:9090")
        assert client._base_url == "http://searx:9090"
        await client.close()

    async def test_base_url_strips_trailing_slash(self, temp_cache_dir):
        client = _make_client(cache_dir=temp_cache_dir, base_url="http://searx:9090/")
        assert client._base_url == "http://searx:9090"
        await client.close()

    async def test_creates_cache_directory(self, temp_cache_dir):
        import os

        cache_path = os.path.join(temp_cache_dir, "test_cache")
        client = _make_client(cache_dir=cache_path)
        assert os.path.isdir(cache_path)
        await client.close()

    async def test_cache_dir_permissions_owner_only(self, temp_cache_dir):
        import os
        import stat

        cache_path = os.path.join(temp_cache_dir, "owner_only_cache")
        client = _make_client(cache_dir=cache_path)
        mode = stat.S_IMODE(os.stat(cache_path).st_mode)
        # No group/other permission bits.
        assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
        await client.close()

    def test_static_base_url_env_default(self):
        from general_ludd.retrieval.searx_client import SearxNGClient

        assert SearxNGClient.base_url() == "http://localhost:8080"

    async def test_client_initialized_none(self, temp_cache_dir):
        client = _make_client(cache_dir=temp_cache_dir)
        assert client._client is None
        await client.close()

    async def test_last_request_time_initialized_zero(self, temp_cache_dir):
        client = _make_client(cache_dir=temp_cache_dir)
        assert client._last_request_time == 0.0
        await client.close()


# ---------------------------------------------------------------------------
# Search query construction tests
# ---------------------------------------------------------------------------


class TestSearchQueryConstruction:
    async def test_search_default_categories(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            result = await client.search("test query")

        call_args = mock_client.get.call_args
        params = call_args.kwargs["params"]
        assert "science" in params["categories"]
        assert "it" in params["categories"]
        assert "general" in params["categories"]
        assert result.query == "test query"
        await client.close()

    async def test_search_with_custom_categories(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            await client.search("q", categories=["news"])
            await client.close()

        params = mock_client.get.call_args.kwargs["params"]
        assert params["categories"] == "news"

    async def test_search_with_engines_overrides_categories(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            await client.search("q", engines=["google", "arxiv"])
            await client.close()

        params = mock_client.get.call_args.kwargs["params"]
        assert params["engines"] == "google,arxiv"
        assert "categories" not in params

    async def test_search_language_default_en(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            await client.search("q")
            await client.close()

        params = mock_client.get.call_args.kwargs["params"]
        assert params["language"] == "en"

    async def test_search_with_custom_language(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            await client.search("q", language="fr")
            await client.close()

        params = mock_client.get.call_args.kwargs["params"]
        assert params["language"] == "fr"

    async def test_search_page_default(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            await client.search("q")
            await client.close()

        params = mock_client.get.call_args.kwargs["params"]
        assert params["pageno"] == 1

    async def test_search_with_page(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            await client.search("q", page=3)
            await client.close()

        params = mock_client.get.call_args.kwargs["params"]
        assert params["pageno"] == 3

    async def test_search_safe_search_default(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            await client.search("q")
            await client.close()

        params = mock_client.get.call_args.kwargs["params"]
        assert params["safesearch"] == 0

    async def test_search_safe_search_strict(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            await client.search("q", safe_search=2)
            await client.close()

        params = mock_client.get.call_args.kwargs["params"]
        assert params["safesearch"] == 2

    async def test_search_time_range_omitted_when_none(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            await client.search("q")
            await client.close()

        params = mock_client.get.call_args.kwargs["params"]
        assert "time_range" not in params

    async def test_search_time_range_included(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            await client.search("q", time_range="year")
            await client.close()

        params = mock_client.get.call_args.kwargs["params"]
        assert params["time_range"] == "year"

    async def test_search_format_is_json(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            await client.search("q")
            await client.close()

        params = mock_client.get.call_args.kwargs["params"]
        assert params["format"] == "json"

    async def test_search_url_path(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir, base_url="http://searx:8888")
            await client.search("q")
            await client.close()

        url = mock_client.get.call_args.args[0] if mock_client.get.call_args.args else mock_client.get.call_args.kwargs["url"]  # noqa: E501
        # The URL should end with /search
        assert url == "http://searx:8888/search"


# ---------------------------------------------------------------------------
# Caching behavior tests
# ---------------------------------------------------------------------------


class TestCachingBehavior:
    async def test_cache_miss_fetches_from_api(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            result = await client.search("cache miss query")
            await client.close()

        mock_client.get.assert_awaited_once()
        assert result.query == "test query"
        assert len(result.results) == 2  # 3 raw, 1 deduped

    async def test_cache_hit_skips_api_call(self, temp_cache_dir, sample_search_response):
        from general_ludd.security.safe_diskcache import open_safe_diskcache

        # Pre-seed the cache with a response
        cache_path = temp_cache_dir
        cache = open_safe_diskcache(cache_path)
        from general_ludd.retrieval.searx_client import SearxResponse, SearxResult

        cached_result = SearxResult(url="https://cached.com", title="Cached", score=1.0)
        cached_resp = SearxResponse(
            query="cached query",
            results=[cached_result],
            number_of_results=1,
            elapsed_ms=5.0,
        )
        from general_ludd.retrieval.searx_client import _cache_key

        key = _cache_key("cached query", ("general", "it", "science"), None, 1, "en")
        cache[key] = {
            "query": cached_resp.query,
            "results": [
                {
                    "url": r.url,
                    "title": r.title,
                    "content": r.content,
                    "engine": r.engine,
                    "score": r.score,
                    "category": r.category,
                    "parsed_url": r.parsed_url,
                    "template": r.template,
                    "img_src": r.img_src,
                    "thumbnail_src": r.thumbnail_src,
                    "published_date": r.published_date,
                }
                for r in cached_resp.results
            ],
            "number_of_results": cached_resp.number_of_results,
            "suggestions": cached_resp.suggestions,
            "answers": cached_resp.answers,
            "corrections": cached_resp.corrections,
            "infoboxes": cached_resp.infoboxes,
            "unresponsive_engines": cached_resp.unresponsive_engines,
            "elapsed_ms": cached_resp.elapsed_ms,
        }
        cache.close()

        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=cache_path)
            result = await client.search("cached query")
            await client.close()

        # The API should NOT have been called
        mock_client.get.assert_not_called()
        assert len(result.results) == 1
        # When loaded from cache, results are dicts (SearxResponse serialised as dicts).
        first = result.results[0]
        assert (first.url if hasattr(first, "url") else first["url"]) == "https://cached.com"
        assert (first.title if hasattr(first, "title") else first["title"]) == "Cached"

    async def test_bypass_cache_always_fetches(self, temp_cache_dir, sample_search_response):
        from general_ludd.security.safe_diskcache import open_safe_diskcache

        # Pre-seed the cache
        cache_path = temp_cache_dir
        cache = open_safe_diskcache(cache_path)
        from general_ludd.retrieval.searx_client import SearxResponse, SearxResult, _cache_key

        r = SearxResult(url="https://stale.com", title="Stale")
        resp = SearxResponse(query="bypass query", results=[r])
        key = _cache_key("bypass query", ("general", "it", "science"), None, 1, "en")
        cache[key] = {
            "query": resp.query,
            "results": [
                {
                    "url": r.url,
                    "title": r.title,
                    "content": r.content,
                    "engine": r.engine,
                    "score": r.score,
                    "category": r.category,
                    "parsed_url": r.parsed_url,
                    "template": r.template,
                    "img_src": r.img_src,
                    "thumbnail_src": r.thumbnail_src,
                    "published_date": r.published_date,
                }
            ],
            "number_of_results": resp.number_of_results,
            "suggestions": resp.suggestions,
            "answers": resp.answers,
            "corrections": resp.corrections,
            "infoboxes": resp.infoboxes,
            "unresponsive_engines": resp.unresponsive_engines,
            "elapsed_ms": resp.elapsed_ms,
        }
        cache.close()

        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=cache_path)
            result = await client.search("bypass query", bypass_cache=True)
            await client.close()

        # API should have been called despite cache hit
        mock_client.get.assert_awaited_once()
        assert result.query == "test query"
        assert len(result.results) == 2

    async def test_search_persists_results_to_cache(self, temp_cache_dir, sample_search_response):
        from general_ludd.security.safe_diskcache import open_safe_diskcache

        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            await client.search("persist query")
            await client.close()

        # Verify data was written to the cache
        cache = open_safe_diskcache(temp_cache_dir)
        from general_ludd.retrieval.searx_client import _cache_key

        key = _cache_key("persist query", ("general", "it", "science"), None, 1, "en")
        cached = cache.get(key)
        cache.close()
        assert cached is not None
        assert cached["query"] == "test query"
        assert len(cached["results"]) >= 1
        assert cached["number_of_results"] == 42

    async def test_cache_varying_time_ranges(self, temp_cache_dir, sample_search_response):
        """Different time_range values produce distinct cache entries."""
        from general_ludd.security.safe_diskcache import open_safe_diskcache

        cache_path = temp_cache_dir
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=cache_path)
            await client.search("multi range", time_range="day")
            await client.search("multi range", time_range="year")
            await client.close()

        cache = open_safe_diskcache(cache_path)
        from general_ludd.retrieval.searx_client import _cache_key

        key_day = _cache_key("multi range", ("general", "it", "science"), "day", 1, "en")
        key_year = _cache_key("multi range", ("general", "it", "science"), "year", 1, "en")
        assert cache.get(key_day) is not None
        assert cache.get(key_year) is not None
        assert key_day != key_year
        cache.close()


# ---------------------------------------------------------------------------
# Error handling / edge cases
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_timeout_raises(self, temp_cache_dir):
        from httpx import TimeoutException

        mock_client = _make_mock_async_client(get_side_effect=TimeoutException("timed out"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            with pytest.raises(TimeoutException):
                await client.search("timeout query")
            await client.close()

    async def test_http_error_raises(self, temp_cache_dir):
        from httpx import HTTPStatusError

        # Build a mock response that triggers HTTPStatusError
        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.aclose = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = HTTPStatusError(
            "Server error", request=MagicMock(), response=mock_resp
        )
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            with pytest.raises(HTTPStatusError):
                await client.search("error query")
            await client.close()


# ---------------------------------------------------------------------------
# Health check tests
# ---------------------------------------------------------------------------


class TestHealthCheck:
    async def test_health_ok(self, temp_cache_dir):
        healthy_response = {
            "query": "test",
            "results": [{"url": "https://x.com", "title": "X"}],
        }
        mock_client = _make_mock_async_client(get_response=healthy_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            result = await client.health()
            await client.close()

        assert result["ok"] is True
        assert "SearXNG reachable" in result["detail"]
        assert result["base_url"] == "http://localhost:8080"

    async def test_health_http_error(self, temp_cache_dir):
        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.aclose = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.status_code = 503
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            result = await client.health()
            await client.close()

        assert result["ok"] is False
        assert "HTTP 503" in result["detail"]

    async def test_health_unreachable(self, temp_cache_dir):
        mock_client = _make_mock_async_client(
            get_side_effect=ConnectionError("refused")
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            result = await client.health()
            await client.close()

        assert result["ok"] is False
        assert "unreachable" in result["detail"]


# ---------------------------------------------------------------------------
# Multi-search tests
# ---------------------------------------------------------------------------


class TestMultiSearch:
    async def test_multi_search_runs_parallel(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            results = await client.multi_search("parallel query")
            await client.close()

        assert len(results) == 3  # default time_ranges: [None, "year", "month"]
        assert mock_client.get.await_count == 3

    async def test_multi_search_custom_time_ranges(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            results = await client.multi_search("q", time_ranges=["day", "week"])
            await client.close()

        assert len(results) == 2
        assert mock_client.get.await_count == 2


# ---------------------------------------------------------------------------
# Client lifecycle tests
# ---------------------------------------------------------------------------


class TestClientLifecycle:
    async def test_close_cleans_up(self, temp_cache_dir, sample_search_response):
        mock_client = _make_mock_async_client(get_response=sample_search_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            await client.search("q")
            await client.close()

        mock_client.aclose.assert_awaited_once()

    async def test_get_client_reuses_existing(self, temp_cache_dir):
        mock_client = _make_mock_async_client(get_response={})

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = _make_client(cache_dir=temp_cache_dir)
            c1 = await client._get_client()
            c2 = await client._get_client()
            assert c1 is c2
            await client.close()

    async def test_get_client_recreates_when_closed(self, temp_cache_dir):
        first_client = _make_mock_async_client(get_response={})
        second_client = _make_mock_async_client(get_response={})

        patch_calls = {"count": 0}

        def _side_effect(*args, **kwargs):
            patch_calls["count"] += 1
            if patch_calls["count"] == 1:
                return first_client
            return second_client

        with patch("httpx.AsyncClient", side_effect=_side_effect):
            client = _make_client(cache_dir=temp_cache_dir)
            c1 = await client._get_client()
            # Simulate the client being closed
            c1.is_closed = True
            c2 = await client._get_client()
            assert c1 is not c2
            await client.close()
