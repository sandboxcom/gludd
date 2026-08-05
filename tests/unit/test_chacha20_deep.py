"""Deep ChaCha20-Poly1305 tests: quarter round, block function, stream cipher,
Poly1305 MAC, AEAD encrypt/decrypt, tag verification, associated data,
nonce generation, key validation, and RFC 8439 test vectors.

Pure-Python, stdlib only implementation.
"""

from __future__ import annotations

import secrets

import pytest

from general_ludd.algorithms.chacha20 import (
    ChaCha20Poly1305Error,
    _chacha20_block,
    _constant_time_compare,
    _poly1305_key_gen,
    chacha20_aead_decrypt,
    chacha20_aead_encrypt,
    chacha20_decrypt,
    chacha20_encrypt,
    chacha20_stream,
    generate_key,
    generate_nonce,
    poly1305_mac,
    quarter_round,
)


class TestQuarterRound:
    def test_rfc8439_example(self) -> None:
        a, b, c, d = 0x11111111, 0x01020304, 0x9B8D6F43, 0x01234567
        a2, b2, c2, d2 = quarter_round(a, b, c, d)
        assert a2 == 0xEA2A92F4
        assert b2 == 0xCB1CF8CE
        assert c2 == 0x4581472E
        assert d2 == 0x5881C4BB

    def test_idempotency_double(self) -> None:
        a, b, c, d = 0xDEADBEEF, 0xCAFEBABE, 0x8BADF00D, 0xFEEDFACE
        r1 = quarter_round(a, b, c, d)
        r2 = quarter_round(*r1)
        assert r1 != (a, b, c, d)
        assert r2 != r1

    def test_zero_input(self) -> None:
        a, b, c, d = quarter_round(0, 0, 0, 0)
        assert a == 0 and b == 0 and c == 0 and d == 0

    def test_max_values(self) -> None:
        a, b, c, d = quarter_round(0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
        assert all(0 <= v <= 0xFFFFFFFF for v in (a, b, c, d))


class TestChaCha20Block:
    def test_block_little_endian_consistency(self) -> None:
        key = bytes(range(32))
        nonce = bytes(range(12))
        b0 = _chacha20_block(key, 0, nonce)
        b1 = _chacha20_block(key, 1, nonce)
        assert len(b0) == 64
        assert len(b1) == 64
        assert b0 != b1
        assert b0 != b"\x00" * 64
        assert b1 != b"\x00" * 64

    def test_block_deterministic(self) -> None:
        key = bytes(32)
        nonce = b"\x00" * 12
        assert _chacha20_block(key, 0, nonce) == _chacha20_block(key, 0, nonce)
        assert _chacha20_block(key, 5, nonce) == _chacha20_block(key, 5, nonce)

    def test_block_is_64_bytes(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        block = _chacha20_block(key, 0, nonce)
        assert len(block) == 64

    def test_counter_increments_change_output(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        b0 = _chacha20_block(key, 0, nonce)
        b1 = _chacha20_block(key, 1, nonce)
        b42 = _chacha20_block(key, 42, nonce)
        assert b0 != b1
        assert b0 != b42
        assert b1 != b42

    def test_nonce_changes_output(self) -> None:
        key = secrets.token_bytes(32)
        b1 = _chacha20_block(key, 0, b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")
        b2 = _chacha20_block(key, 0, b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01")
        assert b1 != b2

    def test_key_changes_output(self) -> None:
        k1 = secrets.token_bytes(32)
        k2 = secrets.token_bytes(32)
        nonce = b"\x00" * 12
        b1 = _chacha20_block(k1, 0, nonce)
        b2 = _chacha20_block(k2, 0, nonce)
        assert b1 != b2

    def test_invalid_key_size(self) -> None:
        with pytest.raises(ChaCha20Poly1305Error, match="Key must be 32 bytes"):
            _chacha20_block(b"\x00" * 16, 0, b"\x00" * 12)

    def test_invalid_nonce_size(self) -> None:
        with pytest.raises(ChaCha20Poly1305Error, match="Nonce must be 12 bytes"):
            _chacha20_block(b"\x00" * 32, 0, b"\x00" * 8)


class TestChacha20Stream:
    def test_stream_empty(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        stream = chacha20_stream(key, 0, nonce, 0)
        assert stream == b""

    def test_stream_64_bytes(self) -> None:
        key = secrets.token_bytes(32)
        nonce = b"\x00" * 12
        stream = chacha20_stream(key, 0, nonce, 64)
        block = _chacha20_block(key, 0, nonce)
        assert stream == block

    def test_stream_cross_block_boundary(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        stream = chacha20_stream(key, 0, nonce, 100)
        assert len(stream) == 100
        block0 = _chacha20_block(key, 0, nonce)
        block1 = _chacha20_block(key, 1, nonce)
        assert stream[:64] == block0
        assert stream[64:100] == block1[:36]

    def test_stream_large(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        length = 100_000
        stream = chacha20_stream(key, 0, nonce, length)
        assert len(stream) == length


class TestChacha20EncryptDecrypt:
    def test_roundtrip_simple(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        plaintext = b"ChaCha20 test message"
        ct = chacha20_encrypt(key, 0, nonce, plaintext)
        assert ct != plaintext
        pt = chacha20_decrypt(key, 0, nonce, ct)
        assert pt == plaintext

    def test_roundtrip_empty(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        ct = chacha20_encrypt(key, 0, nonce, b"")
        assert ct == b""
        pt = chacha20_decrypt(key, 0, nonce, ct)
        assert pt == b""

    def test_roundtrip_large(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        plaintext = secrets.token_bytes(50_000)
        ct = chacha20_encrypt(key, 0, nonce, plaintext)
        pt = chacha20_decrypt(key, 0, nonce, ct)
        assert pt == plaintext

    def test_keystream_xor_property(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        plaintext = secrets.token_bytes(256)
        ks = chacha20_stream(key, 0, nonce, len(plaintext))
        ct = chacha20_encrypt(key, 0, nonce, plaintext)
        expected = bytes(a ^ b for a, b in zip(plaintext, ks, strict=False))
        assert ct == expected

    def test_different_counter_different_output(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        plaintext = b"same"
        c0 = chacha20_encrypt(key, 0, nonce, plaintext)
        c1 = chacha20_encrypt(key, 1, nonce, plaintext)
        assert c0 != c1


class TestPoly1305MAC:
    def test_rfc8439_mac_vector(self) -> None:
        key = bytes.fromhex("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b")
        message = bytes.fromhex("43727970746f6772617068696320466f72756d2052657365617263682047726f7570")
        tag = poly1305_mac(message, key)
        assert tag.hex() == "a8061dc1305136c6c22b8baf0c0127a9"

    def test_mac_deterministic(self) -> None:
        key = secrets.token_bytes(32)
        msg = b"hello world"
        assert poly1305_mac(msg, key) == poly1305_mac(msg, key)

    def test_different_message_different_mac(self) -> None:
        key = secrets.token_bytes(32)
        t1 = poly1305_mac(b"message one", key)
        t2 = poly1305_mac(b"message two", key)
        assert t1 != t2

    def test_different_key_different_mac(self) -> None:
        msg = b"fixed message"
        t1 = poly1305_mac(msg, secrets.token_bytes(32))
        t2 = poly1305_mac(msg, secrets.token_bytes(32))
        assert t1 != t2

    def test_mac_is_16_bytes(self) -> None:
        key = secrets.token_bytes(32)
        for msg in (b"", b"short", secrets.token_bytes(1000)):
            tag = poly1305_mac(msg, key)
            assert len(tag) == 16

    def test_invalid_key_size(self) -> None:
        with pytest.raises(ChaCha20Poly1305Error, match="Poly1305 key must be 32 bytes"):
            poly1305_mac(b"data", b"\x00" * 16)

    def test_empty_message(self) -> None:
        key = secrets.token_bytes(32)
        tag = poly1305_mac(b"", key)
        assert len(tag) == 16

    def test_poly1305_key_gen_same_block(self) -> None:
        key = secrets.token_bytes(32)
        nonce = b"\x00" * 12
        otk = _poly1305_key_gen(key, nonce)
        block = _chacha20_block(key, 0, nonce)
        assert otk == block[:32]


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

    def test_chacha20_as_building_block(self) -> None:
        key = generate_key()
        nonce = generate_nonce()
        plaintext = b"building block test"
        ct_raw = chacha20_encrypt(key, 1, nonce, plaintext)
        ct_aead = chacha20_aead_encrypt(key, nonce, plaintext)[:-16]
        assert ct_raw == ct_aead

    def test_generate_key_32_bytes(self) -> None:
        key = generate_key()
        assert len(key) == 32
        assert isinstance(key, bytes)


class TestConstantTimeCompare:
    def test_equal(self) -> None:
        assert _constant_time_compare(b"abc", b"abc")

    def test_not_equal(self) -> None:
        assert not _constant_time_compare(b"abc", b"abd")

    def test_different_lengths(self) -> None:
        assert not _constant_time_compare(b"abc", b"ab")


class TestPoly1305KeyGen:
    def test_output_is_32_bytes(self) -> None:
        key = secrets.token_bytes(32)
        nonce = b"\x00" * 12
        otk = _poly1305_key_gen(key, nonce)
        assert len(otk) == 32
