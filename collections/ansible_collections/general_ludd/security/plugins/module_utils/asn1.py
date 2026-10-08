"""Security-collection ASN.1 DER parser and encoder.

Handles Tag-Length-Value (TLV) encoding per ITU-T X.690.
Supports common ASN.1 types used in X.509 certificates and PKIX.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

_TAG_NAMES: dict[int, str] = {
    0x01: "BOOLEAN",
    0x02: "INTEGER",
    0x03: "BIT STRING",
    0x04: "OCTET STRING",
    0x05: "NULL",
    0x06: "OID",
    0x0C: "UTF8String",
    0x10: "SEQUENCE",
    0x11: "SET",
    0x13: "PrintableString",
    0x14: "T61String",
    0x16: "IA5String",
    0x17: "UTCTime",
    0x18: "GeneralizedTime",
    0x1C: "UniversalString",
    0x1E: "BMPString",
}

_TAG_CLASS_BITS: dict[int, str] = {
    0x00: "UNIVERSAL",
    0x40: "APPLICATION",
    0x80: "CONTEXT",
    0xC0: "PRIVATE",
}

_NAME_TO_TAG: dict[str, int] = {v: k for k, v in _TAG_NAMES.items()}

_KNOWN_OIDS: dict[str, dict[str, str]] = {
    "1.2.840.113549.1.1.1": {
        "name": "rsaEncryption",
        "description": "RSA public key encryption (PKCS #1)",
    },
    "1.2.840.113549.1.1.5": {
        "name": "sha1WithRSAEncryption",
        "description": "SHA-1 with RSA encryption signature algorithm",
    },
    "1.2.840.113549.1.1.10": {
        "name": "rsassaPss",
        "description": "RSA-PSS signature algorithm",
    },
    "1.2.840.113549.1.1.11": {
        "name": "sha256WithRSAEncryption",
        "description": "SHA-256 with RSA encryption signature algorithm",
    },
    "1.2.840.113549.1.1.12": {
        "name": "sha384WithRSAEncryption",
        "description": "SHA-384 with RSA encryption signature algorithm",
    },
    "1.2.840.113549.1.1.13": {
        "name": "sha512WithRSAEncryption",
        "description": "SHA-512 with RSA encryption signature algorithm",
    },
    "1.2.840.113549.1.1.14": {
        "name": "sha224WithRSAEncryption",
        "description": "SHA-224 with RSA encryption signature algorithm",
    },
    "1.2.840.113549.1.1.7": {
        "name": "rsaesOaep",
        "description": "RSAES-OAEP encryption scheme",
    },
    "1.2.840.113549.1.9.1": {
        "name": "emailAddress",
        "description": "Email address in subject DN",
    },
    "1.2.840.113549.1.9.3": {
        "name": "contentType",
        "description": "PKCS #9 content type attribute",
    },
    "1.2.840.113549.1.9.4": {
        "name": "messageDigest",
        "description": "PKCS #9 message digest attribute",
    },
    "1.2.840.113549.1.9.5": {
        "name": "signingTime",
        "description": "PKCS #9 signing time attribute",
    },
    "1.2.840.113549.3.7": {
        "name": "des-ede3-cbc",
        "description": "Triple DES encryption in CBC mode",
    },
    "2.5.4.3": {
        "name": "commonName",
        "description": "X.500 common name attribute",
    },
    "2.5.4.4": {
        "name": "surname",
        "description": "X.500 surname attribute",
    },
    "2.5.4.5": {
        "name": "serialNumber",
        "description": "X.500 serial number attribute",
    },
    "2.5.4.6": {
        "name": "countryName",
        "description": "X.500 country name attribute",
    },
    "2.5.4.7": {
        "name": "localityName",
        "description": "X.500 locality name attribute",
    },
    "2.5.4.8": {
        "name": "stateOrProvinceName",
        "description": "X.500 state or province name attribute",
    },
    "2.5.4.9": {
        "name": "streetAddress",
        "description": "X.500 street address attribute",
    },
    "2.5.4.10": {
        "name": "organizationName",
        "description": "X.500 organization name attribute",
    },
    "2.5.4.11": {
        "name": "organizationalUnitName",
        "description": "X.500 organizational unit name attribute",
    },
    "2.5.4.12": {
        "name": "title",
        "description": "X.500 title attribute",
    },
    "2.5.4.13": {
        "name": "description",
        "description": "X.500 description attribute",
    },
    "2.5.4.15": {
        "name": "businessCategory",
        "description": "X.500 business category attribute",
    },
    "2.5.4.17": {
        "name": "postalCode",
        "description": "X.500 postal code attribute",
    },
    "2.5.4.20": {
        "name": "telephoneNumber",
        "description": "X.500 telephone number attribute",
    },
    "2.5.4.42": {
        "name": "givenName",
        "description": "X.500 given name attribute",
    },
    "2.5.29.14": {
        "name": "subjectKeyIdentifier",
        "description": "X.509 subject key identifier extension",
    },
    "2.5.29.15": {
        "name": "keyUsage",
        "description": "X.509 key usage extension",
    },
    "2.5.29.17": {
        "name": "subjectAltName",
        "description": "X.509 subject alternative name extension",
    },
    "2.5.29.18": {
        "name": "issuerAltName",
        "description": "X.509 issuer alternative name extension",
    },
    "2.5.29.19": {
        "name": "basicConstraints",
        "description": "X.509 basic constraints extension",
    },
    "2.5.29.20": {
        "name": "cRLNumber",
        "description": "X.509 CRL number extension",
    },
    "2.5.29.21": {
        "name": "cRLReason",
        "description": "X.509 CRL reason code extension",
    },
    "2.5.29.31": {
        "name": "cRLDistributionPoints",
        "description": "X.509 CRL distribution points extension",
    },
    "2.5.29.32": {
        "name": "certificatePolicies",
        "description": "X.509 certificate policies extension",
    },
    "2.5.29.35": {
        "name": "authorityKeyIdentifier",
        "description": "X.509 authority key identifier extension",
    },
    "2.5.29.36": {
        "name": "policyConstraints",
        "description": "X.509 policy constraints extension",
    },
    "2.5.29.37": {
        "name": "extendedKeyUsage",
        "description": "X.509 extended key usage extension",
    },
    "1.2.840.10040.4.1": {
        "name": "id-dsa",
        "description": "DSA public key algorithm",
    },
    "1.2.840.10040.4.3": {
        "name": "id-dsa-with-sha1",
        "description": "DSA with SHA-1 signature algorithm",
    },
    "1.2.840.10045.2.1": {
        "name": "ecPublicKey",
        "description": "Elliptic curve public key algorithm",
    },
    "1.2.840.10045.3.1.7": {
        "name": "secp256r1",
        "description": "NIST P-256 / secp256r1 elliptic curve",
    },
    "1.3.132.0.10": {
        "name": "secp256k1",
        "description": "SEC 2 secp256k1 elliptic curve",
    },
    "1.3.132.0.34": {
        "name": "secp384r1",
        "description": "NIST P-384 / secp384r1 elliptic curve",
    },
    "1.2.840.10045.4.3.2": {
        "name": "ecdsa-with-SHA256",
        "description": "ECDSA with SHA-256 signature algorithm",
    },
    "1.2.840.10045.4.3.3": {
        "name": "ecdsa-with-SHA384",
        "description": "ECDSA with SHA-384 signature algorithm",
    },
    "1.2.840.10045.4.3.4": {
        "name": "ecdsa-with-SHA512",
        "description": "ECDSA with SHA-512 signature algorithm",
    },
    "1.3.101.112": {
        "name": "id-Ed25519",
        "description": "Ed25519 signature algorithm key (EdDSA)",
    },
    "1.3.101.113": {
        "name": "id-Ed448",
        "description": "Ed448 signature algorithm key (EdDSA)",
    },
    "1.3.101.110": {
        "name": "id-X25519",
        "description": "X25519 key agreement algorithm",
    },
    "1.3.101.111": {
        "name": "id-X448",
        "description": "X448 key agreement algorithm",
    },
    "1.3.6.1.5.5.7.3.1": {
        "name": "serverAuth",
        "description": "TLS server authentication EKU",
    },
    "1.3.6.1.5.5.7.3.2": {
        "name": "clientAuth",
        "description": "TLS client authentication EKU",
    },
    "1.3.6.1.5.5.7.3.3": {
        "name": "codeSigning",
        "description": "Code signing EKU",
    },
    "1.3.6.1.5.5.7.3.4": {
        "name": "emailProtection",
        "description": "Email protection EKU",
    },
    "1.3.6.1.5.5.7.3.8": {
        "name": "timeStamping",
        "description": "Time stamping EKU",
    },
    "1.3.6.1.5.5.7.3.9": {
        "name": "OCSPSigning",
        "description": "OCSP signing EKU",
    },
    "2.16.840.1.101.3.4.2.1": {
        "name": "sha256",
        "description": "SHA-256 hash algorithm",
    },
    "2.16.840.1.101.3.4.2.2": {
        "name": "sha384",
        "description": "SHA-384 hash algorithm",
    },
    "2.16.840.1.101.3.4.2.3": {
        "name": "sha512",
        "description": "SHA-512 hash algorithm",
    },
    "2.5.4.41": {
        "name": "name",
        "description": "X.500 name attribute",
    },
    "2.5.4.46": {
        "name": "dnQualifier",
        "description": "X.500 DN qualifier attribute",
    },
    "2.5.4.97": {
        "name": "organizationIdentifier",
        "description": "X.500 organization identifier attribute",
    },
    "1.2.840.113549.1.7.1": {
        "name": "data",
        "description": "PKCS #7 data content type",
    },
    "1.2.840.113549.1.7.2": {
        "name": "signedData",
        "description": "PKCS #7 signed data content type",
    },
    "1.2.840.113549.1.7.3": {
        "name": "envelopedData",
        "description": "PKCS #7 enveloped data content type",
    },
    "2.5.29.1": {
        "name": "authorityInfoAccess",
        "description": "X.509 authority information access extension",
    },
    "2.5.29.9": {
        "name": "subjectDirectoryAttributes",
        "description": "X.509 subject directory attributes extension",
    },
    "1.3.6.1.4.1.311.21.20": {
        "name": "msSGC",
        "description": "Microsoft SGC client information",
    },
    "1.3.6.1.5.5.7.1.1": {
        "name": "authorityInfoAccessOID",
        "description": "PKIX authority information access",
    },
    "2.16.840.1.113730.1.1": {
        "name": "netscapeCertType",
        "description": "Netscape certificate type extension",
    },
    "0.9.2342.19200300.100.1.25": {
        "name": "domainComponent",
        "description": "Domain component attribute for DN",
    },
    "1.2.840.113549.1.9.15": {
        "name": "sMIMECapabilities",
        "description": "S/MIME capabilities attribute",
    },
    "1.2.840.113549.2.5": {
        "name": "md5",
        "description": "MD5 hash algorithm (deprecated)",
    },
    "1.3.14.3.2.26": {
        "name": "sha1",
        "description": "SHA-1 hash algorithm",
    },
    "2.5.29.30": {
        "name": "nameConstraints",
        "description": "X.509 name constraints extension",
    },
    "2.5.29.37.0": {
        "name": "anyExtendedKeyUsage",
        "description": "Any extended key usage",
    },
    "1.2.840.10045.4.1": {
        "name": "ecdsa-with-SHA1",
        "description": "ECDSA with SHA-1 signature algorithm",
    },
    "1.2.840.113549.1.5.12": {
        "name": "pbeWithSHAAnd128BitRC4",
        "description": "PKCS #5 v1.5 PBE with SHA-1 and 128-bit RC4",
    },
}


def _encode_int(value: int) -> bytes:
    if value == 0:
        return b"\x00"
    negative = value < 0
    if negative:
        value = -value
    result = bytearray()
    while value > 0:
        result.append(value & 0xFF)
        value >>= 8
    result.reverse()
    if result[0] & 0x80:
        result.insert(0, 0x00)
    if negative:
        byte_list = list(result)
        for i in range(len(byte_list)):
            byte_list[i] = ~byte_list[i] & 0xFF
        carry = 1
        for i in range(len(byte_list) - 1, -1, -1):
            s = byte_list[i] + carry
            byte_list[i] = s & 0xFF
            carry = s >> 8
        result = bytearray(byte_list)
    return bytes(result)


def _decode_int(data: bytes) -> int:
    if not data:
        return 0
    negative = bool(data[0] & 0x80)
    if negative:
        inverted = bytearray(~b & 0xFF for b in data)
        carry = 1
        for i in range(len(inverted) - 1, -1, -1):
            s = inverted[i] + carry
            inverted[i] = s & 0xFF
            carry = s >> 8
        data = bytes(inverted)
    result = 0
    for byte in data:
        result = (result << 8) | byte
    return -result if negative else result


def _encode_oid(oid_str: str) -> bytes:
    parts = [int(p) for p in oid_str.split(".")]
    if len(parts) < 2:
        raise ValueError(f"OID must have at least 2 arcs: {oid_str}")
    encoded = bytearray()
    encoded.append(parts[0] * 40 + parts[1])
    for part in parts[2:]:
        encoded.extend(_encode_oid_arc(part))
    return bytes(encoded)


def _encode_oid_arc(value: int) -> bytes:
    if value < 128:
        return bytes([value])
    parts = []
    while value > 0:
        parts.append(value & 0x7F)
        value >>= 7
    parts.reverse()
    for i in range(len(parts) - 1):
        parts[i] |= 0x80
    return bytes(parts)


def _decode_oid(data: bytes) -> str:
    parts = [data[0] // 40, data[0] % 40]
    offset = 1
    while offset < len(data):
        value = 0
        while True:
            byte = data[offset]
            offset += 1
            value = (value << 7) | (byte & 0x7F)
            if not (byte & 0x80):
                break
        parts.append(value)
    return ".".join(str(p) for p in parts)


def _encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    needed = 0
    temp = length
    while temp > 0:
        temp >>= 8
        needed += 1
    result = bytes([0x80 | needed]) + length.to_bytes(needed, "big")
    return result


def _decode_length(data: bytes, offset: int) -> tuple[int, int]:
    first = data[offset]
    offset += 1
    if first < 0x80:
        return first, offset
    num_bytes = first & 0x7F
    if num_bytes == 0:
        raise ValueError(f"Indefinite length form not supported in DER at offset {offset - 1}")
    length = int.from_bytes(data[offset : offset + num_bytes], "big")
    offset += num_bytes
    return length, offset


def _decode_tlv(data: bytes, offset: int) -> tuple[dict[str, Any], int]:
    if offset >= len(data):
        raise ValueError("Unexpected end of DER data")

    tag_byte = data[offset]
    tag_number = tag_byte & 0x1F
    tag_class = tag_byte & 0xC0
    constructed = bool(tag_byte & 0x20)
    offset += 1

    if tag_number == 0x1F:
        tag_number = 0
        while True:
            byte = data[offset]
            offset += 1
            tag_number = (tag_number << 7) | (byte & 0x7F)
            if not (byte & 0x80):
                break

    value_length, offset = _decode_length(data, offset)

    value = data[offset : offset + value_length]
    offset += value_length

    tag_name = _TAG_NAMES.get(tag_number)

    if constructed and tag_number not in (0x03, 0x04):
        children = []
        child_offset = 0
        while child_offset < value_length:
            child, _ = _decode_tlv(value, child_offset)
            children.append(child)
            child_offset += _encoded_child_length(value, child_offset)
        return {
            "type": tag_name or f"TAG_{tag_number}",
            "class": _TAG_CLASS_BITS.get(tag_class, "CONTEXT"),
            "children": children,
        }, offset

    result: dict[str, Any] = {
        "type": tag_name or f"TAG_{tag_number}",
        "class": _TAG_CLASS_BITS.get(tag_class, "CONTEXT"),
    }

    if constructed and tag_number == 0x03:
        unused_bits = value[0]
        result["unused_bits"] = unused_bits
        result["value"] = value[1:]
        return result, offset
    if constructed and tag_number == 0x04:
        children = []
        child_offset = 0
        while child_offset < value_length:
            child, _ = _decode_tlv(value, child_offset)
            children.append(child)
            child_offset += _encoded_child_length(value, child_offset)
        result["children"] = children
        return result, offset

    if tag_number == 0x01:
        result["value"] = value[0] != 0
    elif tag_number == 0x02:
        result["value"] = _decode_int(value)
    elif tag_number == 0x03:
        unused_bits = value[0]
        result["unused_bits"] = unused_bits
        result["value"] = value[1:]
    elif tag_number == 0x04:
        result["value"] = value
    elif tag_number == 0x05:
        result["value"] = None
    elif tag_number == 0x06:
        result["value"] = _decode_oid(value)
    elif tag_number in (0x0C, 0x13, 0x14, 0x16, 0x1C, 0x1E):
        result["value"] = value.decode("utf-8" if tag_number == 0x0C else "ascii", errors="replace")
    elif tag_number in (0x17, 0x18):
        result["value"] = value.decode("ascii", errors="replace")
    else:
        result["value"] = value

    return result, offset


def _encoded_child_length(data: bytes, offset: int) -> int:
    if offset >= len(data):
        return 0
    tag_start = offset
    tag_byte = data[tag_start]
    tag_number = tag_byte & 0x1F
    offset += 1
    if tag_number == 0x1F:
        while offset < len(data) and (data[offset] & 0x80):
            offset += 1
        offset += 1
    if offset >= len(data):
        return 0
    first_len = data[offset]
    offset += 1
    if first_len < 0x80:
        total_length = first_len
    else:
        num_bytes = first_len & 0x7F
        total_length = int.from_bytes(data[offset : offset + num_bytes], "big")
        offset += num_bytes
    return (offset - tag_start) + total_length


def parse_der(der_bytes: bytes) -> dict[str, Any]:
    result, offset = _decode_tlv(der_bytes, 0)
    if offset < len(der_bytes):
        der_bytes[offset:]
        if isinstance(result, dict) and "children" in result:
            while offset < len(der_bytes):
                child, offset = _decode_tlv(der_bytes, offset)
                result["children"].append(child)
        else:
            raise ValueError(f"Trailing data at offset {offset}, len={len(der_bytes)}")
    return result


def _encode_tlv(structure: dict[str, Any]) -> bytes:
    tag_name = structure["type"]
    tag_number = _NAME_TO_TAG.get(tag_name)
    if tag_number is None:
        raise ValueError(f"Unknown type: {tag_name}")

    tag_byte = tag_number
    if "children" in structure:
        tag_byte |= 0x20

    content = _encode_value(structure)
    length_bytes = _encode_length(len(content))
    return bytes([tag_byte]) + length_bytes + content


def _encode_value(structure: dict[str, Any]) -> bytes:
    tag_name = structure["type"]

    if "children" in structure:
        parts = []
        for child in structure["children"]:
            parts.append(_encode_tlv(child))
        return b"".join(parts)

    value = structure.get("value")

    if tag_name == "NULL":
        return b""
    if tag_name == "BOOLEAN":
        return b"\xFF" if value else b"\x00"
    if tag_name == "INTEGER":
        assert isinstance(value, int)
        return _encode_int(value)
    if tag_name == "OID":
        assert isinstance(value, str)
        return _encode_oid(value)
    if tag_name == "BIT STRING":
        unused_bits = structure.get("unused_bits", 0)
        return bytes([unused_bits]) + (value or b"")
    if tag_name == "OCTET STRING":
        if isinstance(value, str):
            return value.encode("ascii")
        return value or b""
    if tag_name in ("UTF8String", "PrintableString", "IA5String", "T61String", "BMPString", "UniversalString"):
        if isinstance(value, bytes):
            return value
        return (value or "").encode("utf-8")
    if tag_name in ("UTCTime", "GeneralizedTime"):
        if isinstance(value, bytes):
            return value
        return (value or "").encode("ascii")
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("ascii")
    return b""


def encode_der(structure: dict[str, Any]) -> bytes:
    return _encode_tlv(structure)


def lookup_oid(oid_string: str) -> dict[str, Any]:
    if oid_string in _KNOWN_OIDS:
        entry = _KNOWN_OIDS[oid_string]
        return {
            "oid": oid_string,
            "name": entry["name"],
            "description": entry["description"],
        }
    return {
        "oid": oid_string,
        "name": "unknown",
        "description": "Unknown OID",
    }


def generate_oid(parent_arc: str, description: str) -> str:
    hash_input = f"{parent_arc}:{description}:{time.time()}:{uuid.uuid4().hex}"
    digest = hashlib.sha256(hash_input.encode()).digest()
    arc1 = (digest[3] << 16 | digest[7] << 8 | digest[11]) & 0x7FFFFF
    arc2 = (digest[15] << 16 | digest[19] << 8 | digest[23]) & 0x7FFFFF
    return f"{parent_arc}.{arc1}.{arc2}"
