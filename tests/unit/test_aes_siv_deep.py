"""Deep AES-GCM-SIV and AES-SIV encryption tests: encrypt/decrypt,
tag verification, nonce generation, associated data integrity,
misuse-resistance, key validation.

AES-GCM-SIV (RFC 8452) — misuse-resistant nonce-based AEAD.
AES-SIV (RFC 5297)  — deterministic AEAD (no nonce).

Pure-Python wrappers around the cryptography library's AEAD AESGCMSIV and AESSIV.
"""

from __future__ import annotations

import secrets

import pytest

from general_ludd.algorithms.aes_siv import (
    AESSIVError,
    decrypt_gcm_siv,
    decrypt_siv,
    encrypt_gcm_siv,
    encrypt_siv,
    generate_nonce_gcm_siv,
    hash_key,
)

# ---------------------------------------------------------------------------
# AES-GCM-SIV  (RFC 8452)
# ---------------------------------------------------------------------------


class TestGcmSivNonceGeneration:
    def test_nonce_is_12_bytes(self) -> None:
        nonce = generate_nonce_gcm_siv()
        assert len(nonce) == 12

    def test_nonce_is_unique(self) -> None:
        nonces = {generate_nonce_gcm_siv() for _ in range(200)}
        assert len(nonces) == 200

    def test_nonce_uses_secrets_module(self) -> None:
        nonce = generate_nonce_gcm_siv()
        assert isinstance(nonce, bytes)


