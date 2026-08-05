"""ChaCha20-Poly1305 AEAD tests using the `cryptography` library backend."""

from __future__ import annotations

import secrets

import pytest

from general_ludd.algorithms.chacha20 import (
    ChaCha20Poly1305Error,
    chacha20_aead_decrypt,
    chacha20_aead_encrypt,
    generate_key,
    generate_nonce,
)


class TestAEADEncryptDecrypt:
    def test_rfc8439_aead_encrypt_vector(self) -> None:
        key = bytes.fromhex("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
        nonce = bytes.fromhex("070000004041424344454647")
        plaintext = bytes.fromhex(
            "4c616469657320616e642047656e746c"
            "656d656e206f662074686520636c6173"
            "73206f66202739393a20496620492063"
            "6f756c64206f6666657220796f75206f"
            "6e6c79206f6e652074697020666f7220"
            "746865206675747572652c2073756e73"
            "637265656e20776f756c642062652069"
            "742e"
        )
        ad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
        result = chacha20_aead_encrypt(key, nonce, plaintext, associated_data=ad)
        tag = result[-16:]
        assert tag.hex() == "1ae10b594f09e26a7e902ecbd0600691"

    def test_rfc8439_aead_decrypt_roundtrip(self) -> None:
        key = bytes(range(32))
        nonce = bytes(range(12))
        plaintext = b"RFC 8439 AEAD roundtrip verification: encrypt then decrypt"
        ad = bytes(range(16))
        ct = chacha20_aead_encrypt(key, nonce, plaintext, associated_data=ad)
        pt = chacha20_aead_decrypt(key, nonce, ct, associated_data=ad)
        assert pt == plaintext

    def test_aead_roundtrip_simple(self) -> None:
        key = generate_key()
        nonce = generate_nonce()
        plaintext = b"AEAD test payload"
        ct = chacha20_aead_encrypt(key, nonce, plaintext)
        assert len(ct) == len(plaintext) + 16
        pt = chacha20_aead_decrypt(key, nonce, ct)
        assert pt == plaintext

    def test_aead_roundtrip_empty(self) -> None:
        key = generate_key()
        nonce = generate_nonce()
        ct = chacha20_aead_encrypt(key, nonce, b"")
        assert len(ct) == 16
        pt = chacha20_aead_decrypt(key, nonce, ct)
        assert pt == b""

    def test_aead_roundtrip_large(self) -> None:
        key = generate_key()
        nonce = generate_nonce()
        plaintext = secrets.token_bytes(200_000)
        ct = chacha20_aead_encrypt(key, nonce, plaintext)
        pt = chacha20_aead_decrypt(key, nonce, ct)
        assert pt == plaintext

    def test_aead_with_associated_data(self) -> None:
        key = generate_key()
        nonce = generate_nonce()
        plaintext = b"classified"
        ad = b"header-info-42"
        ct = chacha20_aead_encrypt(key, nonce, plaintext, associated_data=ad)
        pt = chacha20_aead_decrypt(key, nonce, ct, associated_data=ad)
        assert pt == plaintext

    def test_wrong_associated_data_fails(self) -> None:
        key = generate_key()
        nonce = generate_nonce()
        ct = chacha20_aead_encrypt(key, nonce, b"secret", associated_data=b"ad-1")
        with pytest.raises(ChaCha20Poly1305Error, match="Authentication failed"):
            chacha20_aead_decrypt(key, nonce, ct, associated_data=b"ad-2")

    def test_wrong_key_fails(self) -> None:
        nonce = generate_nonce()
        ct = chacha20_aead_encrypt(generate_key(), nonce, b"data")
        with pytest.raises(ChaCha20Poly1305Error, match="Authentication failed"):
            chacha20_aead_decrypt(generate_key(), nonce, ct)

    def test_wrong_nonce_fails(self) -> None:
        key = generate_key()
        ct = chacha20_aead_encrypt(key, generate_nonce(), b"data")
        with pytest.raises(ChaCha20Poly1305Error, match="Authentication failed"):
            chacha20_aead_decrypt(key, generate_nonce(), ct)

    def test_bit_flip_detected(self) -> None:
        key = generate_key()
        nonce = generate_nonce()
        ct = chacha20_aead_encrypt(key, nonce, b"tamper target")
        corrupted = bytes([ct[0] ^ 1]) + ct[1:]
        with pytest.raises(ChaCha20Poly1305Error, match="Authentication failed"):
            chacha20_aead_decrypt(key, nonce, corrupted)

    def test_truncated_ciphertext_fails(self) -> None:
        key = generate_key()
        nonce = generate_nonce()
        ct = chacha20_aead_encrypt(key, nonce, b"data")
        with pytest.raises(ChaCha20Poly1305Error, match="too short"):
            chacha20_aead_decrypt(key, nonce, ct[:5])

    def test_aead_ciphertext_includes_tag(self) -> None:
        key = generate_key()
        nonce = generate_nonce()
        plaintext = b"hello"
        ct = chacha20_aead_encrypt(key, nonce, plaintext)
        assert len(ct) == len(plaintext) + 16
        assert ct[:-16] != ct[-16:]

    def test_invalid_key_rejected_encrypt(self) -> None:
        with pytest.raises(ChaCha20Poly1305Error, match="Key must be 32 bytes"):
            chacha20_aead_encrypt(b"\x00" * 16, b"\x00" * 12, b"data")

    def test_invalid_nonce_rejected_encrypt(self) -> None:
        with pytest.raises(ChaCha20Poly1305Error, match="Nonce must be 12 bytes"):
            chacha20_aead_encrypt(b"\x00" * 32, b"\x00" * 8, b"data")

    def test_invalid_key_rejected_decrypt(self) -> None:
        with pytest.raises(ChaCha20Poly1305Error, match="Key must be 32 bytes"):
            chacha20_aead_decrypt(b"\x00" * 16, b"\x00" * 12, b"\x00" * 32)

    def test_invalid_nonce_rejected_decrypt(self) -> None:
        with pytest.raises(ChaCha20Poly1305Error, match="Nonce must be 12 bytes"):
            chacha20_aead_decrypt(b"\x00" * 32, b"\x00" * 8, b"\x00" * 32)

    def test_different_nonces_produce_different_output(self) -> None:
        key = generate_key()
        plaintext = b"same message"
        ct1 = chacha20_aead_encrypt(key, generate_nonce(), plaintext)
        ct2 = chacha20_aead_encrypt(key, generate_nonce(), plaintext)
        assert ct1 != ct2

    def test_same_nonce_same_output(self) -> None:
        key = generate_key()
        nonce = generate_nonce()
        plaintext = b"deterministic check"
        ct1 = chacha20_aead_encrypt(key, nonce, plaintext)
        ct2 = chacha20_aead_encrypt(key, nonce, plaintext)
        assert ct1 == ct2

    def test_nonce_is_unique(self) -> None:
        nonces = {generate_nonce() for _ in range(200)}
        assert len(nonces) == 200

    def test_generate_key_32_bytes(self) -> None:
        key = generate_key()
        assert len(key) == 32
        assert isinstance(key, bytes)

    def test_generate_nonce_12_bytes(self) -> None:
        nonce = generate_nonce()
        assert len(nonce) == 12
        assert isinstance(nonce, bytes)
