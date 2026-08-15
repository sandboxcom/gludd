"""Deep behavioral tests for the bounded web toolkit facade.

Covers URL normalization, charset decoding, failure mapping, challenge
detection, and the fetch / search-gather / crawl / render orchestration
paths of ``general_ludd.web.toolkit``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

from general_ludd.security.url_fetch import (
    FetchPolicy,
    FetchResult,
    RedirectLimitExceeded,
    ResponseTooLarge,
    UnsafeURLError,
    URLFetchTimeout,
    secure_fetch,
)
from general_ludd.web import DEFAULT_POLICY, WebError, WebPolicy, WebToolkit
from general_ludd.web.toolkit import (
    NullSearchProvider,
    _challenge,
    _decode_body,
    _fetch_error,
    normalize_url,
)
from general_ludd.web.types import SearchHit

_SEED = "https://example.com/"


def _result(
    url: str,
    *,
    status: int = 200,
    content: bytes | None = None,
    content_type: str = "text/html",
    headers: Mapping[str, str] | None = None,
) -> FetchResult:
    merged: dict[str, str] = {"content-type": content_type}
    merged.update(dict(headers or {}))
    return FetchResult(
        url=url,
        status_code=status,
        headers=merged,
        content=content if content is not None else b"<html><body>ok</body></html>",
    )


class RecordingFetcher:
    """Deterministic fetcher double that records every outbound call."""

    def __init__(
        self,
        responses: dict[str, FetchResult] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def __call__(
        self,
        url: str,
        *,
        policy: FetchPolicy,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        del policy
        self.calls.append((url, method, dict(headers or {})))
        error = self.errors.get(url)
        if error is not None:
            raise error
        return self.responses.get(
            url,
            _result(url, content=b"<html><body>default</body></html>"),
        )


class FakeSearchProvider:
    """Deterministic search-provider double that records its queries."""

    configured: bool = True

    def __init__(self, hits: list[SearchHit], error: Exception | None = None) -> None:
        self._hits = hits
        self._error = error
        self.queries: list[tuple[str, int]] = []

    def search(self, query: str, *, top_n: int) -> list[SearchHit]:
        self.queries.append((query, top_n))
        if self._error is not None:
            raise self._error
        return self._hits[:top_n]


class FakeRenderer:
    """Deterministic offline-renderer double that records its invocations."""

    def __init__(self, output: str = "<html></html>", error: Exception | None = None) -> None:
        self._output = output
        self._error = error
        self.calls: list[tuple[str, str, float]] = []

    def render_offline(self, html: str, *, base_url: str, timeout_seconds: float) -> str:
        self.calls.append((html, base_url, timeout_seconds))
        if self._error is not None:
            raise self._error
        return self._output


class _NonStringRenderer:
    """Renderer double that returns a non-string document at runtime."""

    def render_offline(self, html: str, *, base_url: str, timeout_seconds: float) -> str:
        del html, base_url, timeout_seconds
        return cast(str, None)


class TestNormalizeUrl:
    def test_canonicalizes_host_scheme_and_query(self) -> None:
        # arrange
        url = "https://Example.COM.:443/a/b?b=2&a=1&a="
        # act
        canonical = normalize_url(url)
        # assert
        assert canonical == "https://example.com/a/b?a=&a=1&b=2"

    def test_rejects_non_https_schemes(self) -> None:
        # arrange
        urls = ["http://example.com", "ftp://example.com", "ws://example.com"]
        # act / assert
        for url in urls:
            assert normalize_url(url) is None

    def test_rejects_url_without_hostname(self) -> None:
        # arrange
        url = "https:///path-only"
        # act
        canonical = normalize_url(url)
        # assert
        assert canonical is None

    def test_rejects_embedded_credentials(self) -> None:
        # arrange
        url = "https://user:secret@example.com/"
        # act
        canonical = normalize_url(url)
        # assert
        assert canonical is None

    def test_preserves_non_default_port(self) -> None:
        # arrange
        url = "https://example.com:8443/x"
        # act
        canonical = normalize_url(url)
        # assert
        assert canonical == "https://example.com:8443/x"

    def test_rejects_unparseable_port(self) -> None:
        # arrange
        url = "https://example.com:abc/"
        # act
        canonical = normalize_url(url)
        # assert
        assert canonical is None


class TestDecodeBody:
    def test_uses_declared_charset(self) -> None:
        # arrange
        fetched = _result(
            "https://example.com/",
            content="café".encode("latin-1"),
            content_type="text/html; charset=latin-1",
        )
        # act
        body = _decode_body(fetched)
        # assert
        assert body == "café"

    def test_defaults_to_utf8_without_charset(self) -> None:
        # arrange
        fetched = _result("https://example.com/", content="héllo".encode())
        # act
        body = _decode_body(fetched)
        # assert
        assert body == "héllo"

    def test_falls_back_to_replacement_decoding(self) -> None:
        # arrange
        fetched = _result(
            "https://example.com/",
            content=b"\xff\xfe",
            content_type="text/html; charset=ascii",
        )
        # act
        body = _decode_body(fetched)
        # assert
        assert body == "\ufffd\ufffd"


class TestFetchErrorMapping:
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (UnsafeURLError("blocked"), WebError.SSRF_BLOCKED),
            (URLFetchTimeout("slow"), WebError.TIMEOUT),
            (RedirectLimitExceeded("loops"), WebError.REDIRECT_LIMIT),
            (ResponseTooLarge("big"), WebError.RESPONSE_TOO_LARGE),
            (ValueError("bad url"), WebError.INVALID_URL),
            (RuntimeError("offline"), WebError.OFFLINE),
        ],
    )
    def test_maps_expected_exceptions_to_web_errors(self, exc: Exception, expected: WebError) -> None:
        # arrange / act
        error = _fetch_error(exc)
        # assert
        assert error is expected


class TestChallengeDetection:
    def test_detects_cloudflare_via_server_header(self) -> None:
        # arrange / act
        signal = _challenge(403, {"server": "cloudflare"}, "")
        # assert
        assert signal is not None
        assert signal.vendor == "cloudflare"
        assert signal.kind == "captcha"
        assert signal.status == 403

    def test_detects_recaptcha_body_marker_as_rate_limited(self) -> None:
        # arrange / act
        signal = _challenge(429, {}, "please complete g-recaptcha first")
        # assert
        assert signal is not None
        assert signal.vendor == "google"
        assert signal.kind == "rate_limited"

    def test_detects_hcaptcha_body_marker(self) -> None:
        # arrange / act
        signal = _challenge(503, {}, "<div class='h-captcha'>")
        # assert
        assert signal is not None
        assert signal.vendor == "hcaptcha"
        assert signal.kind == "captcha"

    @pytest.mark.parametrize("status", [200, 404, 500])
    def test_returns_none_for_ordinary_statuses(self, status: int) -> None:
        # arrange / act
        signal = _challenge(status, {}, "plain page")
        # assert
        assert signal is None


class TestNullSearchProvider:
    def test_unconfigured_provider_returns_no_hits(self) -> None:
        # arrange
        provider = NullSearchProvider()
        # act
        hits = provider.search("anything", top_n=5)
        # assert
        assert provider.configured is False
        assert hits == ()


class TestWebToolkitDefaults:
    def test_defaults_to_secure_fetch(self) -> None:
        # arrange
        toolkit = WebToolkit()
        # act
        fetcher = toolkit._fetcher
        # assert
        assert fetcher is secure_fetch

    def test_default_policy_respects_robots_and_disables_render(self) -> None:
        # arrange / act
        toolkit = WebToolkit()
        # assert
        assert toolkit.policy is DEFAULT_POLICY
        assert DEFAULT_POLICY.respect_robots is True
        assert DEFAULT_POLICY.allow_render is False


class TestFetchRaw:
    def test_rejects_blank_url(self) -> None:
        # arrange
        toolkit = WebToolkit(fetcher=RecordingFetcher())
        # act
        result = toolkit.fetch_raw("   ")
        # assert
        assert result.ok is False
        assert result.error is WebError.INVALID_URL

    def test_rejects_disallowed_method(self) -> None:
        # arrange
        toolkit = WebToolkit(fetcher=RecordingFetcher())
        # act
        result = toolkit.fetch_raw("https://example.com/", method="POST")
        # assert
        assert result.ok is False
        assert result.error is WebError.INVALID_INPUT

    def test_strips_url_and_injects_default_user_agent(self) -> None:
        # arrange
        fetcher = RecordingFetcher()
        toolkit = WebToolkit(fetcher=fetcher)
        # act
        result = toolkit.fetch_raw("  https://example.com/x  ")
        # assert
        assert result.ok is True
        assert fetcher.calls[0][0] == "https://example.com/x"
        assert fetcher.calls[0][2]["User-Agent"] == DEFAULT_POLICY.user_agent

    def test_maps_fetcher_exceptions_to_web_errors(self) -> None:
        # arrange
        fetcher = RecordingFetcher(errors={"https://example.com/": UnsafeURLError("denied")})
        toolkit = WebToolkit(fetcher=fetcher)
        # act
        result = toolkit.fetch_raw("https://example.com/")
        # assert
        assert result.ok is False
        assert result.error is WebError.SSRF_BLOCKED
        assert result.elapsed_ms >= 0.0

    def test_returns_decoded_success_result(self) -> None:
        # arrange
        fetcher = RecordingFetcher(
            responses={"https://example.com/": _result("https://example.com/", content=b"hello world")}
        )
        toolkit = WebToolkit(fetcher=fetcher)
        # act
        result = toolkit.fetch_raw("https://example.com/")
        # assert
        assert result.ok is True
        assert result.body == "hello world"
        assert result.status == 200
        assert result.headers == {"content-type": "text/html"}

    @pytest.mark.parametrize(
        ("status", "expected"),
        [(404, WebError.HTTP_4XX), (500, WebError.HTTP_5XX)],
    )
    def test_maps_http_error_status_codes(self, status: int, expected: WebError) -> None:
        # arrange
        fetcher = RecordingFetcher(responses={"https://example.com/": _result("https://example.com/", status=status)})
        toolkit = WebToolkit(fetcher=fetcher)
        # act
        result = toolkit.fetch_raw("https://example.com/")
        # assert
        assert result.ok is False
        assert result.error is expected
        assert result.detail == f"HTTP {status}"

    def test_reports_challenge_signal_without_bypass(self) -> None:
        # arrange
        fetcher = RecordingFetcher(
            responses={
                "https://example.com/": _result("https://example.com/", status=403, headers={"server": "cloudflare"})
            }
        )
        toolkit = WebToolkit(fetcher=fetcher)
        # act
        result = toolkit.fetch_raw("https://example.com/")
        # assert
        assert result.ok is False
        assert result.error is WebError.CAPTCHA_DETECTED
        assert result.meta["blocked_by"]["vendor"] == "cloudflare"


class TestFetchParsed:
    def test_non_html_content_is_kept_as_plain_text(self) -> None:
        # arrange
        fetcher = RecordingFetcher(
            responses={
                "https://example.com/data": _result(
                    "https://example.com/data",
                    content=b'{"a": 1}',
                    content_type="application/json",
                )
            }
        )
        toolkit = WebToolkit(fetcher=fetcher)
        # act
        result = toolkit.fetch_parsed("https://example.com/data")
        # assert
        assert result.ok is True
        assert result.parsed is not None
        assert result.parsed.text == '{"a": 1}'

    def test_html_is_parsed_into_a_parsed_page(self) -> None:
        # arrange
        body = b"<html><head><title>Hello</title></head><body>World</body></html>"
        fetcher = RecordingFetcher(responses={"https://example.com/": _result("https://example.com/", content=body)})
        toolkit = WebToolkit(fetcher=fetcher)
        # act
        result = toolkit.fetch_parsed("https://example.com/")
        # assert
        assert result.ok is True
        assert result.parsed is not None
        assert result.parsed.title == "Hello"
        assert "World" in result.parsed.text

    def test_parse_failure_maps_to_parse_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # arrange
        def _boom(
            body: object,
            *,
            base_url: str = "",
            status: int | None = None,
            max_links: int = 100,
        ) -> object:
            del body, base_url, status, max_links
            raise ValueError("malformed")

        monkeypatch.setattr("general_ludd.web.toolkit.parse_html", _boom)
        fetcher = RecordingFetcher(responses={"https://example.com/": _result("https://example.com/")})
        toolkit = WebToolkit(fetcher=fetcher)
        # act
        result = toolkit.fetch_parsed("https://example.com/")
        # assert
        assert result.ok is False
        assert result.error is WebError.PARSE_ERROR
        assert result.detail == "malformed"


class TestSearchGather:
    def test_rejects_blank_query(self) -> None:
        # arrange
        toolkit = WebToolkit(fetcher=RecordingFetcher(), search_provider=FakeSearchProvider([]))
        # act
        result = toolkit.search_gather("   ")
        # assert
        assert result.ok is False
        assert result.error is WebError.INVALID_INPUT

    @pytest.mark.parametrize("top_n", [0, -1, True])
    def test_rejects_non_positive_top_n(self, top_n: int) -> None:
        # arrange
        toolkit = WebToolkit(fetcher=RecordingFetcher(), search_provider=FakeSearchProvider([]))
        # act
        result = toolkit.search_gather("query", top_n=top_n)
        # assert
        assert result.ok is False
        assert result.error is WebError.INVALID_INPUT

    def test_unconfigured_provider_is_reported(self) -> None:
        # arrange
        toolkit = WebToolkit(fetcher=RecordingFetcher())
        # act
        result = toolkit.search_gather("query")
        # assert
        assert result.ok is False
        assert result.error is WebError.PROVIDER_UNCONFIGURED
        assert result.meta["provider_state"] == "unconfigured"

    def test_provider_failure_is_mapped(self) -> None:
        # arrange
        provider = FakeSearchProvider([], error=RuntimeError("search down"))
        toolkit = WebToolkit(fetcher=RecordingFetcher(), search_provider=provider)
        # act
        result = toolkit.search_gather("query")
        # assert
        assert result.ok is False
        assert result.error is WebError.PROVIDER_UNCONFIGURED
        assert result.meta["provider_state"] == "failed"

    def test_returns_hits_without_fetching_when_disabled(self) -> None:
        # arrange
        hits = [
            SearchHit(url="https://a.example.com/", title="A"),
            SearchHit(url="https://b.example.com/", title="B"),
        ]
        provider = FakeSearchProvider(hits)
        fetcher = RecordingFetcher()
        toolkit = WebToolkit(fetcher=fetcher, search_provider=provider)
        # act
        result = toolkit.search_gather("query", fetch_results=False)
        # assert
        assert result.ok is True
        assert result.results == []
        assert [hit.url for hit in result.hits] == [
            "https://a.example.com/",
            "https://b.example.com/",
        ]
        assert result.meta["hit_count"] == 2
        assert fetcher.calls == []

    def test_gathers_partial_results_and_collects_errors(self) -> None:
        # arrange
        hits = [
            SearchHit(url="https://a.example.com/", title="A"),
            SearchHit(url="https://b.example.com/", title="B"),
        ]
        provider = FakeSearchProvider(hits)
        fetcher = RecordingFetcher(
            responses={
                "https://a.example.com/": _result("https://a.example.com/", content=b"<html><body>alpha</body></html>"),
                "https://b.example.com/": _result("https://b.example.com/", status=404),
            }
        )
        toolkit = WebToolkit(fetcher=fetcher, search_provider=provider)
        # act
        result = toolkit.search_gather("query", top_n=2)
        # assert
        assert provider.queries == [("query", 2)]
        assert result.ok is True
        assert result.error is None
        assert result.gathered == 1
        assert result.failed == 1
        assert result.errors == ["HTTP 404"]
        assert result.results is not None
        assert result.results[0].ok is True
        assert result.results[1].ok is False


class TestRobotsHandling:
    def test_robots_disabled_by_policy(self) -> None:
        # arrange
        policy = WebPolicy(respect_robots=False)
        toolkit = WebToolkit(policy=policy, fetcher=RecordingFetcher())
        # act
        parser, failure, state = toolkit._robots_for("https://example.com/")
        # assert
        assert parser is None
        assert failure is None
        assert state == "disabled"

    def test_missing_robots_file_is_tolerated(self) -> None:
        # arrange
        fetcher = RecordingFetcher(
            responses={"https://example.com/robots.txt": _result("https://example.com/robots.txt", status=404)}
        )
        toolkit = WebToolkit(fetcher=fetcher)
        # act
        parser, failure, state = toolkit._robots_for("https://example.com/")
        # assert
        assert parser is None
        assert failure is None
        assert state == "missing"

    def test_unavailable_robots_fails_closed(self) -> None:
        # arrange
        fetcher = RecordingFetcher(errors={"https://example.com/robots.txt": UnsafeURLError("denied")})
        toolkit = WebToolkit(fetcher=fetcher)
        # act
        parser, failure, state = toolkit._robots_for("https://example.com/")
        # assert
        assert parser is None
        assert state == "unavailable_fail_closed"
        assert failure is not None
        assert failure.error is WebError.ROBOTS_DISALLOWED
        assert failure.meta["robots_state"] == "unavailable_fail_closed"

    def test_unavailable_robots_fails_open(self) -> None:
        # arrange
        policy = WebPolicy(robots_fail_closed=False)
        fetcher = RecordingFetcher(errors={"https://example.com/robots.txt": UnsafeURLError("denied")})
        toolkit = WebToolkit(policy=policy, fetcher=fetcher)
        # act
        parser, failure, state = toolkit._robots_for("https://example.com/")
        # assert
        assert parser is None
        assert failure is None
        assert state == "unavailable_fail_open"

    def test_loads_valid_robots_policy(self) -> None:
        # arrange
        robots_body = b"User-agent: *\nDisallow: /private\n"
        fetcher = RecordingFetcher(
            responses={"https://example.com/robots.txt": _result("https://example.com/robots.txt", content=robots_body)}
        )
        toolkit = WebToolkit(fetcher=fetcher)
        # act
        parser, failure, state = toolkit._robots_for("https://example.com/")
        # assert
        assert failure is None
        assert state == "loaded"
        assert parser is not None
        assert parser.can_fetch(DEFAULT_POLICY.user_agent, "https://example.com/allowed") is True
        assert parser.can_fetch(DEFAULT_POLICY.user_agent, "https://example.com/private/x") is False


class TestCrawlSite:
    def test_rejects_non_https_seed(self) -> None:
        # arrange
        toolkit = WebToolkit(policy=WebPolicy(respect_robots=False), fetcher=RecordingFetcher())
        # act
        result = toolkit.crawl_site("http://example.com")
        # assert
        assert result.ok is False
        assert result.error is WebError.INVALID_URL

    def test_rejects_boolean_page_limit(self) -> None:
        # arrange
        toolkit = WebToolkit(policy=WebPolicy(respect_robots=False), fetcher=RecordingFetcher())
        # act
        result = toolkit.crawl_site("https://example.com/", max_pages=True)
        # assert
        assert result.ok is False
        assert result.error is WebError.INVALID_INPUT

    def test_rejects_boolean_depth_limit(self) -> None:
        # arrange
        toolkit = WebToolkit(policy=WebPolicy(respect_robots=False), fetcher=RecordingFetcher())
        # act
        result = toolkit.crawl_site("https://example.com/", max_depth=True)
        # assert
        assert result.ok is False
        assert result.error is WebError.INVALID_INPUT

    def test_robots_disallow_blocks_crawl(self) -> None:
        # arrange
        robots_body = b"User-agent: *\nDisallow: /\n"
        fetcher = RecordingFetcher(
            responses={"https://example.com/robots.txt": _result("https://example.com/robots.txt", content=robots_body)}
        )
        toolkit = WebToolkit(fetcher=fetcher)
        # act
        result = toolkit.crawl_site("https://example.com/")
        # assert
        assert result.ok is False
        assert result.error is WebError.ROBOTS_DISALLOWED
        assert result.meta["robots_state"] == "denied"

    def test_bounded_bfs_crawl_counts_off_domain_links(self) -> None:
        # arrange
        page_a = "https://example.com/a"
        page_b = "https://example.com/b"
        page_c = "https://example.com/c"
        fetcher = RecordingFetcher(
            responses={
                _SEED: _result(
                    _SEED,
                    content=(
                        "<html><body>seed"
                        f'<a href="{page_a}">a</a>'
                        f'<a href="{page_b}">b</a>'
                        '<a href="https://other.example/x">off</a>'
                        "</body></html>"
                    ).encode(),
                ),
                page_a: _result(page_a, content=f'<html><body><a href="{page_c}">c</a></body></html>'.encode()),
                page_b: _result(page_b, content=b"<html><body>b</body></html>"),
                page_c: _result(page_c, content=b"<html><body>c</body></html>"),
            }
        )
        policy = WebPolicy(respect_robots=False, min_request_interval_seconds=0.0)
        toolkit = WebToolkit(policy=policy, fetcher=fetcher)
        # act
        result = toolkit.crawl_site(_SEED)
        # assert
        assert result.ok is True
        assert result.visited == [_SEED, page_a, page_b, page_c]
        assert len(result.pages) == 4
        assert result.truncated is False
        assert result.stats["off_domain"] == 1
        assert result.stats["pages"] == 4
        assert result.meta["robots_state"] == "disabled"

    def test_depth_limit_prunes_link_discovery(self) -> None:
        # arrange
        fetcher = RecordingFetcher(
            responses={
                _SEED: _result(_SEED, content=b'<html><body><a href="https://example.com/a">a</a></body></html>')
            }
        )
        policy = WebPolicy(respect_robots=False, min_request_interval_seconds=0.0)
        toolkit = WebToolkit(policy=policy, fetcher=fetcher)
        # act
        result = toolkit.crawl_site(_SEED, max_depth=0)
        # assert
        assert result.ok is True
        assert result.visited == [_SEED]
        assert len(result.pages) == 1
        assert result.stats["depth_limit"] == 0

    def test_ssrf_blocked_pages_are_skipped_and_counted(self) -> None:
        # arrange
        policy = WebPolicy(respect_robots=False, min_request_interval_seconds=0.0)
        fetcher = RecordingFetcher(errors={_SEED: UnsafeURLError("internal")})
        toolkit = WebToolkit(policy=policy, fetcher=fetcher)
        # act
        result = toolkit.crawl_site(_SEED)
        # assert
        assert result.ok is False
        assert result.error is WebError.OFFLINE
        assert result.stats["blocked"] == 1
        assert result.skipped[0]["reason"] == "ssrf_blocked"

    def test_all_failures_report_offline(self) -> None:
        # arrange
        policy = WebPolicy(respect_robots=False, min_request_interval_seconds=0.0)
        fetcher = RecordingFetcher(responses={_SEED: _result(_SEED, status=404)})
        toolkit = WebToolkit(policy=policy, fetcher=fetcher)
        # act
        result = toolkit.crawl_site(_SEED)
        # assert
        assert result.ok is False
        assert result.error is WebError.OFFLINE
        assert result.errors == ["HTTP 404"]

    def test_deadline_exceeded_truncates_crawl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # arrange
        policy = WebPolicy(respect_robots=False, crawl_timeout_seconds=30.0)
        toolkit = WebToolkit(policy=policy, fetcher=RecordingFetcher())
        clock = iter([100.0, 130.5, 131.0])
        monkeypatch.setattr("general_ludd.web.toolkit.time.monotonic", lambda: next(clock))
        # act
        result = toolkit.crawl_site(_SEED)
        # assert
        assert result.ok is False
        assert result.error is WebError.CRAWL_TIMEOUT
        assert result.truncated is True
        assert result.detail == "crawl deadline exceeded"


class TestRenderJs:
    def test_policy_disabled_rendering(self) -> None:
        # arrange
        toolkit = WebToolkit(
            policy=WebPolicy(allow_render=False),
            fetcher=RecordingFetcher(),
            renderer=FakeRenderer(),
        )
        # act
        result = toolkit.render_js("https://example.com/")
        # assert
        assert result.ok is False
        assert result.error is WebError.RENDERER_UNAVAILABLE
        assert result.meta["renderer_state"] == "disabled"

    def test_missing_renderer_is_unavailable(self) -> None:
        # arrange
        toolkit = WebToolkit(policy=WebPolicy(allow_render=True), fetcher=RecordingFetcher())
        # act
        result = toolkit.render_js("https://example.com/")
        # assert
        assert result.ok is False
        assert result.error is WebError.RENDERER_UNAVAILABLE
        assert result.meta["renderer_state"] == "unavailable"

    def test_renderer_timeout_is_mapped(self) -> None:
        # arrange
        renderer = FakeRenderer(error=TimeoutError("slow"))
        toolkit = WebToolkit(
            policy=WebPolicy(allow_render=True),
            fetcher=RecordingFetcher(),
            renderer=renderer,
        )
        # act
        result = toolkit.render_js("https://example.com/")
        # assert
        assert result.ok is False
        assert result.error is WebError.TIMEOUT
        assert result.meta["renderer_state"] == "timeout"

    def test_non_string_output_is_rejected(self) -> None:
        # arrange
        toolkit = WebToolkit(
            policy=WebPolicy(allow_render=True),
            fetcher=RecordingFetcher(),
            renderer=_NonStringRenderer(),
        )
        # act
        result = toolkit.render_js("https://example.com/")
        # assert
        assert result.ok is False
        assert result.error is WebError.RENDERER_UNAVAILABLE
        assert result.meta["renderer_state"] == "invalid_output"

    def test_oversized_rendered_document_is_rejected(self) -> None:
        # arrange
        policy = WebPolicy(allow_render=True, max_render_bytes=10)
        renderer = FakeRenderer(output="x" * 100)
        toolkit = WebToolkit(policy=policy, fetcher=RecordingFetcher(), renderer=renderer)
        # act
        result = toolkit.render_js("https://example.com/")
        # assert
        assert result.ok is False
        assert result.error is WebError.RESPONSE_TOO_LARGE
        assert result.meta["renderer_state"] == "output_too_large"

    def test_successful_render_parses_offline_html(self) -> None:
        # arrange
        rendered = "<html><head><title>Rendered</title></head><body>hi</body></html>"
        policy = WebPolicy(allow_render=True)
        renderer = FakeRenderer(output=rendered)
        fetcher = RecordingFetcher(responses={_SEED: _result(_SEED)})
        toolkit = WebToolkit(policy=policy, fetcher=fetcher, renderer=renderer)
        # act
        result = toolkit.render_js("https://example.com/")
        # assert
        assert result.ok is True
        assert result.html == rendered
        assert result.parsed is not None
        assert result.parsed.title == "Rendered"
        assert result.meta["renderer_state"] == "offline"
