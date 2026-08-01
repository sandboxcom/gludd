"""Security and resource-bound contracts for Gludd's shared XML parser."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from general_ludd.security.secure_xml import (
    XmlSecurityError,
    XmlSecurityEvent,
    XmlSecurityLimits,
    parse_xml_file,
    parse_xml_string,
)
from general_ludd.xml_utils import (
    apply_xslt,
    build_soap_envelope,
    docbook_to_html,
    infer_xsd,
    parse_gradle_dependencies,
    parse_saml_assertion,
    parse_soap_response,
    parse_xml,
    register_namespaces,
    validate_saml_signature,
    xml_to_dict,
)


def test_safe_xml_preserves_stdlib_elementtree_behavior(tmp_path: Path) -> None:
    xml = '<coverage line-rate="0.9"><package name="app"/></coverage>'

    root = parse_xml_string(xml, source="coverage-memory")
    path = tmp_path / "coverage.xml"
    path.write_text(xml, encoding="utf-8")
    tree = parse_xml_file(path, source="coverage-file")

    assert isinstance(root, ET.Element)
    assert root.get("line-rate") == "0.9"
    assert tree.getroot().find("package").get("name") == "app"


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("<!DOCTYPE root><root/>", "dtd_forbidden"),
        (
            '<!DOCTYPE root [<!ENTITY local "expanded">]><root>&local;</root>',
            "entity_forbidden",
        ),
        (
            '<!DOCTYPE root [<!ENTITY ext SYSTEM "file:///etc/passwd">]>'
            "<root>&ext;</root>",
            "entity_forbidden",
        ),
    ],
)
def test_dtd_and_entities_fail_closed_with_redacted_audit_event(
    payload: str,
    reason: str,
) -> None:
    events: list[XmlSecurityEvent] = []

    with pytest.raises(XmlSecurityError, match=reason):
        parse_xml_string(payload, source="model-output", audit_sink=events.append)

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "xml_parse_rejected"
    assert event.reason == reason
    assert event.source == "model-output"
    assert event.input_bytes == len(payload.encode("utf-8"))
    assert payload not in repr(event)
    assert "passwd" not in repr(event)


def test_encoded_dtd_is_rejected_by_parser_and_classified_for_audit() -> None:
    payload = "<!DOCTYPE root><root/>".encode("utf-16")
    events: list[XmlSecurityEvent] = []

    with pytest.raises(XmlSecurityError, match="dtd_forbidden"):
        parse_xml_string(
            payload,
            source="utf16-model-output",
            audit_sink=events.append,
        )

    assert events == [
        XmlSecurityEvent(
            reason="dtd_forbidden",
            source="utf16-model-output",
            input_bytes=len(payload),
        )
    ]


def test_input_byte_limit_applies_before_parsing() -> None:
    limits = XmlSecurityLimits(max_bytes=15, max_depth=8, max_nodes=8)
    events: list[XmlSecurityEvent] = []

    with pytest.raises(XmlSecurityError, match="input_too_large"):
        parse_xml_string(
            "<root>too-large</root>",
            limits=limits,
            audit_sink=events.append,
        )

    assert events[0].reason == "input_too_large"
    assert events[0].limit == 15


def test_file_byte_limit_reads_no_unbounded_payload(tmp_path: Path) -> None:
    path = tmp_path / "oversized.xml"
    path.write_text("<root>too-large</root>", encoding="utf-8")

    with pytest.raises(XmlSecurityError, match="input_too_large"):
        parse_xml_file(
            path,
            limits=XmlSecurityLimits(max_bytes=15, max_depth=8, max_nodes=8),
        )


def test_depth_limit_is_iterative_and_fail_closed() -> None:
    payload = "<a><b><c><d/></c></b></a>"

    with pytest.raises(XmlSecurityError, match="max_depth_exceeded"):
        parse_xml_string(
            payload,
            limits=XmlSecurityLimits(max_bytes=100, max_depth=3, max_nodes=20),
        )


def test_node_limit_is_fail_closed() -> None:
    payload = "<root><a/><b/><c/></root>"

    with pytest.raises(XmlSecurityError, match="max_nodes_exceeded"):
        parse_xml_string(
            payload,
            limits=XmlSecurityLimits(max_bytes=100, max_depth=8, max_nodes=3),
        )


def test_limits_reject_non_positive_values() -> None:
    with pytest.raises(ValueError, match="positive"):
        XmlSecurityLimits(max_bytes=0)


def test_malformed_xml_keeps_parse_error_contract() -> None:
    with pytest.raises(ET.ParseError):
        parse_xml_string("<root>")


def test_audit_sink_failure_cannot_turn_a_denial_into_acceptance(caplog: pytest.LogCaptureFixture) -> None:
    def broken_sink(_event: XmlSecurityEvent) -> None:
        raise OSError("sink unavailable")

    with pytest.raises(XmlSecurityError, match="dtd_forbidden"):
        parse_xml_string(
            "<!DOCTYPE root><root/>",
            audit_sink=broken_sink,
        )

    assert "XML security audit sink failed" in caplog.text


@pytest.mark.parametrize(
    "consumer",
    [
        parse_xml,
        xml_to_dict,
        parse_soap_response,
        parse_saml_assertion,
        docbook_to_html,
    ],
)
def test_public_xml_consumers_reject_dtd_and_entities(
    consumer: Callable[[str], object],
) -> None:
    payload = '<!DOCTYPE root [<!ENTITY x "secret">]><root>&x;</root>'

    with pytest.raises(XmlSecurityError, match="entity_forbidden"):
        consumer(payload)


def test_soap_builder_does_not_downgrade_forbidden_markup_to_text() -> None:
    payload = '<!DOCTYPE root [<!ENTITY x "secret">]><root>&x;</root>'

    with pytest.raises(XmlSecurityError, match="entity_forbidden"):
        build_soap_envelope(payload)


def test_public_parse_xml_exposes_configurable_limits() -> None:
    with pytest.raises(XmlSecurityError, match="input_too_large"):
        parse_xml(
            "<root>too-large</root>",
            limits=XmlSecurityLimits(max_bytes=15, max_depth=8, max_nodes=8),
        )


def test_html_mode_rejects_dtd_before_tolerant_lxml_parse() -> None:
    with pytest.raises(XmlSecurityError, match="dtd_forbidden"):
        parse_xml("<!DOCTYPE html><html><body>ok</body></html>", html=True)


def test_xslt_rejects_dtd_in_input_or_stylesheet() -> None:
    safe_xml = "<root/>"
    unsafe = '<!DOCTYPE root [<!ENTITY x "secret">]><root>&x;</root>'

    with pytest.raises(XmlSecurityError, match="entity_forbidden"):
        apply_xslt(unsafe, _IDENTITY_XSLT)
    with pytest.raises(XmlSecurityError, match="entity_forbidden"):
        apply_xslt(safe_xml, unsafe)


_IDENTITY_XSLT = (
    '<xsl:stylesheet version="1.0" '
    'xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
    '<xsl:template match="/"><xsl:copy-of select="."/></xsl:template>'
    "</xsl:stylesheet>"
)


def test_xslt_cannot_read_local_files(tmp_path: Path) -> None:
    pytest.importorskip("lxml.etree")
    secret = tmp_path / "secret.xml"
    secret.write_text("<secret>must-not-read</secret>", encoding="utf-8")
    stylesheet = (
        '<xsl:stylesheet version="1.0" '
        'xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/"><out>'
        f'<xsl:value-of select="document(\'{secret.as_uri()}\')/secret"/>'
        "</out></xsl:template></xsl:stylesheet>"
    )

    with pytest.raises(Exception, match=r"read|access|denied|rights"):
        apply_xslt("<root/>", stylesheet)


def test_coverage_consumers_reject_malicious_xml_without_expansion(tmp_path: Path) -> None:
    from general_ludd.code_intelligence.introspect import CodebaseIntrospector
    from general_ludd.quality.preflight import check_coverage
    from general_ludd.self_improve.harness import SelfImprovementHarness

    coverage = tmp_path / "coverage.xml"
    coverage.write_text(
        '<!DOCTYPE coverage [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        '<coverage line-rate="&x;"><packages/></coverage>',
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")

    assert CodebaseIntrospector(str(tmp_path))._coverage() is None
    assert not any(
        finding.get("type") == "low_coverage"
        for finding in SelfImprovementHarness(str(tmp_path)).run_gap_analysis()
    )
    result = check_coverage(coverage_xml=coverage)
    assert result["passed"] is False
    assert "entity_forbidden" in str(result["error"])


def test_soap_response_extracts_header_and_body() -> None:
    payload = (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:app="urn:app"><soap:Header><app:Trace>t1</app:Trace></soap:Header>'
        '<soap:Body><app:Result>ok</app:Result></soap:Body></soap:Envelope>'
    )

    result = parse_soap_response(payload)

    assert "Trace" in str(result["header"])
    assert "Result" in str(result["body"])


def test_soap_builder_preserves_non_xml_text_fallbacks() -> None:
    rendered = build_soap_envelope("plain body", header="plain header")

    assert "plain body" in rendered
    assert "plain header" in rendered


def test_docbook_renderer_walks_known_unknown_and_tail_nodes() -> None:
    rendered = docbook_to_html(
        "<chapter><title>Guide</title><unknown>custom</unknown>tail"
        "<para>Hello <emphasis>secure</emphasis></para></chapter>"
    )

    assert '<div class="chapter">' in rendered
    assert "<h1>Guide</h1>" in rendered
    assert "<div>custom</div>tail" in rendered
    assert "<em>secure</em>" in rendered


def test_gradle_dependency_parser_supports_both_notations() -> None:
    dependencies = parse_gradle_dependencies(
        "implementation 'org.example:alpha:1.2.3'\n"
        "api group: 'org.example', name: 'beta', version: '4.5.6'\n"
        "testImplementation group: 'org.example', name: 'gamma'\n"
    )

    assert {tuple(item.values()) for item in dependencies} == {
        ("org.example", "alpha", "1.2.3"),
        ("org.example", "beta", "4.5.6"),
        ("org.example", "gamma", ""),
    }


def test_register_namespaces_and_list_serialization() -> None:
    tree = parse_xml('<root xmlns:a="urn:app"><a:item/></root>')
    register_namespaces(tree, {"app": "urn:app", "": "urn:ignored"})
    converted = xml_to_dict("<root><item>one</item><item>two</item>tail</root>")

    assert tree.getroot().find("{urn:app}item") is not None
    assert converted["root"]["item"] == ["one", "two"]


def test_xsd_list_samples_and_target_namespace() -> None:
    schema = infer_xsd(
        ["<root><required/></root>", "<root><optional/></root>"],
        target_namespace="urn:example",
    )

    assert 'targetNamespace="urn:example"' in schema
    assert 'name="required"' in schema
    assert 'name="optional"' in schema


def test_signature_validation_fails_closed_without_valid_crypto() -> None:
    assert validate_saml_signature("<Assertion/>", "not-a-certificate") is False
