"""Noise Protocol Framework — handshake patterns, symmetric state, and AEAD transport.

Implements the Noise Protocol Framework (rev 34) backed by the `cryptography` library.
Supported primitives: X25519 DH, AESGCM AEAD, SHA256 hash, HKDF key derivation.
Handshake patterns: NN, NK, KK, XX, IK, IN.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from typing import Final

from cryptography.exceptions import InvalidTag as _InvalidTag
from cryptography.hazmat.primitives import hashes as _hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey as _CryptoPrivateKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PublicKey as _CryptoPublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _CryptoAESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF as _CryptoHKDF


class NoiseError(Exception):
    """Base exception for Noise protocol operations."""


class HandshakeError(NoiseError):
    """Handshake has not completed or state is invalid."""


class DecryptError(NoiseError):
    """AEAD decryption failed (ciphertext tampered or wrong key)."""


# ── constants ──────────────────────────────────────────────────────────

_HASH_LEN: Final[int] = 32
_KEY_LEN: Final[int] = 32
_BLOCK_LEN: Final[int] = 16
_MAX_NONCE: Final[int] = 2**64 - 1
_MAX_MESSAGE_LEN: Final[int] = 65_535
_TAG_LEN: Final[int] = 16

_empty = bytes(_HASH_LEN)

_DEFAULT_PROTOCOL_NAME: Final[bytes] = b"Noise_XX_25519_AESGCM_SHA256"


def _noise_nonce(nonce: int) -> bytes:
    """Encode an AES-GCM nonce as specified by Noise revision 34."""
    if nonce >= _MAX_NONCE:
        raise NoiseError("Nonce exhausted (2^64-1)")
    return bytes(4) + nonce.to_bytes(8, "big")


# ── key material ───────────────────────────────────────────────────────


@dataclass(slots=True)
class KeyPair:
    """An X25519 static or ephemeral key pair."""

    private: bytes
    public: bytes

    @staticmethod
    def generate() -> KeyPair:
        """Generate an X25519 key pair."""
        priv = _CryptoPrivateKey.generate()
        return KeyPair(
            private=priv.private_bytes_raw(),
            public=priv.public_key().public_bytes_raw(),
        )


# ── DH ─────────────────────────────────────────────────────────────────


def dh(private: bytes, public: bytes) -> bytes:
    """X25519 Diffie-Hellman: compute shared secret."""
    priv_key = _CryptoPrivateKey.from_private_bytes(private)
    pub_key = _CryptoPublicKey.from_public_bytes(public)
    return priv_key.exchange(pub_key)


# ── CipherState ────────────────────────────────────────────────────────


@dataclass
class CipherState:
    """AEAD encrypt/decrypt with automatic nonce advancement."""

    key: bytes | None = None
    nonce: int = 0

    def initialize_key(self, key: bytes) -> None:
        """Set a 256-bit cipher key and reset the nonce."""
        if len(key) != _KEY_LEN:
            raise NoiseError(f"CipherState key must be {_KEY_LEN} bytes")
        self.key = key
        self.nonce = 0

    def has_key(self) -> bool:
        """Return whether this cipher state has a key."""
        return self.key is not None

    def encrypt_with_ad(self, ad: bytes, plaintext: bytes) -> bytes:
        """Encrypt plaintext with associated data and advance the nonce."""
        if self.key is None:
            return plaintext
        aead = _CryptoAESGCM(self.key)
        msg = aead.encrypt(_noise_nonce(self.nonce), plaintext, ad)
        self.nonce += 1
        return msg

    def decrypt_with_ad(self, ad: bytes, ciphertext: bytes) -> bytes:
        """Authenticate and decrypt ciphertext, then advance the nonce."""
        if self.key is None:
            return ciphertext
        aead = _CryptoAESGCM(self.key)
        try:
            msg = aead.decrypt(_noise_nonce(self.nonce), ciphertext, ad)
        except _InvalidTag as exc:
            raise DecryptError("AEAD decryption failed") from exc
        self.nonce += 1
        return msg

    def split(self) -> tuple[CipherState, CipherState]:
        """Derive two independent cipher states from this key."""
        if self.key is None:
            raise HandshakeError("Cannot split uninitialized CipherState")
        hkdf = _CryptoHKDF(
            algorithm=_hashes.SHA256(),
            length=_KEY_LEN * 2,
            salt=b"",
            info=b"",
        )
        output = hkdf.derive(self.key)
        c1 = CipherState()
        c1.initialize_key(output[:_KEY_LEN])
        c2 = CipherState()
        c2.initialize_key(output[_KEY_LEN:])
        return c1, c2


# ── SymmetricState ─────────────────────────────────────────────────────


def _hkdf(chaining_key: bytes, ikm: bytes, num_outputs: int) -> list[bytes]:
    """HKDF with the given chaining_key as salt."""
    hkdf = _CryptoHKDF(
        algorithm=_hashes.SHA256(),
        length=_HASH_LEN * num_outputs,
        salt=chaining_key,
        info=b"",
    )
    output = hkdf.derive(ikm)
    return [output[i * _HASH_LEN : (i + 1) * _HASH_LEN] for i in range(num_outputs)]


@dataclass
class SymmetricState:
    """Manages hashing, key derivation, and AEAD encryption during handshake."""

    cipher_state: CipherState = field(default_factory=CipherState)
    chaining_key: bytes = field(default_factory=lambda: bytes(_HASH_LEN))
    h: bytes = field(default_factory=lambda: bytes(_HASH_LEN))

    def initialize_symmetric(self, protocol_name: bytes) -> None:
        """Initialize the chaining key and handshake hash."""
        if len(protocol_name) <= _HASH_LEN:
            self.h = protocol_name.ljust(_HASH_LEN, b"\x00")
        else:
            self.h = hashlib.sha256(protocol_name).digest()
        self.chaining_key = self.h
        self.cipher_state = CipherState()

    def mix_key(self, ikm: bytes) -> None:
        """Mix input key material into the chaining key."""
        ck, key = _hkdf(self.chaining_key, ikm, 2)
        self.chaining_key = ck
        self.cipher_state.initialize_key(key)

    def mix_hash(self, data: bytes) -> None:
        """Mix data into the handshake hash."""
        self.h = hashlib.sha256(self.h + data).digest()

    def mix_key_and_hash(self, ikm: bytes) -> None:
        """Mix input key material into both symmetric values."""
        ck, temp_h, temp_k = _hkdf(self.chaining_key, ikm, 3)
        self.chaining_key = ck
        self.mix_hash(temp_h)
        self.cipher_state.initialize_key(temp_k)

    def get_handshake_hash(self) -> bytes:
        """Return the current handshake hash."""
        return self.h

    def encrypt_and_hash(self, plaintext: bytes) -> bytes:
        """Encrypt plaintext and mix the ciphertext into the hash."""
        ciphertext = self.cipher_state.encrypt_with_ad(self.h, plaintext)
        self.mix_hash(ciphertext)
        return ciphertext

    def decrypt_and_hash(self, ciphertext: bytes) -> bytes:
        """Decrypt ciphertext and mix it into the hash."""
        plaintext = self.cipher_state.decrypt_with_ad(self.h, ciphertext)
        self.mix_hash(ciphertext)
        return plaintext

    def split(self) -> tuple[CipherState, CipherState]:
        """Derive the two post-handshake transport cipher states."""
        hkdf = _CryptoHKDF(
            algorithm=_hashes.SHA256(),
            length=_KEY_LEN * 2,
            salt=self.chaining_key,
            info=b"",
        )
        output = hkdf.derive(b"")
        c1 = CipherState()
        c1.initialize_key(output[:_KEY_LEN])
        c2 = CipherState()
        c2.initialize_key(output[_KEY_LEN:])
        return c1, c2


# ── Handshake patterns ─────────────────────────────────────────────────


class TokenType(enum.IntEnum):
    """Token types used by Noise handshake patterns."""

    E = 1
    S = 2
    DH_EE = 3
    DH_ES = 4
    DH_SE = 5
    DH_SS = 6
    PSK = 7


@dataclass(slots=True, frozen=True)
class _Token:
    type: TokenType

    @staticmethod
    def e() -> _Token:
        return _Token(TokenType.E)

    @staticmethod
    def s() -> _Token:
        return _Token(TokenType.S)

    @staticmethod
    def dh_ee() -> _Token:
        return _Token(TokenType.DH_EE)

    @staticmethod
    def dh_es() -> _Token:
        return _Token(TokenType.DH_ES)

    @staticmethod
    def dh_se() -> _Token:
        return _Token(TokenType.DH_SE)

    @staticmethod
    def dh_ss() -> _Token:
        return _Token(TokenType.DH_SS)

    @staticmethod
    def psk() -> _Token:
        return _Token(TokenType.PSK)


def _parse_pattern(pattern_str: str) -> list[_Token]:
    """Parse a Noise handshake pattern string into a list of tokens."""
    tokens: list[_Token] = []
    for word in pattern_str.replace("\n", " ").split():
        word = word.strip()
        if not word:
            continue
        if word == "e":
            tokens.append(_Token.e())
        elif word == "s":
            tokens.append(_Token.s())
        elif word == "ee":
            tokens.append(_Token.dh_ee())
        elif word == "es":
            tokens.append(_Token.dh_es())
        elif word == "se":
            tokens.append(_Token.dh_se())
        elif word == "ss":
            tokens.append(_Token.dh_ss())
        elif word == "psk":
            tokens.append(_Token.psk())
        else:
            raise NoiseError(f"Unknown pattern token: {word}")
    return tokens


# ── Pre-defined patterns ───────────────────────────────────────────────

_PATTERN_NN: Final[str] = """
  -> e
  <- e, ee
