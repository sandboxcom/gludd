"""Structural tests for ssl/asn1.py — ASN.1 DER parser and encoder."""

from __future__ import annotations

import pytest
from ansible_collections.general_ludd.security.plugins.module_utils.asn1 import (
    _KNOWN_OIDS,
    _NAME_TO_TAG,
    _TAG_CLASS_BITS,
    _TAG_NAMES,
    _decode_int,
    _decode_length,
    _decode_oid,
    _decode_tlv,
    _encode_int,
    _encode_length,
    _encode_oid,
    _encode_value,
    _encoded_child_length,
    encode_der,
    generate_oid,
    lookup_oid,
    parse_der,
)


class TestTagTables:
    def test_tag_names_has_sequence(self) -> None:
        assert _TAG_NAMES[0x10] == "SEQUENCE"
        assert _TAG_NAMES[0x02] == "INTEGER"
        assert _TAG_NAMES[0x06] == "OID"

    def test_tag_class_bits_has_universal(self) -> None:
        assert _TAG_CLASS_BITS[0x00] == "UNIVERSAL"
        assert _TAG_CLASS_BITS[0xC0] == "PRIVATE"

    def test_name_to_tag_is_inverse(self) -> None:
        assert _NAME_TO_TAG["BOOLEAN"] == 0x01
        assert _NAME_TO_TAG["NULL"] == 0x05
        assert _NAME_TO_TAG["OCTET STRING"] == 0x04

    def test_known_oids_contains_common(self) -> None:
        entry = _KNOWN_OIDS["2.5.4.3"]
        assert entry["name"] == "commonName"
        assert isinstance(entry["description"], str)


class TestIntegerEncoding:
    def test_encode_zero(self) -> None:
        assert _encode_int(0) == b"\x00"

    def test_encode_positive_small(self) -> None:
        assert _encode_int(1) == b"\x01"
        assert _encode_int(127) == b"\x7f"

    def test_encode_positive_requires_zero_pad(self) -> None:
        result = _encode_int(128)
        assert result == b"\x00\x80"

    def test_encode_decode_roundtrip(self) -> None:
        for value in (0, 1, 127, 128, 256, 65536, 1234567890):
            assert _decode_int(_encode_int(value)) == value

    def test_decode_empty_is_zero(self) -> None:
        assert _decode_int(b"") == 0

    @pytest.mark.parametrize("value", [-1, -127, -128, -256, -65535])
    def test_negative_encode_decode_roundtrip(self, value: int) -> None:
        assert _decode_int(_encode_int(value)) == value


class TestOIDEncoding:
    def test_encode_decode_simple(self) -> None:
        oid = "1.2.840.113549"
        assert _decode_oid(_encode_oid(oid)) == oid

    def test_encode_oid_needs_two_arcs(self) -> None:
        with pytest.raises(ValueError, match="at least 2 arcs"):
            _encode_oid("1")

    def test_decode_oid_common_name(self) -> None:
        encoded = _encode_oid("2.5.4.3")
        assert _decode_oid(encoded) == "2.5.4.3"


class TestLengthEncoding:
    def test_short_form(self) -> None:
        assert _encode_length(5) == b"\x05"
        assert _encode_length(0x7F) == b"\x7f"

    def test_long_form(self) -> None:
        assert _encode_length(0x80) == b"\x81\x80"
        assert _encode_length(0x0100) == b"\x82\x01\x00"

    def test_decode_length_short(self) -> None:
        length, offset = _decode_length(b"\x05\xff\xff", 0)
        assert length == 5
        assert offset == 1

    def test_decode_length_long(self) -> None:
        length, offset = _decode_length(b"\x82\x01\x00\xff", 0)
        assert length == 256
        assert offset == 3

    def test_indefinite_length_raises(self) -> None:
        with pytest.raises(ValueError, match="Indefinite"):
            _decode_length(b"\x80", 0)


class TestTLVDecoding:
    def test_decode_null(self) -> None:
        result, _ = _decode_tlv(bytes([0x05, 0x00]), 0)
        assert result["type"] == "NULL"
        assert result["value"] is None

    def test_decode_boolean_true(self) -> None:
        result, _ = _decode_tlv(bytes([0x01, 0x01, 0xFF]), 0)
        assert result["value"] is True

    def test_decode_boolean_false(self) -> None:
        result, _ = _decode_tlv(bytes([0x01, 0x01, 0x00]), 0)
        assert result["value"] is False

    def test_decode_integer(self) -> None:
        result, _ = _decode_tlv(bytes([0x02, 0x01, 0x2A]), 0)
        assert result["value"] == 42

    def test_decode_oid(self) -> None:
        der = bytes([0x06, 0x03, 0x55, 0x04, 0x03])
        result, _ = _decode_tlv(der, 0)
        assert result["value"] == "2.5.4.3"

    def test_decode_sequence(self) -> None:
        der = bytes([0x30, 0x06, 0x02, 0x01, 0x01, 0x02, 0x01, 0x02])
        result, _ = _decode_tlv(der, 0)
        assert result["type"] == "SEQUENCE"
        assert len(result["children"]) == 2
        assert result["children"][0]["value"] == 1
        assert result["children"][1]["value"] == 2

    def test_decode_octet_string(self) -> None:
        result, _ = _decode_tlv(bytes([0x04, 0x03, 0x41, 0x42, 0x43]), 0)
        assert result["value"] == b"ABC"

    def test_decode_eof_raises(self) -> None:
        with pytest.raises(ValueError, match="Unexpected end"):
            _decode_tlv(b"", 0)

    def test_decode_constructed_bitstring(self) -> None:
        der = bytes([0x23, 0x05, 0x00, 0x03, 0x00, 0x01, 0x02])
        result, _ = _decode_tlv(der, 0)
        assert result["unused_bits"] == 0
        assert result["value"] == b"\x03\x00\x01\x02"

    def test_decode_constructed_octet_string(self) -> None:
        result, _ = _decode_tlv(bytes([0x24, 0x03, 0x02, 0x01, 0x2A]), 0)
        assert result["children"][0]["value"] == 42

    def test_decode_high_tag_number(self) -> None:
        result, offset = _decode_tlv(bytes([0x1F, 0x20, 0x00]), 0)
        assert result == {"type": "TAG_32", "class": "UNIVERSAL", "value": b""}
        assert offset == 3

    @pytest.mark.parametrize(
        ("der", "expected"),
        [
            (bytes([0x17, 0x03]) + b"now", "now"),
            (bytes([0x18, 0x03]) + b"now", "now"),
            (bytes([0x13, 0x03]) + b"abc", "abc"),
        ],
    )
    def test_decode_text_types(self, der: bytes, expected: str) -> None:
        result, _ = _decode_tlv(der, 0)
        assert result["value"] == expected


