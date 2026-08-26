"""Deep tests for Salsa20/XSalsa20: block, stream, HSalsa20, XSalsa20, edge cases.

Stream encrypt/decrypt and block generation are backed by PyCryptodome.
"""

from __future__ import annotations

import hashlib
import secrets

import pytest

from general_ludd.algorithms.salsa20 import (
    Salsa20Error,
    hsalsa20,
    salsa20_block,
    stream_decrypt,
    stream_encrypt,
    xsalsa20_decrypt,
    xsalsa20_encrypt,
)


class TestSalsa20Block:
    def test_block_produces_64_bytes(self) -> None:
        key = b"\x00" * 32
        nonce = b"\x00" * 8
        out = salsa20_block(key, nonce, 0)
        assert len(out) == 64

    def test_block_different_counter_different_output(self) -> None:
        key = b"\x01" * 32
        nonce = b"\x00" * 8
        b0 = salsa20_block(key, nonce, 0)
        b1 = salsa20_block(key, nonce, 1)
        assert b0 != b1

    def test_block_different_nonce_different_output(self) -> None:
        key = b"\x02" * 32
        a = salsa20_block(key, b"\x00" * 8, 0)
        b = salsa20_block(key, b"\x01" * 8, 0)
        assert a != b

    def test_block_different_key_different_output(self) -> None:
        a = salsa20_block(b"\x00" * 32, b"\x00" * 8, 0)
        b = salsa20_block(b"\x01" * 32, b"\x00" * 8, 0)
        assert a != b

    def test_block_16_byte_key(self) -> None:
        out = salsa20_block(b"\x00" * 16, b"\x00" * 8, 0)
        assert len(out) == 64

    def test_block_invalid_key_size(self) -> None:
        with pytest.raises(Salsa20Error, match="Key"):
            salsa20_block(b"bad", b"\x00" * 8, 0)
        with pytest.raises(Salsa20Error, match="Key"):
            salsa20_block(b"\x00" * 33, b"\x00" * 8, 0)

    def test_block_invalid_nonce_size(self) -> None:
        with pytest.raises(Salsa20Error, match="Nonce"):
            salsa20_block(b"\x00" * 32, b"bad", 0)


