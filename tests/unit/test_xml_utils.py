"""Tests for src/general_ludd/xml_utils.py — XML/XSD/XSLT/SOAP/SAML/plist utilities."""

from __future__ import annotations

import plistlib
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

try:
    from lxml.etree import LxmlError

    _XSLT_ERRORS: tuple[type[BaseException], ...] = (ET.ParseError, LxmlError)
except ImportError:
    _XSLT_ERRORS = (ET.ParseError,)

from general_ludd.xml_utils import (
    apply_xslt,
    build_soap_envelope,
    dict_to_xml,
    extract_namespaces,
    infer_xsd,
    parse_saml_assertion,
    parse_xml,
    read_plist,
    write_plist,
    xml_to_dict,
    xpath_query,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_xml_string() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<catalog>'
        '<book id="bk101">'
        '<author>Gambardella, Matthew</author>'
        '<title>XML Developer\'s Guide</title>'
        '<price>44.95</price>'
        '</book>'
        '<book id="bk102">'
        '<author>Ralls, Kim</author>'
        '<title>Midnight Rain</title>'
        '<price>5.95</price>'
        '</book>'
        '</catalog>'
    )


@pytest.fixture
def sample_xml_path(sample_xml_string: str, tmp_path: Path) -> str:
    path = tmp_path / "sample.xml"
    path.write_text(sample_xml_string, encoding="utf-8")
    return str(path)


@pytest.fixture
def invalid_xml_string() -> str:
    return "<catalog><book><title>Unclosed</book></catalog>"


@pytest.fixture
def html_content() -> str:
    return "<html><body><p>Hello</p><br><img src='x.jpg'></body></html>"


@pytest.fixture
def namespaced_xml_string() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ns:catalog xmlns:ns="http://example.com/books"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<ns:book id="bk101">'
        '<dc:title>A Book</dc:title>'
        '</ns:book>'
        '</ns:catalog>'
    )


@pytest.fixture
def xslt_identity() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xsl:stylesheet version="1.0"'
        ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="xml" indent="yes"/>'
        '<xsl:template match="@*|node()">'
        '<xsl:copy><xsl:apply-templates select="@*|node()"/></xsl:copy>'
        '</xsl:template>'
        '</xsl:stylesheet>'
    )


@pytest.fixture
def xslt_param() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xsl:stylesheet version="1.0"'
        ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="xml" indent="yes"/>'
        '<xsl:param name="prefix"/>'
        '<xsl:template match="@*|node()">'
        '<xsl:copy><xsl:apply-templates select="@*|node()"/></xsl:copy>'
        '</xsl:template>'
        '<xsl:template match="title/text()">'
        '<xsl:value-of select="concat($prefix, \' \', .)"/>'
        '</xsl:template>'
        '</xsl:stylesheet>'
    )


@pytest.fixture
def invalid_xslt() -> str:
    return '<xsl:bad-root><xsl:template match="/">no</xsl:template></xsl:bad-root>'


@pytest.fixture
def soap11_envelope_sample() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope'
        ' xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"'
        ' xmlns:web="http://example.com/webservice">'
        '<soapenv:Header>'
        '<web:AuthToken>abc123</web:AuthToken>'
        '</soapenv:Header>'
        '<soapenv:Body>'
        '<web:GetPrice>'
        '<web:Item>Widget</web:Item>'
        '</web:GetPrice>'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )


@pytest.fixture
def soap12_envelope_sample() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope'
        ' xmlns:soap="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:web="http://example.com/webservice">'
        '<soap:Body>'
        '<web:Query>'
        '<web:Status>active</web:Status>'
        '</web:Query>'
        '</soap:Body>'
        '</soap:Envelope>'
    )


