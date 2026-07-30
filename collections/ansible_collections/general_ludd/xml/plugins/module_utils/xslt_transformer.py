"""xslt_transformer -- Apply and introspect XSLT transformations.

A utility module for the ``general_ludd.xml`` collection. Sibling to
:mod:`xml_core` (parse/XPath/namespace primitives) and :mod:`xsd_generator`
(schema inference).

Three pure-string primitives:

    apply_xslt(xml_text, xslt_text)        -> str          (transformed output)
    validate_xslt(xslt_text)               -> list[str]    (list of issues)
    extract_template_rules(xslt_text)      -> list[dict]   (one per xsl:template)

When ``lxml`` is importable, :func:`apply_xslt` delegates to the native
``lxml.etree.XSLT`` processor (full XSLT 1.0). When ``lxml`` is absent, a
minimal stdlib-only fallback handles the common cases: identity transform,
root match with literal output elements, and ``xsl:value-of`` text
extraction. The fallback is intentionally narrow; callers needing full XSLT
should install ``lxml``.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

_XSL_NS = "http://www.w3.org/1999/XSL/Transform"

try:  # pragma: no cover - import gate
    from lxml import etree as _lxml_etree  # type: ignore[import-not-found]

    _HAS_LXML = True
except ImportError:  # pragma: no cover - import gate
    _lxml_etree = None  # type: ignore[assignment]
    _HAS_LXML = False


# ── apply_xslt ────────────────────────────────────────────────────────


def apply_xslt(xml_text: str, xslt_text: str) -> str:
    """Apply an XSLT stylesheet to an XML document, returning the result string.

    Uses ``lxml.etree.XSLT`` when available (full XSLT 1.0). Otherwise falls
    back to :func:`_apply_xslt_fallback`, a minimal stdlib-only transform
    that handles identity transforms, root-matched literal output, and
    ``xsl:value-of``.

    Args:
        xml_text: a well-formed XML document string.
        xslt_text: a well-formed XSLT stylesheet string.

    Returns:
        The serialized result of the transformation.

    Raises:
        ValueError: if either input cannot be parsed (wraps the underlying
            parse error with context about which document failed).
    """
    if _HAS_LXML:
        return _apply_xslt_lxml(xml_text, xslt_text)
    return _apply_xslt_fallback(xml_text, xslt_text)


def _apply_xslt_lxml(xml_text: str, xslt_text: str) -> str:
    """Native lxml XSLT 1.0 transform path."""
    assert _lxml_etree is not None  # narrowing for type checkers
    try:
        xml_doc = _lxml_etree.fromstring(xml_text.encode("utf-8"))
    except _lxml_etree.XMLSyntaxError as exc:
        raise ValueError(f"XML parse error: {exc}") from exc
    try:
        xslt_doc = _lxml_etree.fromstring(xslt_text.encode("utf-8"))
    except _lxml_etree.XMLSyntaxError as exc:
        raise ValueError(f"XSLT parse error: {exc}") from exc
    try:
        transform = _lxml_etree.XSLT(xslt_doc)
    except _lxml_etree.XSLTParseError as exc:
        raise ValueError(f"XSLT compile error: {exc}") from exc
    try:
        result = transform(xml_doc)
    except _lxml_etree.XSLTApplyError as exc:
        raise ValueError(f"XSLT apply error: {exc}") from exc
    return str(result)


def _apply_xslt_fallback(xml_text: str, xslt_text: str) -> str:
    """Minimal stdlib-only transform (no lxml).

    Supports:
      * ``<xsl:template match="@*|node()">`` identity (copy + apply-templates)
      * ``<xsl:template match="/">`` with literal output elements
      * ``<xsl:value-of select="."/>`` text emission
      * ``<xsl:apply-templates select="@*|node()"/>`` recursion
    """
    try:
        xml_root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"XML parse error: {exc}") from exc
    try:
        xslt_root = ET.fromstring(xslt_text)
    except ET.ParseError as exc:
        raise ValueError(f"XSLT parse error: {exc}") from exc

    templates = _collect_templates_fallback(xslt_root)
    out_buf: list[str] = []

    root_match = templates.get("/") or templates.get("/*")
    if root_match is None:
        return ""
    _render_template(root_match, xml_root, templates, out_buf)
    return "".join(out_buf)


def _collect_templates_fallback(xslt_root: ET.Element) -> dict[str, ET.Element]:
    """Map ``match`` attribute -> template element, for the fallback path."""
    templates: dict[str, ET.Element] = {}
    for tpl in xslt_root.iter(f"{{{_XSL_NS}}}template"):
        match = tpl.get("match")
        if match is not None:
            templates[match] = tpl
    return templates


def _render_template(
    tpl: ET.Element,
    ctx: ET.Element,
    templates: dict[str, ET.Element],
    out: list[str],
) -> None:
    """Render a single template body against ``ctx`` into ``out``."""
    for child in tpl:
        tag = child.tag
        if tag == f"{{{_XSL_NS}}}value-of":
            select = child.get("select", ".")
            if select in (".", "text()") and ctx.text:
                out.append(ctx.text)
        elif tag == f"{{{_XSL_NS}}}copy":
            out.append(_serialize(ctx))
        elif tag == f"{{{_XSL_NS}}}apply-templates":
            select = child.get("select", "@*|node()")
            if select in ("@*|node()", "node()"):
                out.append(_serialize(ctx))
        elif not tag.startswith(f"{{{_XSL_NS}}}"):
            # Literal output element: emit open tag, body, close tag.
            out.append(f"<{tag}>")
            if child.text and child.text.strip():
                out.append(child.text)
            # xsl:value-of nested inside a literal element emits ctx text.
            has_value_of = False
            for _inner in child.iter(f"{{{_XSL_NS}}}value-of"):
                has_value_of = True
                if ctx.text:
                    out.append(ctx.text)
                break
            if not has_value_of:
                for grandchild in child:
                    _render_template(grandchild, ctx, templates, out)
            out.append(f"</{tag}>")


def _serialize(el: ET.Element) -> str:
    """Serialize an element (and children) using stdlib ElementTree."""
    return ET.tostring(el, encoding="unicode")


# ── validate_xslt ─────────────────────────────────────────────────────


def validate_xslt(xslt_text: str) -> list[str]:
    """Return a list of issues found in ``xslt_text``.

    An empty list means the stylesheet is well-formed XML, rooted at
    ``xsl:stylesheet`` (or ``xsl:transform``), and contains at least one
    ``xsl:template`` element. Non-empty entries are human-readable strings.

    Args:
        xslt_text: a candidate XSLT stylesheet string.

    Returns:
        A list of issue strings (empty when the stylesheet is valid).
    """
    issues: list[str] = []

    if _HAS_LXML:
        issues.extend(_validate_xslt_lxml(xslt_text))
    else:
        issues.extend(_validate_xslt_fallback(xslt_text))

    if not issues:
        # Structural: must declare at least one xsl:template.
        try:
            root = ET.fromstring(xslt_text)
        except ET.ParseError:
            return issues  # already reported above
        templates = list(root.iter(f"{{{_XSL_NS}}}template"))
        if not templates:
            issues.append("stylesheet declares no xsl:template elements")
    return issues


def _validate_xslt_lxml(xslt_text: str) -> list[str]:
    """Use lxml's XSLT compiler to surface parse/compile errors."""
    assert _lxml_etree is not None
    issues: list[str] = []
    try:
        xslt_doc = _lxml_etree.fromstring(xslt_text.encode("utf-8"))
    except _lxml_etree.XMLSyntaxError as exc:
        issues.append(f"XSLT parse error: {exc}")
        return issues
    try:
        _lxml_etree.XSLT(xslt_doc)
    except _lxml_etree.XSLTParseError as exc:
        issues.append(f"XSLT compile error: {exc}")
    return issues


