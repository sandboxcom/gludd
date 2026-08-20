"""WebAuthn/FIDO2 credential creation and assertion.

Implements server-side WebAuthn relying party operations using the
``cryptography`` library for all cryptographic primitives (no webauthn
or fido2 third-party packages). Supports:

* Registration: parse attestation, verify signature, store credential.
* Authentication: verify assertion signature, enforce sign-count replay
  protection, check user presence/verification.
* COSE key parsing: ES256 (P-256), EdDSA (Ed25519), RS256 (exposed).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

# ── COSE Algorithm constants ──────────────────────────────────────

_COSE_ES256 = -7
_COSE_EdDSA = -8
_COSE_RS256 = -257

_COSE_ALG_NAMES: dict[int, str] = {
    _COSE_ES256: "ES256",
    _COSE_EdDSA: "EdDSA",
    _COSE_RS256: "RS256",
}

_ES256_KEY_LEN = 32
_ED25519_KEY_LEN = 32
_P256_POINT_LEN = 65


# ── Exceptions ─────────────────────────────────────────────────────


class WebAuthnError(ValueError):
    """Base exception for all WebAuthn verification failures."""


# ── Data classes ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ClientData:
    """Represent ``ClientData`` values."""
    challenge: str
    origin: str
    type: str
    raw: bytes = field(repr=False)


@dataclass(frozen=True)
class ParsedAuthenticatorData:
    """Represent ``ParsedAuthenticatorData`` values."""
    rp_id_hash: bytes
    flags: int
    sign_count: int
    user_present: bool
    user_verified: bool
    has_attested_credential: bool
    has_extensions: bool
    attested_credential_data: bytes | None = None
    extensions: bytes | None = None


@dataclass(frozen=True)
class ParsedCredential:
    """Represent ``ParsedCredential`` values."""
    credential_id: bytes
    public_key_bytes: bytes | None
    cose_key: bytes
    aaguid: bytes = field(default_factory=lambda: b"\x00" * 16)


@dataclass
class WebAuthnCredential:
    """Stored credential for a user."""

    credential_id: bytes
    public_key: bytes
    sign_count: int = 0


# ── base64url helpers ──────────────────────────────────────────────


def bytes_to_base64url(data: bytes) -> str:
    """Execute ``bytes_to_base64url``."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def base64url_to_bytes(data: str) -> bytes:
    """Execute ``base64url_to_bytes``."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


# ── client data JSON ───────────────────────────────────────────────


def parse_client_data_json(raw: bytes) -> ClientData:
    """Execute ``parse_client_data_json``."""
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebAuthnError(f"invalid client data JSON: {exc}") from exc
    challenge = obj.get("challenge")
    origin = obj.get("origin")
    ctype = obj.get("type")
    if not all((challenge, origin, ctype)):
        raise WebAuthnError("client data JSON missing required field")
    return ClientData(challenge=str(challenge), origin=str(origin), type=str(ctype), raw=raw)


# ── authenticator data parsing ─────────────────────────────────────


_FLAG_UP = 0x01
_FLAG_UV = 0x04
_FLAG_AT = 0x40
_FLAG_ED = 0x80

_AUTH_DATA_MIN_LEN = 37


def parse_authenticator_data(auth_data: bytes) -> ParsedAuthenticatorData:
    """Execute ``parse_authenticator_data``."""
    if len(auth_data) < _AUTH_DATA_MIN_LEN:
        raise WebAuthnError(f"authenticator data too short: {len(auth_data)} bytes")
    rp_id_hash = auth_data[0:32]
    flags = auth_data[32]
    sign_count = int.from_bytes(auth_data[33:37], "big")

    user_present = bool(flags & _FLAG_UP)
    user_verified = bool(flags & _FLAG_UV)
    has_attested = bool(flags & _FLAG_AT)
    has_ext = bool(flags & _FLAG_ED)

    offset = 37
    attested_data: bytes | None = None
    extensions_data: bytes | None = None

    if has_attested:
        if len(auth_data) < offset + 4:
            raise WebAuthnError("authenticator data truncated in attested credential data header")
        cred_id_len = int.from_bytes(auth_data[offset : offset + 2], "big")
        offset += 2
        cred_id_end = offset + cred_id_len
        if len(auth_data) < cred_id_end:
            raise WebAuthnError("authenticator data truncated in attested credential data body")
        attested_data = auth_data[37:]
        offset = len(auth_data)

    if has_ext and len(auth_data) > offset:
        extensions_data = auth_data[offset:]

    return ParsedAuthenticatorData(
        rp_id_hash=rp_id_hash,
        flags=flags,
        sign_count=sign_count,
        user_present=user_present,
        user_verified=user_verified,
        has_attested_credential=has_attested,
        has_extensions=has_ext,
        attested_credential_data=attested_data,
        extensions=extensions_data,
    )


def decode_attested_credential_data(data: bytes, offset: int = 0) -> ParsedCredential:
    """Decode attested credential data."""
    if len(data) < offset + 18:
        raise WebAuthnError("attested credential data too short for aaguid + cred-id length")
    aaguid = data[offset : offset + 16]
    cred_id_len = int.from_bytes(data[offset + 16 : offset + 18], "big")
    cred_id_end = offset + 18 + cred_id_len
    if len(data) < cred_id_end:
        raise WebAuthnError("attested credential data too short for credential id")
    credential_id = data[offset + 18 : cred_id_end]
    cose_key = data[cred_id_end:]

    parser = COSEKeyParser(cose_key)
    pub_bytes = parser.public_key_bytes
    return ParsedCredential(
        credential_id=credential_id,
        public_key_bytes=pub_bytes,
        cose_key=cose_key,
        aaguid=aaguid,
    )


# ── COSE key parsing ───────────────────────────────────────────────


class COSEKeyParser:
    """Parse CBOR-encoded COSE_Key structures and extract public keys."""

    def __init__(self, cose_bytes: bytes) -> None:
        """Initialize a ``COSEKeyParser`` instance."""
        self._raw = cose_bytes
        self._alg: int | None = None
        self._kty: int | None = None
        self._crv: int | None = None
        self._x: bytes | None = None
        self._y: bytes | None = None
        self._n: bytes | None = None
        self._e: bytes | None = None
        self._parse()

    def _parse(self) -> None:
        if not self._raw:
            raise WebAuthnError("COSE key: empty input")
        try:
            self._map = _cbor_decode_map(self._raw)
        except (ValueError, IndexError) as exc:
            raise WebAuthnError(f"COSE key: invalid CBOR: {exc}") from exc
        self._alg = _cbor_int(self._map, 3)
        self._kty = _cbor_int(self._map, 1)
        self._crv = _cbor_int(self._map, -1, default=None)
        self._x = _cbor_bytes(self._map, -2, default=None)
        self._y = _cbor_bytes(self._map, -3, default=None)
        self._n = _cbor_bytes(self._map, -1, default=None)
        self._e = _cbor_bytes(self._map, -2, default=None)
        if self._alg is None:
            raise WebAuthnError("COSE key: missing algorithm (label 3)")

    @property
    def algorithm(self) -> int:
        """Execute ``algorithm``."""
        if self._alg is None:
            raise WebAuthnError("COSE key: missing algorithm (label 3)")
        return self._alg

    @property
    def key_type(self) -> int:
        """Execute ``key_type``."""
        if self._kty is None:
            raise WebAuthnError("COSE key: missing key type (label 1)")
        return self._kty

    @property
    def curve(self) -> int | None:
        """Execute ``curve``."""
        return self._crv

    @property
    def public_key_bytes(self) -> bytes | None:
        """Execute ``public_key_bytes``."""
        if self._alg == _COSE_ES256 and self._x is not None and self._y is not None:
            return b"\x04" + self._x + self._y
        if self._alg == _COSE_EdDSA and self._x is not None:
            return self._x
        if self._alg == _COSE_RS256 and self._n is not None and self._e is not None:
            return None
        return None

    def load_public_key(self) -> ec.EllipticCurvePublicKey | ed25519.Ed25519PublicKey | rsa.RSAPublicKey:
        """Execute ``load_public_key``."""
        if self._alg == _COSE_ES256:
            if self._x is None or self._y is None:
                raise WebAuthnError("COSE ES256: missing x or y coordinate")
            pub_bytes = b"\x04" + self._x + self._y
            return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub_bytes)
        if self._alg == _COSE_EdDSA:
            if self._x is None:
                raise WebAuthnError("COSE EdDSA: missing x coordinate")
            return ed25519.Ed25519PublicKey.from_public_bytes(self._x)
        if self._alg == _COSE_RS256:
            if self._n is None or self._e is None:
                raise WebAuthnError("COSE RS256: missing n or e")
            e_int = int.from_bytes(self._e, "big")
            return rsa.RSAPublicNumbers(e_int, int.from_bytes(self._n, "big")).public_key()
        raise WebAuthnError(f"unsupported COSE algorithm: {self._alg}")


# ── minimal CBOR decode for COSE keys ──────────────────────────────


def _cbor_decode_map(buf: bytes) -> dict[int | str, Any]:
    if not buf:
        raise ValueError("empty")
    initial = buf[0]
    if initial >> 5 != 5:
        raise WebAuthnError(f"COSE key: expected CBOR map, got major type {initial >> 5}")
    count = initial & 0x1F
    offset = 1
    if count == 31:
        raise WebAuthnError("COSE key: indefinite-length map not supported")
    if count >= 24:
        extra = count - 23
        if offset + extra > len(buf):
            raise WebAuthnError("COSE key: truncated CBOR map header")
        count = int.from_bytes(buf[offset : offset + extra], "big")
        offset += extra
    result: dict[int | str, Any] = {}
    for _ in range(count):
        key, offset = _cbor_decode_item(buf, offset)
        val, offset = _cbor_decode_item(buf, offset)
        if isinstance(key, (int, str)):
            result[key] = val
    return result


def _cbor_decode_item(buf: bytes, offset: int) -> tuple[Any, int]:
    if offset >= len(buf):
        raise WebAuthnError("COSE key: truncated CBOR")
    initial = buf[offset]
    major = initial >> 5
    info = initial & 0x1F
    offset += 1

    def _arg() -> int:
        nonlocal offset
        if info < 24:
            return info
        extra = info - 23
        if offset + extra > len(buf):
            raise WebAuthnError("COSE key: truncated CBOR argument")
        val = int.from_bytes(buf[offset : offset + extra], "big")
        offset += extra
        return val

    if major == 0:  # unsigned int (labels)
        return _arg(), offset
    if major == 1:  # negative int
        return -1 - _arg(), offset
    if major == 2:  # byte string
        length = _arg()
        if offset + length > len(buf):
            raise WebAuthnError("COSE key: truncated CBOR byte string")
        data = buf[offset : offset + length]
        return data, offset + length
    if major == 3:  # text string
        length = _arg()
        if offset + length > len(buf):
            raise WebAuthnError("COSE key: truncated CBOR text string")
        return buf[offset : offset + length].decode("utf-8"), offset + length
    if major in (4, 5, 6, 7):
        count = _arg()
        for _x in range(count if major != 6 else 1):
            _, offset = _cbor_decode_item(buf, offset)
        return None, offset
    raise WebAuthnError(f"COSE key: unsupported CBOR major type {major}")


def _cbor_int(mapping: dict[int | str, Any], label: int, default: Any = None) -> Any:
    return mapping.get(label, default)


def _cbor_bytes(mapping: dict[int | str, Any], label: int, default: Any = None) -> Any:
    v = mapping.get(label)
    if isinstance(v, bytes) or v is None:
        return v if v is not None else default
    return default


# ── signature verification ─────────────────────────────────────────


def _verify_es256(public_key_bytes: bytes, message: bytes, signature: bytes) -> None:
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key_bytes)
    key.verify(signature, message, ec.ECDSA(hashes.SHA256()))


def _verify_ed25519(public_key_bytes: bytes, message: bytes, signature: bytes) -> None:
    key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
    key.verify(signature, message)


def _verify_rs256(public_key_bytes: bytes, message: bytes, signature: bytes) -> None:
    raise WebAuthnError("RS256 verification not implemented; use ES256 or EdDSA")


_VERIFY_MAP = {
    _COSE_ES256: _verify_es256,
    _COSE_EdDSA: _verify_ed25519,
    _COSE_RS256: _verify_rs256,
}


def _verify_cose(cose_bytes: bytes, message: bytes, signature: bytes) -> None:
    parser = COSEKeyParser(cose_bytes)
    verifier = _VERIFY_MAP.get(parser.algorithm)
    if verifier is None:
        raise WebAuthnError(f"unsupported algorithm: {_COSE_ALG_NAMES.get(parser.algorithm, parser.algorithm)}")
    pub_bytes = parser.public_key_bytes
    if pub_bytes is None:
        raise WebAuthnError("cannot extract public key bytes for signature verification")
    verifier(pub_bytes, message, signature)


def verify_attestation_signature(auth_data: bytes, client_data_hash: bytes, signature: bytes, cose_key: bytes) -> None:
    """Execute ``verify_attestation_signature``."""
    try:
        _verify_cose(cose_key, auth_data + client_data_hash, signature)
    except InvalidSignature as exc:
        raise WebAuthnError("attestation signature invalid") from exc


def verify_assertion_signature(auth_data: bytes, client_data_hash: bytes, signature: bytes, cose_key: bytes) -> None:
    """Execute ``verify_assertion_signature``."""
    try:
        _verify_cose(cose_key, auth_data + client_data_hash, signature)
    except InvalidSignature as exc:
        raise WebAuthnError("assertion signature invalid") from exc


# ── challenge generation ───────────────────────────────────────────


def generate_challenge(length: int = 32) -> bytes:
    """Generate challenge."""
    return os.urandom(length)


# ── credential creation options ────────────────────────────────────


def build_credential_creation_options(
    rp_name: str,
    rp_id: str,
    user_id: bytes,
    user_name: str,
    user_display_name: str | None = None,
    timeout: int = 60000,
) -> dict[str, Any]:
    """Build credential creation options."""
    challenge = generate_challenge()
    return {
        "rp": {"name": rp_name, "id": rp_id},
        "user": {
            "id": bytes_to_base64url(user_id),
            "name": user_name,
            "displayName": user_display_name or user_name,
        },
        "challenge": bytes_to_base64url(challenge),
        "pubKeyCredParams": [
            {"type": "public-key", "alg": _COSE_ES256},
            {"type": "public-key", "alg": _COSE_EdDSA},
        ],
        "timeout": timeout,
        "attestation": "none",
        "authenticatorSelection": {
            "userVerification": "preferred",
        },
    }


def build_credential_request_options(
    rp_id: str,
    allow_credentials: list[bytes] | None = None,
    timeout: int = 60000,
    user_verification: str = "preferred",
) -> dict[str, Any]:
    """Build credential request options."""
    challenge = generate_challenge()
    opts: dict[str, Any] = {
        "challenge": bytes_to_base64url(challenge),
        "rpId": rp_id,
        "timeout": timeout,
        "userVerification": user_verification,
    }
    if allow_credentials:
        opts["allowCredentials"] = [{"type": "public-key", "id": bytes_to_base64url(cid)} for cid in allow_credentials]
    return opts


# ── registration (attestation) verification ───────────────────────


def verify_registration_response(
    credential_id: str,
    client_data_json: str,
    attestation_object: str,
    expected_challenge: str,
    expected_origin: str,
    expected_rp_id: str,
    require_user_verification: bool = False,
) -> WebAuthnCredential:
    """Execute ``verify_registration_response``."""
    client_data_bytes = base64url_to_bytes(client_data_json)
    client_data = parse_client_data_json(client_data_bytes)

    if client_data.origin != expected_origin:
        raise WebAuthnError(f"origin mismatch: {client_data.origin!r} != {expected_origin!r}")

    if client_data.challenge != expected_challenge:
        raise WebAuthnError("challenge mismatch")

    if client_data.type != "webauthn.create":
        raise WebAuthnError(f"unexpected type: {client_data.type!r}")

    att_raw = base64url_to_bytes(attestation_object)
    auth_data, att_stmt = _parse_attestation_object(att_raw)
    parsed = parse_authenticator_data(auth_data)

    expected_hash = hashlib.sha256(expected_rp_id.encode()).digest()
    if parsed.rp_id_hash != expected_hash:
        raise WebAuthnError("RP ID hash mismatch")

    if not parsed.user_present:
        raise WebAuthnError("user presence flag not set")

    if require_user_verification and not parsed.user_verified:
        raise WebAuthnError("user verification required but flag not set")

    if parsed.has_attested_credential and parsed.attested_credential_data is not None:
        cred_info = decode_attested_credential_data(parsed.attested_credential_data)
    else:
        raise WebAuthnError("no attested credential data")

    client_data_hash = hashlib.sha256(client_data_bytes).digest()

    signature = att_stmt.get("sig")
    if isinstance(signature, bytes):
        verify_attestation_signature(auth_data, client_data_hash, signature, cred_info.cose_key)

    pub_key = cred_info.cose_key

    return WebAuthnCredential(
        credential_id=cred_info.credential_id,
        public_key=pub_key,
        sign_count=parsed.sign_count,
    )


def _parse_attestation_object(raw: bytes) -> tuple[bytes, dict[str, Any]]:
    try:
        outer = _cbor_decode_map(raw)
    except WebAuthnError:
        raise
    except ValueError as exc:
        raise WebAuthnError(f"invalid attestation object CBOR: {exc}") from exc
    auth_data = outer.get("authData")
    if not isinstance(auth_data, bytes):
        raise WebAuthnError("attestation object missing authData")
    outer.get("fmt", "none")
    att_stmt = outer.get("attStmt")
    if not isinstance(att_stmt, dict):
        att_stmt = {}

    return auth_data, att_stmt


# ── authentication (assertion) verification ───────────────────────


def verify_authentication_response(
    credential: WebAuthnCredential,
    authenticator_data: str,
    client_data_json: str,
    signature: str,
    expected_challenge: str,
    expected_origin: str,
    expected_rp_id: str,
    require_user_verification: bool = False,
) -> WebAuthnCredential:
    """Execute ``verify_authentication_response``."""
    client_data_bytes = base64url_to_bytes(client_data_json)
    client_data = parse_client_data_json(client_data_bytes)

    if client_data.origin != expected_origin:
        raise WebAuthnError(f"origin mismatch: {client_data.origin!r} != {expected_origin!r}")

    if client_data.challenge != expected_challenge:
        raise WebAuthnError("challenge mismatch")

    if client_data.type != "webauthn.get":
        raise WebAuthnError(f"unexpected type: {client_data.type!r}")

    auth_data_bytes = base64url_to_bytes(authenticator_data)
    parsed = parse_authenticator_data(auth_data_bytes)

    expected_hash = hashlib.sha256(expected_rp_id.encode()).digest()
    if parsed.rp_id_hash != expected_hash:
        raise WebAuthnError("RP ID hash mismatch")

    if not parsed.user_present:
        raise WebAuthnError("user presence flag not set")

    if require_user_verification and not parsed.user_verified:
        raise WebAuthnError("user verification required but flag not set")

    if parsed.sign_count != 0 and parsed.sign_count <= credential.sign_count:
        raise WebAuthnError(f"sign_count regression: {parsed.sign_count} <= {credential.sign_count}")

    client_data_hash = hashlib.sha256(client_data_bytes).digest()
    sig_bytes = base64url_to_bytes(signature)

    verify_assertion_signature(auth_data_bytes, client_data_hash, sig_bytes, credential.public_key)

    credential.sign_count = parsed.sign_count
    return credential