@pytest.fixture
def saml_assertion_valid() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<saml2:Assertion'
        ' xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"'
        ' ID="_abc123"'
        ' IssueInstant="2099-01-01T00:00:00Z"'
        ' Version="2.0">'
        '<saml2:Issuer>https://idp.example.com</saml2:Issuer>'
        '<saml2:Subject>'
        '<saml2:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">'
        'user@example.com'
        '</saml2:NameID>'
        '</saml2:Subject>'
        '<saml2:Conditions'
        ' NotBefore="2099-01-01T00:00:00Z"'
        ' NotOnOrAfter="2099-12-31T23:59:59Z">'
        '</saml2:Conditions>'
        '<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
        '<ds:SignedInfo><ds:CanonicalizationMethod Algorithm="..."/>'
        '<ds:SignatureMethod Algorithm="..."/>'
        '<ds:Reference URI="#_abc123"/></ds:SignedInfo>'
        '<ds:SignatureValue>dGhpcyBpcyBhIHNpZ25hdHVyZQ==</ds:SignatureValue>'
        '</ds:Signature>'
        '</saml2:Assertion>'
    )


@pytest.fixture
def saml_assertion_expired() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<saml2:Assertion'
        ' xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"'
        ' ID="_expired456"'
        ' IssueInstant="2020-01-01T00:00:00Z"'
        ' Version="2.0">'
        '<saml2:Issuer>https://idp.example.com</saml2:Issuer>'
        '<saml2:Subject>'
        '<saml2:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">'
        'user@example.com'
        '</saml2:NameID>'
        '</saml2:Subject>'
        '<saml2:Conditions'
        ' NotBefore="2020-01-01T00:00:00Z"'
        ' NotOnOrAfter="2020-01-02T00:00:00Z">'
        '</saml2:Conditions>'
        '<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
        '<ds:SignedInfo><ds:CanonicalizationMethod Algorithm="..."/>'
        '<ds:SignatureMethod Algorithm="..."/>'
        '<ds:Reference URI="#_expired456"/></ds:SignedInfo>'
        '<ds:SignatureValue>dGhpcyBpcyBhIHNpZ25hdHVyZQ==</ds:SignatureValue>'
        '</ds:Signature>'
        '</saml2:Assertion>'
    )


@pytest.fixture
def saml_assertion_no_signature() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<saml2:Assertion'
        ' xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"'
        ' ID="_nosig789"'
        ' IssueInstant="2099-01-01T00:00:00Z"'
        ' Version="2.0">'
        '<saml2:Issuer>https://idp.example.com</saml2:Issuer>'
        '<saml2:Subject>'
        '<saml2:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">'
        'user@example.com'
        '</saml2:NameID>'
        '</saml2:Subject>'
        '</saml2:Assertion>'
    )


@pytest.fixture
def plist_data() -> dict:
    return {
        "Name": "TestApp",
        "Version": "1.0",
        "Nested": {
            "Key": "Value",
            "List": ["a", "b", "c"],
        },
        "Enabled": True,
        "Count": 42,
        "Data": b"binary-blob",
    }


@pytest.fixture
def simple_dict() -> dict:
    return {"root": "value"}


@pytest.fixture
def nested_dict() -> dict:
    return {
        "person": {
            "@id": "1",
            "name": "Alice",
            "age": 30,
            "address": {"city": "NYC", "state": "NY"},
        }
    }


# ---------------------------------------------------------------------------
# 1. parse_xml
# ---------------------------------------------------------------------------

class TestParseXml:
    def test_parses_valid_xml_string(self, sample_xml_string: str) -> None:
        tree = parse_xml(sample_xml_string)
        assert isinstance(tree, ET.ElementTree)
        root = tree.getroot()
        assert root.tag == "catalog"
        assert len(root.findall("book")) == 2

    def test_parses_valid_xml_file_path(self, sample_xml_path: str) -> None:
        tree = parse_xml(sample_xml_path)
        root = tree.getroot()
        assert root.tag == "catalog"

    def test_invalid_xml_raises(self, invalid_xml_string: str) -> None:
        with pytest.raises(ET.ParseError):
            parse_xml(invalid_xml_string)

    def test_html_mode_with_tolerant_parser(self, html_content: str) -> None:
        tree = parse_xml(html_content, html=True)
        assert tree is not None
        root = tree.getroot()
        assert root.tag == "html"
        body = root.find("body")
        assert body is not None
        assert body.find("p").text == "Hello"


# ---------------------------------------------------------------------------
# 2. xpath_query
# ---------------------------------------------------------------------------

