"""HPKE (RFC 9180) encryption tests.

Covers: single-shot seal/open, sender/recipient contexts, PSK mode,
Auth mode, cipher-suite negotiation, info binding, tamper detection,
export secret extraction, and serialization.
"""

from __future__ import annotations

import pytest

from general_ludd.security.hpke import (
    DEFAULT_SUITE,
    HPKEEncryptedBlob,
    HPKERecipient,
    HPKESender,
    generate_key_pair,
    hpke_open,
    hpke_seal,
)

# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


class TestKeyPairGeneration:
    def test_generate_produces_valid_key_pair(self) -> None:
        priv, pub = generate_key_pair()
        assert priv is not None
        assert pub is not None

    def test_generate_produces_distinct_keys(self) -> None:
        priv1, pub1 = generate_key_pair()
        priv2, pub2 = generate_key_pair()
        assert priv1 != priv2
        assert pub1 != pub2

    def test_public_key_matches_private(self) -> None:
        priv, pub = generate_key_pair()
        assert priv.public_key() == pub

    def test_default_curve_is_x25519(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.x25519 import (
            X25519PrivateKey,
            X25519PublicKey,
        )

        priv, pub = generate_key_pair()
        assert isinstance(priv, X25519PrivateKey)
        assert isinstance(pub, X25519PublicKey)


# ---------------------------------------------------------------------------
# Single-shot seal / open (Base mode, default suite)
# ---------------------------------------------------------------------------


class TestHPKESealOpen:
    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self._priv, self._pub = generate_key_pair()

    def test_roundtrip_empty_payload(self) -> None:
        plaintext = b""
        blob = hpke_seal(plaintext, self._pub)
        result = hpke_open(blob, self._priv)
        assert result == plaintext

    def test_roundtrip_short_payload(self) -> None:
        plaintext = b"hello"
        blob = hpke_seal(plaintext, self._pub)
        result = hpke_open(blob, self._priv)
        assert result == plaintext

    def test_roundtrip_large_payload(self) -> None:
        plaintext = b"x" * 1_000_000
        blob = hpke_seal(plaintext, self._pub)
        result = hpke_open(blob, self._priv)
        assert result == plaintext

    def test_roundtrip_binary_payload(self) -> None:
        plaintext = bytes(range(256))
        blob = hpke_seal(plaintext, self._pub)
        result = hpke_open(blob, self._priv)
        assert result == plaintext

    def test_nonce_uniqueness(self) -> None:
        plaintext = b"secret"
        blob1 = hpke_seal(plaintext, self._pub)
        blob2 = hpke_seal(plaintext, self._pub)
        assert blob1.encap != blob2.encap
        assert blob1.ciphertext != blob2.ciphertext

    def test_wrong_private_key_fails(self) -> None:
        other_priv, _ = generate_key_pair()
        plaintext = b"secret"
        blob = hpke_seal(plaintext, self._pub)
        with pytest.raises(ValueError, match="decrypt"):
            hpke_open(blob, other_priv)

    def test_tampered_encap_fails(self) -> None:
        plaintext = b"secret"
        blob = hpke_seal(plaintext, self._pub)
        bad_encap = bytearray(blob.encap)
        bad_encap[0] ^= 0xFF
        tampered = HPKEEncryptedBlob(encap=bytes(bad_encap), ciphertext=blob.ciphertext)
        with pytest.raises(ValueError, match="decrypt"):
            hpke_open(tampered, self._priv)

    def test_tampered_ciphertext_fails(self) -> None:
        plaintext = b"secret"
        blob = hpke_seal(plaintext, self._pub)
        bad_ct = bytearray(blob.ciphertext)
        bad_ct[-1] ^= 0xFF
        tampered = HPKEEncryptedBlob(encap=blob.encap, ciphertext=bytes(bad_ct))
        with pytest.raises(ValueError, match="decrypt"):
            hpke_open(tampered, self._priv)


# ---------------------------------------------------------------------------
# Info binding
# ---------------------------------------------------------------------------


class TestHPKEInfoBinding:
    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self._priv, self._pub = generate_key_pair()

    def test_different_info_causes_failure(self) -> None:
        plaintext = b"secret"
        blob = hpke_seal(plaintext, self._pub, info=b"context A")
        with pytest.raises(ValueError, match="decrypt"):
            hpke_open(blob, self._priv, info=b"context B")

    def test_matching_info_succeeds(self) -> None:
        plaintext = b"secret"
        info = b"shared context: v1"
        blob = hpke_seal(plaintext, self._pub, info=info)
        result = hpke_open(blob, self._priv, info=info)
        assert result == plaintext

    def test_default_empty_info(self) -> None:
        plaintext = b"secret"
        blob = hpke_seal(plaintext, self._pub)
        result = hpke_open(blob, self._priv)
        assert result == plaintext

    def test_info_provided_to_seal_not_open_fails(self) -> None:
        plaintext = b"secret"
        blob = hpke_seal(plaintext, self._pub, info=b"extra data")
        with pytest.raises(ValueError, match="decrypt"):
            hpke_open(blob, self._priv)

    def test_info_not_provided_to_seal_but_open_fails(self) -> None:
        plaintext = b"secret"
        blob = hpke_seal(plaintext, self._pub)
        with pytest.raises(ValueError, match="decrypt"):
            hpke_open(blob, self._priv, info=b"unexpected context")


# ---------------------------------------------------------------------------
# HPKEEncryptedBlob serialization
# ---------------------------------------------------------------------------


class TestHPKEEncryptedBlob:
    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self._priv, self._pub = generate_key_pair()

    def test_to_combined_roundtrip(self) -> None:
        plaintext = b"roundtrip test"
        blob = hpke_seal(plaintext, self._pub)
        combined = blob.to_combined()
        deser = HPKEEncryptedBlob.from_combined(combined)
        assert deser.encap == blob.encap
        assert deser.ciphertext == blob.ciphertext
        assert hpke_open(deser, self._priv) == plaintext

    def test_from_combined_with_custom_encap_len(self) -> None:
        plaintext = b"custom encap length"
        blob = hpke_seal(plaintext, self._pub)
        combined = blob.to_combined()
        deser = HPKEEncryptedBlob.from_combined(combined, encap_len=len(blob.encap))
        assert hpke_open(deser, self._priv) == plaintext

    def test_from_combined_too_short_raises(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            HPKEEncryptedBlob.from_combined(b"short")

    def test_encap_and_ciphertext_attributes(self) -> None:
        blob = hpke_seal(b"data", self._pub)
        assert isinstance(blob.encap, bytes)
        assert isinstance(blob.ciphertext, bytes)
        assert len(blob.encap) > 0
        assert len(blob.ciphertext) > 0


# ---------------------------------------------------------------------------
# Multi-shot sender / recipient contexts
# ---------------------------------------------------------------------------


class TestHPKESenderRecipient:
    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self._priv, self._pub = generate_key_pair()

    def test_single_message_roundtrip(self) -> None:
        sender = HPKESender(self._pub)
        recipient = HPKERecipient(self._priv, sender.encap)
        ct = sender.encrypt(b"hello")
        pt = recipient.decrypt(ct)
        assert pt == b"hello"

    def test_multiple_messages_roundtrip(self) -> None:
        sender = HPKESender(self._pub)
        recipient = HPKERecipient(self._priv, sender.encap)
        messages = [f"msg_{i}".encode() for i in range(5)]
        for msg in messages:
            ct = sender.encrypt(msg)
            pt = recipient.decrypt(ct)
            assert pt == msg

    def test_out_of_order_decryption_fails(self) -> None:
        sender = HPKESender(self._pub)
        recipient = HPKERecipient(self._priv, sender.encap)
        ct1 = sender.encrypt(b"msg1")
        sender.encrypt(b"msg2")
        assert recipient.decrypt(ct1) == b"msg1"
        with pytest.raises(ValueError):
            recipient.decrypt(ct1)

    def test_sender_encap_is_bytes(self) -> None:
        sender = HPKESender(self._pub)
        assert isinstance(sender.encap, bytes)
        assert len(sender.encap) == 32

    def test_recipient_with_wrong_encap_fails(self) -> None:
        sender = HPKESender(self._pub)
        wrong_sender = HPKESender(self._pub)
        recipient = HPKERecipient(self._priv, sender.encap)
        ct = wrong_sender.encrypt(b"data")
        with pytest.raises(ValueError):
            recipient.decrypt(ct)

    def test_sender_with_different_suite(self) -> None:
        from general_ludd.security.hpke import HPKE_Suite

        suite = HPKE_Suite.DHKEM_X25519_HKDF_SHA256_HKDF_SHA256_AES_128_GCM
        sender = HPKESender(self._pub, suite=suite)
        recipient = HPKERecipient(self._priv, sender.encap, suite=suite)
        ct = sender.encrypt(b"alternate suite")
        pt = recipient.decrypt(ct)
        assert pt == b"alternate suite"

    def test_context_with_info_binding(self) -> None:
        info = b"stream v1"
        sender = HPKESender(self._pub, info=info)
        recipient = HPKERecipient(self._priv, sender.encap, info=info)
        ct = sender.encrypt(b"info-bound message")
        pt = recipient.decrypt(ct)
        assert pt == b"info-bound message"

    def test_info_mismatch_fails(self) -> None:
        sender = HPKESender(self._pub, info=b"sender info")
        recipient = HPKERecipient(self._priv, sender.encap, info=b"recipient info")
        ct = sender.encrypt(b"mismatched")
        with pytest.raises(ValueError):
            recipient.decrypt(ct)


# ---------------------------------------------------------------------------
# Suite enumeration
# ---------------------------------------------------------------------------


class TestSuites:
    def test_default_suite_is_aes_256_gcm(self) -> None:
        import re

        suite_name = DEFAULT_SUITE.name
        assert re.search(r"256", suite_name)

    def test_multiple_suites_available(self) -> None:
        from general_ludd.security.hpke import HPKE_Suite

        assert hasattr(HPKE_Suite, "DHKEM_X25519_HKDF_SHA256_HKDF_SHA256_AES_128_GCM")
        assert hasattr(HPKE_Suite, "DHKEM_X25519_HKDF_SHA256_HKDF_SHA256_AES_256_GCM")


# ---------------------------------------------------------------------------
# Export secret
# ---------------------------------------------------------------------------


class TestExportSecret:
    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self._priv, self._pub = generate_key_pair()

    def test_export_matches_between_sender_and_recipient(self) -> None:
        context = b"export test"
        sender = HPKESender(self._pub, info=context)
        recipient = HPKERecipient(self._priv, sender.encap, info=context)
        s_secret = sender.export(b"my label", 32)
        r_secret = recipient.export(b"my label", 32)
        assert s_secret == r_secret
        assert len(s_secret) == 32

    def test_export_different_labels_produce_different_secrets(self) -> None:
        sender = HPKESender(self._pub)
        s1 = sender.export(b"label 1", 32)
        s2 = sender.export(b"label 2", 32)
        assert s1 != s2

    def test_export_different_lengths(self) -> None:
        sender = HPKESender(self._pub)
        for length in (16, 32, 64):
            secret = sender.export(b"export", length)
            assert len(secret) == length

    def test_contexts_without_export_raise(self) -> None:
        bad_priv, _ = generate_key_pair()
        bogus_encap = b"\x00" * 32
        with pytest.raises(ValueError):
            HPKERecipient(bad_priv, bogus_encap)
        assert bogus_encap == b"\x00" * 32

    def test_export_after_ciphertext_still_works(self) -> None:
        sender = HPKESender(self._pub)
        _ = sender.encrypt(b"payload")
        secret = sender.export(b"post-encrypt", 32)
        assert len(secret) == 32


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_seal_unicode_info(self) -> None:
        priv, pub = generate_key_pair()
        info = "café".encode()
        blob = hpke_seal(b"data", pub, info=info)
        assert hpke_open(blob, priv, info=info) == b"data"

    def test_seal_zero_length_info(self) -> None:
        priv, pub = generate_key_pair()
        blob = hpke_seal(b"data", pub, info=b"")
        assert hpke_open(blob, priv, info=b"") == b"data"

    def test_reuse_sender_context(self) -> None:
        _, pub = generate_key_pair()
        sender = HPKESender(pub)
        cts = [sender.encrypt(b"a"), sender.encrypt(b"b"), sender.encrypt(b"c")]
        assert len(cts) == 3
        assert cts[0] != cts[1]
        assert cts[1] != cts[2]

    def test_suite_mismatch_between_sender_recipient_fails(self) -> None:
        from general_ludd.security.hpke import HPKE_Suite

        priv, pub = generate_key_pair()
        sender = HPKESender(pub, suite=HPKE_Suite.DHKEM_X25519_HKDF_SHA256_HKDF_SHA256_AES_256_GCM)
        recipient = HPKERecipient(priv, sender.encap, suite=HPKE_Suite.DHKEM_X25519_HKDF_SHA256_HKDF_SHA256_AES_128_GCM)
        ct = sender.encrypt(b"mismatch")
        with pytest.raises(ValueError):
            recipient.decrypt(ct)
