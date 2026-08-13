"""Integration / E2E tests for G12 live web retrieval.

Exercises WebPageResult, _extract_title, _normalise_domain, allowed_domains,
and fetch_web_page end-to-end with mocked urllib and diskcache.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.retrieval.web import (
    WebPageResult,
    WebRetriever,
    _extract_title,
    _normalise_domain,
)


class _FakeHeaders(dict):
    """A dict subclass that also exposes an items() method compatible with
    urllib EmailMessage headers — dict(resp.headers) iterates directly over
    the object, so being a real dict (not a MagicMock) is needed.
    """

    def items(self):
        return list(super().items())


def _mock_http_response(
    body: bytes,
    status: int = 200,
    headers: dict[str, str] | None = None,
    url: str = "http://example.com",
) -> MagicMock:
    """Return a MagicMock that mimics urllib.request.urlopen response."""
    if headers is None:
        headers = {"Content-Type": "text/html; charset=utf-8"}
    resp = MagicMock()
    resp.status = status
    resp.headers = _FakeHeaders(headers)
    resp.read.return_value = body
    resp.url = url
    return resp


def _mock_http_error(
    url: str = "http://example.com",
    code: int = 404,
    msg: str = "Not Found",
    headers: dict[str, str] | None = None,
) -> urllib.error.HTTPError:
    """Create a real urllib.error.HTTPError for mocking."""
    if headers is None:
        headers = {"Content-Type": "text/plain"}
    fp = io.BytesIO(b"")
    return urllib.error.HTTPError(url, code, msg, headers, fp)


# ---------------------------------------------------------------------------
# 1. WebPageResult dataclass
# ---------------------------------------------------------------------------
class TestWebPageResultDataclass:
    def test_all_fields_defaults(self):
        result = WebPageResult(url="http://example.com", status_code=200, content="ok")
        assert result.url == "http://example.com"
        assert result.status_code == 200
        assert result.content == "ok"
        assert result.title is None
        assert result.headers is None

    def test_all_fields_explicit(self):
        result = WebPageResult(
            url="https://foo.bar/baz",
            status_code=301,
            content="redirect",
            title="Moved",
            headers={"Location": "https://foo.bar/new"},
        )
        assert result.url == "https://foo.bar/baz"
        assert result.status_code == 301
        assert result.content == "redirect"
        assert result.title == "Moved"
        assert result.headers == {"Location": "https://foo.bar/new"}

    def test_is_dataclass_instance(self):
        result = WebPageResult(url="x", status_code=200, content="x")
        assert hasattr(result, "__dataclass_fields__")


# ---------------------------------------------------------------------------
# 2 & 3. _extract_title
# ---------------------------------------------------------------------------
class TestExtractTitle:
    def test_simple_title(self):
        assert _extract_title("<html><head><title>Hello World</title></head></html>") == "Hello World"

    def test_title_with_attributes(self):
        html = '<title class="page-title" data-id="1">My Page</title>'
        assert _extract_title(html) == "My Page"

    def test_title_across_lines(self):
        html = "<html>\n<head>\n<title>\nMulti\nLine\nTitle\n</title>\n</head>\n</html>"
        assert _extract_title(html) == "Multi\nLine\nTitle"

    def test_title_with_nested_tags(self):
        html = "<title>Welcome to <b>Our</b> <i>Site</i></title>"
        assert _extract_title(html) == "Welcome to Our Site"

    def test_title_with_entities(self):
        html = "<title>Price &amp; Value &lt; 100</title>"
        assert _extract_title(html) == "Price &amp; Value &lt; 100"

    def test_title_present_without_head(self):
        html = "<html><title>Standalone</title><body>text</body></html>"
        assert _extract_title(html) == "Standalone"

    def test_no_title_tag_returns_none(self):
        assert _extract_title("<html><body>No title here</body></html>") is None

    def test_empty_title_returns_none(self):
        assert _extract_title("<html><head><title></title></head></html>") is None

    def test_title_whitespace_only_returns_none(self):
        assert _extract_title("<title>   </title>") is None

    def test_title_with_only_tags_returns_none(self):
        assert _extract_title("<title><b></b><i></i></title>") is None

    def test_uppercase_title_tag(self):
        assert _extract_title("<TITLE>Uppercase Title</TITLE>") == "Uppercase Title"

    def test_mixed_case_title_tag(self):
        assert _extract_title("<TiTlE>Mixed Case</TiTlE>") == "Mixed Case"

    def test_xhtml_self_closing_title_ignored(self):
        assert _extract_title('<title lang="en"/>') is None


# ---------------------------------------------------------------------------
# 4. _normalise_domain
# ---------------------------------------------------------------------------
class TestNormaliseDomain:
    def test_http_url(self):
        assert _normalise_domain("http://example.com/page") == "example.com"

    def test_https_url(self):
        assert _normalise_domain("https://example.com") == "example.com"

    def test_url_with_port(self):
        assert _normalise_domain("http://example.com:8080/path") == "example.com"

    def test_url_with_subdomain(self):
        assert _normalise_domain("https://sub.example.co.uk/path?a=1") == "sub.example.co.uk"

    def test_url_without_scheme(self):
        assert _normalise_domain("example.com/path") == ""

    def test_empty_string(self):
        assert _normalise_domain("") == ""

    def test_ip_address(self):
        assert _normalise_domain("http://192.168.1.1/admin") == "192.168.1.1"

    def test_url_with_userinfo(self):
        assert _normalise_domain("https://user:pass@example.com/secret") == "example.com"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# 5. WebRetriever.allowed_domains()
# ---------------------------------------------------------------------------
class TestAllowedDomains:
    def test_unset_env_returns_empty_list(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        assert WebRetriever.allowed_domains() == []

    def test_env_empty_string_returns_empty_list(self, monkeypatch):
        monkeypatch.setenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "")
        assert WebRetriever.allowed_domains() == []

    def test_single_domain(self, monkeypatch):
        monkeypatch.setenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "example.com")
        assert WebRetriever.allowed_domains() == ["example.com"]

    def test_multiple_domains(self, monkeypatch):
        monkeypatch.setenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "example.com, trusted.org, api.dev")
        assert WebRetriever.allowed_domains() == ["example.com", "trusted.org", "api.dev"]

    def test_trims_whitespace(self, monkeypatch):
        monkeypatch.setenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "  example.com ,  docs.example.com  ")
        assert WebRetriever.allowed_domains() == ["example.com", "docs.example.com"]

    def test_trailing_commas_and_blanks_filtered(self, monkeypatch):
        monkeypatch.setenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "example.com,,trusted.org,")
        assert WebRetriever.allowed_domains() == ["example.com", "trusted.org"]

    def test_static_method_no_instance_needed(self):
        assert isinstance(WebRetriever.allowed_domains(), list)


# ---------------------------------------------------------------------------
# 6. fetch_web_page — successful fetch with title extraction
# ---------------------------------------------------------------------------
class TestFetchWebPageSuccess:
    HTML = (
        b"<html><head><title>Test Page</title></head>"
        b"<body><h1>Hello</h1><p>Content here.</p></body></html>"
    )

    def test_successful_fetch_parses_title_and_content(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        mock_resp = _mock_http_response(
            body=self.HTML,
            status=200,
            headers={"Content-Type": "text/html", "X-Custom": "abc"},
            url="http://example.com/page",
        )

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(return_value=mock_resp))):
            retriever = WebRetriever(timeout_seconds=10)
            result = retriever.fetch_web_page("http://example.com/page")

        assert result.url == "http://example.com/page"
        assert result.status_code == 200
        assert result.title == "Test Page"
        assert "Hello" in result.content
        assert "Content here." in result.content

    def test_successful_fetch_strips_tags_from_title(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        html = b"<html><head><title>My <b>Bold</b> <em>Site</em></title></head></html>"
        mock_resp = _mock_http_response(body=html, status=200)

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(return_value=mock_resp))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://example.com")

        assert result.title == "My Bold Site"


# ---------------------------------------------------------------------------
# 7. fetch_web_page — HTTP errors (404, 500)
# ---------------------------------------------------------------------------
class TestFetchWebPageHttpErrors:
    def test_404_returns_status_code_with_empty_content(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        error = _mock_http_error(url="http://example.com/missing", code=404, msg="Not Found")

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(side_effect=error))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://example.com/missing")

        assert result.url == "http://example.com/missing"
        assert result.status_code == 404
        assert result.content == ""
        assert result.title is None

    def test_500_returns_status_code(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        error = _mock_http_error(
            url="http://example.com/boom", code=500, msg="Internal Server Error"
        )

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(side_effect=error))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://example.com/boom")

        assert result.status_code == 500

    def test_403_returns_status_code(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        error = _mock_http_error(url="http://example.com/forbidden", code=403, msg="Forbidden")

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(side_effect=error))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://example.com/forbidden")

        assert result.status_code == 403


# ---------------------------------------------------------------------------
# 8. fetch_web_page — connection error
# ---------------------------------------------------------------------------
class TestFetchWebPageConnectionError:
    def test_connection_error_returns_negative_status(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        conn_error = urllib.error.URLError("connection refused")

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(side_effect=conn_error))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://down.example.com")

        assert result.url == "http://down.example.com"
        assert result.status_code == -1
        assert result.title is None
        assert result.headers is None
        assert "connection refused" in result.content.lower()

    def test_timeout_error_returns_negative_status(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        timeout_error = TimeoutError("timed out")

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(side_effect=timeout_error))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://slow.example.com")

        assert result.status_code == -1
        assert "timed out" in result.content


# ---------------------------------------------------------------------------
# 9. fetch_web_page — caching
# ---------------------------------------------------------------------------
class TestFetchWebPageCaching:
    def test_second_call_returns_cached_result_no_urlopen(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        html = b"<html><head><title>Cached</title></head><body>body</body></html>"
        mock_resp = _mock_http_response(body=html, status=200)

        cached_data = {
            "url": "http://example.com/cached",
            "status_code": 200,
            "content": "<html><head><title>Cached</title></head><body>body</body></html>",
            "title": "Cached",
            "headers": {"content-type": "text/html"},
        }

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = cached_data

        urlopen_calls = []
        def _tracking_urlopen(*args, **kwargs):
            urlopen_calls.append(1)
            return mock_resp

        _opener = MagicMock(open=MagicMock(side_effect=_tracking_urlopen))
        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=_opener):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://example.com/cached")

        assert result.url == "http://example.com/cached"
        assert result.status_code == 200
        assert result.title == "Cached"
        assert result.headers == {"content-type": "text/html"}
        assert "body" in result.content
        # urlopen was never called — cache hit
        assert len(urlopen_calls) == 0

    def test_cache_miss_then_hit_does_not_call_urlopen_again(self, monkeypatch):
        """First call fills cache (mocked urlopen fires once), second call
        returns cached result without calling urlopen again."""
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        html = b"<html><head><title>Miss Then Hit</title></head></html>"
        mock_resp = _mock_http_response(body=html, status=200)

        cache_store: dict[str, dict] = {}

        class FakeCache:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def get(self, key):
                return cache_store.get(key)

            def set(self, key, value, expire=None):
                cache_store[key] = value

        urlopen_calls = []

        def _tracking_urlopen(*args, **kwargs):
            urlopen_calls.append(1)
            return mock_resp

        _opener = MagicMock(open=MagicMock(side_effect=_tracking_urlopen))
        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=FakeCache()), \
                patch("urllib.request.build_opener", return_value=_opener):
            retriever = WebRetriever()
            result1 = retriever.fetch_web_page("http://example.com/hit")
            result2 = retriever.fetch_web_page("http://example.com/hit")

        assert result1.url == "http://example.com/hit"
        assert result2.url == "http://example.com/hit"
        assert result1.title == "Miss Then Hit"
        assert result2.title == "Miss Then Hit"
        assert len(urlopen_calls) == 1, (
            f"urlopen called {len(urlopen_calls)} times; expected exactly 1 (cache miss only)"
        )


# ---------------------------------------------------------------------------
# 10 & 11 & 12. Domain allowlist
# ---------------------------------------------------------------------------
class TestFetchWebPageDomainAllowlist:
    def test_blocked_domain_raises_valueerror(self, monkeypatch):
        monkeypatch.setenv(
            "GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "trusted.com,approved.org"
        )

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache):
            retriever = WebRetriever()
            with pytest.raises(ValueError, match=r"blocked\.com"):
                retriever.fetch_web_page("http://blocked.com/secret")

    def test_domain_not_matching_exact_str_raises(self, monkeypatch):
        monkeypatch.setenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "example.com")

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache):
            retriever = WebRetriever()
            # sub.ex.com != example.com — exact string match
            with pytest.raises(ValueError):
                retriever.fetch_web_page("http://sub.example.com/page")

    def test_allowed_domain_proceeds_to_fetch(self, monkeypatch):
        monkeypatch.setenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "trusted.com,example.org")
        html = b"<html><head><title>Trusted</title></head></html>"
        mock_resp = _mock_http_response(body=html, status=200)

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(return_value=mock_resp))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://trusted.com/page")

        assert result.title == "Trusted"
        assert result.status_code == 200

    def test_second_allowed_domain_also_proceeds(self, monkeypatch):
        monkeypatch.setenv(
            "GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "trusted.com,example.org"
        )
        html = b"<html><head><title>Example Org</title></head></html>"
        mock_resp = _mock_http_response(body=html, status=200)

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(return_value=mock_resp))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://example.org/about")

        assert result.title == "Example Org"

    def test_empty_allowlist_allows_all_domains(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        html = b"<html><head><title>Unrestricted</title></head></html>"
        mock_resp = _mock_http_response(body=html, status=200)

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(return_value=mock_resp))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://any-domain.xyz/data")

        assert result.title == "Unrestricted"
        assert result.status_code == 200

    def test_allowlist_block_error_includes_domain_and_allowed_list(self, monkeypatch):
        monkeypatch.setenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "only.com")

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache):
            retriever = WebRetriever()
            with pytest.raises(ValueError) as exc_info:
                retriever.fetch_web_page("http://evil.net/payload")
            msg = str(exc_info.value)
            assert "evil.net" in msg
            assert "only.com" in msg


# ---------------------------------------------------------------------------
# 13. Content size cap (1 MB)
# ---------------------------------------------------------------------------
class TestFetchWebPageContentSizeCap:
    def test_content_truncated_at_1mb(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        chunk = b"0123456789"
        big_body = chunk * 200_000  # 2 MB — exceeds 1 MB cap

        mock_resp = _mock_http_response(body=big_body, status=200)

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(return_value=mock_resp))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://big.example.com")

        assert len(result.content.encode("utf-8")) <= 1_048_576, (
            f"Content length {len(result.content.encode('utf-8'))} exceeds 1 MB cap"
        )

    def test_content_at_exactly_1mb_is_not_truncated(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        chunk = b"a"
        exact_1mb = chunk * 1_048_576  # exactly 1 MB

        mock_resp = _mock_http_response(body=exact_1mb, status=200)

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(return_value=mock_resp))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://exact.example.com")

        assert len(result.content.encode("utf-8")) == 1_048_576

    def test_content_at_1mb_plus_one_byte_is_truncated(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        chunk = b"b"
        over_1mb = chunk * (1_048_576 + 1)  # 1 MB + 1 byte

        mock_resp = _mock_http_response(body=over_1mb, status=200)

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(return_value=mock_resp))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://over.example.com")

        assert len(result.content.encode("utf-8")) == 1_048_576


# ---------------------------------------------------------------------------
# 14. Header extraction
# ---------------------------------------------------------------------------
class TestFetchWebPageHeaderExtraction:
    def test_headers_captured_and_lowercased(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        html = b"<html><head><title>Headers Test</title></head></html>"
        mock_resp = _mock_http_response(
            body=html,
            status=200,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Server": "nginx/1.18",
                "X-Cache": "HIT",
            },
        )

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(return_value=mock_resp))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://example.com")

        assert result.headers is not None
        assert result.headers["content-type"] == "text/html; charset=utf-8"
        assert result.headers["server"] == "nginx/1.18"
        assert result.headers["x-cache"] == "HIT"

    def test_http_error_preserves_headers(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        error_headers = {"Content-Type": "text/plain; charset=utf-8", "Retry-After": "120"}
        error = _mock_http_error(
            url="http://example.com/rate-limited",
            code=429,
            msg="Too Many Requests",
            headers=error_headers,
        )

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=MagicMock(open=MagicMock(side_effect=error))):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("http://example.com/rate-limited")

        assert result.status_code == 429
        assert result.headers is not None
        assert "retry-after" in result.headers or "Retry-After" in result.headers


# ---------------------------------------------------------------------------
# 15. Timeout configuration
# ---------------------------------------------------------------------------
class TestWebRetrieverTimeoutConfig:
    def test_default_timeout(self):
        retriever = WebRetriever()
        assert retriever._timeout == 30

    def test_custom_timeout_stored(self):
        retriever = WebRetriever(timeout_seconds=15)
        assert retriever._timeout == 15

    def test_timeout_passed_to_urlopen(self, monkeypatch):
        monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
        html = b"<html><head><title>Timeout Test</title></head></html>"
        mock_resp = _mock_http_response(body=html, status=200)

        fake_cache = MagicMock()
        fake_cache.__enter__.return_value = fake_cache
        fake_cache.get.return_value = None

        mock_opener = MagicMock(open=MagicMock(return_value=mock_resp))
        with patch("general_ludd.retrieval.web.open_safe_diskcache", return_value=fake_cache), \
                patch("urllib.request.build_opener", return_value=mock_opener) as mock_build_opener:
                retriever = WebRetriever(timeout_seconds=5)
                retriever.fetch_web_page("http://example.com")

        mock_build_opener.assert_called_once()
        mock_opener.open.assert_called_once()
        _, kwargs = mock_opener.open.call_args
        assert kwargs["timeout"] == 5


# ---------------------------------------------------------------------------
# Teardown: ensure env var is cleared globally
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clear_web_fetch_env(monkeypatch):
    """Ensure GLUDD_WEB_FETCH_ALLOWED_DOMAINS is not set for any test."""
    monkeypatch.delenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", raising=False)
