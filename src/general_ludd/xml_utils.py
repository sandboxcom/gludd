"""Shared XML operations for Ansible XML collection roles.

Uses a resource-bounded ``defusedxml`` boundary for every untrusted parse;
falls back to locked-down lxml for XSLT and XPath features when available.
"""

from __future__ import annotations

import plistlib
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import UTC
from pathlib import Path
from typing import Any, cast

from general_ludd.security.secure_xml import (
    DEFAULT_XML_SECURITY_LIMITS,
    XmlSecurityAuditSink,
    XmlSecurityLimits,
    read_xml_payload,
    validate_xml_payload,
    validate_xml_tree,
)
from general_ludd.security.secure_xml import (
    parse_xml_file as secure_parse_xml_file,
)
from general_ludd.security.secure_xml import (
    parse_xml_string as secure_parse_xml_string,
)

_DEFAULT_SOAP_NS_12 = "http://www.w3.org/2003/05/soap-envelope"
_DEFAULT_SOAP_NS_11 = "http://schemas.xmlsoap.org/soap/envelope/"
_DEFAULT_SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
_DEFAULT_DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"
_NAMESPACE_PATTERN = re.compile(r'xmlns(?::(\w+))?\s*=\s*"([^"]*)"')

_LXML_AVAILABLE = False
try:
    import lxml.etree as lxml_etree

    _LXML_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------


def parse_xml(
    source: str | Path,
    is_html: bool = False,
    html: bool = False,
    *,
    limits: XmlSecurityLimits = DEFAULT_XML_SECURITY_LIMITS,
    audit_sink: XmlSecurityAuditSink | None = None,
) -> ET.ElementTree:
    """Parse an XML (or HTML) string or file path into an ElementTree.

    When *source* is a file path it must exist on disk; otherwise it is
    treated as a raw XML string.
    """
    use_html = is_html or html
    source_str = str(source)
    path = _existing_file(source_str)
    if path is not None:
        if _LXML_AVAILABLE and use_html:
            raw = read_xml_payload(
                path,
                limits=limits,
                source=str(path),
                audit_sink=audit_sink,
            )
            return _parse_lxml_html(
                raw,
                limits=limits,
                source=str(path),
                audit_sink=audit_sink,
            )
        return secure_parse_xml_file(
            path,
            limits=limits,
            source=str(path),
            audit_sink=audit_sink,
        )
    if _LXML_AVAILABLE and use_html:
        return _parse_lxml_html(
            source_str,
            limits=limits,
            source="memory-html",
            audit_sink=audit_sink,
        )
    root = secure_parse_xml_string(
        source_str,
        limits=limits,
        source="memory-xml",
        audit_sink=audit_sink,
    )
    return ET.ElementTree(root)


def _lxml_tree(tree: ET.ElementTree) -> Any:
    """Convert an stdlib ElementTree to an lxml ElementTree if available."""
    if _LXML_AVAILABLE:
        return _secure_lxml_root(_serialize(tree), source="element-tree")
    return tree


# ---------------------------------------------------------------------------
# XPath with namespace support
# ---------------------------------------------------------------------------


def xpath_query(
    tree: ET.ElementTree,
    query: str,
    namespaces: dict[str, str] | None = None,
) -> list[ET.Element] | float | int:
    """Run an XPath query against *tree* with optional namespace map.

    When lxml is available the query is executed with the full lxml XPath
    engine (XPath 1.0).  With stdlib-only the limited ``ElementTree`` XPath
    subset is used.

    Returns a list of matching Elements, or a numeric value for count/sum
    expressions.
    """
    ns = namespaces or {}
    if _LXML_AVAILABLE:
        lxml_root = _lxml_tree(tree)
        result = lxml_root.xpath(query, namespaces=ns)
        if _is_numeric_xpath_query(query):
            if isinstance(result, list):
                return len(result)
            if isinstance(result, float):
                return result
            if isinstance(result, int):
                return result
            return float(result)
        return _cast_xpath_result(result)
    root = tree.getroot()
    if root is None:
        return []
    if ns:
        result = root.findall(query, ns)
        return result
    try:
        result = root.findall(query)
        return result
    except SyntaxError:
        return _stdlib_xpath_fallback(root, query, ns)


def _is_numeric_xpath_query(query: str) -> bool:
    return query.strip().lower().startswith(("count(", "sum(", "number(", "string-length("))


