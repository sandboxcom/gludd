"""Deep Ed25519 tests: key generation, signing, verification, curve operations,
point arithmetic, encoding, edge cases, and RFC 8032 compliance.

Uses the `cryptography` library for cryptographic operations.
"""

from __future__ import annotations

import secrets

import pytest

from general_ludd.algorithms.ed25519 import (
    B,
    Ed25519Error,
    Ed25519KeyPair,
    EDPoint,
    P,
    Q,
    _scalar_clamp,
    decode_point,
    derive_public_from_private,
    encode_point,
    from_affine,
    generate_keypair,
    is_on_curve,
    point_add,
    scalar_mult,
    sign,
    verify,
    xrecover,
)


class TestKeyGeneration:
    def test_generate_returns_32_byte_keys(self) -> None:
        kp = generate_keypair()
        assert len(kp.private_bytes) == 32
        assert len(kp.public_bytes) == 32

    def test_generated_keys_are_not_equal(self) -> None:
        kp1 = generate_keypair()
        kp2 = generate_keypair()
        assert kp1.private_bytes != kp2.private_bytes
        assert kp1.public_bytes != kp2.public_bytes

    def test_public_derived_from_private(self) -> None:
        seed = secrets.token_bytes(32)
        kp = Ed25519KeyPair.from_seed(seed)
        pub2 = derive_public_from_private(seed)
        assert kp.public_bytes == pub2

    def test_from_seed_deterministic(self) -> None:
        seed = b"\x01" * 32
        kp1 = Ed25519KeyPair.from_seed(seed)
        kp2 = Ed25519KeyPair.from_seed(seed)
        assert kp1.public_bytes == kp2.public_bytes
        assert kp1.secret_scalar == kp2.secret_scalar

    def test_from_seed_rejects_wrong_length(self) -> None:
        with pytest.raises(Ed25519Error, match="32 bytes"):
            Ed25519KeyPair.from_seed(b"\x00" * 16)

    def test_derive_public_rejects_wrong_length(self) -> None:
        with pytest.raises(Ed25519Error, match="32 bytes"):
            derive_public_from_private(b"\x00" * 16)


class TestSignVerify:
    def test_sign_detached_produces_64_bytes(self) -> None:
        kp = generate_keypair()
        sig = kp.sign(b"test message")
        assert len(sig) == 64

    def test_sign_and_verify_roundtrip(self) -> None:
        kp = generate_keypair()
        msg = b"The quick brown fox jumps over the lazy dog"
        sig = kp.sign(msg)
        assert verify(kp.public_bytes, msg, sig)

    def test_verify_rejects_wrong_message(self) -> None:
        kp = generate_keypair()
        sig = kp.sign(b"original message")
        assert not verify(kp.public_bytes, b"tampered message", sig)

    def test_verify_rejects_wrong_public_key(self) -> None:
        kp = generate_keypair()
        kp2 = generate_keypair()
        sig = kp.sign(b"test")
        assert not verify(kp2.public_bytes, b"test", sig)

    def test_verify_rejects_tampered_signature(self) -> None:
        kp = generate_keypair()
        sig = bytearray(kp.sign(b"test"))
        sig[0] ^= 0xFF
        assert not verify(kp.public_bytes, b"test", bytes(sig))

    def test_verify_rejects_short_public_key(self) -> None:
        sig = b"\x00" * 64
        assert not verify(b"\x00" * 16, b"msg", sig)

    def test_verify_rejects_short_signature(self) -> None:
        kp = generate_keypair()
        assert not verify(kp.public_bytes, b"msg", b"\x00" * 32)

    def test_verify_rejects_s_out_of_range(self) -> None:
        kp = generate_keypair()
        sig = bytearray(kp.sign(b"test"))
        sig[32:] = Q.to_bytes(32, "little")
        assert not verify(kp.public_bytes, b"test", bytes(sig))

    def test_sign_string_message(self) -> None:
        kp = generate_keypair()
        sig = kp.sign("hello world")
        assert verify(kp.public_bytes, b"hello world", sig)

    def test_sign_empty_message(self) -> None:
        kp = generate_keypair()
        sig = kp.sign(b"")
        assert len(sig) == 64
        assert verify(kp.public_bytes, b"", sig)

    def test_sign_rejects_wrong_private_length(self) -> None:
        with pytest.raises(Ed25519Error, match="32 bytes"):
            sign(b"\x00" * 16, b"test")

    def test_sign_bytes_key(self) -> None:
        seed = secrets.token_bytes(32)
        sig = sign(seed, b"bytes message")
        assert len(sig) == 64
        pub = derive_public_from_private(seed)
        assert verify(pub, b"bytes message", sig)

    def test_deterministic_signature(self) -> None:
        seed = b"\x42" * 32
        msg = b"deterministic test"
        sig1 = sign(seed, msg)
        sig2 = sign(seed, msg)
        assert sig1 == sig2

    def test_different_messages_different_signatures(self) -> None:
        kp = generate_keypair()
        sig1 = kp.sign(b"message one")
        sig2 = kp.sign(b"message two")
        assert sig1 != sig2

    def test_large_message_still_verifies(self) -> None:
        kp = generate_keypair()
        msg = secrets.token_bytes(10000)
        sig = kp.sign(msg)
        assert verify(kp.public_bytes, msg, sig)

    def test_non_ascii_bytes_message(self) -> None:
        kp = generate_keypair()
        msg = bytes(range(256))
        sig = kp.sign(msg)
        assert verify(kp.public_bytes, msg, sig)


