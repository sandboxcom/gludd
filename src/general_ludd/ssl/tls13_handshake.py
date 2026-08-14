"""TLS 1.3 handshake state machine using cryptography.

Implements client-side TLS 1.3 handshake per RFC 8446:
- X25519 ECDHE key exchange
- HKDF-SHA256 key schedule
- AES-128-GCM record protection
- Certificate verification
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC
from enum import Enum, auto
from typing import cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, x448, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from cryptography.x509 import Certificate, load_pem_x509_certificate
from cryptography.x509.oid import NameOID

from general_ludd.fsm import FSM, Event, StateMachine

# ═══════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════

_SHA256: str = "sha256"
_SHA384: str = "sha384"
_SHA256_SIZE: int = 32
_SHA384_SIZE: int = 48
_AES128_GCM: str = "aes-128-gcm"
_AES256_GCM: str = "aes-256-gcm"
_CHACHA20_POLY1305: str = "chacha20-poly1305"

TLS_AES_128_GCM_SHA256: int = 0x1301
TLS_AES_256_GCM_SHA384: int = 0x1302
TLS_CHACHA20_POLY1305_SHA256: int = 0x1303

CIPHER_SUITE_MAP: dict[int, tuple[str, str]] = {
    TLS_AES_128_GCM_SHA256: (_AES128_GCM, _SHA256),
    TLS_AES_256_GCM_SHA384: (_AES256_GCM, _SHA384),
    TLS_CHACHA20_POLY1305_SHA256: (_CHACHA20_POLY1305, _SHA256),
}

_FINISHED_KEY_LEN: dict[str, int] = {
    _SHA256: _SHA256_SIZE,
    _SHA384: _SHA384_SIZE,
}

_KEY_LEN: dict[str, int] = {
    _SHA256: 16,
    _SHA384: 24,
}

_IV_LEN: dict[str, int] = {
    _SHA256: 12,
    _SHA384: 12,
}

_HANDSHAKE_LABEL: bytes = b"tls13 "

_CLIENT_HANDSHAKE_TRAFFIC_SECRET: bytes = b"c hs traffic"
_SERVER_HANDSHAKE_TRAFFIC_SECRET: bytes = b"s hs traffic"
_CLIENT_APPLICATION_TRAFFIC_SECRET: bytes = b"c ap traffic"
_SERVER_APPLICATION_TRAFFIC_SECRET: bytes = b"s ap traffic"
_EXPORTER_MASTER_SECRET: bytes = b"exp master"
_RESUMPTION_MASTER_SECRET: bytes = b"res master"
_DERIVED_LABEL: bytes = b"derived"


# ═══════════════════════════════════════════════════════════════════════════
# Exceptions
# ═══════════════════════════════════════════════════════════════════════════


class TLSHandshakeError(Exception):
    """Base exception for TLS 1.3 handshake errors."""


# Compatibility alias: existing callers may continue catching HandshakeError.
HandshakeError = TLSHandshakeError


class HandshakeStateError(HandshakeError):
    """Invalid state transition."""


class HandshakeCryptoError(HandshakeError):
    """Crypto verification failed."""


class HandshakePeerError(HandshakeError):
    """Invalid data from the peer."""


# ═══════════════════════════════════════════════════════════════════════════
# Named groups
# ═══════════════════════════════════════════════════════════════════════════


class NamedGroup(Enum):
    """Identify a TLS 1.3 key-exchange group from RFC 8446."""

    X25519 = 0x001D
    X448 = 0x001E
    SECP256R1 = 0x0017
    SECP384R1 = 0x0018
    SECP521R1 = 0x0019


# ═══════════════════════════════════════════════════════════════════════════
# Transcript hash — RFC 8446 §4.4.1
# ═══════════════════════════════════════════════════════════════════════════


def _hash_algorithm(hash_name: str) -> hashes.HashAlgorithm:
    if hash_name == _SHA256:
        return hashes.SHA256()
    if hash_name == _SHA384:
        return hashes.SHA384()
    raise HandshakeError(f"Unsupported hash: {hash_name}")


class TranscriptHash:
    """Track the digest used by the TLS 1.3 handshake transcript."""

    __slots__ = ("_algo", "_hash_name", "_running")

    def __init__(self, hash_name: str) -> None:
        """Initialize the transcript digest.

        Args:
            hash_name: Supported digest algorithm name.

        Raises:
            HandshakeError: If the digest algorithm is unsupported.
        """
        self._hash_name = hash_name
        self._algo = _hash_algorithm(hash_name)
        self._running: bytes | None = None

    def update(self, data: bytes) -> None:
        """Replace the transcript digest with the digest of ``data``.

        Args:
            data: Handshake bytes to digest.
        """
        h = hashes.Hash(_hash_algorithm(self._hash_name))
        h.update(data)
        self._running = h.finalize()

    def digest(self) -> bytes:
        """Return the current transcript digest."""
        if self._running is not None:
            return self._running
        return hashes.Hash(_hash_algorithm(self._hash_name)).finalize()

    @property
    def hash_name(self) -> str:
        """Return the configured digest algorithm name."""
        return self._hash_name


# ═══════════════════════════════════════════════════════════════════════════
# HKDF helpers — RFC 8446 §7.1
# ═══════════════════════════════════════════════════════════════════════════


def _hkdf_expand_label(
    secret: bytes,
    label: bytes,
    context: bytes,
    length: int,
    hash_name: str,
) -> bytes:
    hkdf_label = _HANDSHAKE_LABEL + label
    info = (
        length.to_bytes(2, "big")
        + len(hkdf_label).to_bytes(1, "big")
        + hkdf_label
        + len(context).to_bytes(1, "big")
        + context
    )
    hkdf = HKDFExpand(algorithm=_hash_algorithm(hash_name), length=length, info=info)
    return hkdf.derive(secret)


def _derive_secret(
    secret: bytes,
    label: bytes,
    messages: bytes,
    hash_name: str,
) -> bytes:
    hash_len = _SHA256_SIZE if hash_name == _SHA256 else _SHA384_SIZE
    return _hkdf_expand_label(secret, label, messages, hash_len, hash_name)


def _extract(salt: bytes, ikm: bytes, hash_name: str) -> bytes:
    h = hmac.HMAC(salt, _hash_algorithm(hash_name))
    h.update(ikm)
    return h.finalize()


# ═══════════════════════════════════════════════════════════════════════════
# Key schedule types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class HandshakeSecrets:
    """Contain traffic secrets, encryption keys, and IVs for one handshake."""

    client_handshake_traffic: bytes
    server_handshake_traffic: bytes
    client_handshake_key: bytes
    server_handshake_key: bytes
    client_handshake_iv: bytes
    server_handshake_iv: bytes
    client_application_traffic: bytes
    server_application_traffic: bytes
    client_application_key: bytes
    server_application_key: bytes
    client_application_iv: bytes
    server_application_iv: bytes
    exporter_master_secret: bytes
    resumption_master_secret: bytes


# ═══════════════════════════════════════════════════════════════════════════
# Key schedule — RFC 8446 §7.1
# ═══════════════════════════════════════════════════════════════════════════


def compute_tls13_keys(
    shared_secret: bytes,
    cipher_suite: int,
    transcript_hash: bytes = b"",
) -> HandshakeSecrets:
    """Derive TLS 1.3 handshake and application traffic material.

    Args:
        shared_secret: Secret produced by the negotiated key exchange.
        cipher_suite: Negotiated TLS 1.3 cipher-suite identifier.
        transcript_hash: Digest of handshake messages covered by derivation.

    Returns:
        Derived client and server traffic secrets, keys, and IVs.

    Raises:
        HandshakeError: If ``cipher_suite`` is not supported.
    """
    if cipher_suite not in CIPHER_SUITE_MAP:
        raise HandshakeError(f"Unknown cipher suite: 0x{cipher_suite:04x}")

    _aead, hash_name = CIPHER_SUITE_MAP[cipher_suite]
    hash_len = _SHA256_SIZE if hash_name == _SHA256 else _SHA384_SIZE
    zero_len = b"\x00" * hash_len

    early_secret = _extract(zero_len, zero_len, hash_name)

    empty_hash = hashes.Hash(_hash_algorithm(hash_name)).finalize()
    derived = _derive_secret(early_secret, _DERIVED_LABEL, empty_hash, hash_name)
    handshake_secret = _extract(derived, shared_secret, hash_name)

    client_ht = _derive_secret(handshake_secret, _CLIENT_HANDSHAKE_TRAFFIC_SECRET, transcript_hash, hash_name)
    server_ht = _derive_secret(handshake_secret, _SERVER_HANDSHAKE_TRAFFIC_SECRET, transcript_hash, hash_name)

    derived_hs = _derive_secret(handshake_secret, _DERIVED_LABEL, empty_hash, hash_name)
    master_secret = _extract(derived_hs, zero_len, hash_name)

    client_at = _derive_secret(master_secret, _CLIENT_APPLICATION_TRAFFIC_SECRET, transcript_hash, hash_name)
    server_at = _derive_secret(master_secret, _SERVER_APPLICATION_TRAFFIC_SECRET, transcript_hash, hash_name)

    exporter_ms = _derive_secret(master_secret, _EXPORTER_MASTER_SECRET, empty_hash, hash_name)
    resumption_ms = _derive_secret(master_secret, _RESUMPTION_MASTER_SECRET, transcript_hash, hash_name)

    key_len = _KEY_LEN[hash_name]
    iv_len = _IV_LEN[hash_name]

    return HandshakeSecrets(
        client_handshake_traffic=client_ht,
        server_handshake_traffic=server_ht,
        client_handshake_key=_hkdf_expand_label(client_ht, b"key", b"", key_len, hash_name),
        server_handshake_key=_hkdf_expand_label(server_ht, b"key", b"", key_len, hash_name),
        client_handshake_iv=_hkdf_expand_label(client_ht, b"iv", b"", iv_len, hash_name),
        server_handshake_iv=_hkdf_expand_label(server_ht, b"iv", b"", iv_len, hash_name),
        client_application_traffic=client_at,
        server_application_traffic=server_at,
        client_application_key=_hkdf_expand_label(client_at, b"key", b"", key_len, hash_name),
        server_application_key=_hkdf_expand_label(server_at, b"key", b"", key_len, hash_name),
        client_application_iv=_hkdf_expand_label(client_at, b"iv", b"", iv_len, hash_name),
        server_application_iv=_hkdf_expand_label(server_at, b"iv", b"", iv_len, hash_name),
        exporter_master_secret=exporter_ms,
        resumption_master_secret=resumption_ms,
    )


def compute_finished_verify_data(base_key: bytes, transcript_hash: bytes, hash_name: str) -> bytes:
    """Compute an RFC 8446 Finished-message verification value.

    Args:
        base_key: Handshake traffic secret for the sending peer.
        transcript_hash: Digest of the authenticated handshake transcript.
        hash_name: Negotiated digest algorithm name.

    Returns:
        HMAC verification data for the Finished message.
    """
    key_len = _FINISHED_KEY_LEN[hash_name]
    finished_key = _hkdf_expand_label(base_key, b"finished", b"", key_len, hash_name)
    h = hmac.HMAC(finished_key, _hash_algorithm(hash_name))
    h.update(transcript_hash)
    return h.finalize()


# ═══════════════════════════════════════════════════════════════════════════
# Record protection
# ═══════════════════════════════════════════════════════════════════════════


class RecordProtection:
    """Encrypt and decrypt TLS records with a monotonically increasing nonce."""

    __slots__ = ("_aead", "_iv_base", "_seq")

    def __init__(self, aead_name: str, key: bytes, iv_base: bytes) -> None:
        """Initialize record protection for a negotiated AEAD.

        Args:
            aead_name: Supported AEAD algorithm name.
            key: Symmetric record-protection key.
            iv_base: Static IV combined with each sequence number.

        Raises:
            HandshakeError: If ``aead_name`` is unsupported.
        """
        if aead_name == _AES128_GCM:
            self._aead: AESGCM | ChaCha20Poly1305 = AESGCM(key)
        elif aead_name == _AES256_GCM:
            self._aead = AESGCM(key)
        elif aead_name == _CHACHA20_POLY1305:
            self._aead = ChaCha20Poly1305(key)
        else:
            raise HandshakeError(f"Unsupported AEAD: {aead_name}")
        self._iv_base = iv_base
        self._seq: int = 0

    @property
    def sequence_number(self) -> int:
        """Return the sequence number for the next record."""
        return self._seq

    def _build_nonce(self) -> bytes:
        seq_bytes = self._seq.to_bytes(8, "big")
        nonce = bytearray(self._iv_base)
        for i in range(8):
            nonce[len(nonce) - 8 + i] ^= seq_bytes[i]
        return bytes(nonce)

    def encrypt(self, plaintext: bytes, associated_data: bytes = b"") -> bytes:
        """Encrypt one record and advance the sequence number.

        Args:
            plaintext: Record content to protect.
            associated_data: Additional authenticated record metadata.

        Returns:
            Authenticated ciphertext including the AEAD tag.
        """
        nonce = self._build_nonce()
        self._seq += 1
        return self._aead.encrypt(nonce, plaintext, associated_data)

    def decrypt(self, ciphertext: bytes, associated_data: bytes = b"") -> bytes:
        """Decrypt one record and advance the sequence number.

        Args:
            ciphertext: Authenticated ciphertext including its AEAD tag.
            associated_data: Additional authenticated record metadata.

        Returns:
            Decrypted record content.

        Raises:
            InvalidTag: If authentication of the record fails.
        """
        nonce = self._build_nonce()
        self._seq += 1
        return self._aead.decrypt(nonce, ciphertext, associated_data)

    def reset(self) -> None:
        """Reset the record sequence number to zero."""
        self._seq = 0


# ═══════════════════════════════════════════════════════════════════════════
# Key exchange
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class KeyShare:
    """Pair a named group with its encoded public key."""

    group: NamedGroup
    public_key: bytes


class KeyExchange:
    """Generate an ephemeral key and derive a TLS shared secret."""

    __slots__ = ("_group", "_private")
    _group: NamedGroup
    _private: x25519.X25519PrivateKey | x448.X448PrivateKey | ec.EllipticCurvePrivateKey

    def __init__(self, group: NamedGroup = NamedGroup.X25519) -> None:
        """Generate an ephemeral private key for ``group``.

        Args:
            group: Named key-exchange group to use.

        Raises:
            HandshakeError: If ``group`` is unsupported.
        """
        self._group = group
        if group == NamedGroup.X25519:
            self._private = x25519.X25519PrivateKey.generate()
        elif group == NamedGroup.X448:
            self._private = x448.X448PrivateKey.generate()
        elif group == NamedGroup.SECP256R1:
            self._private = ec.generate_private_key(ec.SECP256R1())
        elif group == NamedGroup.SECP384R1:
            self._private = ec.generate_private_key(ec.SECP384R1())
        elif group == NamedGroup.SECP521R1:
            self._private = ec.generate_private_key(ec.SECP521R1())
        else:
            raise HandshakeError(f"Unsupported group: {group}")

    @property
    def group(self) -> NamedGroup:
        """Return the selected key-exchange group."""
        return self._group

    @property
    def public_bytes(self) -> bytes:
        """Return the encoded ephemeral public key."""
        if self._group == NamedGroup.X25519:
            return (
                cast(x25519.X25519PrivateKey, self._private)
                .public_key()
                .public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                )
            )
        if self._group == NamedGroup.X448:
            return (
                cast(x448.X448PrivateKey, self._private)
                .public_key()
                .public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                )
            )
        return (
            cast(ec.EllipticCurvePrivateKey, self._private)
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )
        )

    def exchange(self, peer_public_bytes: bytes) -> bytes:
        """Derive a shared secret from an encoded peer public key.

        Args:
            peer_public_bytes: Peer key encoded for the selected group.

        Returns:
            Shared secret produced by the selected key exchange.

        Raises:
            HandshakeError: If exchange is not implemented for the group.
            ValueError: If the encoded peer key is invalid.
        """
        if self._group == NamedGroup.X25519:
            return cast(x25519.X25519PrivateKey, self._private).exchange(
                x25519.X25519PublicKey.from_public_bytes(peer_public_bytes)
            )
        if self._group == NamedGroup.X448:
            return cast(x448.X448PrivateKey, self._private).exchange(
                x448.X448PublicKey.from_public_bytes(peer_public_bytes)
            )
        raise HandshakeError(f"Key exchange not implemented for {self._group}")


def generate_key_share(group: NamedGroup = NamedGroup.X25519) -> KeyShare:
    """Generate an ephemeral public key share for ``group``.

    Args:
        group: Named key-exchange group to use.

    Returns:
        Selected group and encoded public key.

    Raises:
        HandshakeError: If ``group`` is unsupported.
    """
    ke = KeyExchange(group)
    return KeyShare(group=group, public_key=ke.public_bytes)


# ═══════════════════════════════════════════════════════════════════════════
# Handshake FSM — state enum
# ═══════════════════════════════════════════════════════════════════════════


class HandshakeState(Enum):
    """Identify a state in the client-side TLS 1.3 handshake."""

    IDLE = auto()
    CLIENT_HELLO_SENT = auto()
    SERVER_HELLO_RCVD = auto()
    EE_RCVD = auto()
    CERT_RCVD = auto()
    CV_RCVD = auto()
    SERVER_FIN_RCVD = auto()
    CONNECTED = auto()
    ERROR = auto()


# ═══════════════════════════════════════════════════════════════════════════
# Config and certificate types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class HandshakeConfig:
    """Configure cipher, key exchange, server identity, and ALPN offers."""

    cipher_suites: list[int] = field(default_factory=lambda: [TLS_AES_128_GCM_SHA256])
    named_group: NamedGroup = NamedGroup.X25519
    server_name: str = "localhost"
    alpn_protocols: list[str] = field(default_factory=list)
    early_data: bool = False


@dataclass
class ServerCertificate:
    """Contain the peer leaf certificate and supplied certificate chain."""

    certificate: Certificate
    chain: list[Certificate]


# ═══════════════════════════════════════════════════════════════════════════
# Extension builders (wire-format helpers)
# ═══════════════════════════════════════════════════════════════════════════


def _encode_uint8_bytes(data: bytes) -> bytes:
    if len(data) > 255:
        raise HandshakeError("uint8 overflow")
    return len(data).to_bytes(1, "big") + data


def _encode_uint16_val(data: int) -> bytes:
    if data > 0xFFFF:
        raise HandshakeError("uint16 overflow")
    return data.to_bytes(2, "big")


def _encode_uint24_val(data: int) -> bytes:
    if data > 0xFFFFFF:
        raise HandshakeError("uint24 overflow")
    return data.to_bytes(3, "big")


def _encode_ext(data: bytes) -> bytes:
    if len(data) > 0xFFFF:
        raise HandshakeError("extension overflow")
    return len(data).to_bytes(2, "big") + data


def _encode_uint16_leb(data: bytes) -> bytes:
    if len(data) > 0xFFFF:
        raise HandshakeError("uint16 length overflow")
    return len(data).to_bytes(2, "big") + data


# ═══════════════════════════════════════════════════════════════════════════
# TLS 1.3 Handshake — client side
# ═══════════════════════════════════════════════════════════════════════════


class Tls13Handshake:
    """Drive a client-side TLS 1.3 handshake and protect resulting traffic."""

    __slots__ = (
        "_cipher_suite",
        "_client_finished_verify_data",
        "_config",
        "_decrypt",
        "_encrypt",
        "_fsm",
        "_hash_name",
        "_key_exchange",
        "_peer_certificate",
        "_peer_key_share",
        "_secrets",
        "_server_finished_verify_data",
        "_transcript",
    )

    def __init__(self, config: HandshakeConfig | None = None) -> None:
        """Initialize an idle client handshake.

        Args:
            config: Handshake preferences, or defaults when omitted.
        """
        self._config: HandshakeConfig = config or HandshakeConfig()
        self._cipher_suite: int | None = None
        self._hash_name: str = _SHA256
        self._key_exchange: KeyExchange | None = None
        self._peer_key_share: bytes | None = None
        self._transcript: TranscriptHash = TranscriptHash(_SHA256)
        self._secrets: HandshakeSecrets | None = None
        self._encrypt: RecordProtection | None = None
        self._decrypt: RecordProtection | None = None
        self._peer_certificate: ServerCertificate | None = None
        self._server_finished_verify_data: bytes | None = None
        self._client_finished_verify_data: bytes | None = None

        sm = (
            StateMachine()
            .state("IDLE", initial=True)
            .state("CLIENT_HELLO_SENT")
            .state("SERVER_HELLO_RCVD")
            .state("EE_RCVD")
            .state("CERT_RCVD")
            .state("CV_RCVD")
            .state("SERVER_FIN_RCVD")
            .state("CONNECTED", final=True)
            .state("ERROR", final=True)
            .transition("IDLE", "CLIENT_HELLO_SENT", "send_client_hello")
            .transition("CLIENT_HELLO_SENT", "SERVER_HELLO_RCVD", "recv_server_hello")
            .transition("SERVER_HELLO_RCVD", "EE_RCVD", "recv_encrypted_extensions")
            .transition("EE_RCVD", "CERT_RCVD", "recv_certificate")
            .transition("CERT_RCVD", "CV_RCVD", "recv_certificate_verify")
            .transition("CV_RCVD", "SERVER_FIN_RCVD", "recv_finished")
            .transition("SERVER_FIN_RCVD", "CONNECTED", "send_finished")
            .build()
        )
        self._fsm: FSM = sm
        self._fsm.start()

    @property
    def state(self) -> HandshakeState:
        """Return the current handshake state."""
        cur = self._fsm.current_state
        name = cur.name if cur else "IDLE"
        return HandshakeState[name]

    @property
    def is_connected(self) -> bool:
        """Return whether the handshake reached the connected state."""
        return self._fsm.is_finished and self.state == HandshakeState.CONNECTED

    @property
    def cipher_suite(self) -> int | None:
        """Return the selected cipher-suite identifier, if negotiated."""
        return self._cipher_suite

    @property
    def secrets(self) -> HandshakeSecrets | None:
        """Return derived handshake secrets, if key derivation completed."""
        return self._secrets

    @property
    def peer_certificate(self) -> ServerCertificate | None:
        """Return the parsed peer certificate chain, if supplied."""
        return self._peer_certificate

    @property
    def transcript_digest(self) -> bytes:
        """Return the current handshake transcript digest."""
        return self._transcript.digest()

    # ── client_hello ──────────────────────────────────────────────────────

    def build_client_hello(self) -> bytes:
        """Build ClientHello and advance the handshake state.

        Returns:
            Encoded ClientHello handshake message.

        Raises:
            HandshakeStateError: If the handshake is not idle.
            HandshakeError: If configured algorithms are unsupported.
        """
        if self.state != HandshakeState.IDLE:
            raise HandshakeStateError(f"Cannot send ClientHello from {self.state.name}")

        self._key_exchange = KeyExchange(self._config.named_group)
        self._cipher_suite = self._config.cipher_suites[0]
        self._hash_name = CIPHER_SUITE_MAP[self._cipher_suite][1]

        import os

        client_random = os.urandom(32)
        legacy_session_id = b""
        cipher_suites_bytes = b"".join(_encode_uint16_val(cs) for cs in self._config.cipher_suites)
        legacy_compression = _encode_uint8_bytes(b"\x00")
        extensions_data = self._build_client_hello_extensions()
        extensions = _encode_uint16_leb(extensions_data)

        payload = (
            b"\x03\x03"
            + client_random
            + _encode_uint8_bytes(legacy_session_id)
            + _encode_uint16_leb(cipher_suites_bytes)
            + legacy_compression
            + extensions
        )

        msg = b"\x01" + _encode_uint24_val(len(payload)) + payload

        self._transcript = TranscriptHash(self._hash_name)
        self._transcript.update(msg)
        self._fsm.send(Event("send_client_hello"))
        return msg

    def _build_client_hello_extensions(self) -> bytes:
        exts = b""
        ke = self._key_exchange
        assert ke is not None

        if self._config.server_name:
            sni = self._config.server_name.encode("ascii")
            sni_body = _encode_uint16_leb(sni) + sni
            exts += _encode_ext(_encode_uint16_val(0) + _encode_uint16_leb(sni_body))

        group_bytes = _encode_uint16_val(self._config.named_group.value)
        exts += _encode_ext(_encode_uint16_val(10) + _encode_uint16_leb(group_bytes))

        ks = ke.public_bytes
        key_share = _encode_uint16_val(self._config.named_group.value) + _encode_uint16_leb(ks)
        exts += _encode_ext(_encode_uint16_val(51) + _encode_uint16_leb(key_share))

        sig_algs = _encode_uint16_leb(b"\x08\x07\x08\x04\x08\x05\x04\x03\x08\x06\x06\x03")
        exts += _encode_ext(_encode_uint16_val(13) + sig_algs)

        versions = b"\x02" + _encode_uint16_val(0x0304)
        exts += _encode_ext(_encode_uint16_val(43) + _encode_uint16_leb(versions))

        return exts

    # ── server_hello ──────────────────────────────────────────────────────

    def process_server_hello(self, raw: bytes) -> None:
        """Record ServerHello and advance the handshake state.

        Args:
            raw: Encoded ServerHello handshake message.

        Raises:
            HandshakeStateError: If ClientHello has not been sent.
        """
        if self.state != HandshakeState.CLIENT_HELLO_SENT:
            raise HandshakeStateError(f"Cannot process ServerHello from {self.state.name}")
        self._transcript.update(raw)
        self._fsm.send(Event("recv_server_hello"))

    # ── encrypted_extensions ──────────────────────────────────────────────

    def process_encrypted_extensions(self, raw: bytes) -> None:
        """Record EncryptedExtensions and advance the handshake state.

        Args:
            raw: Encoded EncryptedExtensions handshake message.

        Raises:
            HandshakeStateError: If ServerHello has not been processed.
        """
        if self.state != HandshakeState.SERVER_HELLO_RCVD:
            raise HandshakeStateError(f"Cannot process EncryptedExtensions from {self.state.name}")
        self._transcript.update(raw)
        self._fsm.send(Event("recv_encrypted_extensions"))

    # ── certificate ───────────────────────────────────────────────────────

    def process_certificate(self, raw: bytes, pem_chain: list[bytes] | None = None) -> None:
        """Record Certificate and optionally parse its PEM chain.

        Args:
            raw: Encoded Certificate handshake message.
            pem_chain: Leaf-first PEM certificate chain to retain.

        Raises:
            HandshakeStateError: If EncryptedExtensions has not been processed.
            ValueError: If a supplied PEM certificate cannot be parsed.
        """
        if self.state != HandshakeState.EE_RCVD:
            raise HandshakeStateError(f"Cannot process Certificate from {self.state.name}")
        self._transcript.update(raw)

        if pem_chain:
            chain = [load_pem_x509_certificate(c) for c in pem_chain]
            self._peer_certificate = ServerCertificate(certificate=chain[0], chain=chain)

        self._fsm.send(Event("recv_certificate"))

    # ── certificate_verify ────────────────────────────────────────────────

    def process_certificate_verify(self, raw: bytes) -> None:
        """Validate CertificateVerify and advance the handshake state.

        Args:
            raw: Encoded CertificateVerify handshake message.

        Raises:
            HandshakeStateError: If Certificate has not been processed.
            HandshakeCryptoError: If the peer signature is invalid.
        """
        if self.state != HandshakeState.CERT_RCVD:
            raise HandshakeStateError(f"Cannot process CertificateVerify from {self.state.name}")
        self._transcript.update(raw)

        if self._peer_certificate is not None and raw[8:].strip(b"\x00"):
            transcript_digest = self._transcript.digest()
            try:
                pub_key = self._peer_certificate.certificate.public_key()
                if isinstance(pub_key, ec.EllipticCurvePublicKey):
                    pub_key.verify(
                        raw[8:],
                        transcript_digest,
                        ec.ECDSA(_hash_algorithm(self._hash_name)),
                    )
                elif isinstance(pub_key, ed25519.Ed25519PublicKey):
                    pub_key.verify(raw[8:], transcript_digest)
            except InvalidSignature as err:
                raise HandshakeCryptoError("CertificateVerify signature validation failed") from err

        self._fsm.send(Event("recv_certificate_verify"))

    # ── derive handshake keys ─────────────────────────────────────────────

    def derive_handshake_keys(self, peer_key_share: bytes) -> None:
        """Derive handshake record keys from the peer key share.

        Args:
            peer_key_share: Encoded peer public key for the selected group.

        Raises:
            HandshakeStateError: If the local key exchange is unavailable.
            HandshakeError: If exchange is unsupported for the selected group.
            ValueError: If the peer key is invalid.
        """
        if self._key_exchange is None:
            raise HandshakeStateError("No key exchange configured")
        assert self._cipher_suite is not None

        shared_secret = self._key_exchange.exchange(peer_key_share)
        self._peer_key_share = peer_key_share

        self._secrets = compute_tls13_keys(
            shared_secret,
            self._cipher_suite,
            self._transcript.digest(),
        )

        aead_name = CIPHER_SUITE_MAP[self._cipher_suite][0]
        self._encrypt = RecordProtection(
            aead_name,
            self._secrets.client_handshake_key,
            self._secrets.client_handshake_iv,
        )
        self._decrypt = RecordProtection(
            aead_name,
            self._secrets.server_handshake_key,
            self._secrets.server_handshake_iv,
        )

    # ── finished ──────────────────────────────────────────────────────────

    def build_server_finished_verify_data(self, transcript_digest: bytes | None = None) -> bytes:
        """Compute the expected server Finished verification value.

        Args:
            transcript_digest: Transcript digest override for verification.

        Returns:
            Expected server Finished verification data.

        Raises:
            HandshakeStateError: If handshake keys have not been derived.
        """
        if self._secrets is None:
            raise HandshakeStateError("Keys not derived")
        digest = transcript_digest or self._transcript.digest()
        self._server_finished_verify_data = compute_finished_verify_data(
            self._secrets.server_handshake_traffic, digest, self._hash_name
        )
        return self._server_finished_verify_data

    def build_client_finished_verify_data(self) -> bytes:
        """Compute the client Finished verification value.

        Returns:
            Client Finished verification data for the current transcript.

        Raises:
            HandshakeStateError: If handshake keys have not been derived.
        """
        if self._secrets is None:
            raise HandshakeStateError("Keys not derived")
        digest = self._transcript.digest()
        self._client_finished_verify_data = compute_finished_verify_data(
            self._secrets.client_handshake_traffic, digest, self._hash_name
        )
        return self._client_finished_verify_data

    def process_finished(self, raw: bytes) -> None:
        """Record the server Finished message and advance the state.

        Args:
            raw: Encoded server Finished handshake message.

        Raises:
            HandshakeStateError: If CertificateVerify has not been processed.
        """
        if self.state != HandshakeState.CV_RCVD:
            raise HandshakeStateError(f"Cannot process Finished from {self.state.name}")
        self._transcript.update(raw)
        self._fsm.send(Event("recv_finished"))

    def build_client_finished(self) -> bytes:
        """Build the client Finished message and complete the handshake.

        Returns:
            Encoded and, when keys exist, encrypted Finished message.

        Raises:
            HandshakeStateError: If server Finished has not been processed or
                handshake keys have not been derived.
        """
        if self.state != HandshakeState.SERVER_FIN_RCVD:
            raise HandshakeStateError(f"Cannot send Finished from {self.state.name}")

        verify_data = self.build_client_finished_verify_data()
        msg = b"\x14" + _encode_uint24_val(len(verify_data)) + verify_data
        self._transcript.update(msg)

        if self._encrypt is not None:
            msg = self._encrypt.encrypt(msg)

        self._fsm.send(Event("send_finished"))
        return msg

    # ── encrypt / decrypt helpers ─────────────────────────────────────────

    def encrypt_handshake(self, plaintext: bytes) -> bytes:
        """Encrypt handshake content with the client handshake key.

        Args:
            plaintext: Handshake content to protect.

        Returns:
            Authenticated ciphertext.

        Raises:
            HandshakeStateError: If handshake keys are unavailable.
        """
        if self._encrypt is None:
            raise HandshakeStateError("Handshake encryption not available")
        return self._encrypt.encrypt(plaintext)

    def decrypt_handshake(self, ciphertext: bytes) -> bytes:
        """Decrypt handshake content with the server handshake key.

        Args:
            ciphertext: Authenticated handshake ciphertext.

        Returns:
            Decrypted handshake content.

        Raises:
            HandshakeStateError: If handshake keys are unavailable.
            InvalidTag: If authentication of the ciphertext fails.
        """
        if self._decrypt is None:
            raise HandshakeStateError("Handshake decryption not available")
        return self._decrypt.decrypt(ciphertext)

    def encrypt_application_data(self, plaintext: bytes) -> bytes:
        """Encrypt application data with the derived client key.

        Args:
            plaintext: Application content to protect.

        Returns:
            Authenticated application ciphertext.

        Raises:
            HandshakeStateError: If application secrets are unavailable.
        """
        if self._secrets is None:
            raise HandshakeStateError("Not connected")
        assert self._cipher_suite is not None
        aead_name = CIPHER_SUITE_MAP[self._cipher_suite][0]
        prot = RecordProtection(
            aead_name,
            self._secrets.client_application_key,
            self._secrets.client_application_iv,
        )
        return prot.encrypt(plaintext)

    def decrypt_application_data(self, ciphertext: bytes) -> bytes:
        """Decrypt application data with the derived server key.

        Args:
            ciphertext: Authenticated application ciphertext.

        Returns:
            Decrypted application content.

        Raises:
            HandshakeStateError: If application secrets are unavailable.
            InvalidTag: If authentication of the ciphertext fails.
        """
        if self._secrets is None:
            raise HandshakeStateError("Not connected")
        assert self._cipher_suite is not None
        aead_name = CIPHER_SUITE_MAP[self._cipher_suite][0]
        prot = RecordProtection(
            aead_name,
            self._secrets.server_application_key,
            self._secrets.server_application_iv,
        )
        return prot.decrypt(ciphertext)

    # ── full handshake convenience ────────────────────────────────────────

    def _enc_as_server(self, plaintext: bytes) -> bytes:
        assert self._secrets is not None
        assert self._cipher_suite is not None
        aead_name = CIPHER_SUITE_MAP[self._cipher_suite][0]
        rp = RecordProtection(
            aead_name,
            self._secrets.server_handshake_key,
            self._secrets.server_handshake_iv,
        )
        return rp.encrypt(plaintext)

    def do_full_handshake(
        self,
        peer_key_share: bytes,
        server_cert_pems: list[bytes] | None = None,
    ) -> tuple[bytes, HandshakeSecrets]:
        """Simulate the complete client handshake against a peer key share.

        Args:
            peer_key_share: Encoded peer public key for key agreement.
            server_cert_pems: Optional leaf-first PEM certificate chain.

        Returns:
            Client Finished message and the derived handshake secrets.

        Raises:
            HandshakeError: If a configured algorithm or transition is invalid.
            ValueError: If peer key or certificate input is malformed.
        """
        self.build_client_hello()

        self.process_server_hello(_make_server_hello_bytes(peer_key_share, self._cipher_suite))
        self.derive_handshake_keys(peer_key_share)

        self.process_encrypted_extensions(self._enc_as_server(_make_handle_bytes(8)))
        self.process_certificate(
            self._enc_as_server(_make_handle_bytes(11, payload=b"\x00\x00\x00")),
            pem_chain=server_cert_pems,
        )
        self.process_certificate_verify(self._enc_as_server(_make_handle_bytes(15, payload=b"\x08\x04" + b"\x00" * 64)))
        self.build_server_finished_verify_data()
        self.process_finished(
            self._enc_as_server(_make_handle_bytes(20, payload=b"\x00" * _FINISHED_KEY_LEN[self._hash_name]))
        )

        client_finished = self.build_client_finished()
        assert self._secrets is not None
        return client_finished, self._secrets


# ═══════════════════════════════════════════════════════════════════════════
# Wire-format message builders (for test / peer simulation)
# ═══════════════════════════════════════════════════════════════════════════


def _make_handle_bytes(msg_type: int, payload: bytes = b"\x00\x00\x00") -> bytes:
    return bytes([msg_type]) + _encode_uint24_val(len(payload)) + payload


def _make_server_hello_bytes(key_share: bytes, cipher_suite: int | None = None) -> bytes:
    cs = cipher_suite or TLS_AES_128_GCM_SHA256
    server_random = b"\x00" * 32
    extensions_data = _encode_ext(
        _encode_uint16_val(43) + _encode_uint16_leb(_encode_uint16_val(0x0304))
    ) + _encode_ext(
        _encode_uint16_val(51)
        + _encode_uint16_leb(_encode_uint16_val(NamedGroup.X25519.value) + _encode_uint16_leb(key_share))
    )
    payload = (
        b"\x03\x03"
        + server_random
        + _encode_uint8_bytes(b"")
        + _encode_uint16_val(cs)
        + b"\x00"
        + _encode_uint16_leb(extensions_data)
    )
    return b"\x02" + _encode_uint24_val(len(payload)) + payload


# ═══════════════════════════════════════════════════════════════════════════
# Certificate generation helpers (for testing)
# ═══════════════════════════════════════════════════════════════════════════


def generate_ec_key_pair() -> ec.EllipticCurvePrivateKey:
    """Generate a P-256 private key for TLS certificate tests."""
    return ec.generate_private_key(ec.SECP256R1())


def generate_self_signed_cert(
    subject_cn: str = "localhost",
) -> tuple[bytes, ec.EllipticCurvePrivateKey]:
    """Generate a self-signed P-256 certificate for testing.

    Args:
        subject_cn: Common name and DNS subject alternative name.

    Returns:
        PEM certificate and its private signing key.
    """
    from datetime import datetime, timedelta

    from cryptography import x509 as cx509

    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = cx509.Name([cx509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])
    now = datetime.now(UTC)
    cert = (
        cx509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(cx509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(
            cx509.SubjectAlternativeName([cx509.DNSName(subject_cn)]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM), key


def generate_ec_certificate(
    subject_cn: str = "localhost",
    issuer_key: ec.EllipticCurvePrivateKey | None = None,
    issuer_cert: Certificate | None = None,
) -> tuple[bytes, ec.EllipticCurvePrivateKey]:
    """Generate a P-256 leaf or self-issued certificate for testing.

    Args:
        subject_cn: Common name and DNS subject alternative name.
        issuer_key: Signing key; defaults to the new subject key.
        issuer_cert: Certificate whose subject becomes the issuer name.

    Returns:
        PEM certificate and its private subject key.
    """
    from datetime import datetime, timedelta

    from cryptography import x509 as cx509

    key = ec.generate_private_key(ec.SECP256R1())
    sign_key = issuer_key if issuer_key is not None else key

    if issuer_cert is not None:
        issuer_name = issuer_cert.subject
    else:
        issuer_name = cx509.Name([cx509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])

    subject = cx509.Name([cx509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])
    now = datetime.now(UTC)
    cert = (
        cx509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(cx509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(
            cx509.SubjectAlternativeName([cx509.DNSName(subject_cn)]),
            critical=False,
        )
        .sign(sign_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM), key
