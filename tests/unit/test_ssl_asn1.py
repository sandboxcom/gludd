"""Unit tests for ASN.1 DER parser and encoder."""

from __future__ import annotations

from general_ludd.ssl.asn1 import (
    encode_der,
    generate_oid,
    lookup_oid,
    parse_der,
)


class TestParseDer:
    def test_parse_integer_zero(self):
        der = bytes([0x02, 0x01, 0x00])
        result = parse_der(der)
        assert result["type"] == "INTEGER"
        assert result["value"] == 0

    def test_parse_integer_positive(self):
        der = bytes([0x02, 0x01, 0x2A])
        result = parse_der(der)
        assert result["type"] == "INTEGER"
        assert result["value"] == 42

    def test_parse_integer_negative(self):
        der = bytes([0x02, 0x01, 0xFF])
        result = parse_der(der)
        assert result["type"] == "INTEGER"
        assert result["value"] == -1

    def test_parse_integer_large_positive(self):
        val = 0x01020304
        der = bytes([0x02, 0x04]) + val.to_bytes(4, "big")
        result = parse_der(der)
        assert result["type"] == "INTEGER"
        assert result["value"] == 0x01020304

    def test_parse_integer_with_padding(self):
        val = 0x7F
        der = bytes([0x02, 0x01, val])
        result = parse_der(der)
        assert result["value"] == 127

    def test_parse_boolean_true(self):
        der = bytes([0x01, 0x01, 0xFF])
        result = parse_der(der)
        assert result["type"] == "BOOLEAN"
        assert result["value"] is True

    def test_parse_boolean_false(self):
        der = bytes([0x01, 0x01, 0x00])
        result = parse_der(der)
        assert result["type"] == "BOOLEAN"
        assert result["value"] is False

    def test_parse_null(self):
        der = bytes([0x05, 0x00])
        result = parse_der(der)
        assert result["type"] == "NULL"
        assert result["value"] is None

    def test_parse_oid(self):
        # 1.2.840.113549.1.1.1 = rsaEncryption
        der = bytes([0x06, 0x09, 0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x01])
        result = parse_der(der)
        assert result["type"] == "OID"
        assert result["value"] == "1.2.840.113549.1.1.1"

    def test_parse_oid_short(self):
        # 1.2.3 = 06 02 2A 03
        der = bytes([0x06, 0x02, 0x2A, 0x03])
        result = parse_der(der)
        assert result["type"] == "OID"
        assert result["value"] == "1.2.3"

    def test_parse_octet_string(self):
        der = bytes([0x04, 0x05, 0x68, 0x65, 0x6C, 0x6C, 0x6F])
        result = parse_der(der)
        assert result["type"] == "OCTET STRING"
        assert result["value"] == b"hello"

    def test_parse_bit_string(self):
        # 3 unused bits, remaining = 0b11110 = 0x1E
        der = bytes([0x03, 0x02, 0x03, 0x1E])
        result = parse_der(der)
        assert result["type"] == "BIT STRING"
        assert result["unused_bits"] == 3
        assert result["value"] == b"\x1E"

    def test_parse_utf8string(self):
        der = bytes([0x0C, 0x05, 0x68, 0x65, 0x6C, 0x6C, 0x6F])
        result = parse_der(der)
        assert result["type"] == "UTF8String"
        assert result["value"] == "hello"

    def test_parse_printable_string(self):
        der = bytes([0x13, 0x03, 0x55, 0x53, 0x41])
        result = parse_der(der)
        assert result["type"] == "PrintableString"
        assert result["value"] == "USA"

    def test_parse_ia5string(self):
        der = bytes([0x16, 0x04, 0x74, 0x65, 0x73, 0x74])
        result = parse_der(der)
        assert result["type"] == "IA5String"
        assert result["value"] == "test"

    def test_parse_utctime(self):
        der = bytes([0x17, 0x0D, 0x32, 0x35, 0x30, 0x36, 0x30, 0x31, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x5A])
        result = parse_der(der)
        assert result["type"] == "UTCTime"
        assert result["value"] == "250601000000Z"

    def test_parse_generalized_time(self):
        der = bytes([
            0x18, 0x0F, 0x32, 0x30, 0x32, 0x35, 0x30, 0x36,
            0x30, 0x31, 0x30, 0x30, 0x30, 0x30, 0x30, 0x30, 0x5A,
        ])
        result = parse_der(der)
        assert result["type"] == "GeneralizedTime"
        assert result["value"] == "20250601000000Z"

    def test_parse_sequence_empty(self):
        der = bytes([0x30, 0x00])
        result = parse_der(der)
        assert result["type"] == "SEQUENCE"
        assert result["children"] == []

    def test_parse_sequence_with_children(self):
        # SEQUENCE { INTEGER 42, OCTET STRING "hello" }
        der = bytes([0x30, 0x08, 0x02, 0x01, 0x2A, 0x04, 0x03, 0x68, 0x69, 0x21])
        result = parse_der(der)
        assert result["type"] == "SEQUENCE"
        assert len(result["children"]) == 2
        assert result["children"][0]["type"] == "INTEGER"
        assert result["children"][0]["value"] == 42
        assert result["children"][1]["type"] == "OCTET STRING"
        assert result["children"][1]["value"] == b"hi!"

    def test_parse_nested_sequence(self):
        # SEQUENCE { SEQUENCE { INTEGER 1 } }
        der = bytes([0x30, 0x05, 0x30, 0x03, 0x02, 0x01, 0x01])
        result = parse_der(der)
        assert result["type"] == "SEQUENCE"
        assert len(result["children"]) == 1
        inner = result["children"][0]
        assert inner["type"] == "SEQUENCE"
        assert len(inner["children"]) == 1
        assert inner["children"][0]["type"] == "INTEGER"
        assert inner["children"][0]["value"] == 1

    def test_parse_set(self):
        der = bytes([0x31, 0x03, 0x02, 0x01, 0x05])
        result = parse_der(der)
        assert result["type"] == "SET"
        assert len(result["children"]) == 1
        assert result["children"][0]["type"] == "INTEGER"
        assert result["children"][0]["value"] == 5

    def test_parse_multiple_elements_at_top_level(self):
        # Wrapped in SEQUENCE for valid DER parse
        der = bytes([0x30, 0x06, 0x02, 0x01, 0x05, 0x02, 0x01, 0x0A])
        result = parse_der(der)
        assert result["type"] == "SEQUENCE"
        assert len(result["children"]) == 2
        assert result["children"][0]["value"] == 5
        assert result["children"][1]["value"] == 10

    def test_parse_long_length(self):
        content = b"\x04" * 200
        der = bytes([0x04, 0x81, 0xC8]) + content
        result = parse_der(der)
        assert result["type"] == "OCTET STRING"
        assert len(result["value"]) == 200


