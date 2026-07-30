"""TDD tests for the xslt_transformer module_util.

Tests the module at:
``collections/ansible_collections/general_ludd/xml/plugins/module_utils/xslt_transformer.py``

Imports directly via :mod:`importlib` from its file path. ``apply_xslt``
requires lxml (skipped if absent); ``validate_xslt`` and
``extract_template_rules`` use the stdlib and always run.
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

lxml = pytest.importorskip("lxml")


# ── fixtures ─────────────────────────────────────────────────────────


PEOPLE_XML = """<?xml version="1.0"?>
<people>
    <person id="1">
        <name>Alice</name>
        <age>30</age>
    </person>
    <person id="2">
        <name>Bob</name>
        <age>25</age>
    </person>
</people>
"""

IDENTITY_XSLT = """<?xml version="1.0"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:template match="@*|node()">
        <xsl:copy>
            <xsl:apply-templates select="@*|node()"/>
        </xsl:copy>
    </xsl:template>
</xsl:stylesheet>
"""

NAME_TO_H1_XSLT = """<?xml version="1.0"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:template match="/">
        <html><body>
            <xsl:for-each select="people/person">
                <h1><xsl:value-of select="name"/></h1>
            </xsl:for-each>
        </body></html>
    </xsl:template>
</xsl:stylesheet>
"""

PARAMETRIC_XSLT = """<?xml version="1.0"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:param name="title" select="'Default'"/>
    <xsl:template match="/">
        <report title="{$title}">
            <xsl:for-each select="people/person">
                <entry><xsl:value-of select="name"/></entry>
            </xsl:for-each>
        </report>
    </xsl:template>
</xsl:stylesheet>
"""

EMPTY_RESULT_XSLT = """<?xml version="1.0"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:template match="/">
        <xsl:for-each select="people/nonexistent">
            <found><xsl:value-of select="."/></found>
        </xsl:for-each>
    </xsl:template>
</xsl:stylesheet>
"""

MULTI_TEMPLATE_XSLT = """<?xml version="1.0"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:template match="people/person">
        <item><xsl:apply-templates select="name"/></item>
    </xsl:template>
    <xsl:template match="name">
        <label><xsl:value-of select="."/></label>
    </xsl:template>
</xsl:stylesheet>
"""

VALID_ROOT_ONLY_XSLT = """<?xml version="1.0"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"/>
"""


# ── apply_xslt ───────────────────────────────────────────────────────


def test_apply_xslt_identity_transform_preserves_structure() -> None:
    """The identity transform reproduces all elements/text of the input."""
    output = apply_xslt(PEOPLE_XML, IDENTITY_XSLT)
    assert "<people>" in output
    assert "<name>Alice</name>" in output
    assert "<name>Bob</name>" in output


def test_apply_xslt_simple_mapping_renames_elements() -> None:
    """A stylesheet maps person names into <h1> headings inside <html>."""
    output = apply_xslt(PEOPLE_XML, NAME_TO_H1_XSLT)
    assert "<h1>Alice</h1>" in output
    assert "<h1>Bob</h1>" in output


def test_apply_xslt_invalid_xml_input_raises() -> None:
    """Malformed XML input is rejected (not silently dropped)."""
    with pytest.raises(Exception):
        apply_xslt("<people><person></people>", IDENTITY_XSLT)


def test_apply_xslt_empty_result_returns_whitespace_only() -> None:
    """A transform that matches nothing yields no element content."""
    output = apply_xslt(PEOPLE_XML, EMPTY_RESULT_XSLT)
    assert "<found>" not in output


# ── validate_xslt ────────────────────────────────────────────────────


def test_validate_xslt_valid_stylesheet_returns_no_issues() -> None:
    """A well-formed xsl:stylesheet with a version attribute is valid."""
    assert validate_xslt(IDENTITY_XSLT) == []


def test_validate_xslt_empty_text_reports_empty_issue() -> None:
    """Empty XSLT text is reported as an issue rather than raising."""
    issues = validate_xslt("")
    assert issues
    assert "empty" in issues[0].lower()


def test_validate_xslt_malformed_xml_reports_parse_issue() -> None:
    """XSLT that is not well-formed XML is reported (no exception)."""
    issues = validate_xslt("<xsl:stylesheet><xsl:template></xsl:stylesheet>")
    assert issues
    assert any("not well-formed" in i.lower() for i in issues)


def test_validate_xslt_wrong_root_element_reports_issue() -> None:
    """A non-xsl root element is reported as a validation issue."""
    issues = validate_xslt("<html><body>not xslt</body></html>")
    assert issues
    assert any("root" in i.lower() for i in issues)


def test_validate_xslt_missing_version_attribute_reports_issue() -> None:
    """An xsl:stylesheet without a version attribute is reported."""
    no_version = '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform"/>'
    issues = validate_xslt(no_version)
    assert issues
    assert any("version" in i.lower() for i in issues)


# ── extract_template_rules ───────────────────────────────────────────


def test_extract_template_rules_single_template() -> None:
    """A stylesheet with one xsl:template yields one rule dict."""
    rules = extract_template_rules(IDENTITY_XSLT)
    assert len(rules) == 1
    assert rules[0]["match"] == "@*|node()"


def test_extract_template_rules_multiple_templates() -> None:
    """A stylesheet with multiple xsl:template elements yields one rule each."""
    rules = extract_template_rules(MULTI_TEMPLATE_XSLT)
    matches = [r["match"] for r in rules]
    assert "people/person" in matches
    assert "name" in matches


def test_extract_template_rules_captures_value_of_selects() -> None:
    """value-of select expressions inside a template are captured."""
    rules = extract_template_rules(NAME_TO_H1_XSLT)
    assert rules
    root_rule = next(r for r in rules if r["match"] == "/")
    assert "name" in root_rule["selects"]


def test_extract_template_rules_no_templates_returns_empty() -> None:
    """A stylesheet with no xsl:template elements yields an empty list."""
    assert extract_template_rules(VALID_ROOT_ONLY_XSLT) == []


def test_extract_template_rules_invalid_xslt_raises() -> None:
    """Non-XML input raises rather than returning a misleading empty list."""
    with pytest.raises(Exception):
        extract_template_rules("<<<not xml>>>")
