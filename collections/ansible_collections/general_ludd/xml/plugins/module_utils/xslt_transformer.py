"""Bounded, fail-closed XSLT 1.0 transformations backed by lxml/libxslt."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, TypeAlias

from lxml import etree

MAX_INPUT_CHARS = 1_000_000
MAX_OUTPUT_CHARS = 4_000_000

XSLT_NS = "http://www.w3.org/1999/XSL/Transform"
_VALID_ROOTS = {f"{{{XSLT_NS}}}stylesheet", f"{{{XSLT_NS}}}transform"}
_PARAMETER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
_ACCESS_CONTROL = etree.XSLTAccessControl.DENY_ALL

TemplateValue: TypeAlias = str | list[str] | None


def _require_bounded(text: str, label: str) -> None:
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(f"{label} exceeds input limit of {MAX_INPUT_CHARS} characters")


def _xml_parser() -> Any:
    return etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        huge_tree=False,
        recover=False,
    )


def _parse_xml(text: str, label: str) -> Any:
    _require_bounded(text, label)
    try:
        return etree.fromstring(text.encode("utf-8"), parser=_xml_parser())
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"{label} is not well-formed XML: {exc}") from exc


def _compile_stylesheet(stylesheet: Any) -> Any:
    try:
        return etree.XSLT(stylesheet, access_control=_ACCESS_CONTROL)
    except etree.XSLTParseError as exc:
        raise ValueError(f"XSLT compile error: {exc}") from exc


def validate_xslt(xslt_text: str) -> list[str]:
    """Parse and compile a bounded stylesheet, returning human-readable issues."""
    _require_bounded(xslt_text, "XSLT stylesheet")
    if not xslt_text.strip():
        return ["XSLT text is empty"]
    try:
        root = _parse_xml(xslt_text, "XSLT")
    except ValueError as exc:
        return [str(exc)]

    issues: list[str] = []
    if root.tag not in _VALID_ROOTS:
        issues.append("Root element must be xsl:stylesheet or xsl:transform")
        return issues
    if root.get("version") is None:
        issues.append("Root xsl:stylesheet must declare a version attribute")
    try:
        _compile_stylesheet(root)
    except ValueError as exc:
        issues.append(str(exc))
    return issues


def _transform_parameters(params: Mapping[str, str] | None) -> dict[str, Any]:
    transformed: dict[str, Any] = {}
    for name, value in (params or {}).items():
        if _PARAMETER_NAME.fullmatch(name) is None:
            raise ValueError(f"invalid XSLT parameter name: {name!r}")
        if not isinstance(value, str):
            raise TypeError(f"XSLT parameter {name!r} must be a string")
        transformed[name] = etree.XSLT.strparam(value)
    return transformed


def apply_xslt(
    xml_text: str,
    xslt_text: str,
    params: Mapping[str, str] | None = None,
) -> str:
    """Apply a sandboxed XSLT stylesheet and return its bounded output."""
    issues = validate_xslt(xslt_text)
    if issues:
        raise ValueError(f"Invalid XSLT: {'; '.join(issues)}")
    xml_document = _parse_xml(xml_text, "XML")
    stylesheet = _parse_xml(xslt_text, "XSLT")
    transform = _compile_stylesheet(stylesheet)
    try:
        result = transform(xml_document, **_transform_parameters(params))
    except etree.XSLTApplyError as exc:
        raise ValueError(f"XSLT apply failed: {exc}") from exc
    output = str(result)
    if len(output) > MAX_OUTPUT_CHARS:
        raise ValueError(f"XSLT output limit exceeded: {len(output)} > {MAX_OUTPUT_CHARS}")
    return output


def extract_template_rules(xslt_text: str) -> list[dict[str, TemplateValue]]:
    """Return match/name/mode/value-of metadata for each XSLT template."""
    root = _parse_xml(xslt_text, "XSLT")
    template_tag = f"{{{XSLT_NS}}}template"
    value_of_tag = f"{{{XSLT_NS}}}value-of"
    rules: list[dict[str, TemplateValue]] = []
    for template in root.iter(template_tag):
        selects = [value for node in template.iter(value_of_tag) if (value := node.get("select"))]
        rules.append(
            {
                "match": template.get("match"),
                "name": template.get("name"),
                "mode": template.get("mode"),
                "selects": selects,
            }
        )
    return rules


__all__ = ["apply_xslt", "extract_template_rules", "validate_xslt"]