def _validate_xslt_fallback(xslt_text: str) -> list[str]:
    """Stdlib-only parse check used when lxml is unavailable."""
    issues: list[str] = []
    try:
        root = ET.fromstring(xslt_text)
    except ET.ParseError as exc:
        issues.append(f"XSLT parse error: {exc}")
        return issues
    if not root.tag.startswith(f"{{{_XSL_NS}}}"):
        issues.append(f"root element is not in the XSLT namespace: {root.tag}")
    return issues


# ── extract_template_rules ────────────────────────────────────────────


def extract_template_rules(xslt_text: str) -> list[dict[str, Any]]:
    """Extract one rule dict per ``xsl:template`` in ``xslt_text``.

    Each returned dict has the keys ``match`` and ``select``:

    * ``match`` — the value of the template's ``match`` attribute, or
      ``None`` when the template is named-only (no ``match``).
    * ``select`` — the value of the first ``xsl:apply-templates`` or
      ``xsl:value-of`` ``select`` attribute within the template body, or
      ``None`` when absent.

    Rules are returned in document order. A stylesheet with no
    ``xsl:template`` elements yields an empty list. Malformed XML raises
    :class:`ValueError` (wrapping the underlying parse error).

    Args:
        xslt_text: a well-formed XSLT stylesheet string.

    Returns:
        A list of ``{"match": str | None, "select": str | None}`` dicts.
    """
    try:
        root = ET.fromstring(xslt_text)
    except ET.ParseError as exc:
        raise ValueError(f"XSLT parse error: {exc}") from exc

    rules: list[dict[str, Any]] = []
    for tpl in root.iter(f"{{{_XSL_NS}}}template"):
        match = tpl.get("match")
        select = _first_select(tpl)
        rules.append({"match": match, "select": select})
    return rules


def _first_select(tpl: ET.Element) -> str | None:
    """Return the first ``select`` attribute on any XSLT action in ``tpl``."""
    for action in tpl.iter():
        if action.tag.startswith(f"{{{_XSL_NS}}}"):
            sel = action.get("select")
            if sel is not None:
                return sel
    return None


__all__ = ["apply_xslt", "extract_template_rules", "validate_xslt"]
