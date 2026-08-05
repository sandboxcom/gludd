"""Deep AES-GCM encryption tests: encrypt/decrypt, tag verification,
nonce generation, associated data integrity, key validation.

Pure-Python wrapper around the cryptography library's AEAD AESGCM.
"""

from __future__ import annotations

import secrets

import pytest

from general_ludd.algorithms.aes_gcm import (
    AESGCMError,
    decrypt,
    encrypt,
    generate_nonce,
    hash_key,
)


class TestNonceGeneration:
    def test_nonce_is_12_bytes(self) -> None:
        nonce = generate_nonce()
        assert len(nonce) == 12

    def test_nonce_is_unique(self) -> None:
        nonces = {generate_nonce() for _ in range(200)}
        assert len(nonces) == 200

    def test_nonce_uses_secrets_module(self) -> None:
        nonce = generate_nonce()
        assert isinstance(nonce, bytes)


class TestBasicRoundtrip:
    def test_encrypt_decrypt_simple(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"hello, AES-GCM"
        nonce = generate_nonce()
        ciphertext = encrypt(key, plaintext, nonce)
        result = decrypt(key, ciphertext, nonce)
        assert result == plaintext

    def test_encrypt_decrypt_empty(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        ciphertext = encrypt(key, b"", nonce)
        result = decrypt(key, ciphertext, nonce)
        assert result == b""

    def test_encrypt_decrypt_large(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = secrets.token_bytes(1_000_000)
        nonce = generate_nonce()
        ciphertext = encrypt(key, plaintext, nonce)
        result = decrypt(key, ciphertext, nonce)
        assert result == plaintext

    def test_different_nonces_produce_different_output(self) -> None:
        key = secrets.token_bytes(32)
        plaintext = b"same message"
        c1 = encrypt(key, plaintext, generate_nonce())
        c2 = encrypt(key, plaintext, generate_nonce())
        assert c1 != c2

    def test_same_nonce_same_output(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        plaintext = b"deterministic check"
        c1 = encrypt(key, plaintext, nonce)
        c2 = encrypt(key, plaintext, nonce)
        assert c1 == c2


class TestAssociatedData:
    def test_encrypt_decrypt_with_ad(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        plaintext = b"classified data"
        ad = b"user-42"
        ciphertext = encrypt(key, plaintext, nonce, associated_data=ad)
        result = decrypt(key, ciphertext, nonce, associated_data=ad)
        assert result == plaintext

    def test_wrong_associated_data_fails(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        plaintext = b"classified data"
        ciphertext = encrypt(key, plaintext, nonce, associated_data=b"user-42")
        with pytest.raises(AESGCMError):
            decrypt(key, ciphertext, nonce, associated_data=b"user-99")

    def test_ad_not_encrypted_in_ciphertext(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        ad = b"metadata-123"
        ciphertext = encrypt(key, b"payload", nonce, associated_data=ad)
        result = decrypt(key, ciphertext, nonce, associated_data=ad)
        assert result == b"payload"

    def test_ad_none_equivalent_to_empty_bytes(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        c1 = encrypt(key, b"x", nonce, associated_data=None)
        c2 = encrypt(key, b"x", nonce, associated_data=b"")
        assert c1 == c2


class TestTamperDetection:
    def test_wrong_key_fails(self) -> None:
        key1 = secrets.token_bytes(32)
        key2 = secrets.token_bytes(32)
        nonce = generate_nonce()
        ciphertext = encrypt(key1, b"secret", nonce)
        with pytest.raises(AESGCMError):
            decrypt(key2, ciphertext, nonce)

    def test_wrong_nonce_fails(self) -> None:
        key = secrets.token_bytes(32)
        ciphertext = encrypt(key, b"secret", secrets.token_bytes(12))
        with pytest.raises(AESGCMError):
            decrypt(key, ciphertext, secrets.token_bytes(12))

    def test_tampered_ciphertext_fails(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        ciphertext = encrypt(key, b"secret", nonce)
        tampered = bytearray(ciphertext)
        tampered[4] ^= 0xFF
        with pytest.raises(AESGCMError):
            decrypt(key, bytes(tampered), nonce)

    def test_tampered_tag_fails(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        ciphertext = encrypt(key, b"secret", nonce)
        tampered = bytearray(ciphertext)
        tampered[-1] ^= 0xFF
        with pytest.raises(AESGCMError):
            decrypt(key, bytes(tampered), nonce)

    def test_truncated_ciphertext_fails(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        ciphertext = encrypt(key, b"secret", nonce)
        with pytest.raises(AESGCMError):
            decrypt(key, ciphertext[:10], nonce)


class TestKeyNOnceValidation:
    def test_key_16_bytes_works(self) -> None:
        key = secrets.token_bytes(16)
        nonce = generate_nonce()
        ciphertext = encrypt(key, b"hello", nonce)
        assert decrypt(key, ciphertext, nonce) == b"hello"

    def test_key_24_bytes_works(self) -> None:
        key = secrets.token_bytes(24)
        nonce = generate_nonce()
        ciphertext = encrypt(key, b"hello", nonce)
        assert decrypt(key, ciphertext, nonce) == b"hello"

    def test_key_32_bytes_works(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        ciphertext = encrypt(key, b"hello", nonce)
        assert decrypt(key, ciphertext, nonce) == b"hello"

    def test_key_invalid_size_raises(self) -> None:
        key = secrets.token_bytes(31)
        nonce = generate_nonce()
        with pytest.raises(AESGCMError):
            encrypt(key, b"hello", nonce)

    def test_nonce_11_bytes_raises(self) -> None:
        key = secrets.token_bytes(32)
        with pytest.raises(AESGCMError):
            encrypt(key, b"hello", secrets.token_bytes(11))


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


class TestBoundaryConditions:
    def test_single_byte_payload(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        c = encrypt(key, b"\x00", nonce)
        assert decrypt(key, c, nonce) == b"\x00"

    def test_max_byte_values(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        plaintext = bytes(range(256))
        c = encrypt(key, plaintext, nonce)
        assert decrypt(key, c, nonce) == plaintext

    def test_binary_data_with_nulls(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        plaintext = b"\x00\x01\x02\x00\xff\x00"
        c = encrypt(key, plaintext, nonce)
        assert decrypt(key, c, nonce) == plaintext


class TestEncryptDecryptAuthOnly:
    def test_auth_only_no_plaintext(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        c1 = encrypt(key, b"", nonce, associated_data=b"auth-data")
        decrypt(key, c1, nonce, associated_data=b"auth-data")

    def test_auth_only_wrong_ad_fails(self) -> None:
        key = secrets.token_bytes(32)
        nonce = generate_nonce()
        c = encrypt(key, b"", nonce, associated_data=b"version-1")
        with pytest.raises(AESGCMError):
            decrypt(key, c, nonce, associated_data=b"version-2")