class TestEncodeDer:
    def test_encode_integer_zero(self):
        struct = {"type": "INTEGER", "value": 0}
        der = encode_der(struct)
        assert der == bytes([0x02, 0x01, 0x00])

    def test_encode_integer_positive(self):
        struct = {"type": "INTEGER", "value": 42}
        der = encode_der(struct)
        assert der == bytes([0x02, 0x01, 0x2A])

    def test_encode_integer_negative(self):
        struct = {"type": "INTEGER", "value": -1}
        der = encode_der(struct)
        assert der == bytes([0x02, 0x01, 0xFF])

    def test_encode_null(self):
        struct = {"type": "NULL", "value": None}
        der = encode_der(struct)
        assert der == bytes([0x05, 0x00])

    def test_encode_boolean_true(self):
        struct = {"type": "BOOLEAN", "value": True}
        der = encode_der(struct)
        assert der == bytes([0x01, 0x01, 0xFF])

    def test_encode_boolean_false(self):
        struct = {"type": "BOOLEAN", "value": False}
        der = encode_der(struct)
        assert der == bytes([0x01, 0x01, 0x00])

    def test_encode_oid(self):
        struct = {"type": "OID", "value": "1.2.840.113549.1.1.1"}
        der = encode_der(struct)
        assert der == bytes([0x06, 0x09, 0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x01])

    def test_encode_octet_string(self):
        struct = {"type": "OCTET STRING", "value": b"hello"}
        der = encode_der(struct)
        assert der == bytes([0x04, 0x05, 0x68, 0x65, 0x6C, 0x6C, 0x6F])

    def test_encode_utf8string(self):
        struct = {"type": "UTF8String", "value": "hello"}
        der = encode_der(struct)
        assert der == bytes([0x0C, 0x05, 0x68, 0x65, 0x6C, 0x6C, 0x6F])

    def test_encode_sequence(self):
        struct = {
            "type": "SEQUENCE",
            "children": [
                {"type": "INTEGER", "value": 42},
                {"type": "OCTET STRING", "value": b"hi"},
            ],
        }
        der = encode_der(struct)
        assert der == bytes([0x30, 0x07, 0x02, 0x01, 0x2A, 0x04, 0x02, 0x68, 0x69])

    def test_encode_bit_string(self):
        struct = {"type": "BIT STRING", "unused_bits": 0, "value": b"\x05"}
        der = encode_der(struct)
        assert der == bytes([0x03, 0x02, 0x00, 0x05])


