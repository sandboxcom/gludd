"""Fail-closed, resource-bounded XML parsing shared by Gludd components.

``defusedxml`` blocks DTD, entity, and external-reference processing.  This
module adds byte, element-count, and depth limits plus a redacted audit hook so
all untrusted XML call sites use one reviewable policy boundary.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from defusedxml import ElementTree as DefusedET
from defusedxml.common import (
    DefusedXmlException,
    DTDForbidden,
    EntitiesForbidden,
    ExternalReferenceForbidden,
)

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BYTES = 4 * 1024 * 1024
_DEFAULT_MAX_DEPTH = 64
_DEFAULT_MAX_NODES = 100_000


@dataclass(frozen=True, slots=True)
class XmlSecurityLimits:
    """Configurable hard limits applied to an XML document before use."""

    max_bytes: int = _DEFAULT_MAX_BYTES
    max_depth: int = _DEFAULT_MAX_DEPTH
    max_nodes: int = _DEFAULT_MAX_NODES

    def __post_init__(self) -> None:
        if self.max_bytes <= 0 or self.max_depth <= 0 or self.max_nodes <= 0:
            raise ValueError("XML security limits must be positive integers")


DEFAULT_XML_SECURITY_LIMITS = XmlSecurityLimits()


@dataclass(frozen=True, slots=True)
class XmlSecurityEvent:
    """Redacted denial event suitable for a durable audit sink."""

    reason: str
    source: str
    input_bytes: int
    limit: int | None = None
    event_type: str = "xml_parse_rejected"


XmlSecurityAuditSink = Callable[[XmlSecurityEvent], None]


class XmlSecurityError(ValueError):
    """Raised when XML violates a security or resource policy."""

    def __init__(self, event: XmlSecurityEvent) -> None:
        self.event = event
        detail = f"{event.reason}: source={event.source} bytes={event.input_bytes}"
        if event.limit is not None:
            detail += f" limit={event.limit}"
        super().__init__(detail)


def parse_xml_string(
    data: str | bytes,
    *,
    limits: XmlSecurityLimits = DEFAULT_XML_SECURITY_LIMITS,
    source: str = "memory",
    audit_sink: XmlSecurityAuditSink | None = None,
) -> ET.Element:
    """Parse bounded XML text with DTD/entities/external references disabled."""

    raw = validate_xml_payload(
        data,
        limits=limits,
        source=source,
        audit_sink=audit_sink,
    )
    input_bytes = len(raw)

    try:
        root = DefusedET.fromstring(
            raw,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except DefusedXmlException as exc:
        _reject(
            reason=_defused_reason(exc),
            source=source,
            input_bytes=input_bytes,
            audit_sink=audit_sink,
        )

    validate_xml_tree(
        root,
        limits=limits,
        source=source,
        input_bytes=input_bytes,
        audit_sink=audit_sink,
    )
    return root


def parse_xml_file(
    path: str | Path,
    *,
    limits: XmlSecurityLimits = DEFAULT_XML_SECURITY_LIMITS,
    source: str | None = None,
    audit_sink: XmlSecurityAuditSink | None = None,
) -> ET.ElementTree:
    """Read at most ``max_bytes + 1`` bytes and securely parse an XML file."""

    xml_path = Path(path)
    source_label = source or str(xml_path)
    raw = read_xml_payload(
        xml_path,
        limits=limits,
        source=source_label,
        audit_sink=audit_sink,
    )
    root = parse_xml_string(
        raw,
        limits=limits,
        source=source_label,
        audit_sink=audit_sink,
    )
    return ET.ElementTree(root)


def read_xml_payload(
    path: str | Path,
    *,
    limits: XmlSecurityLimits = DEFAULT_XML_SECURITY_LIMITS,
    source: str | None = None,
    audit_sink: XmlSecurityAuditSink | None = None,
) -> bytes:
    """Read and declaration-check one bounded XML or HTML payload."""

    xml_path = Path(path)
    with xml_path.open("rb") as stream:
        raw = stream.read(limits.max_bytes + 1)
    return validate_xml_payload(
        raw,
        limits=limits,
        source=source or str(xml_path),
        audit_sink=audit_sink,
    )


def validate_xml_payload(
    data: str | bytes,
    *,
    limits: XmlSecurityLimits = DEFAULT_XML_SECURITY_LIMITS,
    source: str = "memory",
    audit_sink: XmlSecurityAuditSink | None = None,
) -> bytes:
    """Apply byte and forbidden-declaration checks before any parser runs."""

    raw = data.encode("utf-8") if isinstance(data, str) else data
    input_bytes = len(raw)
    if input_bytes > limits.max_bytes:
        _reject(
            reason="input_too_large",
            source=source,
            input_bytes=input_bytes,
            limit=limits.max_bytes,
            audit_sink=audit_sink,
        )

    # Classify common declarations before defusedxml stops at the outer DTD.
    # The parser remains the security control; this bounded scan only improves
    # redacted audit diagnostics and protects tolerant HTML parser paths.
    upper = raw.upper()
    if b"<!ENTITY" in upper:
        _reject(
            reason="entity_forbidden",
            source=source,
            input_bytes=input_bytes,
            audit_sink=audit_sink,
        )
    if b"<!DOCTYPE" in upper:
        _reject(
            reason="dtd_forbidden",
            source=source,
            input_bytes=input_bytes,
            audit_sink=audit_sink,
        )
    return raw


def validate_xml_tree(
    root: ET.Element,
    *,
    limits: XmlSecurityLimits,
    source: str,
    input_bytes: int,
    audit_sink: XmlSecurityAuditSink | None,
) -> None:
    nodes = 0
    stack: list[tuple[ET.Element, int]] = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes:
            _reject(
                reason="max_nodes_exceeded",
                source=source,
                input_bytes=input_bytes,
                limit=limits.max_nodes,
                audit_sink=audit_sink,
            )
        if depth > limits.max_depth:
            _reject(
                reason="max_depth_exceeded",
                source=source,
                input_bytes=input_bytes,
                limit=limits.max_depth,
                audit_sink=audit_sink,
            )
        stack.extend((child, depth + 1) for child in element)


def _defused_reason(exc: DefusedXmlException) -> str:
    if isinstance(exc, DTDForbidden):
        return "dtd_forbidden"
    if isinstance(exc, EntitiesForbidden):
        return "entity_forbidden"
    if isinstance(exc, ExternalReferenceForbidden):
        return "external_reference_forbidden"
    return "unsafe_xml_forbidden"


def _reject(
    *,
    reason: str,
    source: str,
    input_bytes: int,
    audit_sink: XmlSecurityAuditSink | None,
    limit: int | None = None,
) -> None:
    event = XmlSecurityEvent(
        reason=reason,
        source=source,
        input_bytes=input_bytes,
        limit=limit,
    )
    if audit_sink is None:
        logger.warning(
            "security_event type=%s reason=%s source=%s input_bytes=%d limit=%s",
            event.event_type,
            event.reason,
            event.source,
            event.input_bytes,
            event.limit,
        )
    else:
        try:
            audit_sink(event)
        except Exception as exc:
            logger.warning("XML security audit sink failed: %s", exc)
    raise XmlSecurityError(event)