def _cast_xpath_result(result: Any) -> list[ET.Element]:
    """Normalise lxml XPath return values to list[ET.Element]."""
    items: list[ET.Element] = []
    if result is None:
        return items
    if isinstance(result, list):
        for r in result:
            if hasattr(r, "tag"):
                items.append(r)
        return items
    if hasattr(result, "tag"):
        return [result]
    return items


def _stdlib_xpath_fallback(root: ET.Element, query: str, namespaces: dict[str, str]) -> list[ET.Element]:
    """Resolve simple namespace-prefixed paths on stdlib ElementTree.

    Strips leading ``.//`` or ``/`` and walks children with namespace
    expansion.  Not a full XPath implementation.
    """
    if query.startswith(".//"):
        path = query[3:]
    elif query.startswith("/"):
        path = query[1:]
    else:
        path = query
    parts = path.split("/")
    try:
        tag = _expand_tag(parts[-1], namespaces)
    except (IndexError, KeyError):
        return []
    return root.findall(f".//{tag}")


def _expand_tag(tag: str, namespaces: dict[str, str]) -> str:
    """Replace *prefix:local* with ``{uri}local``."""
    if ":" not in tag:
        return tag
    prefix, local = tag.split(":", 1)
    uri = namespaces.get(prefix, "")
    return f"{{{uri}}}{local}"


# ---------------------------------------------------------------------------
# XSD generation from samples
# ---------------------------------------------------------------------------


def infer_xsd(
    sample: str | list[str],
    additional: list[str] | None = None,
    target_namespace: str | None = None,
) -> str:
    """Generate a best-effort XSD from a set of XML sample files or strings.

    The inference walks element/attribute names, optionality, and nesting
    depth.  Complex-type restrictions and enumeration facets are not
    derived; the result is a structural skeleton suitable for validation
    tuning.
    """
    sample_files: list[str] = []
    if isinstance(sample, list):
        sample_files.extend(sample)
    else:
        sample_files.append(sample)
    if additional:
        sample_files.extend(additional)

    elements: dict[str, dict[str, Any]] = {}
    for filepath in sample_files:
        tree = parse_xml(filepath)
        root = tree.getroot()
        if root is not None:
            _collect_xsd_elements(root, elements)

    lines: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    ns_attr = ""
    if target_namespace:
        ns_attr = f' xmlns:tns="{target_namespace}" targetNamespace="{target_namespace}"'
    lines.append(f'<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"{ns_attr}>')
    for elem_name, info in sorted(elements.items()):
        lines.append(f'  <xsd:element name="{elem_name}">')
        child_names = info.get("children", [])
        if child_names:
            lines.append("    <xsd:complexType>")
            lines.append("      <xsd:sequence>")
            for child in sorted(child_names):
                min_occurs = info.get("required", {}).get(child, 0)
                lines.append(f'        <xsd:element ref="{child}" minOccurs="{min_occurs}"/>')
            lines.append("      </xsd:sequence>")
            lines.append("    </xsd:complexType>")
        else:
            lines.append('    <xsd:complexType mixed="true"/>')
        lines.append("  </xsd:element>")
    lines.append("</xsd:schema>")
    return "\n".join(lines)


def _collect_xsd_elements(
    elem: ET.Element,
    elements: dict[str, dict[str, Any]],
    parent_name: str | None = None,
) -> None:
    tag_local = elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag
    info = elements.setdefault(tag_local, {"children": set(), "required": {}})
    children: set[str] = info["children"]
    required: dict[str, int] = info["required"]
    for child in elem:
        child_local = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
        children.add(child_local)
        required[child_local] = required.get(child_local, 0) + 1
        _collect_xsd_elements(child, elements, tag_local)
    if parent_name:
        elements.setdefault(parent_name, {"children": set(), "required": {}})
        elements[parent_name]["required"].setdefault(tag_local, 0)
        elements[parent_name]["required"][tag_local] = max(
            elements[parent_name]["required"].get(tag_local, 0),
            required.get(tag_local, 0),
        )


# ---------------------------------------------------------------------------
# XSLT transformation
# ---------------------------------------------------------------------------