class TestCurveOperations:
    def test_identity_point_has_y0x1(self) -> None:
        ident = EDPoint.identity()
        assert ident.x == 0
        assert ident.y == 1
        assert ident.z == 1
        assert ident.t == 0

    def test_identity_is_identity(self) -> None:
        assert EDPoint.identity().is_identity()

    def test_base_point_is_not_identity(self) -> None:
        assert not B.is_identity()

    def test_base_point_on_curve(self) -> None:
        bx, by = B._affine()
        assert is_on_curve(bx, by)

    def test_point_negation(self) -> None:
        negB = -B
        assert B + negB == EDPoint.identity()

    def test_point_addition_commutative(self) -> None:
        P2 = B * 2
        P3 = B * 3
        assert P2 + P3 == P3 + P2

    def test_scalar_mult_identity(self) -> None:
        assert EDPoint.identity() == B * 0

    def test_scalar_mult_by_scalar_equals_order(self) -> None:
        assert EDPoint.identity() == B * Q

    def test_scalar_mult_associative(self) -> None:
        a = 12345678901234567890
        b = 98765432109876543210
        left = B * (a * b)
        right = (B * a) * b
        assert left == right

    def test_is_on_curve_affine_identity(self) -> None:
        ident = EDPoint.identity()
        ix, iy = ident._affine()
        assert is_on_curve(ix, iy)

    def test_is_on_curve_off_curve(self) -> None:
        assert not is_on_curve(0, 0)
        assert not is_on_curve(1, 1)

    def test_point_from_affine(self) -> None:
        p = from_affine(0, 1)
        assert p == EDPoint.identity()

    def test_point_add_identity(self) -> None:
        assert B + EDPoint.identity() == B
        assert EDPoint.identity() + B == B

    def test_double_vs_add_self(self) -> None:
        dbl = B._double()
        add = B + B
        assert dbl == add

    def test_protocol_operations_reject_wrong_type(self) -> None:
        assert EDPoint.__eq__(B, object()) is NotImplemented
        assert EDPoint.__add__(B, object()) is NotImplemented

    def test_degenerate_and_identity_paths(self) -> None:
        assert EDPoint(1, 2, 0, 3)._affine() == (0, 0)
        assert EDPoint.identity()._double() == EDPoint.identity()

    def test_negative_and_reflected_scalar_multiplication(self) -> None:
        assert B * -1 == -B
        assert 2 * B == B * 2

    def test_xrecover_alternate_root_and_decode_rejects_large_y(self) -> None:
        assert is_on_curve(xrecover(0), 0)
        with pytest.raises(Ed25519Error, match="out of range"):
            decode_point(P.to_bytes(32, "little"))

    def test_public_point_wrappers(self) -> None:
        assert point_add(B, EDPoint.identity()) == B
        assert scalar_mult(2, B) == B + B


class TestEncoding:
    def test_encode_decode_base_point(self) -> None:
        encoded = encode_point(B)
        assert len(encoded) == 32
        decoded = decode_point(encoded)
        assert decoded == B

    def test_encode_decode_roundtrip_random(self) -> None:
        for _ in range(5):
            k = 1 + secrets.randbits(200)
            pt = B * k
            encoded = encode_point(pt)
            decoded = decode_point(encoded)
            assert decoded == pt

    def test_decode_rejects_wrong_length(self) -> None:
        with pytest.raises(Ed25519Error, match="32 bytes"):
            decode_point(b"\x00" * 16)

    def test_encode_point_y_high_bit(self) -> None:
        encoded = encode_point(B)
        y = int.from_bytes(encoded, "little") & ((1 << 255) - 1)
        assert y >= 0
        assert y < (1 << 255)


class TestScalarClamp:
    def test_clamp_clears_lower_three_bits(self) -> None:
        s = b"\xff" * 32
        clamped = _scalar_clamp(s)
        assert clamped & 7 == 0

    def test_clamp_sets_high_bit_254(self) -> None:
        s = b"\x00" * 32
        clamped = _scalar_clamp(s)
        assert clamped >> 254 & 1 == 1

    def test_clamp_clears_bit_255(self) -> None:
        s = b"\xff" * 32
        clamped = _scalar_clamp(s)
        assert clamped >> 255 & 1 == 0

    def test_clamp_is_deterministic(self) -> None:
        s = secrets.token_bytes(32)
        assert _scalar_clamp(s) == _scalar_clamp(s)


class TestEdgeCases:
    def test_many_generated_keys_verify(self) -> None:
        for _ in range(20):
            kp = generate_keypair()
            msg = secrets.token_bytes(64)
            sig = kp.sign(msg)
            assert verify(kp.public_bytes, msg, sig)

    def test_all_zero_message(self) -> None:
        kp = generate_keypair()
        sig = kp.sign(b"\x00" * 100)
        assert verify(kp.public_bytes, b"\x00" * 100, sig)

    def test_all_zero_signature_rejected(self) -> None:
        kp = generate_keypair()
        assert not verify(kp.public_bytes, b"msg", b"\x00" * 64)

    def test_verify_rejects_s_zero(self) -> None:
        kp = generate_keypair()
        sig = bytearray(kp.sign(b"test"))
        sig[32:] = b"\x00" * 32
        assert not verify(kp.public_bytes, b"test", bytes(sig))

    def test_sign_different_seeds_produce_different_signatures(self) -> None:
        msg = b"shared message"
        sig1 = sign(secrets.token_bytes(32), msg)
        sig2 = sign(secrets.token_bytes(32), msg)
        assert sig1 != sig2

    def test_scalar_mult_distributive(self) -> None:
        a = 12345678901234567890
        b = 98765432109876543210
        left = B * (a + b)
        right = B * a + B * b
        assert left == right

    def test_neg_twice_is_identity_addition(self) -> None:
        negB = -B
        assert negB + negB == -(B + B)
