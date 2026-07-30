"""TDD tests for the xslt_transformer module_util.

Tests the module at:
``collections/ansible_collections/general_ludd/xml/plugins/module_utils/xslt_transformer.py``

Imports directly via :mod:`importlib` from its file path.

The module exposes three pure-string functions:
    apply_xslt(xml_text, xslt_text)        -> str   (transformed output)
    validate_xslt(xslt_text)               -> list[str]   (list of issues)
    extract_template_rules(xslt_text)      -> list[dict]  (per xsl:template)
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
    / "xslt_transformer.py"
)


def _load_module() -> Any:
    """Import xslt_transformer.py from disk, bypassing ansible collection path."""
    spec = importlib.util.spec_from_file_location("xslt_transformer", str(MODULE_PATH))
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["xslt_transformer"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
apply_xslt = _mod.apply_xslt
validate_xslt = _mod.validate_xslt
extract_template_rules = _mod.extract_template_rules


# ── apply_xslt ────────────────────────────────────────────────────────


def test_apply_xslt_identity_transform_preserves_structure() -> None:
    """The canonical XSLT identity transform returns the input unchanged."""
    xslt = (
        '<?xml version="1.0"?>\n'
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="@*|node()">'
        '<xsl:copy><xsl:apply-templates select="@*|node()"/></xsl:copy>'
        "</xsl:template>"
        "</xsl:stylesheet>"
    )
    xml = "<root><child>text</child></root>"
    out = apply_xslt(xml, xslt)
    assert "<root>" in out
    assert "<child>text</child>" in out
    assert "</root>" in out


def test_apply_xslt_simple_mapping_replaces_element_name() -> None:
    """A template matching one element emits a renamed wrapper."""
    xslt = (
        '<?xml version="1.0"?>\n'
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/"><wrapped><xsl:value-of select="."/></wrapped></xsl:template>'
        "</xsl:stylesheet>"
    )
    xml = "<data>hello</data>"
    out = apply_xslt(xml, xslt)
    assert "<wrapped>hello</wrapped>" in out


def test_apply_xslt_invalid_xml_input_raises() -> None:
    """A malformed XML input is rejected by the parser."""
    xslt = (
        '<?xml version="1.0"?>\n'
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/"><out/></xsl:template>'
        "</xsl:stylesheet>"
    )
    with pytest.raises((ValueError, Exception)):
        apply_xslt("<not><closed>", xslt)


def test_apply_xslt_empty_result_when_no_template_matches() -> None:
    """A stylesheet whose templates do not match the input yields an empty output."""
    xslt = (
        '<?xml version="1.0"?>\n'
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/nonexistent"><out/></xsl:template>'
        "</xsl:stylesheet>"
    )
    xml = "<root/>"
    out = apply_xslt(xml, xslt).strip()
    # Output may be empty or contain only the XML declaration / whitespace.
    assert "<out>" not in out


# ── validate_xslt ────────────────────────────────────────────────────


def test_validate_xslt_valid_stylesheet_returns_empty_list() -> None:
    """A well-formed stylesheet with at least one template validates cleanly."""
    xslt = (
        '<?xml version="1.0"?>\n'
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/"><out/></xsl:template>'
        "</xsl:stylesheet>"
    )
    issues = validate_xslt(xslt)
    assert issues == []


def test_validate_xslt_malformed_xml_reports_parse_error() -> None:
    """A stylesheet that is not well-formed XML is reported as a parse error."""
    issues = validate_xslt("<xsl:stylesheet><xsl:template match='/'>")
    assert len(issues) >= 1
    assert any("parse" in i.lower() or "xml" in i.lower() for i in issues)


def test_validate_xslt_missing_template_is_flagged() -> None:
    """A stylesheet root element with no xsl:template children is reported."""
    xslt = (
        '<?xml version="1.0"?>\n'
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        "</xsl:stylesheet>"
    )
    issues = validate_xslt(xslt)
    assert any("template" in i.lower() for i in issues)


# ── extract_template_rules ────────────────────────────────────────────


def test_extract_template_rules_single_template() -> None:
    """One xsl:template produces one rule dict with its match attribute."""
    xslt = (
        '<?xml version="1.0"?>\n'
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/root"><out/></xsl:template>'
        "</xsl:stylesheet>"
    )
    rules = extract_template_rules(xslt)
    assert len(rules) == 1
    assert rules[0]["match"] == "/root"


def test_extract_template_rules_multiple_templates_preserve_order() -> None:
    """Multiple xsl:template elements are returned in document order."""
    xslt = (
        '<?xml version="1.0"?>\n'
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/a"><out/></xsl:template>'
        '<xsl:template match="/b"><out/></xsl:template>'
        '<xsl:template match="/c"><out/></xsl:template>'
        "</xsl:stylesheet>"
    )
    rules = extract_template_rules(xslt)
    assert [r["match"] for r in rules] == ["/a", "/b", "/c"]


def test_extract_template_rules_no_templates_returns_empty() -> None:
    """A stylesheet with no xsl:template elements yields an empty list."""
    xslt = (
        '<?xml version="1.0"?>\n'
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:value-of select="."/>'
        "</xsl:stylesheet>"
    )
    rules = extract_template_rules(xslt)
    assert rules == []


def test_extract_template_rules_each_rule_has_match_key() -> None:
    """Every returned rule dict exposes a ``match`` key (None when absent)."""
    xslt = (
        '<?xml version="1.0"?>\n'
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/a"><out/></xsl:template>'
        '<xsl:template name="named-only"><out/></xsl:template>'
        "</xsl:stylesheet>"
    )
    rules = extract_template_rules(xslt)
    assert len(rules) == 2
    for rule in rules:
        assert "match" in rule
        assert "select" in rule
