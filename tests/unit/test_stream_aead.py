"""Streaming AEAD tests — ChaCha20-Poly1305 chunk-based encrypt/decrypt.

Covers: roundtrips, multi-chunk, large payloads, tamper detection,
associated data, is-final semantics, state-machine exhaustion,
key/nonce validation, and determinism.
"""

from __future__ import annotations

import secrets

import pytest

from general_ludd.algorithms.stream_aead import (
    StreamAEADDecryptor,
    StreamAEADEncryptor,
    StreamAEADError,
    generate_key,
    generate_nonce,
)

_CHUNK_SIZES: list[int] = [1, 16, 64, 255, 1024, 65536]


class TestKeyNonceValidation:
    def test_invalid_key_size_raises(self) -> None:
        for bad_len in (0, 16, 31, 33, 64):
            with pytest.raises(StreamAEADError, match="Key must be 32 bytes"):
                StreamAEADEncryptor(secrets.token_bytes(bad_len), generate_nonce())

    def test_invalid_nonce_size_raises_encryptor(self) -> None:
        for bad_len in (0, 8, 11, 13, 24):
            with pytest.raises(StreamAEADError, match="Nonce must be 12 bytes"):
                StreamAEADEncryptor(generate_key(), secrets.token_bytes(bad_len))

    def test_invalid_nonce_size_raises_decryptor(self) -> None:
        with pytest.raises(StreamAEADError, match="Nonce must be 12 bytes"):
            StreamAEADDecryptor(generate_key(), secrets.token_bytes(11))

    def test_generate_key_is_32_bytes(self) -> None:
        assert len(generate_key()) == 32

    def test_generate_nonce_is_12_bytes(self) -> None:
        assert len(generate_nonce()) == 12

    def test_generate_nonce_is_unique(self) -> None:
        nonces = {generate_nonce() for _ in range(200)}
        assert len(nonces) == 200


