"""TDD tests for the xml_core module_util.

Tests the module at:
``collections/ansible_collections/general_ludd/xml/plugins/module_utils/xml_core.py``

Imports directly via :mod:`importlib` from its file path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "xml"
    / "plugins"
    / "module_utils"
    / "xml_core.py"
)


def _load_module() -> Any:
    """Import xml_core.py from disk, bypassing ansible collection path."""
    spec = importlib.util.spec_from_file_location("xml_core", str(MODULE_PATH))
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["xml_core"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
parse_xml = _mod.parse_xml
xpath_eval = _mod.xpath_eval
extract_namespaces = _mod.extract_namespaces
normalize_namespace = _mod.normalize_namespace


# ── fixtures ─────────────────────────────────────────────────────────


WELL_FORMED_XML = """<?xml version="1.0"?>
<catalog xmlns="http://example.com/catalog">
    <book id="b1">
        <title>Pythonic Concepts</title>
        <author>Alice</author>
    </book>
    <book id="b2">
        <title>XPath in Anger</title>
        <author>Bob</author>
    </book>
</catalog>
"""

NAMESPACED_XML = """<?xml version="1.0"?>
<root xmlns="http://default.example" xmlns:a="http://a.example" xmlns:b="http://b.example">
    <a:child>one</a:child>
    <b:child>two</b:child>
    <orphan>three</orphan>
</root>
"""


@pytest.fixture
def catalog_tree() -> Any:
    return parse_xml(WELL_FORMED_XML)


# ── parse_xml ────────────────────────────────────────────────────────


def test_parse_xml_well_formed_returns_element_tree(catalog_tree: Any) -> None:
    """Well-formed XML parses to an ElementTree whose root tag matches."""
    assert isinstance(catalog_tree, ET.ElementTree)
    root = catalog_tree.getroot()
    assert root is not None
    uri, local = normalize_namespace(root.tag)
    assert local == "catalog"
    assert uri == "http://example.com/catalog"


def test_parse_xml_malformed_raises_parse_error() -> None:
    """Malformed XML raises ET.ParseError."""
    with pytest.raises(ET.ParseError):
        parse_xml("<catalog><book></catalog>")


def test_parse_xml_empty_string_raises() -> None:
    """Empty input raises ParseError."""
    with pytest.raises(ET.ParseError):
        parse_xml("")


# ── xpath_eval ───────────────────────────────────────────────────────


def test_xpath_eval_finds_direct_children(catalog_tree: Any) -> None:
    """A simple relative XPath finds all matching children."""
    matches = xpath_eval(catalog_tree, ".//{http://example.com/catalog}book")
    assert len(matches) == 2


def test_xpath_eval_attribute_predicate(catalog_tree: Any) -> None:
    """XPath with an attribute predicate narrows correctly."""
    matches = xpath_eval(
        catalog_tree,
        './/{http://example.com/catalog}book[@id="b2"]',
    )
    assert len(matches) == 1
    assert matches[0].get("id") == "b2"


def test_xpath_eval_no_match_returns_empty_list(catalog_tree: Any) -> None:
    """A non-matching XPath returns an empty list (no exception)."""
    matches = xpath_eval(
        catalog_tree,
        ".//{http://example.com/catalog}nonexistent",
    )
    assert matches == []


def test_xpath_eval_with_namespace_map() -> None:
    """A namespace map lets the caller use prefixes in the expression."""
    tree = parse_xml(NAMESPACED_XML)
    matches = xpath_eval(
        tree,
        ".//a:child",
        namespaces={"a": "http://a.example"},
    )
    assert len(matches) == 1
    assert matches[0].text == "one"


# ── extract_namespaces ───────────────────────────────────────────────


def test_extract_namespaces_collects_distinct_uris() -> None:
    """All distinct namespace URIs embedded in Clark notation are collected."""
    tree = parse_xml(NAMESPACED_XML)
    root = tree.getroot()
    ns_map = extract_namespaces(root)
    uris = set(ns_map.values())
    assert "http://default.example" in uris
    assert "http://a.example" in uris
    assert "http://b.example" in uris


def test_extract_namespaces_plain_xml_returns_empty() -> None:
    """Plain XML with no namespaces yields an empty mapping."""
    tree = parse_xml("<root><child>text</child></root>")
    assert extract_namespaces(tree.getroot()) == {}


def test_extract_namespaces_result_is_usable_for_xpath() -> None:
    """The returned mapping can be passed straight back into xpath_eval."""
    tree = parse_xml(NAMESPACED_XML)
    ns_map = extract_namespaces(tree.getroot())
    # Build a reverse lookup: uri -> generated prefix
    uri_to_prefix = {uri: prefix for prefix, uri in ns_map.items()}
    a_prefix = uri_to_prefix["http://a.example"]
    matches = xpath_eval(tree, f".//{a_prefix}:child", namespaces=ns_map)
    assert len(matches) == 1


# ── normalize_namespace ──────────────────────────────────────────────


def test_normalize_namespace_clark_notation_splits() -> None:
    """Clark notation '{uri}local' splits into (uri, local)."""
    assert normalize_namespace("{http://x.example}foo") == (
        "http://x.example",
        "foo",
    )


def test_normalize_namespace_plain_tag_returns_none_uri() -> None:
    """A plain tag with no namespace yields (None, tag)."""
    assert normalize_namespace("bar") == (None, "bar")


def test_normalize_namespace_empty_uri() -> None:
    """A malformed '{}' empty-URI Clark tag degrades gracefully."""
    assert normalize_namespace("{}weird") == (None, "weird")