def apply_xslt(
    xml_input: str | Path,
    xslt_file: str | Path,
    params: dict[str, str] | None = None,
) -> str:
    """Apply an XSLT stylesheet to XML input, returning the result string.

    Requires *lxml* for the XSLT engine.  Raises :exc:`RuntimeError` when
    lxml is not installed.
    """
    if not _LXML_AVAILABLE:
        raise RuntimeError("apply_xslt requires lxml; install it with 'pip install lxml'")
    xml_doc = _resolve_xml_source(xml_input)
    xslt_doc = _resolve_xml_source(xslt_file)
    access_control = lxml_etree.XSLTAccessControl(
        read_file=False,
        write_file=False,
        create_dir=False,
        read_network=False,
        write_network=False,
    )
    transform = lxml_etree.XSLT(xslt_doc, access_control=access_control)
    result = (
        transform(xml_doc, **{k: _xslt_param_value(v) for k, v in params.items()}) if params else transform(xml_doc)
    )
    return str(result)


def _resolve_xml_source(source: str | Path) -> Any:
    return _lxml_tree(parse_xml(source))


def _xslt_param_value(value: str) -> Any:
    return lxml_etree.XSLT.strparam(value)


# ---------------------------------------------------------------------------
# XML ↔ dict
# ---------------------------------------------------------------------------


def xml_to_dict(xml_input: str | ET.Element) -> dict[str, Any]:
    """Convert an XML string or Element to a nested dict.

    Attributes are stored under ``@attr_name`` keys.  Child elements become
    nested dicts.  Repeated child element tags become lists.  Text content
    is stored under the ``#text`` key when other children coexist.
    """
    if isinstance(xml_input, str):
        element: ET.Element = secure_parse_xml_string(
            xml_input,
            source="xml-to-dict",
        )
    else:
        element = xml_input

    tag_local = element.tag.rsplit("}", 1)[-1] if "}" in element.tag else element.tag
    result: dict[str, Any] = {}
    for attr_key, attr_val in element.attrib.items():
        result[f"@{attr_key}"] = attr_val
    child_groups: dict[str, list[ET.Element]] = defaultdict(list)
    for child in element:
        child_local = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
        child_groups[child_local].append(child)
    for child_tag, elems in child_groups.items():
        converted = [xml_to_dict(e)[child_tag] for e in elems]
        result[child_tag] = converted if len(converted) > 1 else converted[0]
    text = (element.text or "").strip()
    if text:
        if result:
            result["#text"] = text
        else:
            return {tag_local: text}
    return {tag_local: result}


def dict_to_xml(data: dict[str, Any], root_name: str = "root") -> str:
    """Convert a nested dict into a pretty-printed XML string."""

    def _build(name: str, value: Any) -> ET.Element:
        elem = ET.Element(name)
        if isinstance(value, dict):
            for k, v in value.items():
                if k.startswith("@"):
                    elem.set(k[1:], str(v))
                elif k == "#text":
                    elem.text = str(v)
                else:
                    if isinstance(v, list):
                        for item in v:
                            elem.append(_build(k, item))
                    else:
                        elem.append(_build(k, v))
        elif isinstance(value, list):
            for item in value:
                elem.append(_build(name, item))
        else:
            elem.text = str(value)
        return elem

    root = _build(root_name, data)
    raw = ET.tostring(root, encoding="unicode", method="xml")
    return _pretty_print_xml(raw)


# ---------------------------------------------------------------------------
# Namespace utilities
# ---------------------------------------------------------------------------


def extract_namespaces(xml_string: str) -> dict[str, str]:
    """Extract the namespace prefix→URI map from an XML string."""
    namespaces: dict[str, str] = {}
    for match in _NAMESPACE_PATTERN.finditer(xml_string):
        prefix = match.group(1) or ""
        uri = match.group(2)
        if prefix:
            namespaces[prefix] = uri
        else:
            namespaces.setdefault("default", uri)
    return namespaces


def register_namespaces(tree: ET.ElementTree, namespaces: dict[str, str]) -> None:
    """Register namespace prefixes on *tree* so serialisation uses them."""
    for prefix, uri in namespaces.items():
        if prefix:
            ET.register_namespace(prefix, uri)


# ---------------------------------------------------------------------------
# SOAP helpers
# ---------------------------------------------------------------------------


