"""TDD tests for the xslt_transformer module_util.

Tests the module at:
``collections/ansible_collections/general_ludd/xml/plugins/module_utils/xslt_transformer.py``

Imports directly via :mod:`importlib` from its file path. The feature uses
the documented mature ``lxml`` XSLT 1.0 implementation for every operation.
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
MAX_INPUT_CHARS = _mod.MAX_INPUT_CHARS


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
    with pytest.raises(ValueError, match="XML is not well-formed"):
        apply_xslt("<people><person></people>", IDENTITY_XSLT)


def test_apply_xslt_empty_result_returns_whitespace_only() -> None:
    """A transform that matches nothing yields no element content."""
    output = apply_xslt(PEOPLE_XML, EMPTY_RESULT_XSLT)
    assert "<found>" not in output


def test_apply_xslt_escapes_string_parameters() -> None:
    """Caller parameters are passed as literal strings, not XPath expressions."""
    output = apply_xslt(PEOPLE_XML, PARAMETRIC_XSLT, params={"title": "Alice's report"})
    assert 'title="Alice\'s report"' in output


def test_apply_xslt_rejects_invalid_parameter_names() -> None:
    """Parameter names cannot inject expressions or extension syntax."""
    with pytest.raises(ValueError, match="parameter name"):
        apply_xslt(PEOPLE_XML, PARAMETRIC_XSLT, params={"title)": "unsafe"})


def test_apply_xslt_denies_stylesheet_file_reads(tmp_path: Path) -> None:
    """The XSLT document() function cannot read local files."""
    secret = tmp_path / "secret.xml"
    secret.write_text("<secret>classified</secret>", encoding="utf-8")
    stylesheet = f"""<xsl:stylesheet version="1.0"
        xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
        <xsl:template match="/"><out><xsl:value-of select="document('{secret.as_uri()}')/secret"/></out></xsl:template>
    </xsl:stylesheet>"""
    with pytest.raises(ValueError, match=r"access|rights|denied"):
        apply_xslt("<root/>", stylesheet)


def test_apply_xslt_does_not_expand_external_entities(tmp_path: Path) -> None:
    """The XML parser leaves external entities unresolved and never reads the file."""
    secret = tmp_path / "secret.txt"
    secret.write_text("classified", encoding="utf-8")
    xml = f'<!DOCTYPE root [<!ENTITY leaked SYSTEM "{secret.as_uri()}">]><root>&leaked;</root>'
    output = apply_xslt(xml, IDENTITY_XSLT)
    assert "classified" not in output


def test_apply_xslt_enforces_output_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transformation result larger than the configured bound fails closed."""
    monkeypatch.setattr(_mod, "MAX_OUTPUT_CHARS", 4)
    with pytest.raises(ValueError, match="output limit"):
        apply_xslt(PEOPLE_XML, IDENTITY_XSLT)


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


def test_validate_xslt_reports_compile_errors() -> None:
    """Validation asks libxslt to compile expressions, not just parse XML."""
    invalid_xpath = """<xsl:stylesheet version="1.0"
        xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
        <xsl:template match="/"><xsl:value-of select="["/></xsl:template>
    </xsl:stylesheet>"""
    issues = validate_xslt(invalid_xpath)
    assert any("compile" in issue.lower() for issue in issues)


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
    with pytest.raises(ValueError, match="XSLT is not well-formed"):
        extract_template_rules("<<<not xml>>>")


def test_extract_template_rules_captures_named_mode_rule() -> None:
    """Named templates retain their mode and all value-of selectors."""
    stylesheet = """<xsl:stylesheet version="1.0"
        xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
        <xsl:template name="render" mode="compact">
            <xsl:value-of select="name"/><xsl:value-of select="age"/>
        </xsl:template>
    </xsl:stylesheet>"""
    assert extract_template_rules(stylesheet) == [
        {"match": None, "name": "render", "mode": "compact", "selects": ["name", "age"]}
    ]


@pytest.mark.parametrize("operation", [validate_xslt, extract_template_rules])
def test_xslt_text_operations_reject_oversized_input(operation: Any) -> None:
    """Stylesheet inspection shares the transformation resource bound."""
    with pytest.raises(ValueError, match="limit"):
        operation("x" * (MAX_INPUT_CHARS + 1))
