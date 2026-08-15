"""Deep tests for src/general_ludd/web/types.py.

Covers the frozen, JSON-safe result models of the bounded web toolkit:
WebError vocabulary stability and aliases, Link string-compat behavior,
ParsedPage title normalization, GatheredPage error coercion, BlockSignal
advisory shape, WebResult defaults and default-factory isolation, JSON
round-trips, and the compatibility subclass names.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from general_ludd.web.types import (
    BlockSignal,
    CaptchaSignal,
    CrawlResult,
    GatheredPage,
    Link,
    ParsedPage,
    RawFetchResult,
    RenderResult,
    SearchHit,
    SearchResult,
    WebError,
    WebResult,
)

# ---------------------------------------------------------------------------
# WebError vocabulary
# ---------------------------------------------------------------------------


class TestWebErrorVocabulary:
    def test_error_values_are_stable_strings(self) -> None:
        # Arrange
        expected = {
            "offline",
            "timeout",
            "ssrf_blocked",
            "response_too_large",
            "robots_disallowed",
            "captcha_detected",
            "redirect_limit",
            "http_4xx",
            "http_5xx",
            "circuit_open",
            "renderer_unavailable",
            "render_connect_failed",
            "provider_unconfigured",
            "retry_exhausted",
            "parse_error",
            "invalid_url",
            "invalid_input",
            "crawl_timeout",
        }
        # Act
        values = {member.value for member in WebError}
        # Assert
        assert expected <= values

    def test_alias_members_share_values(self) -> None:
        # Arrange / Act
        robots_denied = WebError.ROBOTS_DENIED
        render_disabled = WebError.RENDER_DISABLED
        no_provider = WebError.NO_PROVIDER
        # Assert
        assert robots_denied is WebError.ROBOTS_DISALLOWED
        assert render_disabled is WebError.RENDERER_UNAVAILABLE
        assert no_provider is WebError.PROVIDER_UNCONFIGURED

    def test_members_str_as_value(self) -> None:
        # Arrange
        member = WebError.SSRF_BLOCKED
        # Act
        rendered = str(member)
        # Assert
        assert rendered == "ssrf_blocked"

    def test_enum_from_string_roundtrip(self) -> None:
        # Arrange / Act
        member = WebError("http_4xx")
        # Assert
        assert member is WebError.HTTP_4XX

    def test_invalid_string_raises_value_error(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValueError):
            WebError("not_a_real_error")


# ---------------------------------------------------------------------------
# Link — string-compat hyperlink model
# ---------------------------------------------------------------------------


class TestLink:
    def test_construction_with_defaults(self) -> None:
        # Arrange / Act
        link = Link(href="https://example.com/a")
        # Assert
        assert link.href == "https://example.com/a"
        assert link.text == ""

    def test_str_returns_href(self) -> None:
        # Arrange
        link = Link(href="https://example.com/a", text="Example")
        # Act
        rendered = str(link)
        # Assert
        assert rendered == "https://example.com/a"

    def test_contains_checks_href_substring(self) -> None:
        # Arrange
        link = Link(href="https://example.com/path?q=1", text="results")
        # Act / Assert
        assert "path" in link
        assert "q=1" in link
        assert "unrelated" not in link

    def test_eq_with_plain_string(self) -> None:
        # Arrange
        link = Link(href="https://example.com/x")
        # Act / Assert
        assert link == "https://example.com/x"
        assert link != "https://example.com/y"

    def test_eq_with_other_link(self) -> None:
        # Arrange
        a = Link(href="https://example.com/x", text="same")
        b = Link(href="https://example.com/x", text="same")
        c = Link(href="https://example.com/x", text="different")
        # Act / Assert
        assert a == b
        assert a != c

    def test_eq_with_unrelated_type_is_false(self) -> None:
        # Arrange
        link = Link(href="https://example.com/x")
        # Act
        result = link == 42
        # Assert
        assert result is False

    def test_hash_consistent_with_equality(self) -> None:
        # Arrange
        a = Link(href="https://example.com/x", text="t")
        b = Link(href="https://example.com/x", text="t")
        # Act
        seen = {a}
        # Assert
        assert b in seen
        assert len(seen) == 1

    def test_frozen_mutation_raises(self) -> None:
        # Arrange
        link = Link(href="https://example.com/x")
        # Act / Assert
        with pytest.raises(ValidationError):
            link.href = "https://example.com/y"


# ---------------------------------------------------------------------------
# ParsedPage — bounded visible content
# ---------------------------------------------------------------------------


class TestParsedPage:
    def test_defaults(self) -> None:
        # Arrange / Act
        page = ParsedPage()
        # Assert
        assert page.url == ""
        assert page.title is None
        assert page.text == ""
        assert page.links == []
        assert page.meta == {}
        assert page.headings == []
        assert page.lang is None
        assert page.status is None

    def test_normalized_title_none(self) -> None:
        # Arrange
        page = ParsedPage(title=None)
        # Act
        result = page.normalized_title()
        # Assert
        assert result is None

    def test_normalized_title_collapses_whitespace(self) -> None:
        # Arrange
        page = ParsedPage(title="  Deep   dive\tinto\nweb types  ")
        # Act
        result = page.normalized_title()
        # Assert
        assert result == "Deep dive into web types"

    def test_normalized_title_whitespace_only_is_none(self) -> None:
        # Arrange
        page = ParsedPage(title="   \n\t  ")
        # Act
        result = page.normalized_title()
        # Assert
        assert result is None

    def test_links_are_link_models(self) -> None:
        # Arrange
        page = ParsedPage(
            url="https://example.com",
            links=[Link(href="https://example.com/a"), Link(href="https://example.com/b")],
            headings=["Intro", "Deep dive"],
        )
        # Act
        hrefs = [str(link) for link in page.links]
        # Assert
        assert hrefs == ["https://example.com/a", "https://example.com/b"]
        assert page.headings == ["Intro", "Deep dive"]


# ---------------------------------------------------------------------------
# SearchHit and GatheredPage
# ---------------------------------------------------------------------------


class TestSearchHit:
    def test_defaults(self) -> None:
        # Arrange / Act
        hit = SearchHit(url="https://example.com/result")
        # Assert
        assert hit.url == "https://example.com/result"
        assert hit.title == ""
        assert hit.snippet == ""


class TestGatheredPage:
    def test_successful_page_defaults(self) -> None:
        # Arrange / Act
        page = GatheredPage(url="https://example.com", ok=True)
        # Assert
        assert page.ok is True
        assert page.status is None
        assert page.error is None
        assert page.detail is None

    def test_error_string_coerces_to_web_error(self) -> None:
        # Arrange / Act
        page = GatheredPage(url="https://example.com", ok=False, error="timeout")
        # Assert
        assert page.error is WebError.TIMEOUT

    def test_invalid_error_string_raises(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            GatheredPage(url="https://example.com", ok=False, error="bogus_error")


# ---------------------------------------------------------------------------
# BlockSignal — advisory challenge signal
# ---------------------------------------------------------------------------


class TestBlockSignal:
    def test_required_fields_and_default(self) -> None:
        # Arrange / Act
        signal = BlockSignal(vendor="cf", kind="captcha", status=403, evidence="turnstile")
        # Assert
        assert signal.vendor == "cf"
        assert signal.kind == "captcha"
        assert signal.status == 403
        assert signal.evidence == "turnstile"
        assert signal.retry_after is None

    def test_captcha_signal_is_block_signal(self) -> None:
        # Arrange / Act
        signal = CaptchaSignal(vendor="akamai", kind="challenge", status=429, evidence="script")
        # Assert
        assert isinstance(signal, BlockSignal)
        assert signal.status == 429


# ---------------------------------------------------------------------------
# WebResult — unified model-facing result
# ---------------------------------------------------------------------------


class TestWebResult:
    def test_defaults(self) -> None:
        # Arrange / Act
        result = WebResult(ok=True)
        # Assert
        assert result.ok is True
        assert result.url == ""
        assert result.final_url is None
        assert result.status is None
        assert result.headers == {}
        assert result.body is None
        assert result.parsed is None
        assert result.results is None
        assert result.hits == []
        assert result.pages == []
        assert result.visited == []
        assert result.skipped == []
        assert result.gathered == 0
        assert result.failed == 0
        assert result.errors == []
        assert result.error is None
        assert result.detail is None
        assert result.html is None
        assert result.elapsed_ms == 0.0
        assert result.truncated is False
        assert result.stats == {}
        assert result.meta == {}

    def test_default_factory_isolation(self) -> None:
        # Arrange
        a = WebResult(ok=True)
        b = WebResult(ok=True)
        # Act
        a.errors.append("boom")
        a.hits.append(SearchHit(url="https://example.com/x"))
        a.meta["k"] = "v"
        # Assert
        assert b.errors == []
        assert b.hits == []
        assert b.meta == {}

    def test_json_roundtrip_with_enum_and_nested_models(self) -> None:
        # Arrange
        result = WebResult(
            ok=True,
            url="https://example.com",
            status=200,
            parsed=ParsedPage(title="Title", links=[Link(href="https://example.com/a")]),
            hits=[SearchHit(url="https://example.com/a", title="A")],
            error=WebError.TIMEOUT,
        )
        # Act
        raw = result.model_dump_json()
        restored = WebResult.model_validate_json(raw)
        # Assert
        assert restored.ok is True
        assert restored.error is WebError.TIMEOUT
        assert restored.parsed is not None
        assert restored.parsed.links[0] == "https://example.com/a"
        assert restored == result

    def test_compat_subclass_names(self) -> None:
        # Arrange / Act
        raw = RawFetchResult(ok=True)
        search = SearchResult(ok=True)
        render = RenderResult(ok=True)
        crawl = CrawlResult(ok=True)
        # Assert
        assert isinstance(raw, WebResult)
        assert isinstance(search, WebResult)
        assert isinstance(render, WebResult)
        assert isinstance(crawl, WebResult)

    def test_error_enum_serializes_as_plain_string(self) -> None:
        # Arrange
        result = WebResult(ok=False, error=WebError.SSRF_BLOCKED)
        # Act
        raw = json.loads(result.model_dump_json())
        # Assert
        assert raw["error"] == "ssrf_blocked"

    def test_frozen_mutation_raises(self) -> None:
        # Arrange
        result = WebResult(ok=True)
        # Act / Assert
        with pytest.raises(ValidationError):
            result.ok = False
