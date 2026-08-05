"""XMSS (RFC 8391) stateful hash-based signature tests."""

from __future__ import annotations

import os
import struct

import pytest

XMSS_HEIGHTS = [10, 16]
DIGESTS = ["SHA256", "SHA512", "SHAKE256", "SHAKE512"]


class TestXMSSKeyGeneration:
    def test_generate_produces_usable_keypair(self) -> None:
        from general_ludd.security.xmss import generate_xmss_keypair

        priv, pub = generate_xmss_keypair(height=10)
        assert isinstance(priv, bytes)
        assert isinstance(pub, bytes)
        assert len(priv) > 0
        assert len(pub) > 0
        assert priv != pub

    def test_generate_different_height_produces_different_sizes(self) -> None:
        from general_ludd.security.xmss import generate_xmss_keypair

        _, pub10 = generate_xmss_keypair(height=10)
        _, pub16 = generate_xmss_keypair(height=16)
        assert pub10 != pub16

    def test_generate_unique_keypairs(self) -> None:
        from general_ludd.security.xmss import generate_xmss_keypair

        _, pub1 = generate_xmss_keypair(height=10)
        _, pub2 = generate_xmss_keypair(height=10)
        assert pub1 != pub2

    @pytest.mark.parametrize("height", XMSS_HEIGHTS)
    def test_generate_across_heights(self, height: int) -> None:
        from general_ludd.security.xmss import generate_xmss_keypair

        priv, pub = generate_xmss_keypair(height=height)
        assert len(priv) > 0
        assert len(pub) > 0

    @pytest.mark.parametrize("digest_name", DIGESTS)
    def test_generate_across_digests(self, digest_name: str) -> None:
        from general_ludd.security.xmss import generate_xmss_keypair

        priv, pub = generate_xmss_keypair(height=10, digest_algorithm=digest_name)
        assert len(priv) > 0
        assert len(pub) > 0

    def test_generate_rejects_invalid_height(self) -> None:
        from general_ludd.security.xmss import XMSSError, generate_xmss_keypair

        with pytest.raises(XMSSError):
            generate_xmss_keypair(height=0)

    def test_generate_rejects_invalid_digest(self) -> None:
        from general_ludd.security.xmss import XMSSError, generate_xmss_keypair

        with pytest.raises(XMSSError):
            generate_xmss_keypair(height=10, digest_algorithm="MD5")