class TestRoundtrip:
    def test_roundtrip_integer(self):
        struct = {"type": "INTEGER", "value": 1234567890}
        der = encode_der(struct)
        parsed = parse_der(der)
        assert parsed["type"] == "INTEGER"
        assert parsed["value"] == 1234567890

    def test_roundtrip_negative_integer(self):
        struct = {"type": "INTEGER", "value": -12345}
        der = encode_der(struct)
        parsed = parse_der(der)
        assert parsed["type"] == "INTEGER"
        assert parsed["value"] == -12345

    def test_roundtrip_boolean(self):
        for val in (True, False):
            struct = {"type": "BOOLEAN", "value": val}
            der = encode_der(struct)
            parsed = parse_der(der)
            assert parsed["value"] == val

    def test_roundtrip_null(self):
        struct = {"type": "NULL", "value": None}
        der = encode_der(struct)
        parsed = parse_der(der)
        assert parsed["type"] == "NULL"
        assert parsed["value"] is None

    def test_roundtrip_oid(self):
        struct = {"type": "OID", "value": "1.2.840.10045.2.1"}
        der = encode_der(struct)
        parsed = parse_der(der)
        assert parsed["value"] == "1.2.840.10045.2.1"

    def test_roundtrip_octet_string(self):
        data = b"\x00\x01\x02\xFF\xFE"
        struct = {"type": "OCTET STRING", "value": data}
        der = encode_der(struct)
        parsed = parse_der(der)
        assert parsed["value"] == data

    def test_roundtrip_bit_string(self):
        struct = {"type": "BIT STRING", "unused_bits": 0, "value": b"\xAB\xCD"}
        der = encode_der(struct)
        parsed = parse_der(der)
        assert parsed["value"] == b"\xAB\xCD"
        assert parsed["unused_bits"] == 0

    def test_roundtrip_sequence(self):
        struct = {
            "type": "SEQUENCE",
            "children": [
                {"type": "INTEGER", "value": 7},
                {"type": "BOOLEAN", "value": True},
                {"type": "NULL", "value": None},
                {"type": "OID", "value": "1.3.101.112"},
            ],
        }
        der = encode_der(struct)
        parsed = parse_der(der)
        assert parsed["type"] == "SEQUENCE"
        children = parsed["children"]
        assert len(children) == 4
        assert children[0]["value"] == 7
        assert children[1]["value"] is True
        assert children[2]["value"] is None
        assert children[3]["value"] == "1.3.101.112"

    def test_roundtrip_nested_sequence(self):
        struct = {
            "type": "SEQUENCE",
            "children": [
                {
                    "type": "SEQUENCE",
                    "children": [
                        {"type": "OID", "value": "2.5.4.3"},
                        {"type": "UTF8String", "value": "example.com"},
                    ],
                },
                {"type": "INTEGER", "value": 999},
            ],
        }
        der = encode_der(struct)
        parsed = parse_der(der)
        outer = parsed["children"]
        assert len(outer) == 2
        inner = outer[0]
        assert inner["type"] == "SEQUENCE"
        assert len(inner["children"]) == 2
        assert inner["children"][0]["value"] == "2.5.4.3"
        assert inner["children"][1]["value"] == "example.com"
        assert outer[1]["value"] == 999

    def test_roundtrip_empty_sequence(self):
        struct = {"type": "SEQUENCE", "children": []}
        der = encode_der(struct)
        parsed = parse_der(der)
        assert parsed["type"] == "SEQUENCE"
        assert parsed["children"] == []

    def test_roundtrip_utctime(self):
        struct = {"type": "UTCTime", "value": "250601000000Z"}
        der = encode_der(struct)
        parsed = parse_der(der)
        assert parsed["value"] == "250601000000Z"

    def test_roundtrip_utf8string(self):
        struct = {"type": "UTF8String", "value": "café"}
        der = encode_der(struct)
        parsed = parse_der(der)
        assert parsed["value"] == "café"

    def test_roundtrip_printable_string(self):
        struct = {"type": "PrintableString", "value": "Hello World"}
        der = encode_der(struct)
        parsed = parse_der(der)
        assert parsed["value"] == "Hello World"

    def test_roundtrip_set(self):
        struct = {
            "type": "SET",
            "children": [
                {"type": "INTEGER", "value": 1},
                {"type": "INTEGER", "value": 2},
            ],
        }
        der = encode_der(struct)
        parsed = parse_der(der)
        assert parsed["type"] == "SET"
        assert len(parsed["children"]) == 2
        assert parsed["children"][0]["value"] == 1
        assert parsed["children"][1]["value"] == 2

    def test_roundtrip_generalized_time(self):
        struct = {"type": "GeneralizedTime", "value": "20250601000000Z"}
        der = encode_der(struct)
        parsed = parse_der(der)
        assert parsed["value"] == "20250601000000Z"


