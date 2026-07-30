"""TDD tests for the xsd_generator module_util.

Tests the module at:
``collections/ansible_collections/general_ludd/xml/plugins/module_utils/xsd_generator.py``

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
    / "xsd_generator.py"
)


def _load_module() -> Any:
    """Import xsd_generator.py from disk, bypassing ansible collection path."""
    spec = importlib.util.spec_from_file_location("xsd_generator", str(MODULE_PATH))
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["xsd_generator"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
infer_xsd = _mod.infer_xsd


# ── single sample ────────────────────────────────────────────────────


def test_infer_xsd_single_sample_returns_valid_xsd_schema() -> None:
    """A single well-formed sample yields a schema declaring the root + children."""
    xsd = infer_xsd(["<root><a>1</a><b>hello</b></root>"])
    assert "<xs:schema" in xsd
    assert "</xs:schema>" in xsd
    assert 'name="root"' in xsd
    assert 'name="a"' in xsd
    assert 'name="b"' in xsd


# ── multiple samples ─────────────────────────────────────────────────


def test_infer_xsd_multiple_samples_merges_elements() -> None:
    """Elements seen across distinct samples are merged into one schema."""
    xsd = infer_xsd(
        [
            "<root><a>1</a></root>",
            "<root><b>2</b></root>",
        ]
    )
    assert 'name="a"' in xsd
    assert 'name="b"' in xsd
    # single root declaration (not duplicated)
    assert xsd.count('name="root"') == 1


# ── nested elements ──────────────────────────────────────────────────


def test_infer_xsd_nested_elements_declared() -> None:
    """Deeply nested elements each receive a declaration."""
    xsd = infer_xsd("<root><parent><child>x</child></parent></root>")
    assert 'name="parent"' in xsd
    assert 'name="child"' in xsd
    assert "<xs:sequence>" in xsd


# ── attributes ───────────────────────────────────────────────────────


def test_infer_xsd_attributes_emitted() -> None:
    """Element attributes surface as xs:attribute declarations."""
    xsd = infer_xsd('<root id="r1"><item val="v"/></root>')
    assert '<xs:attribute name="id"' in xsd
    assert '<xs:attribute name="val"' in xsd


# ── empty list ───────────────────────────────────────────────────────


def test_infer_xsd_empty_list_raises_value_error() -> None:
    """An empty sample list is a contract violation."""
    with pytest.raises(ValueError):
        infer_xsd([])


# ── malformed XML ────────────────────────────────────────────────────


def test_infer_xsd_malformed_xml_raises_value_error() -> None:
    """Malformed XML raises ValueError (not a raw ParseError leak)."""
    with pytest.raises(ValueError):
        infer_xsd(["<root><a></root>"])


# ── mixed content ────────────────────────────────────────────────────


def test_infer_xsd_mixed_content_handled_gracefully() -> None:
    """An element carrying both text and children degrades to a complex type."""
    xsd = infer_xsd("<root>text<child>c</child></root>")
    assert 'name="root"' in xsd
    assert 'name="child"' in xsd
    assert "<xs:complexType>" in xsd


# ── namespaced ───────────────────────────────────────────────────────


def test_infer_xsd_namespaced_strips_to_local_names() -> None:
    """Namespace prefixes are stripped; local names appear in the schema."""
    xsd = infer_xsd('<ns:root xmlns:ns="http://x.example"><ns:a>1</ns:a></ns:root>')
    assert 'name="root"' in xsd
    assert 'name="a"' in xsd
    # element declarations must not carry the original namespace prefix
    assert 'name="ns:' not in xsd
    assert 'ref="ns:' not in xsd


# ── type inference ───────────────────────────────────────────────────


def test_infer_xsd_type_inference_integer() -> None:
    """Numeric text is inferred as xs:integer."""
    xsd = infer_xsd("<root><n>42</n></root>")
    assert 'type="xs:integer"' in xsd


def test_infer_xsd_type_inference_boolean() -> None:
    """Boolean literals are inferred as xs:boolean."""
    xsd = infer_xsd("<root><flag>true</flag></root>")
    assert 'type="xs:boolean"' in xsd


def test_infer_xsd_empty_element_defaults_to_string() -> None:
    """An empty leaf element defaults to xs:string."""
    xsd = infer_xsd("<root><empty/></root>")
    assert 'name="empty"' in xsd
    assert 'type="xs:string"' in xsd


def test_infer_xsd_output_is_well_formed_xml() -> None:
    """The returned schema string is itself parseable XML."""
    from xml.etree import ElementTree as ET

    xsd = infer_xsd("<root><a>1</a></root>")
    # Must not raise
    ET.fromstring(xsd)
