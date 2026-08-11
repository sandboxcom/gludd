"""Deep tests for envelope_encryption — DEK/KEK, wrap/unwrap, encrypt/decrypt round-trip."""

from __future__ import annotations

import json

import pytest

from general_ludd.security.envelope_encryption import (
    DEFAULT_DEK_BYTES,
    DEFAULT_KEK_BYTES,
    NONCE_BYTES,
    TAG_BYTES,
    WRAP_PAYLOAD_VERSION,
    EncryptedBlob,
    EnvelopeEncryptor,
    InMemoryKEKStore,
    KeyRotationResult,
    KeyUnwrapError,
    TamperDetected,
    create_envelope_encryptor,
    generate_dek,
    generate_kek,
    unwrap_key,
    wrap_key,
)


class TestKeyGeneration:
    def test_generate_kek_default_length(self) -> None:
        kek = generate_kek()
        assert len(kek) == DEFAULT_KEK_BYTES

    def test_generate_kek_custom_length(self) -> None:
        kek = generate_kek(24)
        assert len(kek) == 24

    def test_generate_kek_too_short(self) -> None:
        with pytest.raises(ValueError, match="at least 16"):
            generate_kek(8)

    def test_generate_dek_default_length(self) -> None:
        dek = generate_dek()
        assert len(dek) == DEFAULT_DEK_BYTES

    def test_generate_dek_too_short(self) -> None:
        with pytest.raises(ValueError, match="at least 16"):
            generate_dek(4)

    def test_keys_are_random(self) -> None:
        k1 = generate_kek()
        k2 = generate_kek()
        assert k1 != k2


class TestKeyWrapping:
    def test_wrap_unwrap_round_trip(self) -> None:
        kek = generate_kek()
        dek = generate_dek()
        wrapped = wrap_key(dek, kek)
        assert wrapped is not None
        assert len(wrapped) > len(dek)
        unwrapped = unwrap_key(wrapped, kek)
        assert unwrapped == dek

    def test_wrap_has_version_prefix(self) -> None:
        kek = generate_kek()
        dek = generate_dek()
        wrapped = wrap_key(dek, kek)
        version = int.from_bytes(wrapped[:4], "big")
        assert version == WRAP_PAYLOAD_VERSION

    def test_wrap_includes_nonce(self) -> None:
        kek = generate_kek()
        dek = generate_dek()
        wrapped = wrap_key(dek, kek)
        assert len(wrapped) == 4 + NONCE_BYTES + 32 + TAG_BYTES

    def test_unwrap_wrong_kek_fails(self) -> None:
        kek1 = generate_kek()
        kek2 = generate_kek()
        dek = generate_dek()
        wrapped = wrap_key(dek, kek1)
        with pytest.raises(KeyUnwrapError):
            unwrap_key(wrapped, kek2)

    def test_unwrap_tampered(self) -> None:
        kek = generate_kek()
        dek = generate_dek()
        wrapped = bytearray(wrap_key(dek, kek))
        wrapped[-1] ^= 0xFF
        with pytest.raises(KeyUnwrapError):
            unwrap_key(bytes(wrapped), kek)

    def test_unwrap_too_short(self) -> None:
        with pytest.raises(KeyUnwrapError, match="too short"):
            unwrap_key(b"short", generate_kek())

    def test_unwrap_wrong_version(self) -> None:
        kek = generate_kek()
        dek = generate_dek()
        wrapped = bytearray(wrap_key(dek, kek))
        wrapped[0] = 0xFF
        with pytest.raises(KeyUnwrapError, match="unsupported"):
            unwrap_key(bytes(wrapped), kek)

    def test_kek_length_validation(self) -> None:
        with pytest.raises(ValueError, match="must be 16-32"):
            wrap_key(generate_dek(), b"tooshort")

    def test_kek_length_validation_too_long(self) -> None:
        with pytest.raises(ValueError, match="must be 16-32"):
            wrap_key(generate_dek(), b"x" * 64)

    def test_wrap_determinism_false(self) -> None:
        kek = generate_kek()
        dek = generate_dek()
        w1 = wrap_key(dek, kek)
        w2 = wrap_key(dek, kek)
        assert w1 != w2


class TestEncryptedBlob:
    def test_json_round_trip(self) -> None:
        original = EncryptedBlob(
            kek_version=1,
            wrapped_dek=b"wrapped" * 4,
            nonce=b"\x00" * NONCE_BYTES,
            ciphertext=b"cipher" * 8,
            tag=b"\x01" * TAG_BYTES,
        )
        json_str = original.to_json()
        parsed = EncryptedBlob.from_json(json_str)
        assert parsed.kek_version == original.kek_version
        assert parsed.wrapped_dek == original.wrapped_dek
        assert parsed.nonce == original.nonce
        assert parsed.ciphertext == original.ciphertext
        assert parsed.tag == original.tag

    def test_from_json_missing_fields(self) -> None:
        bad = json.dumps({"kek_version": 1})
        with pytest.raises(ValueError, match="missing fields"):
            EncryptedBlob.from_json(bad)

    def test_from_json_invalid_json(self) -> None:
        with pytest.raises(ValueError):
            EncryptedBlob.from_json("not json")

    def test_blob_immutable(self) -> None:
        blob = EncryptedBlob(
            kek_version=1,
            wrapped_dek=b"x" * 4,
            nonce=b"\x00" * NONCE_BYTES,
            ciphertext=b"c",
            tag=b"\x01" * TAG_BYTES,
        )
        with pytest.raises(AttributeError):
            blob.kek_version = 2  # type: ignore[misc]