"""

_PATTERN_KN: Final[str] = """
  -> s
  ...
  -> e
  <- e, ee, se
"""

_PATTERN_NK: Final[str] = """
  <- s
  ...
  -> e, es
  <- e, ee
"""

_PATTERN_KK: Final[str] = """
  -> s
  <- s
  ...
  -> e, es, ss
  <- e, ee, se
"""

_PATTERN_XX: Final[str] = """
  -> e
  <- e, ee, s, es
  -> s, se
"""

_PATTERN_IK: Final[str] = """
  <- s
  ...
  -> e, es, s, ss
  <- e, ee, se
"""

_PATTERN_IN: Final[str] = """
  -> e, s
  <- e, ee, se
"""


# ── direction type ─────────────────────────────────────────────────────


class Direction(enum.IntEnum):
    """A participant's role in a Noise handshake."""

    INITIATOR = 0
    RESPONDER = 1

    def opposite(self) -> Direction:
        """Return the other handshake role."""
        return Direction.RESPONDER if self == Direction.INITIATOR else Direction.INITIATOR


# ── HandshakeState ─────────────────────────────────────────────────────


@dataclass
class HandshakeState:
    """Manages a Noise handshake for one party."""

    symmetric_state: SymmetricState = field(default_factory=SymmetricState)
    role: Direction = Direction.INITIATOR
    s: KeyPair | None = None
    e: KeyPair | None = None
    rs: bytes | None = None
    re: bytes | None = None
    psk: bytes | None = None
    _message_patterns: list[list[_Token]] = field(default_factory=list)
    _message_directions: list[Direction] = field(default_factory=list)
    _step: int = 0

    def initialize(
        self,
        handshake_pattern: str,
        initiator: bool,
        *,
        prologue: bytes = b"",
        s: KeyPair | None = None,
        e: KeyPair | None = None,
        rs: bytes | None = None,
        psk: bytes | None = None,
        protocol_name: bytes = _DEFAULT_PROTOCOL_NAME,
    ) -> None:
        """Initialize a handshake from a pattern and participant keys."""
        self.role = Direction.INITIATOR if initiator else Direction.RESPONDER
        self.s = s
        self.e = e
        self.rs = rs
        self.psk = psk
        self.symmetric_state = SymmetricState()
        self.symmetric_state.initialize_symmetric(protocol_name)
        self.symmetric_state.mix_hash(prologue)

        lines = handshake_pattern.strip().split("\n")

        # Search for the "..." separator
        sep_idx = -1
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if line_stripped == "...":
                sep_idx = i
                break

        pre_message_lines = lines[:sep_idx] if sep_idx >= 0 else []
        message_lines = lines[sep_idx + 1 :] if sep_idx >= 0 else lines

        # Process pre-message lines: mix_hash static keys
        for line in pre_message_lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("->"):
                direction = Direction.INITIATOR
                content = line[2:].strip()
            elif line.startswith("<-"):
                direction = Direction.RESPONDER
                content = line[2:].strip()
            else:
                continue
            token_strs = [t.strip() for t in content.split(",") if t.strip()]
            for tok_str in token_strs:
                if tok_str == "s":
                    public_key = (
                        self.s.public if direction == self.role and self.s is not None else self.rs
                    )
                    if public_key is None:
                        identity = "local" if direction == self.role else "remote"
                        raise NoiseError(f"Missing {identity} static pre-message key")
                    self.symmetric_state.mix_hash(public_key)

        # Parse message patterns (after pre-messages)
        self._message_patterns = []
        self._message_directions = []
        for line in message_lines:
            line = line.strip()
            if not line or line == "...":
                continue
            if line.startswith("->"):
                direction = Direction.INITIATOR
            elif line.startswith("<-"):
                direction = Direction.RESPONDER
            else:
                continue
            content = line.lstrip("->").lstrip("<-").strip()
            token_strs = [t.strip() for t in content.split(",") if t.strip()]
            message_tokens: list[_Token] = []
            for tok_str in token_strs:
                token = _parse_pattern(tok_str)[0]
                message_tokens.append(token)
            self._message_patterns.append(message_tokens)
            self._message_directions.append(direction)
        self._step = 0

    def write_message(self, payload: bytes = b"") -> bytes:
        """Write the next handshake message for this participant."""
        if len(payload) > _MAX_MESSAGE_LEN:
            raise NoiseError("Noise messages cannot exceed 65,535 bytes")
        if self._step >= len(self._message_patterns):
            raise NoiseError("No more messages to write in this handshake pattern")
        tokens = self._message_patterns[self._step]
        pattern_direction = self._pattern_direction(self._step)
        if pattern_direction != self.role:
            raise NoiseError("Cannot write at this step; it is the other party's turn")

        buffer = bytearray()
        for token in tokens:
            if token.type == TokenType.E:
                self.e = KeyPair.generate()
                buffer.extend(self.e.public)
                self.symmetric_state.mix_hash(self.e.public)
            elif token.type == TokenType.S:
                if self.s is None:
                    raise NoiseError("Static key not set")
                ct = self.symmetric_state.encrypt_and_hash(self.s.public)
                buffer.extend(ct)
            elif token.type == TokenType.DH_EE:
                if self.e is None or self.re is None:
                    raise NoiseError("Missing ephemeral keys for DH(ee)")
                self.symmetric_state.mix_key(dh(self.e.private, self.re))
            elif token.type == TokenType.DH_ES:
                if self.role == Direction.INITIATOR:
                    if self.e is None or self.rs is None:
                        raise NoiseError("Missing keys for DH(es)")
                    self.symmetric_state.mix_key(dh(self.e.private, self.rs))
                else:
                    if self.s is None or self.re is None:
                        raise NoiseError("Missing keys for DH(es)")
                    self.symmetric_state.mix_key(dh(self.s.private, self.re))
            elif token.type == TokenType.DH_SE:
                if self.role == Direction.INITIATOR:
                    if self.s is None or self.re is None:
                        raise NoiseError("Missing keys for DH(se)")
                    self.symmetric_state.mix_key(dh(self.s.private, self.re))
                else:
                    if self.e is None or self.rs is None:
                        raise NoiseError("Missing keys for DH(se)")
                    self.symmetric_state.mix_key(dh(self.e.private, self.rs))
            elif token.type == TokenType.DH_SS:
                if self.s is None or self.rs is None:
                    raise NoiseError("Missing keys for DH(ss)")
                self.symmetric_state.mix_key(dh(self.s.private, self.rs))
            elif token.type == TokenType.PSK:
                if self.psk is None:
                    raise NoiseError("PSK not set")
                self.symmetric_state.mix_key_and_hash(self.psk)

        payload_size = len(payload) + (
            _TAG_LEN if self.symmetric_state.cipher_state.has_key() else 0
        )
        if len(buffer) + payload_size > _MAX_MESSAGE_LEN:
            raise NoiseError("Noise messages cannot exceed 65,535 bytes")
        ct = self.symmetric_state.encrypt_and_hash(payload)
        buffer.extend(ct)
        self._step += 1
        return bytes(buffer)

    def read_message(self, message: bytes) -> bytes:
        """Read and authenticate the next peer handshake message."""
        if len(message) > _MAX_MESSAGE_LEN:
            raise NoiseError("Noise messages cannot exceed 65,535 bytes")
        if self._step >= len(self._message_patterns):
            raise NoiseError("No more messages to read in this handshake pattern")
        tokens = self._message_patterns[self._step]
        pattern_direction = self._pattern_direction(self._step)
        if pattern_direction == self.role:
            raise NoiseError("Cannot read at this step; it is your turn to write")

        offset = 0
        for token in tokens:
            if token.type == TokenType.E:
                if offset + _KEY_LEN > len(message):
                    raise NoiseError("Message too short for ephemeral key")
                self.re = bytes(message[offset : offset + _KEY_LEN])
                offset += _KEY_LEN
                self.symmetric_state.mix_hash(self.re)
            elif token.type == TokenType.S:
                if self.role == Direction.INITIATOR:
                    key_len = _KEY_LEN + _TAG_LEN if self.symmetric_state.cipher_state.has_key() else _KEY_LEN
                else:
                    key_len = _KEY_LEN + _TAG_LEN if self.symmetric_state.cipher_state.has_key() else _KEY_LEN
                if offset + key_len > len(message):
                    raise NoiseError("Message too short for static key")
                enc_static = bytes(message[offset : offset + key_len])
                offset += key_len
                self.rs = self.symmetric_state.decrypt_and_hash(enc_static)
            elif token.type == TokenType.DH_EE:
                if self.e is None or self.re is None:
                    raise NoiseError("Missing ephemeral keys for DH(ee)")
                self.symmetric_state.mix_key(dh(self.e.private, self.re))
            elif token.type == TokenType.DH_ES:
                if self.role == Direction.INITIATOR:
                    if self.e is None or self.rs is None:
                        raise NoiseError("Missing keys for DH(es)")
                    self.symmetric_state.mix_key(dh(self.e.private, self.rs))
                else:
                    if self.s is None or self.re is None:
                        raise NoiseError("Missing keys for DH(es)")
                    self.symmetric_state.mix_key(dh(self.s.private, self.re))
            elif token.type == TokenType.DH_SE:
                if self.role == Direction.INITIATOR:
                    if self.s is None or self.re is None:
                        raise NoiseError("Missing keys for DH(se)")
                    self.symmetric_state.mix_key(dh(self.s.private, self.re))
                else:
                    if self.e is None or self.rs is None:
                        raise NoiseError("Missing keys for DH(se)")
                    self.symmetric_state.mix_key(dh(self.e.private, self.rs))
            elif token.type == TokenType.DH_SS:
                if self.s is None or self.rs is None:
                    raise NoiseError("Missing keys for DH(ss)")
                self.symmetric_state.mix_key(dh(self.s.private, self.rs))
            elif token.type == TokenType.PSK:
                if self.psk is None:
                    raise NoiseError("PSK not set")
                self.symmetric_state.mix_key_and_hash(self.psk)

        payload = self.symmetric_state.decrypt_and_hash(message[offset:])
        self._step += 1
        return payload

    def _pattern_direction(self, step: int) -> Direction:
        """Determine which party writes at a given handshake step."""
        if step < len(self._message_directions):
            return self._message_directions[step]
        return Direction.INITIATOR if step % 2 == 0 else Direction.RESPONDER

    def _handshake_hash(self) -> bytes:
        return self.symmetric_state.get_handshake_hash()

    def completed(self) -> bool:
        """Return whether all handshake messages were processed."""
        return self._step >= len(self._message_patterns) and len(self._message_patterns) > 0

    def split(self) -> tuple[CipherState, CipherState]:
        """Return the post-handshake transport cipher states."""
        return self.symmetric_state.split()


