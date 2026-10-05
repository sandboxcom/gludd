"""Deep tests for ``src/general_ludd/web/parse.py``.

Covers the dependency-free standard-library HTML extractor: ``parse_html``,
``_normalise_link``, ``_HTMLExtractor``, and the frozen ``Link``/``ParsedPage``
transport models from ``web/types.py``.
"""

from __future__ import annotations

import pytest

from general_ludd.web.parse import _normalise_link, parse_html
from general_ludd.web.types import Link, ParsedPage

# ── parse_html: empty and minimal input ─────────────────────────────────────


def test_empty_body_returns_defaults() -> None:
    page = parse_html("")

    assert page.url == ""
    assert page.title is None
    assert page.text == ""
    assert page.links == []
    assert page.meta == {}
    assert page.headings == []
    assert page.lang is None
    assert page.status is None


def test_title_whitespace_normalised() -> None:
    page = parse_html("<html><head><title>  Hello   World </title></head></html>")

    assert page.title == "Hello World"


def test_title_present_returns_none_for_missing_title() -> None:
    page = parse_html("<html><body>no title here</body></html>")

    assert page.title is None


# ── parse_html: visible text ─────────────────────────────────────────────────


def test_body_text_collected_in_order() -> None:
    page = parse_html("<body>first<p>second</p>third</body>")

    assert page.text == "first second third"


def test_entities_decoded_in_text() -> None:
    page = parse_html("<p>a &amp; b &lt;tag&gt; &quot;q&quot;</p>")

    assert page.text == 'a & b <tag> "q"'


def test_skip_tags_excluded_from_text() -> None:
    body = (
        "<head>meta junk</head><body>a"
        "<script>s();</script><style>.x{}</style>"
        "<noscript>n</noscript><template>t</template>"
        "b</body>"
    )
    page = parse_html(body)

    assert page.text == "a b"


def test_unclosed_skip_tag_still_suppresses_tail() -> None:
    page = parse_html("<body>before<script>never closed after</body>")

    assert page.text == "before"


def test_malformed_markup_is_tolerated() -> None:
    page = parse_html("</div>unbalanced<p>oops<a href='https://e.com/x'>link")

    assert page.text == "unbalanced oops link"
    assert [link.href for link in page.links] == ["https://e.com/x"]


# ── parse_html: headings and language ───────────────────────────────────────


def test_headings_extracted_in_order() -> None:
    page = parse_html("<h1>One</h1><h2> Two   words </h2><h3></h3><h1>Four</h1>")

    assert page.headings == ["One", "Two words", "Four"]


def test_heading_with_inline_markup_joined() -> None:
    page = parse_html("<h2>Hello <em>world</em> again</h2>")

    assert page.headings == ["Hello world again"]


def test_lang_from_html_attribute() -> None:
    page = parse_html("<html lang='en-US'><body>x</body></html>")

    assert page.lang == "en-US"


def test_lang_from_meta_content_language_fallback() -> None:
    page = parse_html("<html><head><meta http-equiv='content-language' content='de'></head></html>")

    assert page.lang == "de"


def test_html_lang_wins_over_meta() -> None:
    page = parse_html("<html lang='fr'><head><meta http-equiv='content-language' content='de'></head></html>")

    assert page.lang == "fr"


# ── parse_html: meta and canonical ──────────────────────────────────────────


def test_meta_name_and_property_collected() -> None:
    page = parse_html(
        "<head><meta name='description' content=' A desc '><meta property='og:title' content='OG'></head>"
    )

    assert page.meta == {"description": "A desc", "og:title": "OG"}


def test_meta_without_key_or_content_ignored() -> None:
    page = parse_html("<head><meta name='x'><meta content='no key'></head>")

    assert page.meta == {}


def test_meta_key_lowercased_and_stripped() -> None:
    page = parse_html("<head><meta name='  DESCRIPTION ' content='d'></head>")

    assert page.meta == {"description": "d"}


def test_canonical_link_captured_absolutely() -> None:
    page = parse_html("<link rel='canonical' href='/page'>", base_url="https://example.com/a/")

    assert page.meta["canonical"] == "https://example.com/page"


def test_canonical_without_rel_canonical_ignored() -> None:
    page = parse_html("<link rel='stylesheet' href='/style.css'>", base_url="https://example.com")

    assert "canonical" not in page.meta


# ── parse_html: links ───────────────────────────────────────────────────────


def test_relative_links_resolved_against_base() -> None:
    page = parse_html(
        "<a href='sub/page?x=1#frag'>t</a>",
        base_url="https://example.com/dir/",
    )

    assert [link.href for link in page.links] == ["https://example.com/dir/sub/page?x=1"]


