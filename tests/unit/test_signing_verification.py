"""Unit tests for self-update Ed25519 signature verification (H.17).

Covers:
  (a) Signed content with matching key → accepted
  (b) Unsigned content (empty signature) → rejected
  (c) Tampered content (signature over different payload) → rejected
  (d) Wrong key (signature from a different keypair) → rejected
"""

from __future__ import annotations

from unittest.mock import mock_open, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from general_ludd.self_update.signing import load_public_key, verify_signature


def _generate_keypair() -> tuple[bytes, bytes]:
    """Return (private_bytes, public_bytes) for a fresh Ed25519 key."""
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes_raw()
    public_bytes = private_key.public_key().public_bytes_raw()
    return private_bytes, public_bytes


def _sign(private_bytes: bytes, content: str) -> bytes:
    """Produce a 64-byte Ed25519 detached signature over *content*."""
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    return private_key.sign(content.encode("utf-8"))


def _hex(b: bytes) -> str:
    return b.hex()


def test_signed_content_accepted():
    """Signed content with the correct key -> True."""
    priv, pub = _generate_keypair()
    content = "key: value\n"
    sig = _sign(priv, content)
    assert verify_signature(content, _hex(sig), _hex(pub)) is True


def test_unsigned_content_rejected():
    """Empty signature -> False (fail-closed)."""
    _priv, pub = _generate_keypair()
    content = "key: value\n"
    assert verify_signature(content, "", _hex(pub)) is False


def test_tampered_content_rejected():
    """Signature over original content, but payload differs -> False."""
    priv, pub = _generate_keypair()
    original = "key: value\n"
    sig = _sign(priv, original)
    tampered = "key: evil\n"
    assert verify_signature(tampered, _hex(sig), _hex(pub)) is False


def test_wrong_key_rejected():
    """Signature produced by key A, verified with key B -> False."""
    priv_a, _pub_a = _generate_keypair()
    _priv_b, pub_b = _generate_keypair()
    content = "key: value\n"
    sig = _sign(priv_a, content)
    assert verify_signature(content, _hex(sig), _hex(pub_b)) is False


def test_missing_content_fail_closed():
    """Empty content string -> False."""
    priv, pub = _generate_keypair()
    content = "payload"
    sig = _sign(priv, content)
    assert verify_signature("", _hex(sig), _hex(pub)) is False


def test_missing_public_key_fail_closed():
    """Empty public key -> False."""
    priv, _pub = _generate_keypair()
    content = "payload"
    sig = _sign(priv, content)
    assert verify_signature(content, _hex(sig), "") is False


def test_malformed_signature_fail_closed():
    """Garbage signature bytes -> False (no crash)."""
    _priv, pub = _generate_keypair()
    assert verify_signature("content", "not-valid-hex", _hex(pub)) is False


def test_malformed_public_key_fail_closed():
    """Garbage public key bytes -> False (no crash)."""
    priv, _pub = _generate_keypair()
    content = "content"
    sig = _sign(priv, content)
    assert verify_signature(content, _hex(sig), "not-valid-hex") is False


def test_signature_too_short_fail_closed():
    """31-byte 'signature' -> False (wrong length)."""
    _priv, pub = _generate_keypair()
    short_sig = b"\x00" * 31
    assert verify_signature("content", _hex(short_sig), _hex(pub)) is False


def test_public_key_too_short_fail_closed():
    """31-byte 'key' -> False (wrong length)."""
    short_key = b"\x00" * 31
    assert verify_signature("content", "", _hex(short_key)) is False


def test_base64_encoding_accepted():
    """Base64-encoded key and signature are decoded correctly."""
    import base64

    priv, pub = _generate_keypair()
    content = "payload"
    sig = _sign(priv, content)
    sig_b64 = base64.b64encode(sig).decode()
    pub_b64 = base64.b64encode(pub).decode()
    assert verify_signature(content, sig_b64, pub_b64) is True


def test_load_public_key_closes_explicit_file() -> None:
    """The key loader must deterministically release its file descriptor."""
    reader = mock_open(read_data=" public-key ")
    with (
        patch("general_ludd.self_update.signing.os.path.isfile", return_value=True),
        patch("general_ludd.self_update.signing.open", reader),
    ):
        assert load_public_key("key-file") == "public-key"

    reader.assert_called_once_with("key-file", encoding="utf-8")
    reader().__enter__.assert_called_once_with()
    reader().__exit__.assert_called_once_with(None, None, None)