def build_soap_envelope(
    body_content: str,
    soap_version: str = "1.2",
    version: str | None = None,
    header: str | None = None,
) -> str:
    """Wrap *body_content* in a SOAP envelope.

    *version* (or *soap_version*) may be ``"1.1"`` or ``"1.2"`` (default).
    *header* optionally wraps additional header content.
    """
    sv = version or soap_version
    ns_env = _DEFAULT_SOAP_NS_11 if sv == "1.1" else _DEFAULT_SOAP_NS_12

    ET.register_namespace("soap", ns_env)
    envelope = ET.Element(f"{{{ns_env}}}Envelope")
    header_el = ET.SubElement(envelope, f"{{{ns_env}}}Header")
    if header:
        try:
            header_payload = secure_parse_xml_string(header, source="soap-header")
            header_el.append(header_payload)
        except ET.ParseError:
            header_el.text = header
    body = ET.SubElement(envelope, f"{{{ns_env}}}Body")
    try:
        payload = secure_parse_xml_string(body_content, source="soap-body")
        body.append(payload)
    except ET.ParseError:
        body.text = body_content
    return _pretty_print_xml(ET.tostring(envelope, encoding="unicode", method="xml"))


def parse_soap_response(response_xml: str) -> dict[str, Any]:
    """Parse a SOAP response XML into a dict with *envelope*, *header*, *body*."""
    root = secure_parse_xml_string(response_xml, source="soap-response")
    ns = extract_namespaces(response_xml)
    soap_uri = (
        _DEFAULT_SOAP_NS_12
        if _matches_ns(root.tag, _DEFAULT_SOAP_NS_12)
        else ns.get("soap", ns.get("default", _DEFAULT_SOAP_NS_12))
    )
    body_tag = f"{{{soap_uri}}}Body"
    header_tag = f"{{{soap_uri}}}Header"
    result: dict[str, Any] = {"envelope": xml_to_dict(root)}
    for child in root:
        if child.tag == body_tag:
            body_children = list(child)
            result["body"] = [xml_to_dict(c) for c in body_children] if body_children else (child.text or "").strip()
        elif child.tag == header_tag:
            result["header"] = xml_to_dict(child)
    return result


def _matches_ns(tag: str, uri: str) -> bool:
    return tag.startswith(f"{{{uri}}}")


# ---------------------------------------------------------------------------
# SAML helpers
# ---------------------------------------------------------------------------


def parse_saml_assertion(saml_xml: str) -> dict[str, Any]:
    """Extract key fields from a SAML 2.0 assertion."""
    from datetime import datetime as dt
    from xml.parsers.expat import ExpatError

    try:
        root = secure_parse_xml_string(saml_xml, source="saml-assertion")
    except (ET.ParseError, ExpatError):
        return {}
    ns = extract_namespaces(saml_xml)
    saml_uri = ns.get("saml", ns.get("saml2", _DEFAULT_SAML_NS))
    dsig_uri = ns.get("ds", _DEFAULT_DSIG_NS)
    result: dict[str, Any] = {}
    assertion = _find_element(root, f"{{{saml_uri}}}Assertion")
    if assertion is None:
        if root.tag == f"{{{saml_uri}}}Assertion":
            assertion = root
        else:
            return result

    sig_el = _find_element(assertion, f"{{{dsig_uri}}}Signature")
    result["signed"] = sig_el is not None
    result["valid"] = sig_el is not None

    conditions_el = _find_element(assertion, f"{{{saml_uri}}}Conditions")
    if conditions_el is not None:
        not_on_or_after = conditions_el.get("NotOnOrAfter", "")
        if not_on_or_after:
            try:
                expiry = dt.fromisoformat(not_on_or_after.replace("Z", "+00:00"))
                result["expired"] = dt.now(UTC) > expiry
                if result["expired"]:
                    result["valid"] = False
            except (ValueError, OverflowError):
                pass

    issuer_el = _find_element(assertion, f"{{{saml_uri}}}Issuer")
    if issuer_el is not None:
        result["issuer"] = issuer_el.text or ""

    subject_el = _find_element(assertion, f"{{{saml_uri}}}Subject")
    if subject_el is not None:
        nameid = _find_element(subject_el, f"{{{saml_uri}}}NameID")
        if nameid is not None:
            result["subject"] = {
                "name_id": (nameid.text or "").strip(),
                "format": nameid.get("Format", ""),
            }

    conditions_el = _find_element(assertion, f"{{{saml_uri}}}Conditions")
    if conditions_el is not None:
        result["conditions"] = {
            "not_before": conditions_el.get("NotBefore", ""),
            "not_on_or_after": conditions_el.get("NotOnOrAfter", ""),
        }

    attr_statement = _find_element(assertion, f"{{{saml_uri}}}AttributeStatement")
    if attr_statement is not None:
        attrs: dict[str, list[str]] = {}
        for attr in attr_statement.findall(f"{{{saml_uri}}}Attribute"):
            name = attr.get("Name", "")
            values = [(val.text or "") for val in attr.findall(f"{{{saml_uri}}}AttributeValue")]
            attrs[name] = values
        result["attributes"] = attrs

    authn_statement = _find_element(assertion, f"{{{saml_uri}}}AuthnStatement")
    if authn_statement is not None:
        result["authn"] = {
            "instant": authn_statement.get("AuthnInstant", ""),
            "context": "",
        }
        context_el = _find_element(authn_statement, f"{{{saml_uri}}}AuthnContext")
        if context_el is not None:
            ref = _find_element(context_el, f"{{{saml_uri}}}AuthnContextClassRef")
            if ref is not None:
                result["authn"]["context"] = ref.text or ""

    return result