class TestStreamEncrypt:
    def test_encrypt_decrypt_roundtrip(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(8)
        plaintext = b"The quick brown fox jumps over the lazy dog"
        ct = stream_encrypt(plaintext, key, nonce)
        pt = stream_decrypt(ct, key, nonce)
        assert pt == plaintext

    def test_encrypt_empty(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(8)
        ct = stream_encrypt(b"", key, nonce)
        assert ct == b""

    def test_encrypt_single_byte(self) -> None:
        key = b"\x00" * 32
        nonce = b"\x00" * 8
        pt = b"A"
        ct = stream_encrypt(pt, key, nonce)
        assert len(ct) == 1
        assert stream_decrypt(ct, key, nonce) == pt

    def test_zero_keystream_byte_may_preserve_plaintext(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class ZeroKeystreamCipher:
            def encrypt(self, plaintext: bytes) -> bytes:
                return plaintext

        def new_zero_keystream_cipher(*, key: bytes, nonce: bytes) -> ZeroKeystreamCipher:
            assert len(key) == 32
            assert len(nonce) == 8
            return ZeroKeystreamCipher()

        monkeypatch.setattr(
            "general_ludd.algorithms.salsa20._PyCryptodomeSalsa20.new",
            new_zero_keystream_cipher,
        )
        plaintext = b"A"
        ciphertext = stream_encrypt(plaintext, b"\x00" * 32, b"\x00" * 8)

        assert ciphertext == plaintext
        assert stream_decrypt(ciphertext, b"\x00" * 32, b"\x00" * 8) == plaintext

    def test_encrypt_long_message(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(8)
        plaintext = secrets.token_bytes(1024)
        ct = stream_encrypt(plaintext, key, nonce)
        assert len(ct) == len(plaintext)
        assert stream_decrypt(ct, key, nonce) == plaintext

    def test_encrypt_with_counter_offset(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(8)
        plaintext = b"hello"
        ct0 = stream_encrypt(plaintext, key, nonce, counter=0)
        ct1 = stream_encrypt(plaintext, key, nonce, counter=1)
        assert ct0 != ct1

    def test_encrypt_same_plaintext_different_key(self) -> None:
        nonce = secrets.token_bytes(8)
        plaintext = b"secret message"
        ct_a = stream_encrypt(plaintext, secrets.token_bytes(32), nonce)
        ct_b = stream_encrypt(plaintext, secrets.token_bytes(32), nonce)
        assert ct_a != ct_b

    def test_encrypt_same_plaintext_different_nonce(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"secret message"
        ct_a = stream_encrypt(plaintext, key, secrets.token_bytes(8))
        ct_b = stream_encrypt(plaintext, key, secrets.token_bytes(8))
        assert ct_a != ct_b

    def test_encrypt_does_not_equal_plaintext(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(8)
        plaintext = b"Hello, World!"
        ct = stream_encrypt(plaintext, key, nonce)
        assert ct != plaintext

    def test_encrypt_16_byte_key(self) -> None:
        key = secrets.token_bytes(16)
        nonce = secrets.token_bytes(8)
        plaintext = b"the short key test"
        ct = stream_encrypt(plaintext, key, nonce)
        assert len(ct) == len(plaintext)
        assert stream_decrypt(ct, key, nonce) == plaintext

    def test_encrypt_byte_distribution(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(8)
        plaintext = b"\x00" * 4096
        ct = stream_encrypt(plaintext, key, nonce)
        counts = [0] * 256
        for b in ct:
            counts[b] += 1
        for c in counts:
            assert c > 0


class TestXSalsa20:
    def test_xsalsa20_encrypt_decrypt_roundtrip(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(24)
        plaintext = b"XSalsa20 test vector"
        ct = xsalsa20_encrypt(plaintext, key, nonce)
        pt = xsalsa20_decrypt(ct, key, nonce)
        assert pt == plaintext

    def test_xsalsa20_long_message(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(24)
        plaintext = secrets.token_bytes(1500)
        ct = xsalsa20_encrypt(plaintext, key, nonce)
        assert len(ct) == len(plaintext)
        assert xsalsa20_decrypt(ct, key, nonce) == plaintext

    def test_xsalsa20_invalid_key_size(self) -> None:
        with pytest.raises(Salsa20Error, match="requires 32-byte"):
            xsalsa20_encrypt(b"", b"\x00" * 16, b"\x00" * 24)
        with pytest.raises(Salsa20Error, match="requires 32-byte"):
            xsalsa20_encrypt(b"", b"\x00" * 33, b"\x00" * 24)

    def test_xsalsa20_invalid_nonce_size(self) -> None:
        key = secrets.token_bytes(32)
        with pytest.raises(Salsa20Error, match="requires 24-byte"):
            xsalsa20_encrypt(b"", key, b"bad")

    def test_xsalsa20_differs_from_salsa20(self) -> None:
        key = secrets.token_bytes(32)
        nonce24 = secrets.token_bytes(24)
        plaintext = b"same plaintext, different constructions"
        ct_x = xsalsa20_encrypt(plaintext, key, nonce24)
        ct_s = stream_encrypt(plaintext, key, nonce24[:8])
        assert ct_x != ct_s


class TestHSalsa20:
    def test_hsalsa20_returns_32_bytes(self) -> None:
        key = secrets.token_bytes(32)
        nonce_prefix = secrets.token_bytes(16)
        subkey = hsalsa20(key, nonce_prefix)
        assert len(subkey) == 32

    def test_hsalsa20_invalid_key(self) -> None:
        with pytest.raises(Salsa20Error, match="HSalsa20 requires 32-byte"):
            hsalsa20(b"\x00" * 16, b"\x00" * 16)

    def test_hsalsa20_invalid_nonce(self) -> None:
        with pytest.raises(Salsa20Error, match="HSalsa20 requires 16-byte"):
            hsalsa20(b"\x00" * 32, b"bad")

    def test_hsalsa20_deterministic(self) -> None:
        key = b"\x00" * 32
        nonce = b"\x00" * 16
        a = hsalsa20(key, nonce)
        b = hsalsa20(key, nonce)
        assert a == b

    def test_hsalsa20_different_nonce_different_subkey(self) -> None:
        key = secrets.token_bytes(32)
        a = hsalsa20(key, b"\x00" * 16)
        b = hsalsa20(key, b"\x01" * 16)
        assert a != b


class TestStreamProperties:
    def test_entirely_zero_input(self) -> None:
        key = b"\x00" * 32
        nonce = b"\x00" * 8
        pt = b"\x00" * 128
        ct = stream_encrypt(pt, key, nonce)
        assert len(ct) == 128

    def test_multi_block_exact_boundary(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(8)
        pt = b"\x00" * 128
        ct = stream_encrypt(pt, key, nonce)
        assert len(ct) == 128
        assert stream_decrypt(ct, key, nonce) == pt

    def test_stream_encrypt_is_deterministic(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(8)
        pt = b"deterministic check"
        a = stream_encrypt(pt, key, nonce)
        b = stream_encrypt(pt, key, nonce)
        assert a == b

    def test_encrypt_output_not_a_substring_of_key(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(8)
        ct = stream_encrypt(key, key, nonce)
        assert key not in ct

    def test_hash_of_keystream_changes_with_counter(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(8)
        k0 = stream_encrypt(b"\x00" * 128, key, nonce, counter=0)
        k1 = stream_encrypt(b"\x00" * 128, key, nonce, counter=1)
        assert hashlib.sha256(k0).digest() != hashlib.sha256(k1).digest()
