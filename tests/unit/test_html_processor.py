"""TDD tests for the html_processor module_util.

Tests the module at:
``collections/ansible_collections/general_ludd/xml/plugins/module_utils/html_processor.py``

Imports directly via :mod:`importlib` from its file path (same pattern as
``test_xml_core.py``).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "xml"
    / "plugins"
    / "module_utils"
    / "html_processor.py"
)


def _load_module() -> Any:
    """Import html_processor.py from disk, bypassing ansible collection path."""
    spec = importlib.util.spec_from_file_location("html_processor", str(MODULE_PATH))
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["html_processor"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
parse_html = _mod.parse_html
extract_text = _mod.extract_text
extract_links = _mod.extract_links
strip_tags = _mod.strip_tags
MAX_INPUT_CHARS = _mod.MAX_INPUT_CHARS


# ── fixtures ─────────────────────────────────────────────────────────


WELL_FORMED_HTML = """<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
    <h1>Welcome</h1>
    <p class="intro">Hello world.</p>
    <ul>
        <li><a href="https://example.com/a">Link A</a></li>
        <li><a href="https://example.com/b">Link B</a></li>
    </ul>
    <div id="footer">Copyright 2026</div>
</body>
</html>
"""

MALFORMED_HTML = """<html>
<body>
    <p>Unclosed paragraph
    <div><span>nested unclosed</div>
    <img src='x.jpg'>
</body>"""

NESTED_HTML = """<html><body>
    <div class="outer">
        <div class="middle">
            <div class="inner">deep text</div>
        </div>
    </div>
</body></html>"""


@pytest.fixture
def parsed_doc() -> Any:
    return parse_html(WELL_FORMED_HTML)


# ── parse_html ───────────────────────────────────────────────────────


def test_parse_html_well_formed_returns_element(parsed_doc: Any) -> None:
    """Well-formed HTML parses to an element with the expected root tag."""
    assert parsed_doc is not None
    tag = getattr(parsed_doc, "tag", None)
    assert tag is not None
    assert "html" in str(tag).lower()


def test_parse_html_extracts_title(parsed_doc: Any) -> None:
    """The <title> element text is reachable after parsing."""
    title_el = parsed_doc.xpath("//title/text()")
    assert title_el == ["Test Page"]


def test_parse_html_malformed_is_tolerant() -> None:
    """Tag-soup / malformed HTML does not raise; a usable tree comes back."""
    doc = parse_html(MALFORMED_HTML)
    assert doc is not None
    text_content = doc.text_content()
    assert "Unclosed paragraph" in text_content
    assert "nested unclosed" in text_content


def test_parse_html_empty_string_returns_empty_tree() -> None:
    """Empty input does not raise; yields an empty/usable tree."""
    doc = parse_html("")
    assert doc is not None


# ── extract_text ─────────────────────────────────────────────────────


def test_extract_text_default_returns_all_text() -> None:
    """With no selector, all visible text is returned."""
    text = extract_text(WELL_FORMED_HTML)
    assert "Welcome" in text
    assert "Hello world." in text
    assert "Link A" in text
    assert "Copyright 2026" in text


def test_extract_text_with_css_selector_filters() -> None:
    """A CSS selector narrows extraction to matching elements only."""
    text = extract_text(WELL_FORMED_HTML, selector="p.intro")
    assert "Hello world." in text
    assert "Welcome" not in text


def test_extract_text_selector_class_dot() -> None:
    """A class-based selector picks up the footer text only."""
    text = extract_text(WELL_FORMED_HTML, selector="#footer")
    assert "Copyright 2026" in text
    assert "Welcome" not in text


def test_extract_text_omits_script_and_style_payloads() -> None:
    """Visible-text extraction excludes executable and stylesheet content."""
    html = "<main>Shown<script>secret()</script><style>.hidden{}</style></main>"
    assert extract_text(html) == "Shown"


def test_extract_text_supports_descendant_selector() -> None:
    """The reconciled selector subset supports bounded descendant matching."""
    html = '<section class="outer"><div><span class="inner">kept</span></div></section>'
    assert extract_text(html, selector="section.outer span.inner") == "kept"


@pytest.mark.parametrize("selector", ["", "a[href]", "div + p", "a\" | //* | \""])
def test_extract_text_rejects_unsupported_or_unsafe_selectors(selector: str) -> None:
    """Selectors outside the documented subset fail closed instead of reaching XPath."""
    with pytest.raises(ValueError, match="selector"):
        extract_text(WELL_FORMED_HTML, selector=selector)


# ── extract_links ────────────────────────────────────────────────────


def test_extract_links_returns_href_and_text() -> None:
    """Every <a href> is returned as a {href, text} dict."""
    links = extract_links(WELL_FORMED_HTML)
    assert isinstance(links, list)
    assert len(links) == 2
    assert {"href": "https://example.com/a", "text": "Link A"} in links
    assert {"href": "https://example.com/b", "text": "Link B"} in links


def test_extract_links_empty_when_no_anchors() -> None:
    """HTML with no <a> tags yields an empty list."""
    links = extract_links("<html><body><p>no links here</p></body></html>")
    assert links == []


def test_extract_links_handles_anchor_without_href() -> None:
    """An <a> with no href is skipped (no KeyError)."""
    html = '<html><body><a name="top">named</a><a href="/x">x</a></body></html>'
    links = extract_links(html)
    assert len(links) == 1
    assert links[0]["href"] == "/x"


def test_extract_links_preserves_nested_visible_text() -> None:
    """Nested anchor labels are normalized without leaking script content."""
    links = extract_links('<a href="/safe">Go <strong>now</strong><script>bad()</script></a>')
    assert links == [{"href": "/safe", "text": "Go now"}]


# ── strip_tags ───────────────────────────────────────────────────────


def test_strip_tags_returns_plain_text() -> None:
    """All markup is removed; only concatenated text remains."""
    text = strip_tags("<p>Hello <b>bold</b> <i>italic</i></p>")
    assert "<" not in text
    assert ">" not in text
    assert "Hello" in text
    assert "bold" in text
    assert "italic" in text


def test_strip_tags_collapses_whitespace() -> None:
    """Whitespace between block elements is normalized sensibly."""
    text = strip_tags("<div>one</div><div>two</div>")
    assert "one" in text
    assert "two" in text
    assert "<" not in text


def test_strip_tags_empty_input() -> None:
    """Empty input returns an empty string, not None."""
    assert strip_tags("") == ""


# ── nested elements ──────────────────────────────────────────────────


def test_parse_html_nested_structure_preserved() -> None:
    """Deeply nested divs are navigable via CSS selectors."""
    text = extract_text(NESTED_HTML, selector="div.inner")
    assert text.strip() == "deep text"


def test_extract_text_nested_outer_selector() -> None:
    """Selecting an outer div returns the concatenated text of its subtree."""
    text = extract_text(NESTED_HTML, selector="div.middle")
    assert "deep text" in text


@pytest.mark.parametrize("operation", [parse_html, extract_text, extract_links, strip_tags])
def test_public_operations_reject_oversized_input(operation: Any) -> None:
    """Parsing and extraction share one explicit in-memory resource bound."""
    with pytest.raises(ValueError, match="limit"):
        operation("x" * (MAX_INPUT_CHARS + 1))