def validate_saml_signature(saml_xml: str, cert_pem: str) -> bool:
    """Verify the XML digital signature on a SAML assertion using *cert_pem*.

    Returns ``True`` when the signature is valid for the supplied
    certificate.  Requires ``lxml`` and ``xmlsec``.
    """
    if not _LXML_AVAILABLE:
        return False
    try:
        import xmlsec
    except ImportError:
        return False
    sig_nodes = _secure_lxml_root(saml_xml, source="saml-signature").xpath(
        "//ds:Signature",
        namespaces={"ds": _DEFAULT_DSIG_NS},
    )
    if not sig_nodes:
        return False
    sig_node = sig_nodes[0]
    ctx = xmlsec.SignatureContext(None)
    ctx.key = xmlsec.Key.from_memory(cert_pem, xmlsec.KeyFormat.CERT_PEM, None)
    try:
        ctx.verify(sig_node)
    except Exception:
        return False
    return True


def _find_element(parent: ET.Element, tag: str) -> ET.Element | None:
    for child in parent:
        if child.tag == tag:
            return child
    return None


# ---------------------------------------------------------------------------
# DocBook helpers
# ---------------------------------------------------------------------------


def docbook_to_html(docbook_xml: str, xslt_path: str | None = None) -> str:
    """Convert DocBook XML to HTML.

    When *xslt_path* is ``None`` the transformation outputs a basic HTML
    structure using a built-in algorithm.  With a path it delegates to
    *apply_xslt* (requires lxml).
    """
    if xslt_path:
        return apply_xslt(docbook_xml, xslt_path)
    root = secure_parse_xml_string(docbook_xml, source="docbook")
    return _docbook_to_html_stdlib(root)


def _docbook_to_html_stdlib(root: ET.Element) -> str:
    parts: list[str] = ["<html><body>\n"]
    _walk_docbook(root, parts)
    parts.append("\n</body></html>")
    return "".join(parts)


_TAG_TO_HTML: dict[str, tuple[str, str]] = {
    "chapter": ('<div class="chapter"><h2>', "</h2></div>"),
    "section": ('<div class="section"><h3>', "</h3></div>"),
    "para": ("<p>", "</p>"),
    "title": ("<h1>", "</h1>"),
    "emphasis": ("<em>", "</em>"),
    "link": ("<a>", "</a>"),
    "itemizedlist": ("<ul>", "</ul>"),
    "orderedlist": ("<ol>", "</ol>"),
    "listitem": ("<li>", "</li>"),
    "programlisting": ("<pre><code>", "</code></pre>"),
    "screen": ("<pre>", "</pre>"),
    "note": ('<div class="note">', "</div>"),
    "warning": ('<div class="warning">', "</div>"),
}


def _walk_docbook(elem: ET.Element, parts: list[str]) -> None:
    tag_local = elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag
    open_tag, close_tag = _TAG_TO_HTML.get(tag_local, ("<div>", "</div>"))
    parts.append(open_tag)
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        _walk_docbook(child, parts)
    parts.append(close_tag)
    if elem.tail:
        parts.append(elem.tail)


# ---------------------------------------------------------------------------
# Gradle helpers
# ---------------------------------------------------------------------------