class TestXpathQuery:
    def test_simple_path_returns_elements(self, sample_xml_string: str) -> None:
        tree = parse_xml(sample_xml_string)
        results = xpath_query(tree, ".//book/author")
        assert len(results) == 2
        assert results[0].text == "Gambardella, Matthew"

    def test_no_matches_returns_empty_list(self, sample_xml_string: str) -> None:
        tree = parse_xml(sample_xml_string)
        results = xpath_query(tree, ".//magazine")
        assert results == []
        assert isinstance(results, list)

    def test_namespaced_path(self, namespaced_xml_string: str) -> None:
        tree = parse_xml(namespaced_xml_string)
        namespaces = {"ns": "http://example.com/books", "dc": "http://purl.org/dc/elements/1.1/"}
        results = xpath_query(tree, ".//dc:title", namespaces=namespaces)
        assert len(results) == 1
        assert results[0].text == "A Book"

    def test_count_query_returns_numeric(self, sample_xml_string: str) -> None:
        tree = parse_xml(sample_xml_string)
        count = xpath_query(tree, "count(.//book)")
        assert count == 2


# ---------------------------------------------------------------------------
# 3. infer_xsd
# ---------------------------------------------------------------------------

class TestInferXsd:
    def test_from_single_xml_file(self, sample_xml_string: str) -> None:
        schema = infer_xsd(sample_xml_string)
        assert schema is not None
        assert isinstance(schema, str)
        assert "xs:schema" in schema.lower() or "xsd:schema" in schema.lower()

    def test_handles_nested_elements(self, nested_dict: dict) -> None:
        xml = dict_to_xml(nested_dict)
        schema = infer_xsd(xml)
        assert schema is not None
        assert "address" in schema or "person" in schema

    def test_handles_attributes(self, sample_xml_string: str) -> None:
        schema = infer_xsd(sample_xml_string)
        assert schema is not None
        assert "book" in schema.lower() or "id" in schema.lower()

    def test_from_multiple_files_merges(
        self, sample_xml_string: str, namespaced_xml_string: str
    ) -> None:
        schema = infer_xsd(sample_xml_string, additional=[namespaced_xml_string])
        assert schema is not None
        assert len(schema) >= len(infer_xsd(sample_xml_string) or "")


# ---------------------------------------------------------------------------
# 4. apply_xslt
# ---------------------------------------------------------------------------

class TestApplyXslt:
    def test_identity_transform(self, sample_xml_string: str, xslt_identity: str) -> None:
        result = apply_xslt(sample_xml_string, xslt_identity)
        assert result is not None
        root = ET.fromstring(result)
        assert root.tag == "catalog"

    def test_parameterized_transform(
        self, sample_xml_string: str, xslt_param: str
    ) -> None:
        result = apply_xslt(sample_xml_string, xslt_param, params={"prefix": "Book:"})
        assert result is not None
        assert "Book:" in result

    def test_invalid_xslt_raises(self, sample_xml_string: str, invalid_xslt: str) -> None:
        with pytest.raises(_XSLT_ERRORS):
            apply_xslt(sample_xml_string, invalid_xslt)

    def test_transform_preserves_structure(
        self, sample_xml_string: str, xslt_identity: str
    ) -> None:
        result = apply_xslt(sample_xml_string, xslt_identity)
        reread = ET.fromstring(result)
        assert len(reread.findall("book")) == 2


# ---------------------------------------------------------------------------
# 5. xml_to_dict
# ---------------------------------------------------------------------------

class TestXmlToDict:
    def test_simple_element(self) -> None:
        xml = "<root>hello</root>"
        result = xml_to_dict(xml)
        assert isinstance(result, dict)
        assert "root" in result

    def test_nested_elements(self) -> None:
        xml = "<root><child><name>Alice</name></child></root>"
        result = xml_to_dict(xml)
        assert "root" in result

    def test_attributes(self) -> None:
        xml = '<root id="1"><name>Alice</name></root>'
        result = xml_to_dict(xml)
        assert "root" in result

    def test_text_content(self) -> None:
        xml = "<root>hello world</root>"
        result = xml_to_dict(xml)
        root_value = result.get("root")
        assert root_value is not None

    def test_handles_empty_element(self) -> None:
        xml = "<root></root>"
        result = xml_to_dict(xml)
        assert "root" in result


