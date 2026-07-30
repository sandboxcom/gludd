"""xslt_transformer -- Apply and author XSLT transformations.

Text-based primitives shared by the ``general_ludd.xml.xslt_transformer``
role. Three public functions operate on in-memory strings (not file paths):

    apply_xslt(xml_text, xslt_text)          -> str
        Apply a stylesheet to an XML document; return the transformed
        output. Requires ``lxml`` (the stdlib has no XSLT processor).

    validate_xslt(xslt_text)                 -> list[str]
        Static checks against a stylesheet; returns a list of human-readable
        issues (empty list == valid). Uses the stdlib ``ElementTree`` so it
        works without ``lxml`` installed.

    extract_template_rules(xslt_text)        -> list[dict]
        Parse ``xsl:template`` declarations from a stylesheet, returning one
        dict per template with its ``match`` (or ``name``), any ``value-of``
        selects, and its ``mode``.

The XSLT namespace is ``http://www.w3.org/1999/XSL/Transform``.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

XSLT_NS = "http://www.w3.org/1999/XSL/Transform"
_VALID_ROOTS = (f"{{{XSLT_NS}}}stylesheet", f"{{{XSLT_NS}}}transform")


def _strip_ns(tag: str) -> str:
    """Return the local name of a (possibly Clark-notation) tag."""
    return tag.rpartition("}")[2] if "}" in tag else tag


def validate_xslt(xslt_text: str) -> list[str]:
    """Static-validate an XSLT stylesheet.

    Returns a list of issue strings; an empty list means the stylesheet is
    structurally valid (well-formed XML, an ``xsl:stylesheet``/``xsl:transform``
    root, and a declared ``version`` attribute). Never raises on bad input —
    a problem is reported as an issue.
    """
    issues: list[str] = []
    if not xslt_text or not xslt_text.strip():
        issues.append("XSLT text is empty")
        return issues

    try:
        root = ET.fromstring(xslt_text)
    except ET.ParseError as exc:
        issues.append(f"XSLT is not well-formed XML: {exc}")
        return issues

    if root.tag not in _VALID_ROOTS:
        issues.append(f"Root element must be xsl:stylesheet or xsl:transform (got {_strip_ns(root.tag)!r})")
        return issues

    if root.get("version") is None:
        issues.append("Root xsl:stylesheet must declare a version attribute")

    return issues


def apply_xslt(xml_text: str, xslt_text: str) -> str:
    """Apply ``xslt_text`` to ``xml_text`` and return the transformed string.

    Raises:
        ValueError: if the stylesheet is structurally invalid.
        ImportError: if ``lxml`` is not installed.
        lxml.etree.XMLSyntaxError: if ``xml_text`` is not well-formed.
        lxml.etree.XSLTParseError: if the stylesheet cannot be compiled.
        lxml.etree.XSLTApplyError: if the transformation itself fails.
    """
    issues = validate_xslt(xslt_text)
    if issues:
        raise ValueError(f"Invalid XSLT: {'; '.join(issues)}")

    try:
        from lxml import etree
    except ImportError as exc:
        raise ImportError("apply_xslt requires lxml. Install with: pip install lxml") from exc

    xml_doc = etree.fromstring(xml_text.encode("utf-8"))
    xslt_doc = etree.fromstring(xslt_text.encode("utf-8"))
    transform = etree.XSLT(xslt_doc)
    result = transform(xml_doc)
    return str(result)


def extract_template_rules(xslt_text: str) -> list[dict]:
    """Parse ``xsl:template`` rules from a stylesheet.

    Returns a list of dicts, one per ``xsl:template`` element, in document
    order. Each dict has:

        ``match``:   the ``match`` attribute, or ``None`` for named templates
        ``name``:    the ``name`` attribute, or ``None``
        ``mode``:    the ``mode`` attribute, or ``None``
        ``selects``: the ``select`` values of descendant ``xsl:value-of`` elements

    Raises:
        xml.etree.ElementTree.ParseError: if ``xslt_text`` is not well-formed
            XML (so callers cannot mistake a parse failure for "no templates").
    """
    root = ET.fromstring(xslt_text)
    template_tag = f"{{{XSLT_NS}}}template"
    value_of_tag = f"{{{XSLT_NS}}}value-of"

    rules: list[dict] = []
    for tmpl in root.iter(template_tag):
        selects = [v.get("select") for v in tmpl.iter(value_of_tag) if v.get("select")]
        rules.append(
            {
                "match": tmpl.get("match"),
                "name": tmpl.get("name"),
                "mode": tmpl.get("mode"),
                "selects": selects,
            }
        )
    return rules