class TestSingleChunk:
    def test_encrypt_decrypt_single_chunk(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        plaintext = b"hello streaming AEAD"
        enc = StreamAEADEncryptor(key, nonce)
        dec = StreamAEADDecryptor(key, nonce)
        ct = enc.encrypt(plaintext, is_final=True)
        pt = dec.decrypt(ct, is_final=True)
        assert pt == plaintext

    def test_single_chunk_empty_plaintext(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        dec = StreamAEADDecryptor(key, nonce)
        ct = enc.encrypt(b"", is_final=True)
        pt = dec.decrypt(ct, is_final=True)
        assert pt == b""

    def test_single_chunk_ciphertext_longer_than_plaintext_by_tag(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        ct = enc.encrypt(b"data", is_final=True)
        assert len(ct) == len(b"data") + 16  # 16-byte Poly1305 tag


class TestMultiChunk:
    def test_two_chunks_roundtrip(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        dec = StreamAEADDecryptor(key, nonce)
        c1 = enc.encrypt(b"chunk one")
        c2 = enc.encrypt(b"chunk two", is_final=True)
        assert dec.decrypt(c1) == b"chunk one"
        assert dec.decrypt(c2, is_final=True) == b"chunk two"

    def test_three_chunks_roundtrip(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        dec = StreamAEADDecryptor(key, nonce)
        c1 = enc.encrypt(b"first")
        c2 = enc.encrypt(b"second")
        c3 = enc.encrypt(b"third", is_final=True)
        assert dec.decrypt(c1) == b"first"
        assert dec.decrypt(c2) == b"second"
        assert dec.decrypt(c3, is_final=True) == b"third"

    @pytest.mark.parametrize("chunk_size", _CHUNK_SIZES)
    def test_variable_chunk_sizes(self, chunk_size: int) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        plaintext_chunks: list[bytes] = []
        for _ in range(6):
            pt = secrets.token_bytes(chunk_size)
            plaintext_chunks.append(pt)
        ciphertext_chunks: list[bytes] = []
        for i, pt in enumerate(plaintext_chunks):
            is_final = i == len(plaintext_chunks) - 1
            ciphertext_chunks.append(enc.encrypt(pt, is_final=is_final))
        dec = StreamAEADDecryptor(key, nonce)
        for i, ct in enumerate(ciphertext_chunks):
            is_final = i == len(ciphertext_chunks) - 1
            assert dec.decrypt(ct, is_final=is_final) == plaintext_chunks[i]

    def test_many_small_chunks(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        plaintext_chunks = [secrets.token_bytes(7) for _ in range(50)]
        ct_chunks = []
        for i, pt in enumerate(plaintext_chunks):
            ct_chunks.append(enc.encrypt(pt, is_final=(i == len(plaintext_chunks) - 1)))
        dec = StreamAEADDecryptor(key, nonce)
        for i, ct in enumerate(ct_chunks):
            assert dec.decrypt(ct, is_final=(i == len(ct_chunks) - 1)) == plaintext_chunks[i]


class TestLargeData:
    def test_large_payload_many_chunks(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        plaintext = secrets.token_bytes(1_000_000)
        chunk_size = 8192
        enc = StreamAEADEncryptor(key, nonce)
        ct_chunks: list[bytes] = []
        for offset in range(0, len(plaintext), chunk_size):
            chunk = plaintext[offset : offset + chunk_size]
            is_final = offset + chunk_size >= len(plaintext)
            ct_chunks.append(enc.encrypt(chunk, is_final=is_final))
        dec = StreamAEADDecryptor(key, nonce)
        result = bytearray()
        for i, ct in enumerate(ct_chunks):
            result.extend(dec.decrypt(ct, is_final=(i == len(ct_chunks) - 1)))
        assert bytes(result) == plaintext

    def test_1mb_plaintext_roundtrip(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        plaintext = secrets.token_bytes(1_000_000)
        enc = StreamAEADEncryptor(key, nonce)
        dec = StreamAEADDecryptor(key, nonce)
        ct = enc.encrypt(plaintext, is_final=True)
        assert dec.decrypt(ct, is_final=True) == plaintext


class TestAssociatedData:
    def test_ad_roundtrip_single_chunk(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        ad = b"session-v1"
        enc = StreamAEADEncryptor(key, nonce, associated_data=ad)
        dec = StreamAEADDecryptor(key, nonce, associated_data=ad)
        ct = enc.encrypt(b"payload", is_final=True)
        assert dec.decrypt(ct, is_final=True) == b"payload"

    def test_ad_roundtrip_multi_chunk(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        ad = b"ctx-42"
        enc = StreamAEADEncryptor(key, nonce, associated_data=ad)
        dec = StreamAEADDecryptor(key, nonce, associated_data=ad)
        c1 = enc.encrypt(b"p1")
        c2 = enc.encrypt(b"p2", is_final=True)
        assert dec.decrypt(c1) == b"p1"
        assert dec.decrypt(c2, is_final=True) == b"p2"

    def test_wrong_ad_fails_decrypt(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce, associated_data=b"good-ad")
        dec = StreamAEADDecryptor(key, nonce, associated_data=b"wrong-ad")
        ct = enc.encrypt(b"secret", is_final=True)
        with pytest.raises(StreamAEADError, match="authentication failed"):
            dec.decrypt(ct, is_final=True)

    def test_missing_ad_on_decrypt_fails(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce, associated_data=b"required")
        ct = enc.encrypt(b"secret", is_final=True)
        dec = StreamAEADDecryptor(key, nonce)
        with pytest.raises(StreamAEADError, match="authentication failed"):
            dec.decrypt(ct, is_final=True)

    def test_ad_with_empty_plaintext(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        ad = b"auth-only"
        enc = StreamAEADEncryptor(key, nonce, associated_data=ad)
        dec = StreamAEADDecryptor(key, nonce, associated_data=ad)
        ct = enc.encrypt(b"", is_final=True)
        assert dec.decrypt(ct, is_final=True) == b""


class TestTamperDetection:
    def test_wrong_key_fails(self) -> None:
        k1, k2 = generate_key(), generate_key()
        nonce = generate_nonce()
        enc = StreamAEADEncryptor(k1, nonce)
        dec = StreamAEADDecryptor(k2, nonce)
        ct = enc.encrypt(b"secret", is_final=True)
        with pytest.raises(StreamAEADError, match="authentication failed"):
            dec.decrypt(ct, is_final=True)

    def test_wrong_nonce_fails(self) -> None:
        key = generate_key()
        enc = StreamAEADEncryptor(key, generate_nonce())
        dec = StreamAEADDecryptor(key, generate_nonce())
        ct = enc.encrypt(b"secret", is_final=True)
        with pytest.raises(StreamAEADError, match="authentication failed"):
            dec.decrypt(ct, is_final=True)

    def test_tampered_ciphertext_body(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        dec = StreamAEADDecryptor(key, nonce)
        ct = enc.encrypt(b"tamper-me", is_final=True)
        corrupted = bytearray(ct)
        corrupted[2] ^= 0xFF
        with pytest.raises(StreamAEADError):
            dec.decrypt(bytes(corrupted), is_final=True)

    def test_tampered_tag(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        dec = StreamAEADDecryptor(key, nonce)
        ct = enc.encrypt(b"tamper-me", is_final=True)
        corrupted = bytearray(ct)
        corrupted[-1] ^= 0xFF
        with pytest.raises(StreamAEADError):
            dec.decrypt(bytes(corrupted), is_final=True)

    def test_chunk_reordering_detected(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        c1 = enc.encrypt(b"chunk-0")
        c2 = enc.encrypt(b"chunk-1", is_final=True)
        dec = StreamAEADDecryptor(key, nonce)
        with pytest.raises(StreamAEADError, match="authentication failed"):
            dec.decrypt(c2)
            dec.decrypt(c1, is_final=True)

    def test_chunk_truncation_detected(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        ct = enc.encrypt(b"payload", is_final=True)
        dec = StreamAEADDecryptor(key, nonce)
        with pytest.raises(StreamAEADError):
            dec.decrypt(ct[:10], is_final=True)

    def test_middle_chunk_tamper_detected(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        c0 = enc.encrypt(b"chunk-0")
        c1 = enc.encrypt(b"chunk-1")
        enc.encrypt(b"chunk-2", is_final=True)
        dec = StreamAEADDecryptor(key, nonce)
        assert dec.decrypt(c0) == b"chunk-0"
        corrupted = bytearray(c1)
        corrupted[3] ^= 0xFF
        with pytest.raises(StreamAEADError):
            dec.decrypt(bytes(corrupted))

    def test_truncated_below_tag_length_fails(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        ct = enc.encrypt(b"data", is_final=True)
        dec = StreamAEADDecryptor(key, nonce)
        with pytest.raises(StreamAEADError, match="too short for tag"):
            dec.decrypt(ct[:8], is_final=True)


class TestIsFinalSemantics:
    def test_encrypt_after_final_raises(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        enc.encrypt(b"done", is_final=True)
        with pytest.raises(StreamAEADError, match="already finalized"):
            enc.encrypt(b"more")

    def test_decrypt_after_final_raises(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        dec = StreamAEADDecryptor(key, nonce)
        ct = enc.encrypt(b"done", is_final=True)
        dec.decrypt(ct, is_final=True)
        with pytest.raises(StreamAEADError, match="already finalized"):
            dec.decrypt(b"\x00" * 32, is_final=True)

    def test_missing_is_final_on_encrypt_ok(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        c0 = enc.encrypt(b"not final")
        c1 = enc.encrypt(b"also not final")
        c2 = enc.encrypt(b"last", is_final=True)
        dec = StreamAEADDecryptor(key, nonce)
        assert dec.decrypt(c0) == b"not final"
        assert dec.decrypt(c1) == b"also not final"
        assert dec.decrypt(c2, is_final=True) == b"last"

    def test_extra_final_on_decrypt_fails(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        ct = enc.encrypt(b"only", is_final=True)
        dec = StreamAEADDecryptor(key, nonce)
        assert dec.decrypt(ct, is_final=True) == b"only"
        with pytest.raises(StreamAEADError, match="already finalized"):
            dec.decrypt(b"\x00" * 32, is_final=True)


class TestDeterminism:
    def test_same_key_nonce_produces_deterministic_output(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        pt = b"deterministic test payload"
        enc1 = StreamAEADEncryptor(key, nonce)
        enc2 = StreamAEADEncryptor(key, nonce)
        ct1 = enc1.encrypt(pt, is_final=True)
        ct2 = enc2.encrypt(pt, is_final=True)
        assert ct1 == ct2

    def test_same_key_nonce_multi_chunk_deterministic(self) -> None:
        key, nonce = generate_key(), generate_nonce()

        def _encrypt_stream() -> list[bytes]:
            enc = StreamAEADEncryptor(key, nonce)
            return [
                enc.encrypt(b"a"),
                enc.encrypt(b"b"),
                enc.encrypt(b"c", is_final=True),
            ]

        assert _encrypt_stream() == _encrypt_stream()

    def test_different_nonce_different_output(self) -> None:
        key = generate_key()
        pt = b"data"
        enc1 = StreamAEADEncryptor(key, generate_nonce())
        enc2 = StreamAEADEncryptor(key, generate_nonce())
        assert enc1.encrypt(pt, is_final=True) != enc2.encrypt(pt, is_final=True)

    def test_different_chunk_index_different_ciphertext(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc1 = StreamAEADEncryptor(key, nonce)
        enc2 = StreamAEADEncryptor(key, nonce)
        enc1.encrypt(b"\x00" * 32)
        enc2.encrypt(b"\x00" * 32)
        ct_a = enc1.encrypt(b"target", is_final=True)
        ct_b = enc2.encrypt(b"target", is_final=True)
        assert ct_a == ct_b  # same chunk idx, same key+nonce → deterministic


class TestBoundaryConditions:
    def test_single_byte_plaintext(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        dec = StreamAEADDecryptor(key, nonce)
        ct = enc.encrypt(b"\x00", is_final=True)
        assert dec.decrypt(ct, is_final=True) == b"\x00"

    def test_max_byte_values_range_256(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        plaintext = bytes(range(256))
        enc = StreamAEADEncryptor(key, nonce)
        dec = StreamAEADDecryptor(key, nonce)
        ct = enc.encrypt(plaintext, is_final=True)
        assert dec.decrypt(ct, is_final=True) == plaintext

    def test_binary_data_with_nulls(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        plaintext = b"\x00\x01\x02\x00\xff\x00"
        enc = StreamAEADEncryptor(key, nonce)
        dec = StreamAEADDecryptor(key, nonce)
        ct = enc.encrypt(plaintext, is_final=True)
        assert dec.decrypt(ct, is_final=True) == plaintext

    def test_chunk_at_tag_length_boundary(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        ct = enc.encrypt(b"\x00" * 16, is_final=True)
        assert len(ct) == 32  # 16 plaintext + 16 tag

    def test_chunk_just_below_tag_length(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        enc = StreamAEADEncryptor(key, nonce)
        ct = enc.encrypt(b"\x00" * 15, is_final=True)
        dec = StreamAEADDecryptor(key, nonce)
        assert dec.decrypt(ct, is_final=True) == b"\x00" * 15


class TestInterleaved:
    def test_encrypt_only_first_then_second(self) -> None:
        key, nonce = generate_key(), generate_nonce()
        plaintext = secrets.token_bytes(50000)
        enc = StreamAEADEncryptor(key, nonce)
        half = len(plaintext) // 2
        c1 = enc.encrypt(plaintext[:half])
        c2 = enc.encrypt(plaintext[half:], is_final=True)
        dec = StreamAEADDecryptor(key, nonce)
        assert dec.decrypt(c1) + dec.decrypt(c2, is_final=True) == plaintext

    def test_encrypt_interleaved_both_directions(self) -> None:
        key_a, nonce_a = generate_key(), generate_nonce()
        key_b, nonce_b = generate_key(), generate_nonce()
        enc_a = StreamAEADEncryptor(key_a, nonce_a)
        enc_b = StreamAEADEncryptor(key_b, nonce_b)
        ct_a = enc_a.encrypt(b"stream-a", is_final=True)
        ct_b = enc_b.encrypt(b"stream-b", is_final=True)
        dec_a = StreamAEADDecryptor(key_a, nonce_a)
        dec_b = StreamAEADDecryptor(key_b, nonce_b)
        assert dec_a.decrypt(ct_a, is_final=True) == b"stream-a"
        assert dec_b.decrypt(ct_b, is_final=True) == b"stream-b"

    def test_two_independent_streams_different_nonces(self) -> None:
        key = generate_key()
        n1, n2 = generate_nonce(), generate_nonce()
        e1 = StreamAEADEncryptor(key, n1)
        e2 = StreamAEADEncryptor(key, n2)
        ct1 = e1.encrypt(b"stream-1", is_final=True)
        ct2 = e2.encrypt(b"stream-2", is_final=True)
        assert ct1 != ct2
        d1 = StreamAEADDecryptor(key, n1)
        d2 = StreamAEADDecryptor(key, n2)
        assert d1.decrypt(ct1, is_final=True) == b"stream-1"
        assert d2.decrypt(ct2, is_final=True) == b"stream-2"