def parse_gradle_dependencies(gradle_content: str) -> list[dict[str, str]]:
    """Extract dependency declarations from a ``build.gradle`` file.

    Recognises both Groovy-style (``group 'g', name 'n', version 'v'``)
    and shorthand (``'g:n:v'``) dependency notations.
    """
    deps: list[dict[str, str]] = []
    shorthand = re.findall(r"[\"']([^\"':]+:[^\"':]+:[^\"']+)[\"']", gradle_content)
    for match in shorthand:
        parts = match.split(":")
        if len(parts) >= 2:
            deps.append(
                {
                    "group": parts[0],
                    "name": parts[1],
                    "version": parts[2] if len(parts) > 2 else "",
                }
            )
    for block in gradle_content.splitlines():
        block = block.strip()
        if "group:" in block and "name:" in block:
            entry: dict[str, str] = {}
            m_group = re.search(r"group\s*:\s*[\"']([^\"']+)[\"']", block)
            m_name = re.search(r"name\s*:\s*[\"']([^\"']+)[\"']", block)
            m_ver = re.search(r"version\s*:\s*[\"']([^\"']+)[\"']", block)
            if m_group and m_name:
                entry["group"] = m_group.group(1)
                entry["name"] = m_name.group(1)
                entry["version"] = m_ver.group(1) if m_ver else ""
                deps.append(entry)
    return deps


# ---------------------------------------------------------------------------
# plist helpers
# ---------------------------------------------------------------------------


def read_plist(plist_file: str | Path) -> dict[str, Any]:
    """Read a property list file and return its root dict."""
    plist_path = Path(str(plist_file))
    with plist_path.open("rb") as fh:
        return cast(dict[str, Any], plistlib.load(fh))


def write_plist(
    data: dict[str, Any],
    output_file: str | Path,
    binary: bool = False,
    fmt: str = "xml",
) -> None:
    """Write *data* as a property list file.

    *fmt* may be ``"xml"`` or ``"binary"``.
    """
    out_path = Path(str(output_file))
    use_binary = binary or fmt == "binary"
    plist_fmt = plistlib.FMT_BINARY if use_binary else plistlib.FMT_XML
    with out_path.open("wb") as fh:
        plistlib.dump(data, fh, fmt=plist_fmt)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _serialize(tree: ET.ElementTree) -> str:
    root = tree.getroot()
    if root is None:
        return ""
    return ET.tostring(root, encoding="unicode", method="xml")


def _pretty_print_xml(raw: str) -> str:
    root = secure_parse_xml_string(raw, source="internal-pretty-print")
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    pretty_root = tree.getroot()
    if pretty_root is None:
        return ""
    return ET.tostring(
        pretty_root,
        encoding="unicode",
        method="xml",
        xml_declaration=True,
    )


def _existing_file(value: str) -> Path | None:
    """Resolve an existing path without treating long XML text as a filename."""
    try:
        path = Path(value)
        return path if path.is_file() else None
    except OSError:
        return None


def _secure_lxml_root(
    data: str | bytes,
    *,
    source: str,
    limits: XmlSecurityLimits = DEFAULT_XML_SECURITY_LIMITS,
    audit_sink: XmlSecurityAuditSink | None = None,
) -> Any:
    raw = validate_xml_payload(
        data,
        limits=limits,
        source=source,
        audit_sink=audit_sink,
    )
    parser = lxml_etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
    )
    root = lxml_etree.fromstring(raw, parser)
    validate_xml_tree(
        cast(ET.Element, root),
        limits=limits,
        source=source,
        input_bytes=len(raw),
        audit_sink=audit_sink,
    )
    return root


def _parse_lxml_html(
    data: str | bytes,
    *,
    limits: XmlSecurityLimits,
    source: str,
    audit_sink: XmlSecurityAuditSink | None,
) -> ET.ElementTree:
    raw = validate_xml_payload(
        data,
        limits=limits,
        source=source,
        audit_sink=audit_sink,
    )
    parser = lxml_etree.HTMLParser(
        no_network=True,
        recover=True,
        huge_tree=False,
    )
    root = lxml_etree.fromstring(raw, parser)
    validate_xml_tree(
        cast(ET.Element, root),
        limits=limits,
        source=source,
        input_bytes=len(raw),
        audit_sink=audit_sink,
    )
    return cast(ET.ElementTree, lxml_etree.ElementTree(root))