class TestDERRoundtrip:
    def test_encode_decode_null(self) -> None:
        structure = {"type": "NULL", "value": None}
        der = encode_der(structure)
        parsed = parse_der(der)
        assert parsed["type"] == "NULL"

    def test_encode_decode_integer(self) -> None:
        structure = {"type": "INTEGER", "value": 42}
        der = encode_der(structure)
        parsed = parse_der(der)
        assert parsed["value"] == 42

    def test_encode_decode_oid(self) -> None:
        structure = {"type": "OID", "value": "1.2.840.113549.1.1.11"}
        der = encode_der(structure)
        parsed = parse_der(der)
        assert parsed["value"] == "1.2.840.113549.1.1.11"

    def test_encode_decode_string(self) -> None:
        structure = {"type": "UTF8String", "value": "hello"}
        der = encode_der(structure)
        parsed = parse_der(der)
        assert parsed["value"] == "hello"

    def test_encode_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown type"):
            encode_der({"type": "MADE_UP"})

    def test_encode_decode_sequence(self) -> None:
        structure = {
            "type": "SEQUENCE",
            "children": [
                {"type": "INTEGER", "value": 1},
                {"type": "OCTET STRING", "value": b"AB"},
            ],
        }
        der = encode_der(structure)
        parsed = parse_der(der)
        assert parsed["type"] == "SEQUENCE"
        assert len(parsed["children"]) == 2

    def test_encode_decode_bitstring(self) -> None:
        structure = {"type": "BIT STRING", "value": b"\x0f\x0f", "unused_bits": 4}
        der = encode_der(structure)
        parsed = parse_der(der)
        assert parsed["unused_bits"] == 4
        assert parsed["value"] == b"\x0f\x0f"

    def test_parse_appends_trailing_children_to_constructed_value(self) -> None:
        parsed = parse_der(bytes([0x30, 0x03, 0x02, 0x01, 0x01, 0x02, 0x01, 0x02]))
        assert [child["value"] for child in parsed["children"]] == [1, 2]

    def test_parse_rejects_trailing_primitive_data(self) -> None:
        with pytest.raises(ValueError, match="Trailing data"):
            parse_der(bytes([0x02, 0x01, 0x01, 0x05, 0x00]))

    def test_encode_value_accepts_collection_wire_types(self) -> None:
        assert _encode_value({"type": "OCTET STRING", "value": "abc"}) == b"abc"
        assert _encode_value({"type": "UTF8String", "value": b"abc"}) == b"abc"
        assert _encode_value({"type": "UTCTime", "value": b"now"}) == b"now"
        assert _encode_value({"type": "GeneralizedTime", "value": "later"}) == b"later"

    def test_encode_value_fallbacks_are_bounded(self) -> None:
        assert _encode_value({"type": "TAG_99", "value": b"raw"}) == b"raw"
        assert _encode_value({"type": "TAG_99", "value": "text"}) == b"text"
        assert _encode_value({"type": "TAG_99", "value": object()}) == b""


class TestLookupOID:
    def test_known_oid(self) -> None:
        result = lookup_oid("2.5.4.3")
        assert result["name"] == "commonName"
        assert result["oid"] == "2.5.4.3"

    def test_unknown_oid(self) -> None:
        result = lookup_oid("9.9.9.9.9.9")
        assert result["name"] == "unknown"
        assert result["oid"] == "9.9.9.9.9.9"


class TestGenerateOID:
    def test_generates_child_arcs(self) -> None:
        oid = generate_oid("1.2.3", "test oid")
        assert oid.startswith("1.2.3.")
        parts = oid.split(".")
        assert len(parts) == 5

    def test_generated_oids_are_unique(self) -> None:
        oid1 = generate_oid("1.2", "a")
        oid2 = generate_oid("1.2", "a")
        assert oid1 != oid2


class TestEncodedChildLength:
    def test_offset_past_end(self) -> None:
        assert _encoded_child_length(b"", 0) == 0

    def test_simple_tlv(self) -> None:
        der = bytes([0x02, 0x01, 0x2A])
        assert _encoded_child_length(der, 0) == 3

    def test_high_tag_and_long_length(self) -> None:
        der = bytes([0x1F, 0x81, 0x20, 0x81, 0x80]) + bytes(128)
        assert _encoded_child_length(der, 0) == len(der)

    def test_truncated_tag_or_length_returns_zero(self) -> None:
        assert _encoded_child_length(bytes([0x1F, 0x81]), 0) == 0
