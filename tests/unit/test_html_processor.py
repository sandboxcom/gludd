"""TDD tests for the html_processor module_util.

Tests the module at:
``collections/ansible_collections/general_ludd/xml/plugins/module_utils/html_processor.py``

Imports directly via :mod:`importlib` from its file path.
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


# -- parse_html: valid HTML -------------------------------------------------


def test_parse_html_valid_returns_element_tree() -> None:
    """A well-formed HTML string produces an Element tree whose root holds children."""
    root = parse_html("<html><body><h1>Title</h1></body></html>")
    # Element exposes a tag, attrib, and children — duck-typed ET.Element
    assert hasattr(root, "tag")
    assert hasattr(root, "iter")
    tags = [el.tag for el in root.iter()]
    assert "h1" in tags
    assert "body" in tags


# -- parse_html: malformed input -------------------------------------------


def test_parse_html_malformed_does_not_raise() -> None:
    """Malformed HTML (unclosed tags) is repaired gracefully, no exception."""
    root = parse_html("<div><p>hello")
    # Should not raise; should still expose a div and p
    tags = [el.tag for el in root.iter()]
    assert "div" in tags
    assert "p" in tags


# -- extract_text ----------------------------------------------------------


def test_extract_text_returns_concatenated_text_content() -> None:
    """extract_text returns the visible text inside the HTML."""
    html = "<div>Hello <b>world</b>!</div>"
    assert "Hello" in extract_text(html)
    assert "world" in extract_text(html)
    assert "!" in extract_text(html)


def test_extract_text_with_selector_filterss_to_tag() -> None:
    """extract_text with selector returns only text within matching tags."""
    html = "<h1>Top</h1><p>Body text</p>"
    text = extract_text(html, selector="p")
    assert "Body text" in text
    assert "Top" not in text


# -- extract_links ---------------------------------------------------------


def test_extract_links_returns_href_and_text_for_each_anchor() -> None:
    """Each <a href> becomes a dict with href and text keys."""
    html = '<ul><li><a href="https://a.example">A</a></li><li><a href="/b">B</a></li></ul>'
    links = extract_links(html)
    assert len(links) == 2
    assert {"href": "https://a.example", "text": "A"} in links
    assert {"href": "/b", "text": "B"} in links


def test_extract_links_anchor_without_href_yields_empty_href() -> None:
    """An <a> with no href attribute yields href=""."""
    html = "<a>no link</a>"
    links = extract_links(html)
    assert len(links) == 1
    assert links[0]["href"] == ""
    assert links[0]["text"] == "no link"


# -- strip_tags ------------------------------------------------------------


def test_strip_tags_removes_all_markup_preserving_text() -> None:
    """strip_tags returns plain text with all tags removed."""
    html = "<p>Hello <strong>there</strong></p>"
    out = strip_tags(html)
    assert "<" not in out
    assert ">" not in out
    assert "Hello" in out
    assert "there" in out


# -- empty input -----------------------------------------------------------


def test_parse_html_empty_string_yields_empty_root() -> None:
    """An empty input string yields an Element tree with no child tags."""
    root = parse_html("")
    # iter() always includes the root itself, but no descendant tags
    descendant_tags = [el.tag for el in root.iter() if not isinstance(el.tag, str) or el.tag != root.tag]
    # No real child tags should exist
    assert descendant_tags == [] or all(t in ("_root",) for t in [root.tag])


def test_extract_text_empty_input_returns_empty_string() -> None:
    """extract_text on empty HTML returns an empty string."""
    assert extract_text("") == ""


# -- nested elements -------------------------------------------------------


def test_parse_html_nested_elements_preserve_hierarchy() -> None:
    """Deeply nested tags are accessible via tree iteration."""
    html = "<div><ul><li><span>deep</span></li></ul></div>"
    root = parse_html(html)
    tags = [el.tag for el in root.iter()]
    assert "div" in tags
    assert "ul" in tags
    assert "li" in tags
    assert "span" in tags


# -- HTML entities ---------------------------------------------------------


def test_extract_text_decodes_html_entities() -> None:
    """Named and numeric entities are decoded to their characters."""
    html = "<p>5 &lt; 6 &amp; 7 &gt; 4 &copy; 2024</p>"
    text = extract_text(html)
    assert "5 < 6" in text
    assert "& 7" in text
    assert "> 4" in text