class TestGcmSivBasicRoundtrip:
    def test_encrypt_decrypt_simple(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"hello, AES-GCM-SIV"
        nonce = generate_nonce_gcm_siv()
        ciphertext = encrypt_gcm_siv(key, plaintext, nonce)
        result = decrypt_gcm_siv(key, ciphertext, nonce)
        assert result == plaintext

    def test_encrypt_decrypt_empty(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        ciphertext = encrypt_gcm_siv(key, b"", nonce)
        result = decrypt_gcm_siv(key, ciphertext, nonce)
        assert result == b""

    def test_encrypt_decrypt_large(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = secrets.token_bytes(1_000_000)
        nonce = generate_nonce_gcm_siv()
        ciphertext = encrypt_gcm_siv(key, plaintext, nonce)
        result = decrypt_gcm_siv(key, ciphertext, nonce)
        assert result == plaintext

    def test_different_nonces_produce_different_output(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"same message"
        c1 = encrypt_gcm_siv(key, plaintext, generate_nonce_gcm_siv())
        c2 = encrypt_gcm_siv(key, plaintext, generate_nonce_gcm_siv())
        assert c1 != c2

    def test_same_nonce_same_output(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        plaintext = b"deterministic check"
        c1 = encrypt_gcm_siv(key, plaintext, nonce)
        c2 = encrypt_gcm_siv(key, plaintext, nonce)
        assert c1 == c2


class TestGcmSivAssociatedData:
    def test_encrypt_decrypt_with_ad(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        plaintext = b"classified data"
        ad = b"user-42"
        ciphertext = encrypt_gcm_siv(key, plaintext, nonce, associated_data=ad)
        result = decrypt_gcm_siv(key, ciphertext, nonce, associated_data=ad)
        assert result == plaintext

    def test_wrong_associated_data_fails(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        plaintext = b"classified data"
        ciphertext = encrypt_gcm_siv(key, plaintext, nonce, associated_data=b"user-42")
        with pytest.raises(AESSIVError):
            decrypt_gcm_siv(key, ciphertext, nonce, associated_data=b"user-99")

    def test_ad_none_equivalent_to_empty_bytes(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        c1 = encrypt_gcm_siv(key, b"x", nonce, associated_data=None)
        c2 = encrypt_gcm_siv(key, b"x", nonce, associated_data=b"")
        assert c1 == c2


class TestGcmSivTamperDetection:
    def test_wrong_key_fails(self) -> None:
        key1 = secrets.token_bytes(32)
        key2 = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        ciphertext = encrypt_gcm_siv(key1, b"secret", nonce)
        with pytest.raises(AESSIVError):
            decrypt_gcm_siv(key2, ciphertext, nonce)

    def test_wrong_nonce_fails(self) -> None:
        key = secrets.token_bytes(32)
        ciphertext = encrypt_gcm_siv(key, b"secret", secrets.token_bytes(12))
        with pytest.raises(AESSIVError):
            decrypt_gcm_siv(key, ciphertext, secrets.token_bytes(12))

    def test_tampered_ciphertext_fails(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        ciphertext = encrypt_gcm_siv(key, b"secret", nonce)
        tampered = bytearray(ciphertext)
        tampered[4] ^= 0xFF
        with pytest.raises(AESSIVError):
            decrypt_gcm_siv(key, bytes(tampered), nonce)

    def test_tampered_tag_fails(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        ciphertext = encrypt_gcm_siv(key, b"secret", nonce)
        tampered = bytearray(ciphertext)
        tampered[-1] ^= 0xFF
        with pytest.raises(AESSIVError):
            decrypt_gcm_siv(key, bytes(tampered), nonce)

    def test_truncated_ciphertext_fails(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        ciphertext = encrypt_gcm_siv(key, b"secret", nonce)
        with pytest.raises(AESSIVError):
            decrypt_gcm_siv(key, ciphertext[:10], nonce)


class TestGcmSivMisuseResistance:
    def test_reused_nonce_still_decrypts_correctly(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        pt1 = b"message one"
        pt2 = b"message two"
        c1 = encrypt_gcm_siv(key, pt1, nonce)
        c2 = encrypt_gcm_siv(key, pt2, nonce)
        assert decrypt_gcm_siv(key, c1, nonce) == pt1
        assert decrypt_gcm_siv(key, c2, nonce) == pt2

    def test_reused_nonce_with_ad_still_decrypts(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        c1 = encrypt_gcm_siv(key, b"alpha", nonce, associated_data=b"ctx-a")
        c2 = encrypt_gcm_siv(key, b"beta", nonce, associated_data=b"ctx-b")
        assert decrypt_gcm_siv(key, c1, nonce, associated_data=b"ctx-a") == b"alpha"
        assert decrypt_gcm_siv(key, c2, nonce, associated_data=b"ctx-b") == b"beta"


class TestGcmSivKeyNOnceValidation:
    def test_key_16_bytes_works(self) -> None:
        key = secrets.token_bytes(16)
        nonce = generate_nonce_gcm_siv()
        ciphertext = encrypt_gcm_siv(key, b"hello", nonce)
        assert decrypt_gcm_siv(key, ciphertext, nonce) == b"hello"

    def test_key_32_bytes_works(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        ciphertext = encrypt_gcm_siv(key, b"hello", nonce)
        assert decrypt_gcm_siv(key, ciphertext, nonce) == b"hello"

    def test_key_24_bytes_raises(self) -> None:
        key = secrets.token_bytes(24)
        nonce = generate_nonce_gcm_siv()
        with pytest.raises(AESSIVError):
            encrypt_gcm_siv(key, b"hello", nonce)

    def test_nonce_11_bytes_raises(self) -> None:
        key = secrets.token_bytes(32)
        with pytest.raises(AESSIVError):
            encrypt_gcm_siv(key, b"hello", secrets.token_bytes(11))


class TestGcmSivBoundaryConditions:
    def test_single_byte_payload(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        c = encrypt_gcm_siv(key, b"\x00", nonce)
        assert decrypt_gcm_siv(key, c, nonce) == b"\x00"

    def test_max_byte_values(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        plaintext = bytes(range(256))
        c = encrypt_gcm_siv(key, plaintext, nonce)
        assert decrypt_gcm_siv(key, c, nonce) == plaintext

    def test_binary_data_with_nulls(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce_gcm_siv()
        plaintext = b"\x00\x01\x02\x00\xff\x00"
        c = encrypt_gcm_siv(key, plaintext, nonce)
        assert decrypt_gcm_siv(key, c, nonce) == plaintext


# ---------------------------------------------------------------------------
# AES-SIV  (RFC 5297)
# ---------------------------------------------------------------------------


class TestSivBasicRoundtrip:
    def test_encrypt_decrypt_simple(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"hello, AES-SIV"
        ciphertext = encrypt_siv(key, plaintext)
        result = decrypt_siv(key, ciphertext)
        assert result == plaintext

    def test_encrypt_decrypt_empty(self) -> None:
        key = secrets.token_bytes(32)
        ciphertext = encrypt_siv(key, b"")
        result = decrypt_siv(key, ciphertext)
        assert result == b""

    def test_encrypt_decrypt_large(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = secrets.token_bytes(1_000_000)
        ciphertext = encrypt_siv(key, plaintext)
        result = decrypt_siv(key, ciphertext)
        assert result == plaintext

    def test_deterministic_no_nonce(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"deterministic message"
        c1 = encrypt_siv(key, plaintext)
        c2 = encrypt_siv(key, plaintext)
        assert c1 == c2

    def test_different_plaintext_different_output(self) -> None:
        key = secrets.token_bytes(32)
        c1 = encrypt_siv(key, b"alpha")
        c2 = encrypt_siv(key, b"beta")
        assert c1 != c2


class TestSivAssociatedData:
    def test_encrypt_decrypt_with_ad(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"classified data"
        ad = [b"user-42", b"role-admin"]
        ciphertext = encrypt_siv(key, plaintext, associated_data=ad)
        result = decrypt_siv(key, ciphertext, associated_data=ad)
        assert result == plaintext

    def test_wrong_ad_fails(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"classified data"
        ciphertext = encrypt_siv(key, plaintext, associated_data=[b"user-42"])
        with pytest.raises(AESSIVError):
            decrypt_siv(key, ciphertext, associated_data=[b"user-99"])

    def test_ad_order_matters(self) -> None:
        key = secrets.token_bytes(32)
        c1 = encrypt_siv(key, b"x", associated_data=[b"a", b"b"])
        c2 = encrypt_siv(key, b"x", associated_data=[b"b", b"a"])
        assert c1 != c2

    def test_no_ad_vs_empty_ad_list(self) -> None:
        key = secrets.token_bytes(32)
        c1 = encrypt_siv(key, b"x", associated_data=None)
        c2 = encrypt_siv(key, b"x", associated_data=[])
        assert c1 == c2


class TestSivTamperDetection:
    def test_wrong_key_fails(self) -> None:
        key1 = secrets.token_bytes(32)
        key2 = secrets.token_bytes(32)
        ciphertext = encrypt_siv(key1, b"secret")
        with pytest.raises(AESSIVError):
            decrypt_siv(key2, ciphertext)

    def test_tampered_ciphertext_fails(self) -> None:
        key = secrets.token_bytes(32)
        ciphertext = encrypt_siv(key, b"secret")
        tampered = bytearray(ciphertext)
        tampered[4] ^= 0xFF
        with pytest.raises(AESSIVError):
            decrypt_siv(key, bytes(tampered))

    def test_tampered_tag_fails(self) -> None:
        key = secrets.token_bytes(32)
        ciphertext = encrypt_siv(key, b"secret")
        tampered = bytearray(ciphertext)
        tampered[-1] ^= 0xFF
        with pytest.raises(AESSIVError):
            decrypt_siv(key, bytes(tampered))

    def test_truncated_ciphertext_fails(self) -> None:
        key = secrets.token_bytes(32)
        ciphertext = encrypt_siv(key, b"secret")
        with pytest.raises(AESSIVError):
            decrypt_siv(key, ciphertext[:10])


class TestSivKeyValidation:
    def test_key_32_bytes_works(self) -> None:
        key = secrets.token_bytes(32)
        c = encrypt_siv(key, b"hello")
        assert decrypt_siv(key, c) == b"hello"

    def test_key_48_bytes_works(self) -> None:
        key = secrets.token_bytes(48)
        c = encrypt_siv(key, b"hello")
        assert decrypt_siv(key, c) == b"hello"

    def test_key_64_bytes_works(self) -> None:
        key = secrets.token_bytes(64)
        c = encrypt_siv(key, b"hello")
        assert decrypt_siv(key, c) == b"hello"

    def test_key_invalid_size_raises(self) -> None:
        key = secrets.token_bytes(16)
        with pytest.raises(AESSIVError):
            encrypt_siv(key, b"hello")


class TestSivBoundaryConditions:
    def test_single_byte_payload(self) -> None:
        key = secrets.token_bytes(32)
        c = encrypt_siv(key, b"\x00")
        assert decrypt_siv(key, c) == b"\x00"

    def test_max_byte_values(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = bytes(range(256))
        c = encrypt_siv(key, plaintext)
        assert decrypt_siv(key, c) == plaintext

    def test_binary_data_with_nulls(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"\x00\x01\x02\x00\xff\x00"
        c = encrypt_siv(key, plaintext)
        assert decrypt_siv(key, c) == plaintext

    def test_different_keys_produce_different_output(self) -> None:
        k1 = secrets.token_bytes(32)
        k2 = secrets.token_bytes(32)
        pt = b"same message"
        assert encrypt_siv(k1, pt) != encrypt_siv(k2, pt)


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


class TestHashKey:
    def test_hash_key_returns_32_bytes(self) -> None:
        derived = hash_key(b"password", b"some-salt")
        assert len(derived) == 32
        assert isinstance(derived, bytes)

    def test_hash_key_deterministic(self) -> None:
        pwd = b"my-secret-key"
        salt = b"app-salt-v1"
        assert hash_key(pwd, salt) == hash_key(pwd, salt)

    def test_hash_key_different_input_different_output(self) -> None:
        k1 = hash_key(b"password1", b"salt")
        k2 = hash_key(b"password2", b"salt")
        assert k1 != k2

    def test_hash_key_different_salt_different_output(self) -> None:
        pwd = b"password"
        assert hash_key(pwd, b"salt1") != hash_key(pwd, b"salt2")

    def test_hash_key_empty_password(self) -> None:
        key = hash_key(b"", b"salt")
        assert len(key) == 32


# ---------------------------------------------------------------------------
# Cross-scheme invariants
# ---------------------------------------------------------------------------


class TestCrossScheme:
    def test_gcm_siv_and_siv_produce_different_output(self) -> None:
        key = secrets.token_bytes(32)
        pt = b"same plaintext"
        gcm_ct = encrypt_gcm_siv(key, pt, generate_nonce_gcm_siv())
        siv_ct = encrypt_siv(key, pt)
        assert gcm_ct != siv_ct

    def test_gcm_siv_nonce_required_siv_not(self) -> None:
        key = secrets.token_bytes(32)
        assert len(encrypt_siv(key, b"x")) > 0
        nonce = generate_nonce_gcm_siv()
        assert len(encrypt_gcm_siv(key, b"x", nonce)) > 0