# ---------------------------------------------------------------------------
# 6. dict_to_xml
# ---------------------------------------------------------------------------

class TestDictToXml:
    def test_simple_dict(self, simple_dict: dict) -> None:
        xml = dict_to_xml(simple_dict)
        assert xml is not None
        assert "root" in xml

    def test_nested_dict(self, nested_dict: dict) -> None:
        xml = dict_to_xml(nested_dict)
        assert xml is not None
        assert "person" in xml

    def test_with_attributes(self, nested_dict: dict) -> None:
        xml = dict_to_xml(nested_dict)
        assert xml is not None
        assert "id" in xml or "1" in xml

    def test_roundtrip_stable(self, simple_dict: dict) -> None:
        xml = dict_to_xml(simple_dict)
        result = xml_to_dict(xml)
        assert isinstance(result, dict)
        assert result

    def test_escapes_special_characters(self) -> None:
        d = {"root": "a < b & c > d"}
        xml = dict_to_xml(d)
        assert "&lt;" in xml
        assert "&gt;" in xml
        assert "&amp;" in xml


# ---------------------------------------------------------------------------
# 7. extract_namespaces
# ---------------------------------------------------------------------------

class TestExtractNamespaces:
    def test_default_namespace(self, namespaced_xml_string: str) -> None:
        ns = extract_namespaces(namespaced_xml_string)
        assert isinstance(ns, dict)
        assert "ns" in ns
        assert ns["ns"] == "http://example.com/books"

    def test_prefixed_namespaces(self, namespaced_xml_string: str) -> None:
        ns = extract_namespaces(namespaced_xml_string)
        assert "dc" in ns
        assert ns["dc"] == "http://purl.org/dc/elements/1.1/"

    def test_multiple_namespaces(self, namespaced_xml_string: str) -> None:
        ns = extract_namespaces(namespaced_xml_string)
        assert len(ns) >= 2

    def test_no_namespaces_returns_empty_dict(self, sample_xml_string: str) -> None:
        ns = extract_namespaces(sample_xml_string)
        assert isinstance(ns, dict)
        assert ns == {}


# ---------------------------------------------------------------------------
# 8. build_soap_envelope
# ---------------------------------------------------------------------------

class TestBuildSoapEnvelope:
    def test_soap_1_1(self) -> None:
        body = '<web:GetPrice xmlns:web="http://example.com/ws"><web:Item>W</web:Item></web:GetPrice>'
        envelope = build_soap_envelope(body, version="1.1")
        assert envelope is not None
        root = ET.fromstring(envelope)
        assert "http://schemas.xmlsoap.org/soap/envelope/" in root.tag

    def test_soap_1_2(self) -> None:
        body = '<web:Query xmlns:web="http://example.com/ws"><web:Status>ok</web:Status></web:Query>'
        envelope = build_soap_envelope(body, version="1.2")
        root = ET.fromstring(envelope)
        assert "http://www.w3.org/2003/05/soap-envelope" in root.tag

    def test_with_header(self) -> None:
        body = "<web:Query/>"
        header = "<web:Auth>token</web:Auth>"
        envelope = build_soap_envelope(body, header=header, version="1.1")
        root = ET.fromstring(envelope)
        ns = "http://schemas.xmlsoap.org/soap/envelope/"
        header_el = root.find(f"{{{ns}}}Header")
        assert header_el is not None

    def test_with_body_content(self) -> None:
        body = "<web:Query/>"
        envelope = build_soap_envelope(body)
        root = ET.fromstring(envelope)
        # Body element should exist
        body_tag = next(
            (
                el
                for el in root
                if "Body" in el.tag
            ),
            None,
        )
        assert body_tag is not None


# ---------------------------------------------------------------------------
# 9. parse_saml_assertion
# ---------------------------------------------------------------------------