class TestInMemoryKEKStore:
    def test_save_and_load(self) -> None:
        store = InMemoryKEKStore()
        kek = generate_kek()
        store.save(1, kek)
        assert store.load(1) == kek

    def test_load_missing(self) -> None:
        store = InMemoryKEKStore()
        assert store.load(999) is None

    def test_list_versions(self) -> None:
        store = InMemoryKEKStore()
        store.save(5, generate_kek())
        store.save(3, generate_kek())
        assert store.list_versions() == [3, 5]

    def test_delete(self) -> None:
        store = InMemoryKEKStore()
        store.save(1, generate_kek())
        store.delete(1)
        assert store.load(1) is None

    def test_delete_missing_noop(self) -> None:
        store = InMemoryKEKStore()
        store.delete(999)

    def test_active_version(self) -> None:
        store = InMemoryKEKStore()
        assert store.active_version() == 0
        store.save(1, generate_kek())
        assert store.active_version() == 1
        store.save(2, generate_kek())
        assert store.active_version() == 2

    def test_expiry_of(self) -> None:
        store = InMemoryKEKStore()
        store.save(1, generate_kek(), expires_at=100.0)
        assert store.expiry_of(1) == 100.0
        assert store.expiry_of(999) is None


class TestEnvelopeEncryptorEncryptDecrypt:
    @pytest.fixture
    def store(self) -> InMemoryKEKStore:
        s = InMemoryKEKStore()
        s.save(1, generate_kek())
        return s

    @pytest.fixture
    def encryptor(self, store: InMemoryKEKStore) -> EnvelopeEncryptor:
        return EnvelopeEncryptor(store)

    def test_encrypt_decrypt_round_trip(self, encryptor: EnvelopeEncryptor) -> None:
        plaintext = b"Hello, secure world!"
        blob = encryptor.encrypt(plaintext)
        assert blob.kek_version == 1
        assert blob.wrapped_dek
        assert blob.nonce
        assert blob.ciphertext
        assert blob.tag
        decrypted = encryptor.decrypt(blob)
        assert decrypted == plaintext

    def test_encrypt_no_active_kek(self) -> None:
        store = InMemoryKEKStore()
        encryptor = EnvelopeEncryptor(store)
        with pytest.raises(ValueError, match="no active KEK"):
            encryptor.encrypt(b"data")

    def test_encrypt_empty_plaintext(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"")
        decrypted = encryptor.decrypt(blob)
        assert decrypted == b""

    def test_encrypt_large_plaintext(self, encryptor: EnvelopeEncryptor) -> None:
        plaintext = b"x" * 10000
        blob = encryptor.encrypt(plaintext)
        decrypted = encryptor.decrypt(blob)
        assert decrypted == plaintext

    def test_decrypt_wrong_kek_version(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"data")
        blob = EncryptedBlob(
            kek_version=999,
            wrapped_dek=blob.wrapped_dek,
            nonce=blob.nonce,
            ciphertext=blob.ciphertext,
            tag=blob.tag,
        )
        with pytest.raises(KeyUnwrapError):
            encryptor.decrypt(blob)

    def test_decrypt_tampered_ciphertext(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"data")
        tampered = bytearray(blob.ciphertext)
        tampered[0] ^= 0xFF
        bad_blob = EncryptedBlob(
            kek_version=blob.kek_version,
            wrapped_dek=blob.wrapped_dek,
            nonce=blob.nonce,
            ciphertext=bytes(tampered),
            tag=blob.tag,
        )
        with pytest.raises(TamperDetected):
            encryptor.decrypt(bad_blob)

    def test_decrypt_tampered_tag(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"data")
        tampered = bytearray(blob.tag)
        tampered[0] ^= 0xFF
        bad_blob = EncryptedBlob(
            kek_version=blob.kek_version,
            wrapped_dek=blob.wrapped_dek,
            nonce=blob.nonce,
            ciphertext=blob.ciphertext,
            tag=bytes(tampered),
        )
        with pytest.raises(TamperDetected):
            encryptor.decrypt(bad_blob)

    def test_encrypt_expired_kek(self) -> None:
        import time

        store = InMemoryKEKStore()
        store.save(1, generate_kek(), expires_at=time.time() - 100)
        encryptor = EnvelopeEncryptor(store)
        with pytest.raises(ValueError, match="expired"):
            encryptor.encrypt(b"data")


