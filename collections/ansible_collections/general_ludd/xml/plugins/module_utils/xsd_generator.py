"""xsd_generator -- Infer XSD schemas from XML sample documents.

A stdlib-only (xml.etree.ElementTree) utility module for the
general_ludd.xml collection. The :func:`infer_xsd` function walks one or
more XML instance strings, collects element/attribute structure, infers
simple types from text content, and returns a basic XSD schema string.

Sibling module to :mod:`xml_core` (parse/XPath/namespace primitives).
"""

from __future__ import annotations

import re
from collections import defaultdict
from xml.etree import ElementTree as ET

_XS = "http://www.w3.org/2001/XMLSchema"
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T|\s)")


def _local_name(tag: str) -> str:
    """Return the local part of a (possibly Clark-notation) tag."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _infer_simple_type(text: str | None) -> str:
    """Infer an XSD simple type from element text content.

    Order matters: boolean before numeric (``"true"``/``"false"``),
    integer before decimal, datetime heuristic last.
    """
    if text is None:
        return "xs:string"
    stripped = text.strip()
    if stripped == "":
        return "xs:string"
    if stripped.lower() in ("true", "false"):
        return "xs:boolean"
    try:
        int(stripped)
        return "xs:integer"
    except ValueError:
        pass
    try:
        float(stripped)
        return "xs:decimal"
    except ValueError:
        pass
    if _DATETIME_RE.match(stripped):
        return "xs:dateTime"
    return "xs:string"


class _ElementInfo:
    """Accumulated structure for a single element local-name across samples."""

    __slots__ = ("attributes", "children", "text_samples")

    def __init__(self) -> None:
        self.children: set[str] = set()
        self.attributes: set[str] = set()
        self.text_samples: list[str] = []


def _walk(el: ET.Element, registry: dict[str, _ElementInfo]) -> None:
    """Depth-first walk populating ``registry`` for ``el`` and its descendants."""
    name = _local_name(el.tag)
    info = registry[name]
    for child in el:
        info.children.add(_local_name(child.tag))
    for attr in el.attrib:
        info.attributes.add(attr)
    text = el.text
    if text is not None and text.strip():
        info.text_samples.append(text)
    for child in el:
        _walk(child, registry)


def _emit_element(
    lines: list[str],
    name: str,
    registry: dict[str, _ElementInfo],
    declared: set[str],
) -> None:
    """Append a top-level ``xs:element`` declaration for ``name`` (once)."""
    if name in declared:
        return
    declared.add(name)
    info = registry.get(name)
    if info is None:
        lines.append(f'  <xs:element name="{name}" type="xs:string"/>')
        return

    has_children = bool(info.children)
    has_attributes = bool(info.attributes)

    if not has_children and not has_attributes:
        sample = info.text_samples[0] if info.text_samples else None
        inferred = _infer_simple_type(sample)
        lines.append(f'  <xs:element name="{name}" type="{inferred}"/>')
        return

    lines.append(f'  <xs:element name="{name}">')
    lines.append("    <xs:complexType>")
    if has_children:
        lines.append("      <xs:sequence>")
        for child in sorted(info.children):
            lines.append(f'        <xs:element ref="{child}"/>')
        lines.append("      </xs:sequence>")
    for attr in sorted(info.attributes):
        lines.append(f'      <xs:attribute name="{attr}" type="xs:string"/>')
    lines.append("    </xs:complexType>")
    lines.append("  </xs:element>")

    for child in sorted(info.children):
        _emit_element(lines, child, registry, declared)


def infer_xsd(xml_samples: list[str] | str) -> str:
    """Infer a basic XSD schema from XML sample strings.

    Walks each sample, collecting element/attribute structure and inferring
    simple types (``xs:string``, ``xs:integer``, ``xs:boolean``, ``xs:decimal``,
    ``xs:dateTime``) from text content. Namespace prefixes are stripped to
    local names. The result is a self-contained XSD document.

    Args:
        xml_samples: one or more well-formed XML document strings. A single
            string is accepted as a convenience and wrapped into a one-element
            list.

    Returns:
        An XSD schema document string (parseable as XML).

    Raises:
        ValueError: if ``xml_samples`` is empty or any sample is malformed.
    """
    if isinstance(xml_samples, str):
        samples: list[str] = [xml_samples]
    else:
        samples = list(xml_samples)

    if not samples:
        raise ValueError("infer_xsd requires at least one XML sample")

    registry: dict[str, _ElementInfo] = defaultdict(_ElementInfo)
    roots: list[str] = []

    for idx, sample in enumerate(samples):
        try:
            root = ET.fromstring(sample)
        except ET.ParseError as exc:
            raise ValueError(f"Malformed XML in sample {idx}: {exc}") from exc
        root_name = _local_name(root.tag)
        roots.append(root_name)
        _walk(root, registry)

    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<xs:schema xmlns:xs="{_XS}">',
    ]

    declared: set[str] = set()
    for root_name in dict.fromkeys(roots):
        _emit_element(lines, root_name, registry, declared)

    for name in sorted(registry):
        if name not in declared:
            _emit_element(lines, name, registry, declared)

    lines.append("</xs:schema>")
    return "\n".join(lines)