def test_absolute_links_kept() -> None:
    page = parse_html("<a href='https://other.example.net/p'>t</a>")

    assert [link.href for link in page.links] == ["https://other.example.net/p"]


def test_non_http_schemes_filtered() -> None:
    page = parse_html(
        "<a href='javascript:alert(1)'>a</a><a href='mailto:a@b.c'>b</a><a href='ftp://example.com/f'>c</a>"
    )

    assert page.links == []


def test_links_with_credentials_rejected() -> None:
    page = parse_html("<a href='https://user:pass@example.com/x'>t</a>")

    assert page.links == []


def test_duplicate_links_deduplicated() -> None:
    page = parse_html("<a href='/x'>1</a><a href='https://example.com/x'>2</a>", base_url="https://example.com")

    assert [link.href for link in page.links] == ["https://example.com/x"]


def test_max_links_cap_enforced() -> None:
    body = "".join(f"<a href='https://example.com/p{i}'>l</a>" for i in range(20))

    page = parse_html(body, max_links=5)

    assert len(page.links) == 5


def test_default_link_cap_is_100() -> None:
    body = "".join(f"<a href='https://example.com/p{i}'>l</a>" for i in range(150))

    page = parse_html(body)

    assert len(page.links) == 100


def test_anchor_without_href_ignored() -> None:
    page = parse_html("<a>no href</a>")

    assert page.links == []


# ── parse_html: validation and passthrough ──────────────────────────────────


def test_status_passed_through() -> None:
    page = parse_html("<p>x</p>", status=200)

    assert page.status == 200


def test_non_string_body_raises_type_error() -> None:
    with pytest.raises(TypeError):
        parse_html(123)
    with pytest.raises(TypeError):
        parse_html(b"<html></html>")
    with pytest.raises(TypeError):
        parse_html(None)


def test_invalid_max_links_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_html("<p>x</p>", max_links=0)
    with pytest.raises(ValueError):
        parse_html("<p>x</p>", max_links=501)
    with pytest.raises(ValueError):
        parse_html("<p>x</p>", max_links=True)
    with pytest.raises(ValueError):
        parse_html("<p>x</p>", max_links="10")


def test_max_links_boundaries_accepted() -> None:
    assert parse_html("<p>x</p>", max_links=1).text == "x"
    assert parse_html("<p>x</p>", max_links=500).text == "x"


def test_rich_flag_is_a_compatible_noop() -> None:
    body = "<title>t</title><p>x</p>"

    assert parse_html(body, rich=True) == parse_html(body, rich=False)


# ── _normalise_link ─────────────────────────────────────────────────────────


def test_normalise_link_strips_fragment_and_lowercases_scheme() -> None:
    result = _normalise_link("", "HTTPS://Example.com/a/b?q=1#frag")

    assert result == "https://Example.com/a/b?q=1"


def test_normalise_link_relative_resolution() -> None:
    assert _normalise_link("https://example.com/dir/", "../up") == "https://example.com/up"


def test_normalise_link_rejects_scheme_and_empty_host() -> None:
    assert _normalise_link("", "javascript:void(0)") is None
    assert _normalise_link("", "") is None


def test_normalise_link_rejects_credentials() -> None:
    assert _normalise_link("", "https://user:secret@example.com/x") is None


# ── Link / ParsedPage transport models ──────────────────────────────────────


def test_link_string_compatibility() -> None:
    link = Link(href="https://example.com/a")

    assert str(link) == "https://example.com/a"
    assert link == "https://example.com/a"
    assert "example.com" in link


def test_link_equality_with_non_string() -> None:
    link = Link(href="https://example.com/a")

    assert link != 42
    assert link != Link(href="https://example.com/b")


def test_link_hash_and_equality_use_fields() -> None:
    first = Link(href="https://example.com/a", text="t")
    second = Link(href="https://example.com/a", text="t")

    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1


def test_link_is_frozen() -> None:
    link = Link(href="https://example.com/a")

    with pytest.raises(Exception) as excinfo:
        link.href = "https://evil.example.com"

    assert "frozen" in str(excinfo.value).lower()


def test_parsed_page_is_frozen() -> None:
    page = parse_html("<p>x</p>")

    with pytest.raises(Exception) as excinfo:
        page.title = "tampered"

    assert "frozen" in str(excinfo.value).lower()


def test_parsed_page_normalized_title() -> None:
    page = ParsedPage(url="https://example.com", title="  a   b ")
    empty = ParsedPage(url="https://example.com", title="   ")
    missing = ParsedPage(url="https://example.com", title=None)

    assert page.normalized_title() == "a b"
    assert empty.normalized_title() is None
    assert missing.normalized_title() is None
