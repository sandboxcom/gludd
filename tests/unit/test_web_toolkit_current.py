"""Current-development contract for the reconciled bounded web toolkit."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

import general_ludd.web.crawl as crawl_compat
import general_ludd.web.render as render_compat
import general_ludd.web.results as results_compat
import general_ludd.web.search as search_compat
from general_ludd.mcp.builtins import (
    BUILTIN_SERVER_ID,
    WEB_CRAWL_TOOL,
    WEB_FETCH_PARSED_TOOL,
    WEB_FETCH_TOOL,
    WEB_RENDER_TOOL,
    WEB_SEARCH_TOOL,
    BuiltinToolHandler,
    register_builtins,
)
from general_ludd.security.url_fetch import (
    FetchPolicy,
    FetchResult,
    RedirectLimitExceeded,
    ResponseTooLarge,
    UnsafeURLError,
    URLFetchTimeout,
)
from general_ludd.web import (
    DEFAULT_POLICY,
    Link,
    SearchHit,
    WebError,
    WebPolicy,
    WebToolkit,
    parse_html,
)
from general_ludd.web.tools import (
    TOOL_SPECS,
    crawl_site,
    fetch_parsed,
    fetch_raw,
    render_js,
    search_gather,
)


def _response(
    url: str,
    body: str = "<html><title>Example</title><body>hello</body></html>",
    *,
    status: int = 200,
    content_type: str = "text/html; charset=utf-8",
) -> FetchResult:
    return FetchResult(
        url=url,
        status_code=status,
        headers={"content-type": content_type},
        content=body.encode(),
    )


class _FakeFetcher:
    def __init__(self, responses: Mapping[str, FetchResult | BaseException]) -> None:
        self.responses = dict(responses)
        self.calls: list[tuple[str, FetchPolicy, str, Mapping[str, str]]] = []

    def __call__(
        self,
        url: str,
        *,
        policy: FetchPolicy,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        content: bytes | str | None = None,
    ) -> FetchResult:
        del content
        self.calls.append((url, policy, method, headers or {}))
        response = self.responses[url]
        if isinstance(response, BaseException):
            raise response
        return response


class _Provider:
    configured = True

    def __init__(self, hits: Sequence[SearchHit], error: RuntimeError | None = None) -> None:
        self.hits = list(hits)
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, top_n: int) -> list[SearchHit]:
        self.calls.append((query, top_n))
        if self.error is not None:
            raise self.error
        return self.hits[:top_n]


class _Renderer:
    def __init__(self, output: object) -> None:
        self.output = output
        self.calls: list[tuple[str, str, float]] = []

    def render_offline(self, html: str, *, base_url: str, timeout_seconds: float) -> Any:
        self.calls.append((html, base_url, timeout_seconds))
        if isinstance(self.output, BaseException):
            raise self.output
        return self.output


def _policy(**changes: Any) -> WebPolicy:
    values: dict[str, Any] = {
        "min_request_interval_seconds": 0.0,
        "crawl_timeout_seconds": 5.0,
    }
    values.update(changes)
    return WebPolicy(**values)


def test_policy_delegates_destination_security_to_current_fetch_boundary() -> None:
    policy = WebPolicy(allowed_hosts=frozenset({"EXAMPLE.com"}), max_bytes=1234)

    fetch_policy = policy.fetch_policy()

    assert fetch_policy.allowed_hosts == frozenset({"example.com"})
    assert fetch_policy.allowed_schemes == frozenset({"https"})
    assert fetch_policy.max_bytes == 1234
    assert DEFAULT_POLICY.max_pages <= 100


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_bytes", 0),
        ("timeout_seconds", "slow"),
        ("timeout_seconds", float("inf")),
        ("max_redirects", 11),
        ("max_pages", 101),
        ("max_depth", 6),
        ("max_links_per_page", 501),
        ("crawl_timeout_seconds", 0.0),
        ("min_request_interval_seconds", -1.0),
        ("respect_robots", 1),
        ("allow_render", 1),
        ("user_agent", ""),
        ("user_agent", "x" * 257),
    ],
)
def test_policy_rejects_unbounded_or_ambiguous_values(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        WebPolicy(**cast(Any, {field: value}))


def test_parse_html_is_bounded_tolerant_and_filters_active_links() -> None:
    page = parse_html(
        """
        <html lang='en'><head><title> A   title </title>
        <meta name='description' content='desc'><style>secret</style></head>
        <body><h1>Heading</h1><script>hidden()</script><p>Visible</p>
        <a href='/one'>one</a><a href='/one'>dup</a>
        <a href='javascript:alert(1)'>bad</a><a href='https://other.test/two#x'>two</a>
        """,
        base_url="https://example.com/start",
        status=200,
        max_links=2,
    )

    assert page.normalized_title() == "A title"
    assert page.lang == "en"
    assert page.status == 200
    assert "Visible" in page.text and "hidden" not in page.text and "secret" not in page.text
    assert page.headings == ["Heading"]
    assert page.meta["description"] == "desc"
    assert [link.href for link in page.links] == [
        "https://example.com/one",
        "https://other.test/two",
    ]
    assert "https://example.com/one" in page.links


def test_parse_html_malformed_and_empty_documents_degrade_without_error() -> None:
    malformed = parse_html(
        "<html><body><p>unclosed <a href=/next>link",
        base_url="https://example.com/",
    )
    empty = parse_html("", base_url="https://example.com/")

    assert "unclosed" in malformed.text
    assert malformed.links[0].href == "https://example.com/next"
    assert empty.title is None and empty.text == "" and empty.links == []


def test_compatibility_modules_share_the_current_models_and_primitives() -> None:
    link = Link(href="https://example.com/path", text="path")

    assert str(link) == link.href
    assert "example.com" in link
    assert link == Link(href=link.href, text="path")
    assert hash(link) == hash((link.href, link.text))
    assert crawl_compat.CrawlPolicy is WebPolicy
    assert render_compat.render_page is render_compat.render_js
    assert search_compat.NullProvider is search_compat.NullSearchProvider
    assert results_compat.WebResult.__name__ == "WebResult"


@pytest.mark.parametrize("url", ["http://example.com/", "https://user:x@example.com/", "https://[::1"])
def test_crawl_url_normalization_rejects_unsafe_or_malformed_shapes(url: str) -> None:
    assert crawl_compat.normalize_url(url) is None


def test_fetch_raw_uses_bounded_current_fetcher_and_decodes_charset() -> None:
    url = "https://example.com/data"
    fetcher = _FakeFetcher(
        {
            url: FetchResult(
                url=url,
                status_code=200,
                headers={"content-type": "text/plain; charset=latin-1"},
                content="café".encode("latin-1"),
            )
        }
    )
    toolkit = WebToolkit(policy=_policy(), fetcher=fetcher)

    result = toolkit.fetch_raw(url)

    assert result.ok is True
    assert result.body == "café"
    assert result.final_url == url
    assert fetcher.calls[0][1].allowed_schemes == frozenset({"https"})
    assert fetcher.calls[0][3]["User-Agent"] == toolkit.policy.user_agent


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (UnsafeURLError("private destination"), WebError.SSRF_BLOCKED),
        (URLFetchTimeout("slow"), WebError.TIMEOUT),
        (RedirectLimitExceeded("loop"), WebError.REDIRECT_LIMIT),
        (ResponseTooLarge("large"), WebError.RESPONSE_TOO_LARGE),
        (OSError("offline"), WebError.OFFLINE),
    ],
)
def test_fetch_raw_maps_current_fetch_failures(
    failure: BaseException,
    error: WebError,
) -> None:
    url = "https://example.com/"
    result = WebToolkit(policy=_policy(), fetcher=_FakeFetcher({url: failure})).fetch_raw(url)

    assert result.ok is False
    assert result.error is error
    assert result.url == url


@pytest.mark.parametrize(
    ("status", "error"),
    [(404, WebError.HTTP_4XX), (503, WebError.HTTP_5XX)],
)
def test_fetch_raw_maps_terminal_http_status(status: int, error: WebError) -> None:
    url = "https://example.com/"
    result = WebToolkit(
        policy=_policy(),
        fetcher=_FakeFetcher({url: _response(url, status=status)}),
    ).fetch_raw(url)

    assert result.ok is False
    assert result.error is error
    assert result.status == status


def test_fetch_raw_detects_bounded_challenge_without_attempting_bypass() -> None:
    url = "https://example.com/"
    fetcher = _FakeFetcher(
        {
            url: FetchResult(
                url=url,
                status_code=403,
                headers={"server": "cloudflare"},
                content=b"Just a moment... cf-mitigated",
            )
        }
    )

    result = WebToolkit(policy=_policy(), fetcher=fetcher).fetch_raw(url)

    assert result.error is WebError.CAPTCHA_DETECTED
    assert result.meta["blocked_by"]["vendor"] == "cloudflare"
    assert len(fetcher.calls) == 1


def test_fetch_rejects_empty_or_mutating_requests_before_network() -> None:
    fetcher = _FakeFetcher({})
    toolkit = WebToolkit(policy=_policy(), fetcher=fetcher)

    empty = toolkit.fetch_raw("")
    mutating = toolkit.fetch_raw("https://example.com/", method="POST")

    assert empty.error is WebError.INVALID_URL
    assert mutating.error is WebError.INVALID_INPUT
    assert fetcher.calls == []


def test_fetch_parsed_keeps_non_html_as_bounded_text() -> None:
    url = "https://example.com/plain"
    toolkit = WebToolkit(
        policy=_policy(),
        fetcher=_FakeFetcher({url: _response(url, "plain data", content_type="text/plain")}),
    )

    result = toolkit.fetch_parsed(url)

    assert result.ok is True
    assert result.parsed is not None
    assert result.parsed.text == "plain data"
    assert result.parsed.links == []


def test_search_distinguishes_unconfigured_provider_from_zero_results() -> None:
    toolkit = WebToolkit(policy=_policy())
    unconfigured = toolkit.search_gather("query")
    configured = WebToolkit(policy=_policy(), search_provider=_Provider([])).search_gather("query")

    assert unconfigured.ok is False
    assert unconfigured.error is WebError.PROVIDER_UNCONFIGURED
    assert unconfigured.meta["provider_state"] == "unconfigured"
    assert configured.ok is True
    assert configured.results == []


def test_search_gathers_partial_results_and_respects_policy_cap() -> None:
    good = "https://example.com/good"
    blocked = "https://example.com/blocked"
    provider = _Provider(
        [SearchHit(url=good, title="good"), SearchHit(url=blocked, title="blocked")]
    )
    toolkit = WebToolkit(
        policy=_policy(max_search_results=2),
        fetcher=_FakeFetcher(
            {good: _response(good), blocked: UnsafeURLError("blocked by DNS policy")}
        ),
        search_provider=provider,
    )

    result = toolkit.search_gather(" query ", top_n=50)

    assert result.ok is True
    assert provider.calls == [("query", 2)]
    assert result.gathered == 1 and result.failed == 1
    assert result.results is not None
    assert [page.ok for page in result.results] == [True, False]


def test_search_provider_failure_and_hits_only_are_structured() -> None:
    failing = WebToolkit(
        policy=_policy(),
        search_provider=_Provider([], RuntimeError("provider offline")),
    ).search_gather("query")
    hits_only = WebToolkit(
        policy=_policy(),
        search_provider=_Provider([SearchHit(url="https://example.com", title="Example")]),
    ).search_gather("query", fetch_results=False)

    assert failing.ok is False and failing.error is WebError.PROVIDER_UNCONFIGURED
    assert hits_only.ok is True and hits_only.hits[0].title == "Example"
    assert hits_only.gathered == 0


def test_search_rejects_invalid_inputs_and_reports_all_hit_failures() -> None:
    url = "https://example.com/fail"
    toolkit = WebToolkit(
        policy=_policy(),
        fetcher=_FakeFetcher({url: OSError("offline")}),
        search_provider=_Provider([SearchHit(url=url)]),
    )

    empty = toolkit.search_gather(" ")
    invalid_limit = toolkit.search_gather("query", top_n=0)
    failed = toolkit.search_gather("query")

    assert empty.error is WebError.INVALID_INPUT
    assert invalid_limit.error is WebError.INVALID_INPUT
    assert failed.ok is False and failed.error is WebError.OFFLINE


def test_crawl_is_robots_aware_same_host_breadth_first_and_bounded() -> None:
    root = "https://example.com/"
    robots = "https://example.com/robots.txt"
    first = "https://example.com/a"
    second = "https://example.com/b"
    fetcher = _FakeFetcher(
        {
            robots: _response(robots, "User-agent: *\nAllow: /\n", content_type="text/plain"),
            root: _response(
                root,
                "<a href='/a'>a</a><a href='/b'>b</a><a href='https://other.test/x'>x</a>",
            ),
            first: _response(first, "<p>first</p>"),
            second: _response(second, "<p>second</p>"),
        }
    )
    toolkit = WebToolkit(policy=_policy(max_pages=2, max_depth=2), fetcher=fetcher)

    result = toolkit.crawl_site(root)

    assert result.ok is True
    assert result.visited == [root, first]
    assert len(result.pages) == 2
    assert result.truncated is True
    assert result.stats["off_domain"] == 1
    assert second not in result.visited


def test_crawl_robots_denial_and_unavailable_policy_fail_closed() -> None:
    root = "https://example.com/private"
    robots = "https://example.com/robots.txt"
    denied = WebToolkit(
        policy=_policy(),
        fetcher=_FakeFetcher(
            {
                robots: _response(
                    robots,
                    "User-agent: *\nDisallow: /private\n",
                    content_type="text/plain",
                )
            }
        ),
    ).crawl_site(root)
    unavailable = WebToolkit(
        policy=_policy(),
        fetcher=_FakeFetcher({robots: OSError("offline")}),
    ).crawl_site(root)

    assert denied.ok is False and denied.error is WebError.ROBOTS_DISALLOWED
    assert denied.visited == []
    assert unavailable.ok is False and unavailable.error is WebError.ROBOTS_DISALLOWED
    assert unavailable.meta["robots_state"] == "unavailable_fail_closed"


def test_crawl_missing_robots_file_is_allowed_but_never_unbounded() -> None:
    root = "https://example.com/"
    robots = "https://example.com/robots.txt"
    toolkit = WebToolkit(
        policy=_policy(max_pages=1),
        fetcher=_FakeFetcher(
            {robots: _response(robots, status=404), root: _response(root, "<p>ok</p>")}
        ),
    )

    result = toolkit.crawl_site(root, max_pages=999, max_depth=999)

    assert result.ok is True
    assert len(result.pages) == 1
    assert result.stats["page_limit"] == 1


def test_crawl_can_disable_robots_but_rejects_invalid_limits_and_fetch_failures() -> None:
    root = "https://example.com/"
    toolkit = WebToolkit(
        policy=_policy(respect_robots=False),
        fetcher=_FakeFetcher({root: UnsafeURLError("blocked")}),
    )

    invalid_seed = toolkit.crawl_site("http://example.com/")
    invalid_pages = toolkit.crawl_site(root, max_pages=True)
    invalid_depth = toolkit.crawl_site(root, max_depth=cast(Any, "two"))
    failed = toolkit.crawl_site(root)

    assert invalid_seed.error is WebError.INVALID_URL
    assert invalid_pages.error is WebError.INVALID_INPUT
    assert invalid_depth.error is WebError.INVALID_INPUT
    assert failed.error is WebError.OFFLINE
    assert failed.stats["blocked"] == 1
    assert failed.meta["robots_state"] == "disabled"


def test_render_is_disabled_or_unavailable_without_touching_network() -> None:
    url = "https://example.com/"
    disabled_fetcher = _FakeFetcher({})
    disabled = WebToolkit(policy=_policy(), fetcher=disabled_fetcher).render_js(url)
    unavailable_fetcher = _FakeFetcher({})
    unavailable = WebToolkit(
        policy=_policy(allow_render=True),
        fetcher=unavailable_fetcher,
    ).render_js(url)

    assert disabled.error is WebError.RENDERER_UNAVAILABLE
    assert unavailable.error is WebError.RENDERER_UNAVAILABLE
    assert disabled_fetcher.calls == unavailable_fetcher.calls == []


def test_render_only_processes_a_securely_prefetched_document_offline() -> None:
    url = "https://example.com/"
    renderer = _Renderer("<html><title>Rendered</title><body>dynamic</body></html>")
    toolkit = WebToolkit(
        policy=_policy(allow_render=True),
        fetcher=_FakeFetcher({url: _response(url, "<html>static</html>")}),
        renderer=renderer,
    )

    result = toolkit.render_js(url)

    assert result.ok is True
    assert result.html is not None and "dynamic" in result.html
    assert result.parsed is not None and result.parsed.title == "Rendered"
    assert renderer.calls[0][1] == url


@pytest.mark.parametrize(
    ("output", "error"),
    [
        (RuntimeError("renderer crashed"), WebError.RENDERER_UNAVAILABLE),
        ("x" * 1025, WebError.RESPONSE_TOO_LARGE),
    ],
)
def test_render_failure_and_output_limit_are_structured(
    output: str | BaseException,
    error: WebError,
) -> None:
    url = "https://example.com/"
    toolkit = WebToolkit(
        policy=_policy(allow_render=True, max_render_bytes=1024),
        fetcher=_FakeFetcher({url: _response(url)}),
        renderer=_Renderer(output),
    )

    assert toolkit.render_js(url).error is error


@pytest.mark.parametrize(
    ("fetch_or_render", "expected", "renderer_state"),
    [
        (UnsafeURLError("blocked"), WebError.SSRF_BLOCKED, "prefetch_failed"),
        (TimeoutError("renderer timeout"), WebError.TIMEOUT, "timeout"),
        (object(), WebError.RENDERER_UNAVAILABLE, "invalid_output"),
    ],
)
def test_render_prefetch_timeout_and_invalid_output_fail_closed(
    fetch_or_render: object,
    expected: WebError,
    renderer_state: str,
) -> None:
    url = "https://example.com/"
    if isinstance(fetch_or_render, UnsafeURLError):
        fetcher = _FakeFetcher({url: fetch_or_render})
        renderer: _Renderer | None = _Renderer("unused")
    else:
        fetcher = _FakeFetcher({url: _response(url)})
        renderer = _Renderer(fetch_or_render)
    result = WebToolkit(
        policy=_policy(allow_render=True),
        fetcher=fetcher,
        renderer=renderer,
    ).render_js(url)

    assert result.error is expected
    assert result.meta["renderer_state"] == renderer_state


def test_flat_tools_preserve_injection_and_self_describe_bounded_operations() -> None:
    url = "https://example.com/"
    toolkit = WebToolkit(policy=_policy(), fetcher=_FakeFetcher({url: _response(url)}))

    result = fetch_raw(url, toolkit=toolkit)

    assert result.ok is True
    assert set(TOOL_SPECS) == {
        "web_fetch",
        "web_fetch_parsed",
        "web_search",
        "web_crawl",
        "web_render",
    }


def test_flat_tool_adapters_cover_every_current_operation() -> None:
    url = "https://example.com/"
    robots = "https://example.com/robots.txt"
    fetcher = _FakeFetcher(
        {
            url: _response(url),
            robots: _response(robots, status=404),
        }
    )
    provider = _Provider([SearchHit(url=url)])
    renderer = _Renderer("<html><body>rendered</body></html>")
    toolkit = WebToolkit(
        policy=_policy(allow_render=True),
        fetcher=fetcher,
        search_provider=provider,
        renderer=renderer,
    )

    assert fetch_raw(url, policy=_policy(), fetcher=fetcher).ok is True
    assert fetch_parsed(url, toolkit=toolkit).parsed is not None
    assert search_gather("query", toolkit=toolkit).gathered == 1
    assert crawl_site(url, toolkit=toolkit, max_pages=1).ok is True
    assert render_js(url, toolkit=toolkit).ok is True


@pytest.mark.asyncio
async def test_builtin_handler_routes_web_tools_without_blocking_the_event_loop() -> None:
    url = "https://example.com/"
    toolkit = WebToolkit(policy=_policy(), fetcher=_FakeFetcher({url: _response(url)}))
    handler = BuiltinToolHandler(web_toolkit=toolkit)

    result = await handler("web_fetch_parsed", {"url": url})
    invalid = await handler("web_fetch", {"url": url, "method": "POST"})

    assert result["ok"] is True and result["parsed"]["title"] == "Example"
    assert invalid["ok"] is False and invalid["error"] == "invalid_input"


@pytest.mark.asyncio
async def test_builtin_handler_validates_and_routes_all_web_tool_shapes() -> None:
    url = "https://example.com/"
    toolkit = WebToolkit(policy=_policy())
    handler = BuiltinToolHandler(web_toolkit=toolkit)

    missing_query = await handler("web_search", {})
    bad_top_n = await handler("web_search", {"query": "q", "top_n": "many"})
    bad_fetch = await handler("web_search", {"query": "q", "fetch_results": "yes"})
    search = await handler("web_search", {"query": "q", "top_n": 2})
    missing_crawl = await handler("web_crawl", {})
    bad_pages = await handler("web_crawl", {"seed_url": url, "max_pages": "many"})
    bad_depth = await handler("web_crawl", {"seed_url": url, "max_depth": "deep"})
    render = await handler("web_render", {"url": url})
    missing_parse = await handler("web_fetch_parsed", {})
    unknown = await handler._run_web_tool("unexpected", {"url": url})

    assert missing_query["error"] == "invalid_input"
    assert bad_top_n["error"] == bad_fetch["error"] == "invalid_input"
    assert search["error"] == "provider_unconfigured"
    assert missing_crawl["error"] == bad_pages["error"] == bad_depth["error"] == "invalid_input"
    assert render["error"] == "renderer_unavailable"
    assert missing_parse["error"] == unknown["error"] == "invalid_input"


def test_builtin_registration_uses_current_synthetic_mcp_path() -> None:
    client = MagicMock()
    toolkit = WebToolkit(policy=_policy())

    register_builtins(client, web_toolkit=toolkit)

    server_id, tools, handler = client.register_builtin.call_args.args
    assert server_id == BUILTIN_SERVER_ID
    assert {tool.name for tool in tools}.issuperset(
        {
            WEB_FETCH_TOOL.name,
            WEB_FETCH_PARSED_TOOL.name,
            WEB_SEARCH_TOOL.name,
            WEB_CRAWL_TOOL.name,
            WEB_RENDER_TOOL.name,
        }
    )
    assert handler._web_toolkit is toolkit
