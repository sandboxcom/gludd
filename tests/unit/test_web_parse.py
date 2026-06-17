"""Offline tests for the stdlib HTML parser (no new dependency)."""

from __future__ import annotations

from general_ludd.web.parse import parse_html


def test_title_text_links_meta_lang() -> None:
    html = (
        '<html lang="fr"><head><title>  Hello  </title>'
        '<meta property="og:title" content="OG">'
        '<meta name="robots" content="noindex"></head>'
        '<body><h1>Heading</h1><p>Body paragraph.</p>'
        '<a href="https://other.com/a">A</a>'
        '<a href="rel/b">B</a>'
        '<a href="mailto:x@y.com">mail</a></body></html>'
    )
    page = parse_html(html, base_url="https://example.com/dir/", status=200)
    assert page.title == "Hello"
    assert page.lang == "fr"
    assert "Heading" in page.text and "Body paragraph." in page.text
    assert page.meta["og:title"] == "OG"
    assert page.meta["robots"] == "noindex"
    hrefs = {link.href for link in page.links}
    assert "https://other.com/a" in hrefs
    assert "https://example.com/dir/rel/b" in hrefs
    assert not any(h.startswith("mailto:") for h in hrefs)  # non-http dropped
    assert page.status == 200


def test_malformed_html_does_not_raise() -> None:
    page = parse_html("<html><body><p>unclosed <a href=/x>link", base_url="https://e.com/")
    assert "unclosed" in page.text
    assert any(link.href == "https://e.com/x" for link in page.links)


def test_empty_body() -> None:
    page = parse_html("", base_url="https://e.com/")
    assert page.title is None
    assert page.text == ""
    assert page.links == []


def test_script_style_head_stripped() -> None:
    html = (
        "<head><style>p{color:red}</style></head>"
        "<body><script>alert(1)</script><div>keep</div></body>"
    )
    page = parse_html(html, base_url="https://e.com/")
    assert "keep" in page.text
    assert "alert" not in page.text
    assert "color:red" not in page.text


def test_rich_falls_back_without_trafilatura() -> None:
    # trafilatura is not a hard dep; rich=True must still return stdlib text.
    page = parse_html("<body><p>fallback text</p></body>", base_url="https://e.com/", rich=True)
    assert "fallback text" in page.text