# ── Pattern name → full pattern mapping ────────────────────────────────


_PATTERN_MAP: Final[dict[str, str]] = {
    "NN": _PATTERN_NN,
    "KN": _PATTERN_KN,
    "NK": _PATTERN_NK,
    "KK": _PATTERN_KK,
    "XX": _PATTERN_XX,
    "IK": _PATTERN_IK,
    "IN": _PATTERN_IN,
}


# ── High-level noise factory ──────────────────────────────────────────


def create_noise_session(
    pattern: str,
    initiator: bool,
    *,
    local_static: KeyPair | None = None,
    remote_static: bytes | None = None,
    prologue: bytes = b"",
    psk: bytes | None = None,
) -> HandshakeState:
    """Create a Noise handshake session for the given pattern and role."""
    hs = HandshakeState()
    full_pattern = _PATTERN_MAP.get(pattern.strip(), pattern)
    normalized_pattern = pattern.strip()
    protocol_name = (
        f"Noise_{normalized_pattern}_25519_AESGCM_SHA256".encode()
        if normalized_pattern in _PATTERN_MAP
        else _DEFAULT_PROTOCOL_NAME
    )
    hs.initialize(
        handshake_pattern=full_pattern,
        initiator=initiator,
        prologue=prologue,
        s=local_static,
        rs=remote_static,
        psk=psk,
        protocol_name=protocol_name,
    )
    return hs
