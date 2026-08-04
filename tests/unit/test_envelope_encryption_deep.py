"""Deep encryption-at-rest and envelope encryption tests.

Covers: key wrapping, DEK/KEK hierarchy, AES-GCM encrypt/decrypt,
key rotation, tamper detection, re-wrap, metadata binding, and
concurrent access safety.
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from general_ludd.security.envelope_encryption import (
    DEFAULT_DEK_BYTES,
    DEFAULT_KEK_BYTES,
    EncryptedBlob,
    EnvelopeEncryptor,
    InMemoryKEKStore,
    KeyUnwrapError,
    TamperDetected,
    generate_dek,
    generate_kek,
    unwrap_key,
    wrap_key,
)


@pytest.fixture
def kek_store() -> InMemoryKEKStore:
    return InMemoryKEKStore()


@pytest.fixture
def active_kek(kek_store: InMemoryKEKStore) -> bytes:
    kek = generate_kek()
    kek_store.save(version=1, kek=kek)
    return kek


@pytest.fixture
def encryptor(active_kek: bytes, kek_store: InMemoryKEKStore) -> EnvelopeEncryptor:
    return EnvelopeEncryptor(kek_store=kek_store)


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


class TestKeyGeneration:
    def test_generate_kek_is_correct_length(self) -> None:
        kek = generate_kek()
        assert len(kek) == DEFAULT_KEK_BYTES

    def test_generate_dek_is_correct_length(self) -> None:
        dek = generate_dek()
        assert len(dek) == DEFAULT_DEK_BYTES

    def test_generate_kek_is_random(self) -> None:
        keks = {generate_kek() for _ in range(50)}
        assert len(keks) == 50

    def test_generate_dek_is_random(self) -> None:
        deks = {generate_dek() for _ in range(50)}
        assert len(deks) == 50


# ---------------------------------------------------------------------------
# Key wrapping
# ---------------------------------------------------------------------------


class TestKeyWrapping:
    def test_wrap_unwrap_roundtrip(self) -> None:
        kek = generate_kek()
        dek = generate_dek()
        wrapped = wrap_key(dek, kek)
        assert isinstance(wrapped, bytes)
        assert wrapped != dek
        unwrapped = unwrap_key(wrapped, kek)
        assert unwrapped == dek

    def test_wrap_produces_different_output_each_time(self) -> None:
        kek = generate_kek()
        dek = generate_dek()
        wrapped_1 = wrap_key(dek, kek)
        wrapped_2 = wrap_key(dek, kek)
        assert wrapped_1 != wrapped_2  # unique IV/nonce each wrap

    def test_unwrap_with_wrong_kek_fails(self) -> None:
        kek = generate_kek()
        other_kek = generate_kek()
        dek = generate_dek()
        wrapped = wrap_key(dek, kek)
        with pytest.raises(KeyUnwrapError):
            unwrap_key(wrapped, other_kek)

    def test_unwrap_with_wrong_length_kek_fails(self) -> None:
        kek = generate_kek()
        dek = generate_dek()
        wrapped = wrap_key(dek, kek)
        too_short = os.urandom(DEFAULT_KEK_BYTES - 1)
        with pytest.raises((KeyUnwrapError, ValueError)):
            unwrap_key(wrapped, too_short)


# ---------------------------------------------------------------------------
# Envelope encrypt / decrypt
# ---------------------------------------------------------------------------


class TestEnvelopeEncryptDecrypt:
    def test_encrypt_decrypt_roundtrip(self, encryptor: EnvelopeEncryptor) -> None:
        plaintext = b"attack at dawn"
        blob = encryptor.encrypt(plaintext)
        assert isinstance(blob, EncryptedBlob)
        assert blob.kek_version == 1
        assert blob.ciphertext != plaintext
        assert blob.wrapped_dek
        assert blob.nonce
        assert blob.tag
        decrypted = encryptor.decrypt(blob)
        assert decrypted == plaintext

    def test_encrypt_decrypt_empty_payload(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"")
        decrypted = encryptor.decrypt(blob)
        assert decrypted == b""

    def test_encrypt_decrypt_large_payload(self, encryptor: EnvelopeEncryptor) -> None:
        plaintext = os.urandom(1_000_000)
        blob = encryptor.encrypt(plaintext)
        decrypted = encryptor.decrypt(blob)
        assert decrypted == plaintext

    def test_encrypt_produces_different_ciphertext_each_time(self, encryptor: EnvelopeEncryptor) -> None:
        plaintext = b"same plaintext"
        ct1 = encryptor.encrypt(plaintext).ciphertext
        ct2 = encryptor.encrypt(plaintext).ciphertext
        assert ct1 != ct2

    def test_decrypt_with_wrong_kek_version_fails(
        self, encryptor: EnvelopeEncryptor, kek_store: InMemoryKEKStore
    ) -> None:
        blob = encryptor.encrypt(b"secret")
        broken = EncryptedBlob(
            kek_version=99,
            wrapped_dek=blob.wrapped_dek,
            nonce=blob.nonce,
            ciphertext=blob.ciphertext,
            tag=blob.tag,
        )
        with pytest.raises(KeyUnwrapError):
            encryptor.decrypt(broken)

    def test_encrypt_with_string_payload(self, encryptor: EnvelopeEncryptor) -> None:
        plaintext = "unicode string 😀"
        blob = encryptor.encrypt(plaintext.encode("utf-8"))
        decrypted = encryptor.decrypt(blob)
        assert decrypted.decode("utf-8") == plaintext


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------


class TestTamperDetection:
    def test_tampered_ciphertext(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"secret")
        corrupted = EncryptedBlob(
            kek_version=blob.kek_version,
            wrapped_dek=blob.wrapped_dek,
            nonce=blob.nonce,
            ciphertext=bytes([b ^ 0xFF for b in blob.ciphertext]),
            tag=blob.tag,
        )
        with pytest.raises(TamperDetected):
            encryptor.decrypt(corrupted)

    def test_tampered_nonce(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"secret")
        corrupted = EncryptedBlob(
            kek_version=blob.kek_version,
            wrapped_dek=blob.wrapped_dek,
            nonce=bytes([b ^ 0x01 for b in blob.nonce]),
            ciphertext=blob.ciphertext,
            tag=blob.tag,
        )
        with pytest.raises(TamperDetected):
            encryptor.decrypt(corrupted)

    def test_tampered_tag(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"secret")
        corrupted = EncryptedBlob(
            kek_version=blob.kek_version,
            wrapped_dek=blob.wrapped_dek,
            nonce=blob.nonce,
            ciphertext=blob.ciphertext,
            tag=bytes([b ^ 0x01 for b in blob.tag]),
        )
        with pytest.raises(TamperDetected):
            encryptor.decrypt(corrupted)

    def test_tampered_wrapped_dek(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"secret")
        corrupted = EncryptedBlob(
            kek_version=blob.kek_version,
            wrapped_dek=bytes([b ^ 0x01 for b in blob.wrapped_dek]),
            nonce=blob.nonce,
            ciphertext=blob.ciphertext,
            tag=blob.tag,
        )
        with pytest.raises((TamperDetected, KeyUnwrapError)):
            encryptor.decrypt(corrupted)

    def test_truncated_ciphertext(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"secret")
        corrupted = EncryptedBlob(
            kek_version=blob.kek_version,
            wrapped_dek=blob.wrapped_dek,
            nonce=blob.nonce,
            ciphertext=blob.ciphertext[:5],
            tag=blob.tag,
        )
        with pytest.raises(TamperDetected):
            encryptor.decrypt(corrupted)

    def test_empty_ciphertext_tamper(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"secret")
        corrupted = EncryptedBlob(
            kek_version=blob.kek_version,
            wrapped_dek=blob.wrapped_dek,
            nonce=blob.nonce,
            ciphertext=b"",
            tag=blob.tag,
        )
        with pytest.raises(TamperDetected):
            encryptor.decrypt(corrupted)


# ---------------------------------------------------------------------------
# Key rotation
# ---------------------------------------------------------------------------


class TestKeyRotation:
    def test_rotate_creates_new_kek_version(self, encryptor: EnvelopeEncryptor, kek_store: InMemoryKEKStore) -> None:
        old_kek = kek_store.load(1)
        result = encryptor.rotate_kek(ttl_seconds=3600)
        assert result.success
        assert result.new_version == 2
        new_kek = kek_store.load(2)
        assert new_kek is not None
        assert new_kek != old_kek

    def test_rotation_result_contains_prior_version(self, encryptor: EnvelopeEncryptor) -> None:
        encryptor.rotate_kek()
        result = encryptor.rotate_kek()
        assert result.prior_version == 2
        assert result.new_version == 3

    def test_encrypt_decrypt_after_rotation(self, encryptor: EnvelopeEncryptor) -> None:
        plaintext = b"secret before rotation"
        blob = encryptor.encrypt(plaintext)
        encryptor.rotate_kek()
        decrypted = encryptor.decrypt(blob)
        assert decrypted == plaintext

    def test_encrypt_uses_latest_kek_after_rotation(self, encryptor: EnvelopeEncryptor) -> None:
        encryptor.rotate_kek()
        blob = encryptor.encrypt(b"after rotation")
        assert blob.kek_version == 2

    def test_decrypt_old_blob_after_key_removal_fails(
        self, encryptor: EnvelopeEncryptor, kek_store: InMemoryKEKStore
    ) -> None:
        blob = encryptor.encrypt(b"old secret")
        encryptor.rotate_kek()
        kek_store.delete(blob.kek_version)
        with pytest.raises(KeyUnwrapError):
            encryptor.decrypt(blob)

    def test_rotation_timestamp_is_set(self, encryptor: EnvelopeEncryptor) -> None:
        before = time.time()
        result = encryptor.rotate_kek(ttl_seconds=3600)
        after = time.time()
        assert result.rotated_at is not None
        assert before <= result.rotated_at <= after


# ---------------------------------------------------------------------------
# Re-wrap
# ---------------------------------------------------------------------------


class TestReWrap:
    def test_rewrap_encrypted_blob(self, encryptor: EnvelopeEncryptor, kek_store: InMemoryKEKStore) -> None:
        blob = encryptor.encrypt(b"re-wrap me")
        old_wrapped_dek = blob.wrapped_dek
        encryptor.rotate_kek()
        rewrapped = encryptor.rewrap(blob)
        assert rewrapped.kek_version == 2
        assert rewrapped.wrapped_dek != old_wrapped_dek
        assert rewrapped.nonce == blob.nonce
        assert rewrapped.tag == blob.tag
        assert rewrapped.ciphertext == blob.ciphertext
        decrypted = encryptor.decrypt(rewrapped)
        assert decrypted == b"re-wrap me"

    def test_rewrap_when_already_latest_is_noop(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"data")
        rewrapped = encryptor.rewrap(blob)
        assert rewrapped == blob

    def test_rewrap_preserves_plaintext_through_multiple_rotations(self, encryptor: EnvelopeEncryptor) -> None:
        plaintext = b"survive all rotations"
        blob = encryptor.encrypt(plaintext)
        for _ in range(3):
            encryptor.rotate_kek()
            blob = encryptor.rewrap(blob)
        assert encryptor.decrypt(blob) == plaintext

    def test_batch_rewrap(self, encryptor: EnvelopeEncryptor, kek_store: InMemoryKEKStore) -> None:
        blobs = [encryptor.encrypt(f"payload {i}".encode()) for i in range(10)]
        encryptor.rotate_kek()
        rewrapped = encryptor.rewrap_batch(blobs)
        for i, rw in enumerate(rewrapped):
            assert rw.kek_version == 2
            assert encryptor.decrypt(rw) == f"payload {i}".encode()


# ---------------------------------------------------------------------------
# EncryptedBlob serialisation
# ---------------------------------------------------------------------------


class TestEncryptedBlobSerialisation:
    def test_to_json_roundtrip(self) -> None:
        blob = EncryptedBlob(
            kek_version=1,
            wrapped_dek=b"a" * 64,
            nonce=b"n" * 12,
            ciphertext=b"c" * 32,
            tag=b"t" * 16,
        )
        data = blob.to_json()
        assert isinstance(data, str)
        parsed = EncryptedBlob.from_json(data)
        assert parsed == blob

    def test_to_json_includes_version(self) -> None:
        blob = EncryptedBlob(
            kek_version=5,
            wrapped_dek=b"a" * 64,
            nonce=b"n" * 12,
            ciphertext=b"c" * 32,
            tag=b"t" * 16,
        )
        data = json.loads(blob.to_json())
        assert data["kek_version"] == 5
        assert "wrapped_dek" in data
        assert "nonce" in data
        assert "ciphertext" in data
        assert "tag" in data

    def test_from_json_rejects_missing_fields(self) -> None:
        incomplete = json.dumps({"kek_version": 1, "wrapped_dek": "YQ=="})
        with pytest.raises(ValueError):
            EncryptedBlob.from_json(incomplete)


# ---------------------------------------------------------------------------
# KEK expiry
# ---------------------------------------------------------------------------


class TestKEKExpiry:
    def test_encrypt_uses_active_kek_only(self, kek_store: InMemoryKEKStore) -> None:
        old = generate_kek()
        new = generate_kek()
        kek_store.save(version=1, kek=old, expires_at=time.time() - 1)
        kek_store.save(version=2, kek=new, expires_at=time.time() + 3600)
        encryptor = EnvelopeEncryptor(kek_store=kek_store)
        blob = encryptor.encrypt(b"data")
        assert blob.kek_version == 2

    def test_rotate_mark_expires(self, encryptor: EnvelopeEncryptor) -> None:
        result = encryptor.rotate_kek(ttl_seconds=3600)
        assert result.expires_at is not None
        assert result.expires_at > time.time()

    def test_expired_kek_cannot_encrypt(self, kek_store: InMemoryKEKStore) -> None:
        expired = generate_kek()
        kek_store.save(version=1, kek=expired, expires_at=time.time() - 10)
        encryptor = EnvelopeEncryptor(kek_store=kek_store)
        with pytest.raises(ValueError):
            encryptor.encrypt(b"data")


# ---------------------------------------------------------------------------
# Concurrent access
# ---------------------------------------------------------------------------


class TestConcurrentAccess:
    def test_concurrent_encrypt_same_encryptor(self, encryptor: EnvelopeEncryptor) -> None:
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(50):
                    blob = encryptor.encrypt(os.urandom(256))
                    encryptor.decrypt(blob)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_concurrent_rotate_and_encrypt(self, encryptor: EnvelopeEncryptor) -> None:
        errors: list[Exception] = []

        def encryptor_worker() -> None:
            try:
                for _ in range(30):
                    blob = encryptor.encrypt(os.urandom(128))
                    encryptor.decrypt(blob)
            except Exception as exc:
                errors.append(exc)

        def rotator() -> None:
            try:
                for _ in range(5):
                    encryptor.rotate_kek(ttl_seconds=3600)
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=encryptor_worker) for _ in range(4)] + [threading.Thread(target=rotator)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_concurrent_rewrap(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"shared blob")
        encryptor.rotate_kek()
        errors: list[Exception] = []

        def rewrap_worker() -> None:
            try:
                for _ in range(20):
                    rewrapped = encryptor.rewrap(blob)
                    encryptor.decrypt(rewrapped)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=rewrap_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


# ---------------------------------------------------------------------------
# InMemoryKEKStore
# ---------------------------------------------------------------------------


class TestInMemoryKEKStore:
    def test_save_load(self, kek_store: InMemoryKEKStore) -> None:
        kek = generate_kek()
        kek_store.save(version=1, kek=kek)
        loaded = kek_store.load(1)
        assert loaded == kek

    def test_load_missing_returns_none(self, kek_store: InMemoryKEKStore) -> None:
        assert kek_store.load(999) is None

    def test_list_versions(self, kek_store: InMemoryKEKStore) -> None:
        for v in (3, 1, 2):
            kek_store.save(version=v, kek=generate_kek())
        assert kek_store.list_versions() == [1, 2, 3]

    def test_delete(self, kek_store: InMemoryKEKStore) -> None:
        kek_store.save(version=1, kek=generate_kek())
        kek_store.delete(1)
        assert kek_store.load(1) is None

    def test_active_version_returns_max(self, kek_store: InMemoryKEKStore) -> None:
        for v in (1, 2, 3):
            kek_store.save(version=v, kek=generate_kek())
        assert kek_store.active_version() == 3

    def test_active_version_empty_store(self, kek_store: InMemoryKEKStore) -> None:
        assert kek_store.active_version() == 0

    def test_all_cheap_kek_store_methods(self, kek_store: InMemoryKEKStore) -> None:
        """Verify KEKStore abstract methods are callable."""
        kek = generate_kek()
        kek_store.save(version=1, kek=kek)
        assert isinstance(kek_store.load(1), bytes)
        assert isinstance(kek_store.list_versions(), list)
        kek_store.delete(1)
        assert kek_store.load(1) is None