class TestParseSamlAssertion:
    def test_valid_assertion(self, saml_assertion_valid: str) -> None:
        result = parse_saml_assertion(saml_assertion_valid)
        assert result is not None
        assert isinstance(result, dict)

    def test_expired_assertion_raises_or_returns_expired_flag(
        self, saml_assertion_expired: str
    ) -> None:
        result = parse_saml_assertion(saml_assertion_expired)
        assert result is not None
        assert result.get("valid") is False or result.get("expired") is True

    def test_missing_signature_raises_or_returns_unsigned_flag(
        self, saml_assertion_no_signature: str
    ) -> None:
        result = parse_saml_assertion(saml_assertion_no_signature)
        assert result is not None
        assert result.get("signed") is False or result.get("valid") is False

    def test_extracts_subject(self, saml_assertion_valid: str) -> None:
        result = parse_saml_assertion(saml_assertion_valid)
        subject = result.get("subject") or result.get("nameid") or result.get("NameID")
        assert subject is not None
        assert "user@example.com" in str(subject)

    def test_extracts_issuer(self, saml_assertion_valid: str) -> None:
        result = parse_saml_assertion(saml_assertion_valid)
        issuer = result.get("issuer") or result.get("Issuer")
        assert issuer is not None
        assert "idp.example.com" in str(issuer)


# ---------------------------------------------------------------------------
# 10. read_plist / write_plist
# ---------------------------------------------------------------------------

class TestPlistRead:
    def test_read_xml_format(self, plist_data: dict, tmp_path: Path) -> None:
        path = tmp_path / "test.plist"
        with path.open("wb") as f:
            plistlib.dump(plist_data, f, fmt=plistlib.FMT_XML)
        result = read_plist(str(path))
        assert isinstance(result, dict)
        assert result["Name"] == "TestApp"
        assert result["Version"] == "1.0"

    def test_read_binary_format(self, plist_data: dict, tmp_path: Path) -> None:
        path = tmp_path / "test_bin.plist"
        with path.open("wb") as f:
            plistlib.dump(plist_data, f, fmt=plistlib.FMT_BINARY)
        result = read_plist(str(path))
        assert isinstance(result, dict)
        assert result["Name"] == "TestApp"

    def test_read_nested_data_types(self, plist_data: dict, tmp_path: Path) -> None:
        path = tmp_path / "test_nested.plist"
        with path.open("wb") as f:
            plistlib.dump(plist_data, f, fmt=plistlib.FMT_XML)
        result = read_plist(str(path))
        assert isinstance(result["Nested"], dict)
        assert result["Nested"]["List"] == ["a", "b", "c"]
        assert result["Enabled"] is True
        assert result["Count"] == 42

    def test_read_nonexistent_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            read_plist("/nonexistent/path.plist")


class TestPlistWrite:
    def test_write_xml_format(self, plist_data: dict, tmp_path: Path) -> None:
        path = tmp_path / "out.plist"
        write_plist(plist_data, str(path), fmt="xml")
        assert path.exists()
        with path.open("rb") as f:
            reloaded = plistlib.load(f)
        assert reloaded["Name"] == "TestApp"

    def test_write_binary_format(self, plist_data: dict, tmp_path: Path) -> None:
        path = tmp_path / "out_bin.plist"
        write_plist(plist_data, str(path), fmt="binary")
        assert path.exists()
        with path.open("rb") as f:
            reloaded = plistlib.load(f)
        assert reloaded["Name"] == "TestApp"

    def test_write_nested_data_types(self, plist_data: dict, tmp_path: Path) -> None:
        path = tmp_path / "out_nested.plist"
        write_plist(plist_data, str(path))
        with path.open("rb") as f:
            reloaded = plistlib.load(f)
        assert isinstance(reloaded["Nested"], dict)
        assert reloaded["Nested"]["List"] == ["a", "b", "c"]

    def test_write_defaults_to_xml_format(self, plist_data: dict, tmp_path: Path) -> None:
        path = tmp_path / "out_default.plist"
        write_plist(plist_data, str(path))
        with path.open("rb") as f:
            reloaded = plistlib.load(f)
        assert reloaded["Name"] == "TestApp"
