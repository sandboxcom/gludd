"""Deep tests for ``src/general_ludd/web/tools.py``.

Covers the flat injectable model-facing web functions: ``TOOL_SPECS``,
``fetch_raw``, ``fetch_parsed``, ``search_gather``, ``crawl_site``, and
``render_js`` — including parameter forwarding, toolkit-injection precedence,
defaults, and end-to-end wiring through a real ``WebToolkit`` with a fake
fetcher.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from general_ludd.security.url_fetch import FetchPolicy, FetchResult, URLFetchTimeout
from general_ludd.web.policy import WebPolicy
from general_ludd.web.toolkit import WebToolkit
from general_ludd.web.tools import (
    TOOL_SPECS,
    crawl_site,
    fetch_parsed,
    fetch_raw,
    render_js,
    search_gather,
)
from general_ludd.web.types import SearchHit, WebResult


class FakeToolkit:
    """Recording stand-in for ``WebToolkit`` capturing forwarded calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _record(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> WebResult:
        self.calls.append((name, args, kwargs))
        return WebResult(ok=True)

    def fetch_raw(self, url: str, *, method: str = "GET", headers: dict[str, str] | None = None) -> WebResult:
        return self._record("fetch_raw", (url,), {"method": method, "headers": headers})

    def fetch_parsed(self, url: str) -> WebResult:
        return self._record("fetch_parsed", (url,), {})

    def search_gather(self, query: str, *, top_n: int = 5, fetch_results: bool = True) -> WebResult:
        return self._record(
            "search_gather",
            (query,),
            {"top_n": top_n, "fetch_results": fetch_results},
        )

    def crawl_site(self, seed_url: str, *, max_pages: int | None = None, max_depth: int | None = None) -> WebResult:
        return self._record(
            "crawl_site",
            (seed_url,),
            {"max_pages": max_pages, "max_depth": max_depth},
        )

    def render_js(self, url: str) -> WebResult:
        return self._record("render_js", (url,), {})


def make_fetcher(status_code: int = 200, body: bytes = b"<p>hello</p>") -> Any:
    """Return a callable fetcher producing a fixed successful response."""

    def fetcher(
        url: str,
        *,
        policy: FetchPolicy,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        content: bytes | str | None = None,
    ) -> FetchResult:
        del policy, content
        return FetchResult(
            url=url,
            status_code=status_code,
            headers={"content-type": "text/html; charset=utf-8", "user-agent": (headers or {}).get("User-Agent", "")},
            content=body,
        )

    return fetcher


def _as_toolkit(fake: FakeToolkit) -> WebToolkit:
    """View the recording fake as a ``WebToolkit`` for the typed seam."""
    return cast(WebToolkit, fake)


def _require_error(result: WebResult, expected: str) -> None:
    """Assert the result failed with the expected stable error value."""
    assert result.ok is False
    assert result.error is not None
    assert result.error.value == expected


# ── TOOL_SPECS ───────────────────────────────────────────────────────────────


def test_tool_specs_names_cover_all_five_operations() -> None:
    spec_names = set(TOOL_SPECS)

    assert spec_names == {
        "web_fetch",
        "web_fetch_parsed",
        "web_search",
        "web_crawl",
        "web_render",
    }


def test_tool_specs_entries_have_description_and_params() -> None:
    for _name, spec in TOOL_SPECS.items():
        assert isinstance(spec["description"], str)
        assert spec["description"].strip()
        assert isinstance(spec["params"], dict)


# ── fetch_raw: forwarding and validation ─────────────────────────────────────


def test_fetch_raw_defaults_are_get_with_no_headers() -> None:
    toolkit = FakeToolkit()

    result = fetch_raw("https://example.com", toolkit=_as_toolkit(toolkit))

    name, args, kwargs = toolkit.calls[0]
    assert result.ok is True
    assert name == "fetch_raw"
    assert args == ("https://example.com",)
    assert kwargs == {"method": "GET", "headers": None}


def test_fetch_raw_forwards_method_and_headers() -> None:
    toolkit = FakeToolkit()
    headers = {"Accept": "text/html"}

    fetch_raw("https://example.com", method="HEAD", headers=headers, toolkit=_as_toolkit(toolkit))

    _, _, kwargs = toolkit.calls[0]
    assert kwargs == {"method": "HEAD", "headers": headers}


def test_fetch_raw_rejects_methods_other_than_get_and_head() -> None:
    result = fetch_raw("https://example.com", method="POST")

    _require_error(result, "invalid_input")


def test_fetch_raw_rejects_empty_url() -> None:
    result = fetch_raw("   ")

    _require_error(result, "invalid_url")


# ── injection precedence ─────────────────────────────────────────────────────


def test_supplied_toolkit_wins_over_injected_fetcher() -> None:
    toolkit = FakeToolkit()
    fetcher = make_fetcher()

    fetch_raw("https://example.com", fetcher=fetcher, toolkit=_as_toolkit(toolkit))

    assert len(toolkit.calls) == 1


# ── fetch_parsed ─────────────────────────────────────────────────────────────


def test_fetch_parsed_forwards_url_to_toolkit() -> None:
    toolkit = FakeToolkit()

    fetch_parsed("https://example.com/docs", toolkit=_as_toolkit(toolkit))

    name, args, kwargs = toolkit.calls[0]
    assert name == "fetch_parsed"
    assert args == ("https://example.com/docs",)
    assert kwargs == {}


# ── search_gather ────────────────────────────────────────────────────────────


def test_search_gather_forwards_query_top_n_and_fetch_results() -> None:
    toolkit = FakeToolkit()

    search_gather("gludd", top_n=3, fetch_results=False, toolkit=_as_toolkit(toolkit))

    _, args, kwargs = toolkit.calls[0]
    assert args == ("gludd",)
    assert kwargs == {"top_n": 3, "fetch_results": False}


# ── crawl_site ───────────────────────────────────────────────────────────────


def test_crawl_site_forwards_seed_without_limits_by_default() -> None:
    toolkit = FakeToolkit()

    crawl_site("https://example.com", toolkit=_as_toolkit(toolkit))

    _, args, kwargs = toolkit.calls[0]
    assert args == ("https://example.com",)
    assert kwargs == {"max_pages": None, "max_depth": None}


def test_crawl_site_forwards_explicit_limits() -> None:
    toolkit = FakeToolkit()

    crawl_site("https://example.com", max_pages=5, max_depth=2, toolkit=_as_toolkit(toolkit))

    _, _, kwargs = toolkit.calls[0]
    assert kwargs == {"max_pages": 5, "max_depth": 2}


# ── render_js ────────────────────────────────────────────────────────────────


def test_render_js_forwards_url_to_toolkit() -> None:
    toolkit = FakeToolkit()

    render_js("https://example.com", toolkit=_as_toolkit(toolkit))

    name, args, kwargs = toolkit.calls[0]
    assert name == "render_js"
    assert args == ("https://example.com",)
    assert kwargs == {}


# ── end-to-end through a real WebToolkit ─────────────────────────────────────


def test_fetch_raw_real_toolkit_success_decodes_body_and_injects_user_agent() -> None:
    fetcher = make_fetcher(status_code=200, body=b"<p>hello</p>")
    policy = WebPolicy(allowed_hosts=frozenset({"example.com"}))

    result = fetch_raw("https://example.com", policy=policy, fetcher=fetcher)

    assert result.ok is True
    assert result.status == 200
    assert result.body == "<p>hello</p>"
    assert result.headers["user-agent"] == policy.user_agent


def test_fetch_raw_real_toolkit_maps_4xx_to_http_error() -> None:
    fetcher = make_fetcher(status_code=404, body=b"missing")

    result = fetch_raw("https://example.com/missing", fetcher=fetcher)

    _require_error(result, "http_4xx")
    assert result.detail == "HTTP 404"


def test_fetch_raw_real_toolkit_maps_5xx_to_http_error() -> None:
    fetcher = make_fetcher(status_code=503, body=b"down")

    result = fetch_raw("https://example.com", fetcher=fetcher)

    _require_error(result, "http_5xx")


def test_fetch_raw_real_toolkit_maps_timeout_exception() -> None:
    def timeout_fetcher(
        url: str,
        *,
        policy: FetchPolicy,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        content: bytes | str | None = None,
    ) -> FetchResult:
        del url, policy, method, headers, content
        raise URLFetchTimeout("deadline exceeded")

    result = fetch_raw("https://example.com", fetcher=timeout_fetcher)

    _require_error(result, "timeout")
    assert result.detail


def test_fetch_parsed_real_toolkit_attaches_parsed_page() -> None:
    fetcher = make_fetcher(status_code=200, body=b"<html><body>hi there</body></html>")
    policy = WebPolicy(allowed_hosts=frozenset({"example.com"}))

    result = fetch_parsed("https://example.com", policy=policy, fetcher=fetcher)

    assert result.ok is True
    assert result.parsed is not None
    assert "hi there" in result.parsed.text


def test_search_gather_real_toolkit_without_provider_fails_closed() -> None:
    toolkit = WebToolkit(policy=WebPolicy(allowed_hosts=frozenset({"example.com"})))

    result = search_gather("gludd", toolkit=toolkit)

    _require_error(result, "provider_unconfigured")
    assert result.meta["provider_state"] == "unconfigured"


def test_search_gather_real_toolkit_honours_fetch_results_false() -> None:
    class Provider:
        configured = True

        def search(self, query: str, *, top_n: int) -> list[SearchHit]:
            assert query == "gludd"
            assert top_n <= 2
            return [SearchHit(url="https://example.com/a", title="a")]

    toolkit = WebToolkit(policy=WebPolicy(max_search_results=2), search_provider=Provider())

    result = search_gather("gludd", top_n=9, fetch_results=False, toolkit=toolkit)

    assert result.ok is True
    assert len(result.hits) == 1
    assert result.results == []
    assert result.meta["provider_state"] == "configured"
    assert result.meta["hit_count"] == 1


def test_render_js_real_toolkit_disabled_by_default_policy() -> None:
    toolkit = WebToolkit()

    result = render_js("https://example.com", toolkit=toolkit)

    _require_error(result, "renderer_unavailable")
    assert result.meta["renderer_state"] == "disabled"


def test_crawl_site_real_toolkit_rejects_non_https_seed() -> None:
    result = crawl_site("http://example.com")

    _require_error(result, "invalid_url")
