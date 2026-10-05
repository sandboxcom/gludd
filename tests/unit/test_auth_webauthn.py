"""WebAuthn/FIDO2 credential creation and assertion tests.

Covers: credential registration (create), authentication (get),
COSE key parsing (ES256, EdDSA, RS256), authenticator data parsing,
signature verification, and edge cases (truncated data, mismatched
key types, tampered client data, replay protection).
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

import general_ludd.auth.webauthn as webauthn
from general_ludd.auth.webauthn import (
    COSEKeyParser,
    ParsedCredential,
    WebAuthnCredential,
    WebAuthnError,
    base64url_to_bytes,
    build_credential_creation_options,
    build_credential_request_options,
    bytes_to_base64url,
    decode_attested_credential_data,
    parse_authenticator_data,
    parse_client_data_json,
    verify_authentication_response,
    verify_registration_response,
)

_ORIGIN = "https://auth.example.com"
_RP_ID = "example.com"


def _b64url(s: bytes) -> str:
    return base64.urlsafe_b64encode(s).rstrip(b"=").decode()


def _client_data(challenge: bytes, origin: str = _ORIGIN) -> bytes:
    return json.dumps(
        {
            "type": "webauthn.create",
            "challenge": _b64url(challenge),
            "origin": origin,
            "crossOrigin": False,
        }
    ).encode()


def _client_data_get(challenge: bytes, origin: str = _ORIGIN) -> bytes:
    return json.dumps(
        {
            "type": "webauthn.get",
            "challenge": _b64url(challenge),
            "origin": origin,
            "crossOrigin": False,
        }
    ).encode()


# ── CBOR encoding helpers (minimal) ────────────────────────────────


def _cbor_uint(v: int) -> bytes:
    if v < 24:
        return bytes([v])
    if v < 256:
        return bytes([24, v])
    if v < 65536:
        return bytes([25]) + v.to_bytes(2, "big")
    return bytes([26]) + v.to_bytes(4, "big")


def _cbor_nint(v: int) -> bytes:
    n = -1 - v
    if n < 24:
        return bytes([0x20 | n])
    if n < 256:
        return bytes([0x38, n])
    if n < 65536:
        return bytes([0x39]) + n.to_bytes(2, "big")
    return bytes([0x3A]) + n.to_bytes(4, "big")


def _cbor_bytes(data: bytes) -> bytes:
    length = len(data)
    if length < 24:
        return bytes([0x40 | length]) + data
    if length < 256:
        return bytes([0x58, length]) + data
    if length < 65536:
        return bytes([0x59]) + length.to_bytes(2, "big") + data
    return bytes([0x5A]) + length.to_bytes(4, "big") + data


def _cbor_text(s: str) -> bytes:
    b = s.encode("utf-8")
    length = len(b)
    if length < 24:
        return bytes([0x60 | length]) + b
    if length < 256:
        return bytes([0x78, length]) + b
    if length < 65536:
        return bytes([0x79]) + length.to_bytes(2, "big") + b
    return bytes([0x7A]) + length.to_bytes(4, "big") + b


def _cbor_map(pairs: list[tuple[Any, Any]]) -> bytes:
    n = len(pairs)
    if n < 24:
        header = bytes([0xA0 | n])
    elif n < 256:
        header = bytes([0xB8, n])
    elif n < 65536:
        header = bytes([0xB9]) + n.to_bytes(2, "big")
    else:
        header = bytes([0xBA]) + n.to_bytes(4, "big")
    result = header
    for k, v in pairs:
        result += _cbor_encode(k) + _cbor_encode(v)
    return result


def _cbor_encode(v: Any) -> bytes:
    if isinstance(v, int):
        if v >= 0:
            return _cbor_uint(v)
        return _cbor_nint(v)
    if isinstance(v, bytes):
        return _cbor_bytes(v)
    if isinstance(v, str):
        return _cbor_text(v)
    if isinstance(v, dict):
        return _cbor_map(list(v.items()))
    raise TypeError(f"cannot encode {type(v)}")


# ── COSE key builders ──────────────────────────────────────────────


def _build_cose_es256(xy: bytes) -> bytes:
    assert len(xy) == 65
    x, y = xy[1:33], xy[33:65]
    return _cbor_encode({1: 2, 3: -7, -1: 1, -2: x, -3: y})


def _build_cose_ed25519(pub: bytes) -> bytes:
    return _cbor_encode({1: 1, 3: -8, -1: 6, -2: pub})


# ── packed attestation builder ─────────────────────────────────────


def _make_aaguid() -> bytes:
    return b"\x00" * 16


def _make_credential_id() -> bytes:
    return b"\x01\x02\x03\x04" * 4


def _fmt_packed_attestation(auth_data: bytes, sig: bytes) -> str:
    outer = _cbor_map(
        [
            ("authData", auth_data),
            ("fmt", "packed"),
            ("attStmt", {"alg": -7, "sig": sig}),
        ]
    )
    return _b64url(outer)


# ── base64url helpers ──────────────────────────────────────────────


def test_bytes_to_base64url():
    assert bytes_to_base64url(b"\x00\xff") == "AP8"
    assert bytes_to_base64url(b"") == ""
    assert bytes_to_base64url(b"test") == "dGVzdA"


def test_base64url_to_bytes():
    assert base64url_to_bytes("AP8") == b"\x00\xff"
    assert base64url_to_bytes("dGVzdA") == b"test"
    assert base64url_to_bytes("") == b""


def test_base64url_roundtrip():
    for b in [b"", b"\x00", b"\xff" * 32, b"hello world", b"a" * 100]:
        assert base64url_to_bytes(bytes_to_base64url(b)) == b


# ── client data JSON parsing ───────────────────────────────────────


def test_parse_client_data_json_valid():
    raw = json.dumps({"challenge": "abc", "origin": "https://x.com", "type": "t"}).encode()
    cd = parse_client_data_json(raw)
    assert cd.challenge == "abc"
    assert cd.origin == "https://x.com"
    assert cd.type == "t"


def test_parse_client_data_json_invalid_utf8():
    with pytest.raises(WebAuthnError, match="client data"):
        parse_client_data_json(b"\xff\xfe\x00\x00")


def test_parse_client_data_json_missing_fields():
    with pytest.raises(WebAuthnError, match="missing"):
        parse_client_data_json(b"{}")


# ── authenticator data parsing ─────────────────────────────────────


def _auth_data(rp_id_hash: bytes, flags: int, sign_count: int) -> bytes:
    return rp_id_hash + flags.to_bytes(1, "big") + sign_count.to_bytes(4, "big")


def test_parse_authenticator_data_minimal():
    rp_id_hash = hashlib.sha256(_RP_ID.encode()).digest()
    ad = _auth_data(rp_id_hash, 0x01, 0)
    parsed = parse_authenticator_data(ad)
    assert parsed.user_present
    assert parsed.sign_count == 0
    assert parsed.user_verified is False
    assert parsed.attested_credential_data is None


def test_parse_authenticator_data_truncated():
    with pytest.raises(WebAuthnError, match="too short"):
        parse_authenticator_data(b"\x00" * 10)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"\x00" * 37, "header"),
        (b"\x00" * 37 + b"\x00\x04ab", "body"),
    ],
)
def test_parse_authenticator_data_rejects_truncated_attested_data(
    payload: bytes,
    message: str,
) -> None:
    auth_data = payload[:32] + b"\x41" + payload[33:]
    with pytest.raises(WebAuthnError, match=message):
        parse_authenticator_data(auth_data)


def test_parse_authenticator_data_user_verified():
    rp_id_hash = hashlib.sha256(_RP_ID.encode()).digest()
    ad = _auth_data(rp_id_hash, 0x05, 7)
    parsed = parse_authenticator_data(ad)
    assert parsed.user_present
    assert parsed.user_verified


def test_parse_authenticator_data_with_extensions():
    rp_id_hash = hashlib.sha256(_RP_ID.encode()).digest()
    header = _auth_data(rp_id_hash, 0x81, 0)
    extensions = b"\x01\x02\x03\x04"
    parsed = parse_authenticator_data(header + extensions)
    assert parsed.has_extensions
    assert parsed.extensions == extensions


# ── decode attested credential data ────────────────────────────────


def test_decode_attested_credential_data_es256():
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key()
    pub_bytes = pub.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    cose_key = _build_cose_es256(pub_bytes)
    aaguid = _make_aaguid()
    cred_id = _make_credential_id()
    cred_id_len = len(cred_id).to_bytes(2, "big")
    data = aaguid + cred_id_len + cred_id + cose_key
    result = decode_attested_credential_data(data, 0)
    assert result.credential_id == cred_id
    assert result.aaguid == aaguid
    assert result.cose_key == cose_key
    assert result.public_key_bytes is not None


def test_decode_attested_credential_data_ed25519():
    key = ed25519.Ed25519PrivateKey.generate()
    key.public_key()
    pub_bytes = key.public_key().public_bytes_raw()
    cose_key = _build_cose_ed25519(pub_bytes)
    cred_id = b"\xaa" * 8
    data = _make_aaguid() + len(cred_id).to_bytes(2, "big") + cred_id + cose_key
    result = decode_attested_credential_data(data, 0)
    assert result.credential_id == cred_id
    assert result.cose_key == cose_key


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "aaguid"),
        (b"\x00" * 16 + b"\x00\x04ab", "credential id"),
    ],
)
def test_decode_attested_credential_data_rejects_truncated_fields(
    payload: bytes,
    message: str,
) -> None:
    with pytest.raises(WebAuthnError, match=message):
        decode_attested_credential_data(payload)


# ── COSE key parsing ───────────────────────────────────────────────


def test_cose_parse_es256():
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key()
    pub_bytes = pub.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    cose = _build_cose_es256(pub_bytes)
    parser = COSEKeyParser(cose)
    assert parser.algorithm == -7
    assert parser.key_type == 2
    assert parser.curve == 1
    loaded = parser.load_public_key()
    assert isinstance(loaded, ec.EllipticCurvePublicKey)


def test_cose_parse_ed25519():
    key = ed25519.Ed25519PrivateKey.generate()
    pub_bytes = key.public_key().public_bytes_raw()
    cose = _build_cose_ed25519(pub_bytes)
    parser = COSEKeyParser(cose)
    assert parser.algorithm == -8
    assert parser.key_type == 1
    assert parser.curve == 6
    loaded = parser.load_public_key()
    assert isinstance(loaded, ed25519.Ed25519PublicKey)


def test_cose_parse_empty():
    with pytest.raises(WebAuthnError, match="empty"):
        COSEKeyParser(b"")


def test_cose_parse_invalid_cbor():
    with pytest.raises(WebAuthnError, match="CBOR"):
        COSEKeyParser(b"\xff\xff\xff")


def test_cose_parse_missing_alg():
    with pytest.raises(WebAuthnError, match="algorithm"):
        parsed = _cbor_encode({1: 2})
        COSEKeyParser(parsed)


def test_cose_parse_missing_key_type_fails_closed() -> None:
    parser = COSEKeyParser(_cbor_encode({3: -7}))

    with pytest.raises(WebAuthnError, match="key type"):
        _ = parser.key_type


@pytest.mark.parametrize(
    ("mapping", "message"),
    [
        ({1: 2, 3: -7}, "missing x or y"),
        ({1: 1, 3: -8}, "missing x"),
        ({1: 3, 3: -257}, "missing n or e"),
        ({1: 2, 3: -999}, "unsupported"),
    ],
)
def test_cose_key_loader_rejects_incomplete_or_unsupported_keys(
    mapping: dict[int, int],
    message: str,
) -> None:
    parser = COSEKeyParser(_cbor_encode(mapping))

    with pytest.raises(WebAuthnError, match=message):
        parser.load_public_key()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "empty"),
        (b"\xbf", "indefinite-length"),
        (b"\xb8", "truncated CBOR map header"),
        (b"\xa1", "truncated CBOR"),
        (b"\xa1\x18", "truncated CBOR argument"),
        (b"\xa1\x42a", "truncated CBOR byte string"),
        (b"\xa1\x62a", "truncated CBOR text string"),
    ],
)
def test_cbor_map_decoder_rejects_truncated_structures(
    payload: bytes,
    message: str,
) -> None:
    with pytest.raises((ValueError, WebAuthnError), match=message):
        webauthn._cbor_decode_map(payload)


def test_cbor_decoder_consumes_nested_values_without_treating_them_as_labels() -> None:
    assert webauthn._cbor_decode_map(b"\xa1\x81\x01\x01") == {}


# ── credential creation options ────────────────────────────────────


def test_build_credential_creation_options():
    opts = build_credential_creation_options(
        rp_name="Example",
        rp_id=_RP_ID,
        user_id=b"user-1",
        user_name="alice",
        user_display_name="Alice",
    )
    assert opts["rp"]["id"] == _RP_ID
    assert opts["rp"]["name"] == "Example"
    assert opts["user"]["id"] == _b64url(b"user-1")
    assert "challenge" in opts
    assert len(base64url_to_bytes(opts["challenge"])) == 32
    assert {"alg": -7, "type": "public-key"} in opts["pubKeyCredParams"]


# ── credential request options ─────────────────────────────────────


def test_build_credential_request_options():
    opts = build_credential_request_options(rp_id=_RP_ID)
    assert opts["rpId"] == _RP_ID
    assert "challenge" in opts
    assert len(base64url_to_bytes(opts["challenge"])) == 32


def test_build_credential_request_options_with_allow():
    opts = build_credential_request_options(
        rp_id=_RP_ID,
        allow_credentials=[b"cred-1", b"cred-2"],
    )
    assert len(opts["allowCredentials"]) == 2
    assert opts["allowCredentials"][0]["id"] == _b64url(b"cred-1")


# ── WebAuthnCredential records ─────────────────────────────────────


def test_webauthn_credential_creation():
    cred = WebAuthnCredential(
        credential_id=b"abc",
        public_key=b"pub",
        sign_count=0,
    )
    assert cred.credential_id == b"abc"
    assert cred.sign_count == 0


# ── registration verification (ES256) ──────────────────────────────


def test_verify_registration_es256():
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key()
    pub_bytes = pub.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    cose = _build_cose_es256(pub_bytes)
    challenge = b"\x00" * 32
    rp_id_hash = hashlib.sha256(_RP_ID.encode()).digest()
    client_data = _client_data(challenge)
    client_data_hash = hashlib.sha256(client_data).digest()
    aaguid = _make_aaguid()
    cred_id = _make_credential_id()
    cred_id_len = len(cred_id).to_bytes(2, "big")
    attested = aaguid + cred_id_len + cred_id + cose
    auth_data = _auth_data(rp_id_hash, 0x41, 0) + attested
    sig_data = auth_data + client_data_hash
    sig = key.sign(sig_data, ec.ECDSA(hashes.SHA256()))

    cred = verify_registration_response(
        credential_id=_b64url(cred_id),
        client_data_json=_b64url(client_data),
        attestation_object=_fmt_packed_attestation(auth_data, sig),
        expected_challenge=_b64url(challenge),
        expected_origin=_ORIGIN,
        expected_rp_id=_RP_ID,
    )
    assert cred.credential_id == cred_id
    assert cred.sign_count == 0


def test_verify_registration_wrong_rp_id():
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key()
    pub_bytes = pub.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    cose = _build_cose_es256(pub_bytes)
    challenge = b"\x00" * 32
    rp_id_hash = hashlib.sha256(_RP_ID.encode()).digest()
    client_data = _client_data(challenge)
    client_data_hash = hashlib.sha256(client_data).digest()
    aaguid = _make_aaguid()
    cred_id = _make_credential_id()
    attested = aaguid + len(cred_id).to_bytes(2, "big") + cred_id + cose
    auth_data = _auth_data(rp_id_hash, 0x41, 0) + attested
    sig_data = auth_data + client_data_hash
    sig = key.sign(sig_data, ec.ECDSA(hashes.SHA256()))

    with pytest.raises(WebAuthnError, match="RP ID"):
        verify_registration_response(
            credential_id=_b64url(cred_id),
            client_data_json=_b64url(client_data),
            attestation_object=_fmt_packed_attestation(auth_data, sig),
            expected_challenge=_b64url(challenge),
            expected_origin=_ORIGIN,
            expected_rp_id="wrong.example.com",
        )


def test_verify_registration_wrong_challenge():
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key()
    pub_bytes = pub.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    cose = _build_cose_es256(pub_bytes)
    challenge = b"\x00" * 32
    rp_id_hash = hashlib.sha256(_RP_ID.encode()).digest()
    client_data = _client_data(challenge)
    client_data_hash = hashlib.sha256(client_data).digest()
    attested = _make_aaguid() + len(b"\x01" * 32).to_bytes(2, "big") + b"\x01" * 32 + cose
    auth_data = _auth_data(rp_id_hash, 0x41, 0) + attested
    sig_data = auth_data + client_data_hash
    sig = key.sign(sig_data, ec.ECDSA(hashes.SHA256()))

    with pytest.raises(WebAuthnError, match="challenge"):
        verify_registration_response(
            credential_id=_b64url(b"\x01" * 32),
            client_data_json=_b64url(client_data),
            attestation_object=_fmt_packed_attestation(auth_data, sig),
            expected_challenge=_b64url(b"\xff" * 32),
            expected_origin=_ORIGIN,
            expected_rp_id=_RP_ID,
        )


# ── assertion verification (ES256) ─────────────────────────────────


def test_verify_authentication_es256():
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key()
    pub_bytes = pub.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    cose = _build_cose_es256(pub_bytes)
    challenge = b"\x01" * 32
    rp_id_hash = hashlib.sha256(_RP_ID.encode()).digest()
    client_data = _client_data_get(challenge)
    client_data_hash = hashlib.sha256(client_data).digest()
    auth_data = _auth_data(rp_id_hash, 0x05, 3)
    sig_data = auth_data + client_data_hash
    sig = key.sign(sig_data, ec.ECDSA(hashes.SHA256()))

    cred = WebAuthnCredential(
        credential_id=b"cred-1",
        public_key=cose,
        sign_count=1,
    )
    updated = verify_authentication_response(
        credential=cred,
        authenticator_data=_b64url(auth_data),
        client_data_json=_b64url(client_data),
        signature=_b64url(sig),
        expected_challenge=_b64url(challenge),
        expected_origin=_ORIGIN,
        expected_rp_id=_RP_ID,
    )
    assert updated.sign_count == 3


def test_verify_authentication_sign_count_regression():
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key()
    pub_bytes = pub.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    cose = _build_cose_es256(pub_bytes)
    challenge = b"\x02" * 32
    rp_id_hash = hashlib.sha256(_RP_ID.encode()).digest()
    client_data = _client_data_get(challenge)
    client_data_hash = hashlib.sha256(client_data).digest()
    auth_data = _auth_data(rp_id_hash, 0x05, 1)
    sig_data = auth_data + client_data_hash
    sig = key.sign(sig_data, ec.ECDSA(hashes.SHA256()))

    cred = WebAuthnCredential(
        credential_id=b"cred-1",
        public_key=cose,
        sign_count=5,
    )
    with pytest.raises(WebAuthnError, match="sign_count"):
        verify_authentication_response(
            credential=cred,
            authenticator_data=_b64url(auth_data),
            client_data_json=_b64url(client_data),
            signature=_b64url(sig),
            expected_challenge=_b64url(challenge),
            expected_origin=_ORIGIN,
            expected_rp_id=_RP_ID,
        )


def test_verify_authentication_wrong_rp_id():
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key()
    pub_bytes = pub.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    cose = _build_cose_es256(pub_bytes)
    challenge = b"\x03" * 32
    rp_id_hash = hashlib.sha256(b"other.com").digest()
    client_data = _client_data_get(challenge)
    client_data_hash = hashlib.sha256(client_data).digest()
    auth_data = _auth_data(rp_id_hash, 0x05, 1)
    sig_data = auth_data + client_data_hash
    sig = key.sign(sig_data, ec.ECDSA(hashes.SHA256()))

    cred = WebAuthnCredential(
        credential_id=b"cred-1",
        public_key=cose,
        sign_count=0,
    )
    with pytest.raises(WebAuthnError, match="RP ID"):
        verify_authentication_response(
            credential=cred,
            authenticator_data=_b64url(auth_data),
            client_data_json=_b64url(client_data),
            signature=_b64url(sig),
            expected_challenge=_b64url(challenge),
            expected_origin=_ORIGIN,
            expected_rp_id=_RP_ID,
        )


def test_verify_authentication_bad_signature():
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key()
    pub_bytes = pub.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    cose = _build_cose_es256(pub_bytes)
    challenge = b"\x04" * 32
    rp_id_hash = hashlib.sha256(_RP_ID.encode()).digest()
    client_data = _client_data_get(challenge)
    hashlib.sha256(client_data).digest()
    auth_data = _auth_data(rp_id_hash, 0x05, 1)
    sig = b"\x00" * 64

    cred = WebAuthnCredential(
        credential_id=b"cred-1",
        public_key=cose,
        sign_count=0,
    )
    with pytest.raises(WebAuthnError, match="signature"):
        verify_authentication_response(
            credential=cred,
            authenticator_data=_b64url(auth_data),
            client_data_json=_b64url(client_data),
            signature=_b64url(sig),
            expected_challenge=_b64url(challenge),
            expected_origin=_ORIGIN,
            expected_rp_id=_RP_ID,
        )


def test_verify_authentication_missing_user_present():
    challenge = b"\x05" * 32
    rp_id_hash = hashlib.sha256(_RP_ID.encode()).digest()
    auth_data = _auth_data(rp_id_hash, 0x00, 1)

    cred = WebAuthnCredential(
        credential_id=b"cred-1",
        public_key=b"dummy",
        sign_count=0,
    )
    with pytest.raises(WebAuthnError, match="user presence"):
        verify_authentication_response(
            credential=cred,
            authenticator_data=_b64url(auth_data),
            client_data_json=_b64url(_client_data_get(challenge)),
            signature=_b64url(b"\x00" * 64),
            expected_challenge=_b64url(challenge),
            expected_origin=_ORIGIN,
            expected_rp_id=_RP_ID,
        )


# ── EdDSA (Ed25519) attestation ────────────────────────────────────


def test_verify_registration_ed25519():
    key = ed25519.Ed25519PrivateKey.generate()
    pub_bytes = key.public_key().public_bytes_raw()
    cose = _build_cose_ed25519(pub_bytes)
    challenge = b"\x00" * 32
    rp_id_hash = hashlib.sha256(_RP_ID.encode()).digest()
    client_data = _client_data(challenge)
    client_data_hash = hashlib.sha256(client_data).digest()
    aaguid = _make_aaguid()
    cred_id = _make_credential_id()
    attested = aaguid + len(cred_id).to_bytes(2, "big") + cred_id + cose
    auth_data = _auth_data(rp_id_hash, 0x41, 0) + attested
    sig_data = auth_data + client_data_hash
    sig = key.sign(sig_data)

    cred = verify_registration_response(
        credential_id=_b64url(cred_id),
        client_data_json=_b64url(client_data),
        attestation_object=_fmt_packed_attestation(auth_data, sig),
        expected_challenge=_b64url(challenge),
        expected_origin=_ORIGIN,
        expected_rp_id=_RP_ID,
    )
    assert cred.credential_id == cred_id


def test_verify_authentication_ed25519():
    key = ed25519.Ed25519PrivateKey.generate()
    pub_bytes = key.public_key().public_bytes_raw()
    cose = _build_cose_ed25519(pub_bytes)
    challenge = b"\x06" * 32
    rp_id_hash = hashlib.sha256(_RP_ID.encode()).digest()
    client_data = _client_data_get(challenge)
    client_data_hash = hashlib.sha256(client_data).digest()
    auth_data = _auth_data(rp_id_hash, 0x05, 7)
    sig_data = auth_data + client_data_hash
    sig = key.sign(sig_data)

    cred = WebAuthnCredential(
        credential_id=b"cred-ed",
        public_key=cose,
        sign_count=0,
    )
    updated = verify_authentication_response(
        credential=cred,
        authenticator_data=_b64url(auth_data),
        client_data_json=_b64url(client_data),
        signature=_b64url(sig),
        expected_challenge=_b64url(challenge),
        expected_origin=_ORIGIN,
        expected_rp_id=_RP_ID,
    )
    assert updated.sign_count == 7


# ── ParsedCredential / ParsedAuthenticatorData ─────────────────────


def test_parsed_credential_equality():
    a = ParsedCredential(credential_id=b"x", public_key_bytes=b"yp", cose_key=b"c")
    b = ParsedCredential(credential_id=b"x", public_key_bytes=b"yp", cose_key=b"c")
    assert a == b
    assert a != ParsedCredential(credential_id=b"y", public_key_bytes=b"yp", cose_key=b"c")


def test_parsed_authenticator_data_repr():
    rp_id_hash = hashlib.sha256(_RP_ID.encode()).digest()
    ad = _auth_data(rp_id_hash, 0x01, 0)
    parsed = parse_authenticator_data(ad)
    repr_text = repr(parsed)
    assert "user_present" in repr_text
    assert "sign_count=0" in repr_text