class TestKeyRotation:
    @pytest.fixture
    def store(self) -> InMemoryKEKStore:
        s = InMemoryKEKStore()
        s.save(1, generate_kek())
        return s

    @pytest.fixture
    def encryptor(self, store: InMemoryKEKStore) -> EnvelopeEncryptor:
        return EnvelopeEncryptor(store)

    def test_rotate_kek(self, encryptor: EnvelopeEncryptor) -> None:
        result = encryptor.rotate_kek()
        assert result.success
        assert result.new_version == 2
        assert result.prior_version == 1
        assert result.rotated_at is not None

    def test_rotate_first_kek(self) -> None:
        store = InMemoryKEKStore()
        encryptor = EnvelopeEncryptor(store)
        result = encryptor.rotate_kek()
        assert result.success
        assert result.new_version == 1
        assert result.prior_version == 0

    def test_current_kek_version(self, encryptor: EnvelopeEncryptor) -> None:
        assert encryptor.current_kek_version() == 1

    def test_encrypt_after_rotation(self, encryptor: EnvelopeEncryptor) -> None:
        encryptor.rotate_kek()
        blob = encryptor.encrypt(b"data")
        assert blob.kek_version == 2

    def test_decrypt_old_blob_after_rotation(self, store: InMemoryKEKStore) -> None:
        encryptor = EnvelopeEncryptor(store)
        blob = encryptor.encrypt(b"data")
        assert blob.kek_version == 1
        encryptor.rotate_kek()
        decrypted = encryptor.decrypt(blob)
        assert decrypted == b"data"

    def test_rewrap(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"data")
        assert blob.kek_version == 1
        encryptor.rotate_kek()
        rewrapped = encryptor.rewrap(blob)
        assert rewrapped.kek_version == 2
        decrypted = encryptor.decrypt(rewrapped)
        assert decrypted == b"data"

    def test_rewrap_already_current(self, encryptor: EnvelopeEncryptor) -> None:
        blob = encryptor.encrypt(b"data")
        rewrapped = encryptor.rewrap(blob)
        assert rewrapped is blob

    def test_rewrap_batch(self, encryptor: EnvelopeEncryptor) -> None:
        b1 = encryptor.encrypt(b"a")
        b2 = encryptor.encrypt(b"b")
        encryptor.rotate_kek()
        result = encryptor.rewrap_batch([b1, b2])
        assert len(result) == 2
        assert all(r.kek_version == 2 for r in result)

    def test_rewrap_missing_old_kek(self, store: InMemoryKEKStore) -> None:
        encryptor = EnvelopeEncryptor(store)
        blob = encryptor.encrypt(b"data")
        encryptor.rotate_kek()
        store.delete(1)
        with pytest.raises(KeyUnwrapError):
            encryptor.rewrap(blob)


class TestKeyRotationResult:
    def test_defaults(self) -> None:
        r = KeyRotationResult(success=False, error="fail")
        assert not r.success
        assert r.new_version == 0
        assert r.prior_version == 0
        assert r.error == "fail"


class TestCreateEnvelopeEncryptor:
    def test_default_creates(self) -> None:
        e = create_envelope_encryptor()
        assert isinstance(e, EnvelopeEncryptor)

    def test_with_custom_store(self) -> None:
        store = InMemoryKEKStore()
        store.save(1, generate_kek())
        e = create_envelope_encryptor(kek_store=store)
        blob = e.encrypt(b"data")
        assert blob.kek_version == 1

    def test_with_env_kek(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import base64

        kek = generate_kek()
        monkeypatch.setenv("GLUDD_ENVELOPE_KEK_B64", base64.b64encode(kek).decode())
        e = create_envelope_encryptor()
        blob = e.encrypt(b"data")
        assert blob.kek_version == 1
        decrypted = e.decrypt(blob)
        assert decrypted == b"data"

    def test_without_env_kek_no_keys(self) -> None:
        e = create_envelope_encryptor()
        with pytest.raises(ValueError, match="no active KEK"):
            e.encrypt(b"data")


class TestTamperDetection:
    @pytest.fixture
    def encryption(self) -> tuple[InMemoryKEKStore, EnvelopeEncryptor]:
        store = InMemoryKEKStore()
        store.save(1, generate_kek())
        return store, EnvelopeEncryptor(store)

    def test_different_ciphertext_for_same_plaintext(
        self, encryption: tuple[InMemoryKEKStore, EnvelopeEncryptor]
    ) -> None:
        _, enc = encryption
        b1 = enc.encrypt(b"same")
        b2 = enc.encrypt(b"same")
        assert b1.ciphertext != b2.ciphertext
        assert b1.nonce != b2.nonce
        assert b1.wrapped_dek != b2.wrapped_dek

    def test_decrypt_wrong_ciphertext_length(self, encryption: tuple[InMemoryKEKStore, EnvelopeEncryptor]) -> None:
        _, enc = encryption
        blob = enc.encrypt(b"data")
        bad = EncryptedBlob(
            kek_version=blob.kek_version,
            wrapped_dek=blob.wrapped_dek,
            nonce=blob.nonce,
            ciphertext=b"short",
            tag=blob.tag,
        )
        with pytest.raises(TamperDetected):
            enc.decrypt(bad)