class TestLookupOid:
    def test_known_rsa(self):
        info = lookup_oid("1.2.840.113549.1.1.1")
        assert info["name"] == "rsaEncryption"
        assert "RSA" in info["description"]

    def test_known_ecdsa(self):
        info = lookup_oid("1.2.840.10045.2.1")
        assert info["name"] == "ecPublicKey"

    def test_known_ed25519(self):
        info = lookup_oid("1.3.101.112")
        assert info["name"] == "id-Ed25519"

    def test_known_subject_alt_name(self):
        info = lookup_oid("2.5.29.17")
        assert info["name"] == "subjectAltName"

    def test_known_basic_constraints(self):
        info = lookup_oid("2.5.29.19")
        assert info["name"] == "basicConstraints"

    def test_known_key_usage(self):
        info = lookup_oid("2.5.29.15")
        assert info["name"] == "keyUsage"

    def test_known_extended_key_usage(self):
        info = lookup_oid("2.5.29.37")
        assert info["name"] == "extendedKeyUsage"

    def test_known_common_name(self):
        info = lookup_oid("2.5.4.3")
        assert info["name"] == "commonName"

    def test_known_server_auth(self):
        info = lookup_oid("1.3.6.1.5.5.7.3.1")
        assert info["name"] == "serverAuth"

    def test_unknown_oid(self):
        info = lookup_oid("9.9.9.9.9.9")
        assert info["name"] == "unknown"
        assert info["oid"] == "9.9.9.9.9.9"

    def test_returns_all_fields(self):
        info = lookup_oid("2.5.29.1")
        assert "oid" in info
        assert "name" in info
        assert "description" in info
        assert info["name"] == "authorityInfoAccess"


class TestGenerateOid:
    def test_generates_under_parent(self):
        oid = generate_oid("1.3.6.1.4.1", "test-description")
        assert oid.startswith("1.3.6.1.4.1.")
        parts = oid.split(".")
        assert len(parts) >= 8

    def test_generates_unique_oids(self):
        oid1 = generate_oid("1.2.3", "desc-a")
        oid2 = generate_oid("1.2.3", "desc-b")
        assert oid1 != oid2

    def test_generates_distinct_under_different_parents(self):
        oid1 = generate_oid("1.2.3", "x")
        oid2 = generate_oid("4.5.6", "x")
        assert oid1.split(".")[:3] != oid2.split(".")[:3]
        assert oid1.startswith("1.2.3.")
        assert oid2.startswith("4.5.6.")
