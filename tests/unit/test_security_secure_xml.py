"""Deep tests for secure_xml — XML parsing security module."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from xml.etree import ElementTree as ET

import pytest
from defusedxml.common import (
    DefusedXmlException,
    DTDForbidden,
    EntitiesForbidden,
    ExternalReferenceForbidden,
)

from general_ludd.security.secure_xml import (
    DEFAULT_XML_SECURITY_LIMITS,
    XmlSecurityError,
    XmlSecurityEvent,
    XmlSecurityLimits,
    _defused_reason,
    _reject,
    parse_xml_file,
    parse_xml_string,
    read_xml_payload,
    validate_xml_payload,
    validate_xml_tree,
)

# ---- XmlSecurityLimits ----------------------------------------------------


class TestXmlSecurityLimits:
    def test_defaults(self) -> None:
        limits = XmlSecurityLimits()
        assert limits.max_bytes == 4 * 1024 * 1024
        assert limits.max_depth == 64
        assert limits.max_nodes == 100_000

    def test_custom_values(self) -> None:
        limits = XmlSecurityLimits(max_bytes=100, max_depth=10, max_nodes=50)
        assert limits.max_bytes == 100
        assert limits.max_depth == 10
        assert limits.max_nodes == 50

    def test_frozen(self) -> None:
        limits = XmlSecurityLimits()
        with pytest.raises(AttributeError):
            limits.max_bytes = 999  # type: ignore[misc]

    @pytest.mark.parametrize(
        "field,value",
        [
            ("max_bytes", 0),
            ("max_bytes", -1),
            ("max_depth", 0),
            ("max_depth", -5),
            ("max_nodes", 0),
            ("max_nodes", -100),
        ],
    )
    def test_rejects_non_positive(self, field: str, value: int) -> None:
        with pytest.raises(ValueError, match="positive integers"):
            XmlSecurityLimits(**{field: value})

    def test_default_singleton_matches(self) -> None:
        assert DEFAULT_XML_SECURITY_LIMITS.max_bytes == 4 * 1024 * 1024


# ---- XmlSecurityEvent -----------------------------------------------------


class TestXmlSecurityEvent:
    def test_basic_creation(self) -> None:
        event = XmlSecurityEvent(reason="test", source="unit", input_bytes=42)
        assert event.reason == "test"
        assert event.source == "unit"
        assert event.input_bytes == 42
        assert event.limit is None
        assert event.event_type == "xml_parse_rejected"

    def test_with_limit(self) -> None:
        event = XmlSecurityEvent(reason="overflow", source="file", input_bytes=999, limit=500)
        assert event.limit == 500

    def test_custom_event_type(self) -> None:
        event = XmlSecurityEvent(reason="x", source="s", input_bytes=1, event_type="custom_event")
        assert event.event_type == "custom_event"

    def test_frozen(self) -> None:
        event = XmlSecurityEvent(reason="r", source="s", input_bytes=1)
        with pytest.raises(AttributeError):
            event.reason = "other"  # type: ignore[misc]


# ---- XmlSecurityError -----------------------------------------------------


class TestXmlSecurityError:
    def test_from_event_without_limit(self) -> None:
        event = XmlSecurityEvent(reason="dtd_forbidden", source="test", input_bytes=128)
        err = XmlSecurityError(event)
        assert err.event is event
        assert "dtd_forbidden" in str(err)
        assert "source=test" in str(err)
        assert "bytes=128" in str(err)
        assert "limit=" not in str(err)

    def test_from_event_with_limit(self) -> None:
        event = XmlSecurityEvent(reason="input_too_large", source="file", input_bytes=999, limit=500)
        err = XmlSecurityError(event)
        assert "limit=500" in str(err)

    def test_is_value_error(self) -> None:
        event = XmlSecurityEvent(reason="r", source="s", input_bytes=0)
        err = XmlSecurityError(event)
        assert isinstance(err, ValueError)


# ---- validate_xml_payload -------------------------------------------------


class TestValidateXmlPayload:
    def test_accepts_valid_bytes(self) -> None:
        result = validate_xml_payload(b"<root/>")
        assert result == b"<root/>"

    def test_accepts_valid_string(self) -> None:
        result = validate_xml_payload("<root/>")
        assert result == b"<root/>"

    def test_rejects_oversized_bytes(self) -> None:
        limits = XmlSecurityLimits(max_bytes=5, max_depth=64, max_nodes=100)
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_payload(b"123456", limits=limits)
        assert exc_info.value.event.reason == "input_too_large"
        assert exc_info.value.event.input_bytes == 6
        assert exc_info.value.event.limit == 5

    def test_rejects_oversized_string(self) -> None:
        limits = XmlSecurityLimits(max_bytes=3, max_depth=64, max_nodes=100)
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_payload("abcd", limits=limits)
        assert exc_info.value.event.reason == "input_too_large"

    def test_rejects_entity_declaration(self) -> None:
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_payload(b'<!ENTITY foo "bar">')
        assert exc_info.value.event.reason == "entity_forbidden"

    def test_rejects_entity_lowercase_insensitive(self) -> None:
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_payload(b"<!entity foo 'bar'>")
        assert exc_info.value.event.reason == "entity_forbidden"

    def test_rejects_dtd_declaration(self) -> None:
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_payload(b"<!DOCTYPE html>")
        assert exc_info.value.event.reason == "dtd_forbidden"

    def test_rejects_dtd_lowercase_insensitive(self) -> None:
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_payload(b"<!doctype note>")
        assert exc_info.value.event.reason == "dtd_forbidden"

    def test_entity_checked_before_dtd(self) -> None:
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_payload(b'<!ENTITY x "val"><!DOCTYPE y>')
        assert exc_info.value.event.reason == "entity_forbidden"

    def test_size_checked_before_declarations(self) -> None:
        limits = XmlSecurityLimits(max_bytes=3, max_depth=64, max_nodes=100)
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_payload(b"<!DOCTYPE too_big>", limits=limits)
        assert exc_info.value.event.reason == "input_too_large"

    def test_calls_audit_sink_on_reject(self) -> None:
        sink = MagicMock()
        limits = XmlSecurityLimits(max_bytes=3, max_depth=64, max_nodes=100)
        with pytest.raises(XmlSecurityError):
            validate_xml_payload(b"big!", limits=limits, audit_sink=sink)
        sink.assert_called_once()
        event = sink.call_args[0][0]
        assert event.reason == "input_too_large"

    def test_audit_sink_exception_does_not_crash(self) -> None:
        sink = MagicMock(side_effect=RuntimeError("audit dead"))
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_payload(b"<!DOCTYPE x>", audit_sink=sink)
        assert exc_info.value.event.reason == "dtd_forbidden"

    def test_no_audit_sink_logs_warning(self, caplog) -> None:
        caplog.set_level(logging.WARNING)
        with pytest.raises(XmlSecurityError):
            validate_xml_payload(b"<!DOCTYPE x>")
        assert "security_event" in caplog.text
        assert "dtd_forbidden" in caplog.text

    def test_source_propagated_to_event(self) -> None:
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_payload(b"<!DOCTYPE x>", source="custom_src")
        assert exc_info.value.event.source == "custom_src"

    def test_default_source_memory(self) -> None:
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_payload(b"<!DOCTYPE x>")
        assert exc_info.value.event.source == "memory"


# ---- validate_xml_tree ----------------------------------------------------


class TestValidateXmlTree:
    @staticmethod
    def _make_tree(depth: int, width: int = 1) -> ET.Element:
        root = ET.Element("root")
        stack: list[tuple[ET.Element, int]] = [(root, 1)]
        while stack:
            parent, d = stack.pop()
            if d >= depth:
                continue
            for _ in range(width):
                child = ET.SubElement(parent, "e")
                stack.append((child, d + 1))
        return root

    def _limits(self, **kw: int) -> XmlSecurityLimits:
        defaults = {"max_bytes": 1024, "max_depth": 64, "max_nodes": 100_000}
        defaults.update(kw)
        return XmlSecurityLimits(**defaults)

    def test_passes_valid_shallow_tree(self) -> None:
        root = ET.fromstring("<root><a/><b/></root>")
        validate_xml_tree(
            root,
            limits=self._limits(max_nodes=5, max_depth=10),
            source="test",
            input_bytes=100,
            audit_sink=None,
        )

    def test_rejects_too_many_nodes(self) -> None:
        root = ET.fromstring("<root><a/><b/><c/></root>")
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_tree(
                root,
                limits=self._limits(max_nodes=3),
                source="test",
                input_bytes=100,
                audit_sink=None,
            )
        assert exc_info.value.event.reason == "max_nodes_exceeded"
        assert exc_info.value.event.limit == 3

    def test_rejects_too_deep(self) -> None:
        root = self._make_tree(depth=5)
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_tree(
                root,
                limits=self._limits(max_depth=3),
                source="test",
                input_bytes=100,
                audit_sink=None,
            )
        assert exc_info.value.event.reason == "max_depth_exceeded"
        assert exc_info.value.event.limit == 3

    def test_nodes_checked_before_depth_on_same_element(self) -> None:
        root = self._make_tree(depth=3, width=1)
        with pytest.raises(XmlSecurityError) as exc_info:
            validate_xml_tree(
                root,
                limits=self._limits(max_nodes=1, max_depth=1),
                source="test",
                input_bytes=100,
                audit_sink=None,
            )
        assert exc_info.value.event.reason == "max_nodes_exceeded"

    def test_deep_tree_breadth_first_count(self) -> None:
        root = ET.fromstring("<r><a><x/></a><b><y/></b></r>")
        validate_xml_tree(
            root,
            limits=self._limits(max_nodes=6, max_depth=10),
            source="test",
            input_bytes=100,
            audit_sink=None,
        )

    def test_zero_depth_root_passes(self) -> None:
        root = ET.Element("sole")
        validate_xml_tree(
            root,
            limits=self._limits(max_nodes=10, max_depth=1),
            source="test",
            input_bytes=10,
            audit_sink=None,
        )


# ---- parse_xml_string -----------------------------------------------------


class TestParseXmlString:
    def test_parses_simple_xml(self) -> None:
        root = parse_xml_string("<root><child>text</child></root>", source="test")
        assert root.tag == "root"
        assert len(root) == 1
        assert root[0].text == "text"

    def test_parses_bytes_input(self) -> None:
        root = parse_xml_string(b"<x/>", source="test")
        assert root.tag == "x"

    def test_rejects_dtd_in_payload(self) -> None:
        with pytest.raises(XmlSecurityError) as exc_info:
            parse_xml_string("<!DOCTYPE note><root/>")
        assert exc_info.value.event.reason == "dtd_forbidden"

    def test_rejects_entity_in_payload(self) -> None:
        with pytest.raises(XmlSecurityError) as exc_info:
            parse_xml_string('<!ENTITY e "boom"><root/>')
        assert exc_info.value.event.reason == "entity_forbidden"

    def test_rejects_oversized_payload(self) -> None:
        limits = XmlSecurityLimits(max_bytes=10, max_depth=64, max_nodes=100)
        with pytest.raises(XmlSecurityError) as exc_info:
            parse_xml_string(b"01234567890", limits=limits)
        assert exc_info.value.event.reason == "input_too_large"

    def test_rejects_too_many_nodes_after_parse(self) -> None:
        data = "<r>" + "".join(f"<e{i}/>" for i in range(200)) + "</r>"
        limits = XmlSecurityLimits(max_bytes=100000, max_depth=64, max_nodes=50)
        with pytest.raises(XmlSecurityError) as exc_info:
            parse_xml_string(data, limits=limits)
        assert exc_info.value.event.reason == "max_nodes_exceeded"

    def test_rejects_too_deep_after_parse(self) -> None:
        nesting = "".join("<a>" for _ in range(70)) + "x" + "".join("</a>" for _ in range(70))
        limits = XmlSecurityLimits(max_bytes=10000, max_depth=30, max_nodes=1000)
        with pytest.raises(XmlSecurityError) as exc_info:
            parse_xml_string(nesting, limits=limits)
        assert exc_info.value.event.reason == "max_depth_exceeded"

    def test_audit_sink_called_on_dtd(self) -> None:
        sink = MagicMock()
        with pytest.raises(XmlSecurityError):
            parse_xml_string("<!DOCTYPE x><root/>", audit_sink=sink)
        sink.assert_called_once()

    def test_returns_element_tree_element(self) -> None:
        root = parse_xml_string("<a><b/></a>")
        assert isinstance(root, ET.Element)

    def test_accepts_valid_xml_with_namespaces(self) -> None:
        root = parse_xml_string('<ns:a xmlns:ns="urn:x"/>', source="test")
        assert root.tag == "{urn:x}a"


# ---- read_xml_payload -----------------------------------------------------


class TestReadXmlPayload:
    def test_reads_valid_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as f:
            f.write(b"<root/>")
            tmp = f.name
        try:
            result = read_xml_payload(tmp)
            assert result == b"<root/>"
        finally:
            Path(tmp).unlink()

    def test_rejects_too_large_file(self) -> None:
        limits = XmlSecurityLimits(max_bytes=5, max_depth=64, max_nodes=100)
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as f:
            f.write(b"1234567890")
            tmp = f.name
        try:
            with pytest.raises(XmlSecurityError) as exc_info:
                read_xml_payload(tmp, limits=limits)
            assert exc_info.value.event.reason == "input_too_large"
        finally:
            Path(tmp).unlink()

    def test_rejects_dtd_in_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as f:
            f.write(b"<!DOCTYPE note><root/>")
            tmp = f.name
        try:
            with pytest.raises(XmlSecurityError) as exc_info:
                read_xml_payload(tmp)
            assert exc_info.value.event.reason == "dtd_forbidden"
        finally:
            Path(tmp).unlink()

    def test_rejects_entity_in_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as f:
            f.write(b'<!ENTITY foo "bar"><root/>')
            tmp = f.name
        try:
            with pytest.raises(XmlSecurityError) as exc_info:
                read_xml_payload(tmp)
            assert exc_info.value.event.reason == "entity_forbidden"
        finally:
            Path(tmp).unlink()

    def test_uses_filename_as_source_label(self) -> None:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as f:
            f.write(b"<!DOCTYPE x>")
            tmp = f.name
        try:
            with pytest.raises(XmlSecurityError) as exc_info:
                read_xml_payload(tmp)
            assert tmp in exc_info.value.event.source
        finally:
            Path(tmp).unlink()

    def test_custom_source_overrides_path(self) -> None:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as f:
            f.write(b"<!DOCTYPE x>")
            tmp = f.name
        try:
            with pytest.raises(XmlSecurityError) as exc_info:
                read_xml_payload(tmp, source="custom_label")
            assert exc_info.value.event.source == "custom_label"
        finally:
            Path(tmp).unlink()


# ---- parse_xml_file -------------------------------------------------------


class TestParseXmlFile:
    def test_parses_valid_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as f:
            f.write(b"<root><child/></root>")
            tmp = f.name
        try:
            tree = parse_xml_file(tmp)
            assert tree.getroot().tag == "root"
            assert len(tree.getroot()) == 1
        finally:
            Path(tmp).unlink()

    def test_rejects_too_large_file(self) -> None:
        limits = XmlSecurityLimits(max_bytes=3, max_depth=64, max_nodes=100)
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as f:
            f.write(b"123456")
            tmp = f.name
        try:
            with pytest.raises(XmlSecurityError) as exc_info:
                parse_xml_file(tmp, limits=limits)
            assert exc_info.value.event.reason == "input_too_large"
        finally:
            Path(tmp).unlink()

    def test_rejects_dtd_in_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as f:
            f.write(b"<!DOCTYPE note [<!ELEMENT note (#PCDATA)>]><note/>")
            tmp = f.name
        try:
            with pytest.raises(XmlSecurityError) as exc_info:
                parse_xml_file(tmp)
            assert exc_info.value.event.reason == "dtd_forbidden"
        finally:
            Path(tmp).unlink()

    def test_returns_element_tree(self) -> None:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as f:
            f.write(b"<r/>")
            tmp = f.name
        try:
            tree = parse_xml_file(tmp)
            assert isinstance(tree, ET.ElementTree)
        finally:
            Path(tmp).unlink()


# ---- _defused_reason ------------------------------------------------------


class TestDefusedReason:
    def test_dtd_forbidden(self) -> None:
        err = DTDForbidden("root", "", "")
        assert _defused_reason(err) == "dtd_forbidden"

    def test_entities_forbidden(self) -> None:
        err = EntitiesForbidden("e", "v", "", "", "", "")
        assert _defused_reason(err) == "entity_forbidden"

    def test_external_reference_forbidden(self) -> None:
        err = ExternalReferenceForbidden("ctx", "", "", "")
        assert _defused_reason(err) == "external_reference_forbidden"

    def test_generic_defused_exception(self) -> None:
        assert _defused_reason(DefusedXmlException()) == "unsafe_xml_forbidden"


# ---- _reject --------------------------------------------------------------


class TestReject:
    def _call(self, **kw: object) -> None:
        kwargs: dict[str, object] = {
            "reason": "test",
            "source": "test_src",
            "input_bytes": 42,
            "audit_sink": None,
        }
        kwargs.update(kw)
        _reject(**kwargs)  # type: ignore[arg-type]

    def test_raises_xml_security_error(self) -> None:
        with pytest.raises(XmlSecurityError) as exc_info:
            self._call()
        assert exc_info.value.event.reason == "test"
        assert exc_info.value.event.source == "test_src"
        assert exc_info.value.event.input_bytes == 42
        assert exc_info.value.event.limit is None

    def test_logs_warning_without_audit_sink(self, caplog) -> None:
        caplog.set_level(logging.WARNING)
        with pytest.raises(XmlSecurityError):
            self._call()
        assert "security_event" in caplog.text

    def test_calls_audit_sink_when_provided(self) -> None:
        sink = MagicMock()
        with pytest.raises(XmlSecurityError):
            self._call(audit_sink=sink)
        sink.assert_called_once()
        event = sink.call_args[0][0]
        assert event.reason == "test"

    def test_audit_sink_exception_caught(self, caplog) -> None:
        sink = MagicMock(side_effect=Boom("\u00d6\u00e4"))
        caplog.set_level(logging.WARNING)
        with pytest.raises(XmlSecurityError):
            self._call(audit_sink=sink)
        assert "XML security audit sink failed" in caplog.text

    def test_limit_propagated_to_event(self) -> None:
        with pytest.raises(XmlSecurityError) as exc_info:
            self._call(limit=500)
        assert exc_info.value.event.limit == 500


class Boom(Exception):
    pass