class TestXMSSSignVerify:
    def test_sign_verify_roundtrip(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        sig, updated_priv = xmss_sign(priv, b"hello world")
        assert len(sig) > 0
        assert updated_priv != priv
        assert xmss_verify(pub, b"hello world", sig)

    def test_sign_different_messages(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
        )

        priv, _ = generate_xmss_keypair(height=10)
        sig1, priv1 = xmss_sign(priv, b"message one")
        sig2, priv2 = xmss_sign(priv1, b"message two")
        assert sig1 != sig2
        assert priv2 != priv1

    def test_verify_rejects_wrong_message(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        sig, _ = xmss_sign(priv, b"original message")
        assert not xmss_verify(pub, b"wrong message", sig)

    def test_verify_rejects_wrong_public_key(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, _ = generate_xmss_keypair(height=10)
        _, wrong_pub = generate_xmss_keypair(height=10)
        sig, _ = xmss_sign(priv, b"test")
        assert not xmss_verify(wrong_pub, b"test", sig)

    def test_verify_rejects_tampered_signature(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        sig, _ = xmss_sign(priv, b"test")
        tampered = bytearray(sig)
        tampered[0] ^= 0x01
        assert not xmss_verify(pub, b"test", bytes(tampered))

    def test_sign_accepts_string_message(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        sig, _ = xmss_sign(priv, "string message")
        assert xmss_verify(pub, "string message", sig)

    def test_sign_empty_message(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        sig, _ = xmss_sign(priv, b"")
        assert xmss_verify(pub, b"", sig)

    def test_sign_large_message(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        large = os.urandom(65536)
        sig, _ = xmss_sign(priv, large)
        assert xmss_verify(pub, large, sig)

    def test_sign_verify_multiple_signatures(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        for i in range(100):
            msg = f"message {i}".encode()
            sig, priv = xmss_sign(priv, msg)
            assert xmss_verify(pub, msg, sig)


class TestXMSSStateTracking:
    def test_signature_count_starts_at_zero(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_signature_count,
        )

        priv, _ = generate_xmss_keypair(height=10)
        assert xmss_signature_count(priv) == 0

    def test_signature_count_increments(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_signature_count,
        )

        priv, _ = generate_xmss_keypair(height=10)
        for i in range(1, 6):
            _, priv = xmss_sign(priv, f"msg {i}".encode())
            assert xmss_signature_count(priv) == i

    def test_remaining_signatures_decreases(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_remaining_signatures,
            xmss_sign,
        )

        height = 10
        max_sigs = 1 << height
        priv, _ = generate_xmss_keypair(height=height)
        assert xmss_remaining_signatures(priv, height=height) == max_sigs

        _, priv = xmss_sign(priv, b"test")
        assert xmss_remaining_signatures(priv, height=height) == max_sigs - 1

    def test_sign_exhaustion_raises(self) -> None:
        from general_ludd.security.xmss import (
            XMSSError,
            generate_xmss_keypair,
            xmss_sign,
        )

        height = 10
        max_sigs = 1 << height
        priv, _ = generate_xmss_keypair(height=height)
        for i in range(max_sigs):
            _, priv = xmss_sign(priv, f"sig {i}".encode())
        with pytest.raises(XMSSError, match="exhausted"):
            xmss_sign(priv, b"one too many")

    def test_remaining_signatures_reported_before_exhaustion(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_remaining_signatures,
            xmss_sign,
        )

        height = 10
        max_sigs = 1 << height
        priv, _ = generate_xmss_keypair(height=height)
        for i in range(max_sigs - 1):
            _, priv = xmss_sign(priv, f"sig {i}".encode())
        assert xmss_remaining_signatures(priv, height=height) == 1
        _, priv = xmss_sign(priv, b"last one")
        assert xmss_remaining_signatures(priv, height=height) == 0


class TestXMSSSerialization:
    def test_private_key_roundtrip(self) -> None:
        from general_ludd.security.xmss import (
            deserialize_private_key,
            generate_xmss_keypair,
            serialize_private_key,
        )

        priv, _ = generate_xmss_keypair(height=10)
        serialized = serialize_private_key(priv)
        deserialized = deserialize_private_key(serialized)
        assert isinstance(serialized, bytes)
        assert isinstance(deserialized, bytes)
        assert deserialized == priv

    def test_public_key_roundtrip(self) -> None:
        from general_ludd.security.xmss import (
            deserialize_public_key,
            generate_xmss_keypair,
            serialize_public_key,
        )

        _, pub = generate_xmss_keypair(height=10)
        serialized = serialize_public_key(pub)
        deserialized = deserialize_public_key(serialized)
        assert isinstance(serialized, bytes)
        assert isinstance(deserialized, bytes)
        assert deserialized == pub

    def test_serialized_private_key_preserves_state(self) -> None:
        from general_ludd.security.xmss import (
            deserialize_private_key,
            generate_xmss_keypair,
            serialize_private_key,
            xmss_sign,
            xmss_signature_count,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        sig1, priv = xmss_sign(priv, b"msg 1")
        sig2, priv = xmss_sign(priv, b"msg 2")
        assert xmss_signature_count(priv) == 2

        serialized = serialize_private_key(priv)
        restored = deserialize_private_key(serialized)
        assert xmss_signature_count(restored) == 2

        sig3, _ = xmss_sign(restored, b"msg 3")
        assert xmss_verify(pub, b"msg 1", sig1)
        assert xmss_verify(pub, b"msg 2", sig2)
        assert xmss_verify(pub, b"msg 3", sig3)

    def test_deserialize_rejects_bad_private_key(self) -> None:
        from general_ludd.security.xmss import XMSSError, deserialize_private_key

        with pytest.raises(XMSSError):
            deserialize_private_key(b"not a valid key")

    def test_deserialize_rejects_bad_public_key(self) -> None:
        from general_ludd.security.xmss import XMSSError, deserialize_public_key

        with pytest.raises(XMSSError):
            deserialize_public_key(b"not a valid public key")

    def test_serialize_then_sign_after_restore(self) -> None:
        from general_ludd.security.xmss import (
            deserialize_private_key,
            generate_xmss_keypair,
            serialize_private_key,
            xmss_sign,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        for i in range(5):
            _, priv = xmss_sign(priv, f"pre-serialize {i}".encode())

        serialized = serialize_private_key(priv)
        restored = deserialize_private_key(serialized)

        for i in range(10):
            sig, restored = xmss_sign(restored, f"post-serialize {i}".encode())
            assert xmss_verify(pub, f"post-serialize {i}".encode(), sig)

    def test_serialize_private_key_length(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            serialize_private_key,
        )

        for height in XMSS_HEIGHTS:
            priv, _ = generate_xmss_keypair(height=height)
            ser = serialize_private_key(priv)
            assert len(ser) > 0

    def test_serialize_public_key_length(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            serialize_public_key,
        )

        for height in XMSS_HEIGHTS:
            _, pub = generate_xmss_keypair(height=height)
            ser = serialize_public_key(pub)
            assert len(ser) > 0


class TestXMSSSignatureSizes:
    def test_signature_size_increases_with_height(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
        )

        priv10, _ = generate_xmss_keypair(height=10)
        priv16, _ = generate_xmss_keypair(height=16)
        priv20, _ = generate_xmss_keypair(height=20)

        sig10, _ = xmss_sign(priv10, b"test")
        sig16, _ = xmss_sign(priv16, b"test")
        sig20, _ = xmss_sign(priv20, b"test")

        assert len(sig10) > 0
        assert len(sig16) > len(sig10)
        assert len(sig20) > len(sig16)

    def test_signature_not_empty(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
        )

        for height in XMSS_HEIGHTS:
            for digest_name in DIGESTS:
                priv, _ = generate_xmss_keypair(height=height, digest_algorithm=digest_name)
                sig, _ = xmss_sign(priv, b"test")
                assert len(sig) > 0, f"height={height}, digest={digest_name}"


class TestXMSSVerifyEdgeCases:
    def test_verify_rejects_empty_signature(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_verify,
        )

        _, pub = generate_xmss_keypair(height=10)
        assert not xmss_verify(pub, b"test", b"")

    def test_verify_rejects_short_signature(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_verify,
        )

        _, pub = generate_xmss_keypair(height=10)
        assert not xmss_verify(pub, b"test", b"\x00" * 32)

    def test_verify_rejects_truncated_signature_middle(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        sig, _ = xmss_sign(priv, b"test")
        truncated = sig[: len(sig) // 2]
        assert not xmss_verify(pub, b"test", truncated)

    def test_verify_rejects_tampered_signature_mid(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        sig, _ = xmss_sign(priv, b"test")
        tampered = bytearray(sig)
        tampered[len(tampered) // 2] ^= 0xFF
        assert not xmss_verify(pub, b"test", bytes(tampered))

    def test_verify_rejects_null_public_key(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, _ = generate_xmss_keypair(height=10)
        sig, _ = xmss_sign(priv, b"test")
        assert not xmss_verify(b"\x00" * 64, b"test", sig)

    def test_verify_different_height_public_key(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, _ = generate_xmss_keypair(height=10)
        _, pub16 = generate_xmss_keypair(height=16)
        sig, _ = xmss_sign(priv, b"test")
        assert not xmss_verify(pub16, b"test", sig)

    def test_verify_different_digest_public_key(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, _ = generate_xmss_keypair(height=10, digest_algorithm="SHA256")
        _, pub512 = generate_xmss_keypair(height=10, digest_algorithm="SHA512")
        sig, _ = xmss_sign(priv, b"test")
        assert not xmss_verify(pub512, b"test", sig)


class TestXMSSHighLevelWorkflow:
    def test_full_signer_verifier_workflow(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        signer_priv, verifier_pub = generate_xmss_keypair(height=16)
        messages = [f"message {i}".encode() for i in range(200)]

        signatures: list[bytes] = []
        for msg in messages:
            sig, signer_priv = xmss_sign(signer_priv, msg)
            signatures.append(sig)

        for msg, sig in zip(messages, signatures, strict=False):
            assert xmss_verify(verifier_pub, msg, sig)

    def test_sign_with_larger_height(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        height = 20
        priv, pub = generate_xmss_keypair(height=height)
        for i in range(50):
            msg = f"h20 msg {i}".encode()
            sig, priv = xmss_sign(priv, msg)
            assert xmss_verify(pub, msg, sig)

    def test_unicode_message(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        msg = "caf\u00e9 r\u00e9sum\u00e9 na\u00efve \U0001f600"
        sig, _ = xmss_sign(priv, msg)
        assert xmss_verify(pub, msg, sig)

    def test_sign_then_serialize_then_continue_signing(self) -> None:
        from general_ludd.security.xmss import (
            deserialize_private_key,
            generate_xmss_keypair,
            serialize_private_key,
            xmss_sign,
            xmss_signature_count,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        for i in range(3):
            _, priv = xmss_sign(priv, f"before {i}".encode())

        serialized = serialize_private_key(priv)
        restored = deserialize_private_key(serialized)
        assert xmss_signature_count(restored) == 3

        for i in range(5):
            sig, restored = xmss_sign(restored, f"after {i}".encode())
            assert xmss_verify(pub, f"after {i}".encode(), sig)

    def test_sign_exhaustion_low_height(self) -> None:
        from general_ludd.security.xmss import (
            XMSSError,
            generate_xmss_keypair,
            xmss_sign,
        )

        height = 4
        max_sigs = 1 << height
        priv, _ = generate_xmss_keypair(height=height)
        for i in range(max_sigs):
            _, priv = xmss_sign(priv, f"sig {i}".encode())
        with pytest.raises(XMSSError, match="exhausted"):
            xmss_sign(priv, b"one too many")

    def test_sign_exhaustion_high_height_expects_many(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_remaining_signatures,
        )

        priv, _ = generate_xmss_keypair(height=20)
        remaining = xmss_remaining_signatures(priv, height=20)
        expected = 1 << 20
        assert remaining == expected


class TestXMSSBinaryFormats:
    def test_signature_is_verifiable_after_many_signatures(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        for i in range(200):
            msg = struct.pack(">I", i)
            sig, priv = xmss_sign(priv, msg)
            assert xmss_verify(pub, msg, sig)

    def test_signature_uniqueness_across_same_message(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
            xmss_verify,
        )

        priv, pub = generate_xmss_keypair(height=10)
        sigs = []
        for _ in range(5):
            sig, priv = xmss_sign(priv, b"repeated message")
            sigs.append(sig)
            assert xmss_verify(pub, b"repeated message", sig)

        assert len(set(sigs)) == len(sigs)

    def test_signature_deterministic_only_with_same_state(self) -> None:
        from general_ludd.security.xmss import (
            generate_xmss_keypair,
            xmss_sign,
        )

        priv1, _ = generate_xmss_keypair(height=10)
        priv2, _ = generate_xmss_keypair(height=10)

        sig1, _ = xmss_sign(priv1, b"test")
        sig2, _ = xmss_sign(priv2, b"test")
        assert sig1 != sig2
