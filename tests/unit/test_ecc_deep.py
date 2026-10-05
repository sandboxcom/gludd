"""Deep tests for elliptic curve cryptography: point ops, ECDH, ECDSA."""

from __future__ import annotations

import hashlib

import pytest

from general_ludd.algorithms.ecc import (
    SECP256K1,
    ECCError,
    ECCurve,
    ECPoint,
    _crypto_curve,
    _hash_alg_for_msg,
    _nonce_rfc6979,
    _public_key_from_point,
    ecdh_shared_secret,
    ecdsa_sign,
    ecdsa_verify,
    generate_keypair,
)


@pytest.fixture
def G() -> ECPoint:
    return ECPoint(SECP256K1.Gx, SECP256K1.Gy, SECP256K1)


@pytest.fixture
def identity() -> ECPoint:
    return SECP256K1.identity


class TestPointOnCurve:
    def test_G_on_curve(self, G: ECPoint) -> None:
        assert G.on_curve()

    def test_identity_on_curve(self, identity: ECPoint) -> None:
        assert identity.on_curve()

    def test_random_point_off_curve(self) -> None:
        p = ECPoint(42, 42, SECP256K1)
        assert not p.on_curve()

    def test_point_on_small_curve(self) -> None:
        curve = ECCurve(p=17, a=2, b=2, Gx=5, Gy=1, n=19)
        p = ECPoint(5, 1, curve)
        assert p.on_curve()
        p2 = ECPoint(6, 3, curve)
        assert p2.on_curve()


class TestIdentityAndEquality:
    def test_identity_is_identity(self, identity: ECPoint) -> None:
        assert identity.is_identity

    def test_G_not_identity(self, G: ECPoint) -> None:
        assert not G.is_identity

    def test_identity_equal_self(self) -> None:
        assert SECP256K1.identity == SECP256K1.identity

    def test_point_not_equal_identity(self, G: ECPoint) -> None:
        assert SECP256K1.identity != G

    def test_identity_constructor_rejects_mixed(self) -> None:
        with pytest.raises(ECCError, match="both x and y"):
            ECPoint(1, None, SECP256K1)

    def test_identity_coordinates_fail_closed(self, identity: ECPoint) -> None:
        with pytest.raises(ECCError, match="no finite coordinates"):
            identity._coordinates()

    def test_equality_protocol_and_curve_identity(self, G: ECPoint) -> None:
        other_curve = ECCurve(
            p=17,
            a=2,
            b=2,
            Gx=5,
            Gy=1,
            n=19,
        )

        assert G.__eq__(object()) is NotImplemented
        assert ECPoint(5, 1, other_curve) != G
        assert SECP256K1.identity != G


class TestNegation:
    def test_negation_modp(self, G: ECPoint) -> None:
        neg_G = -G
        assert G.y is not None
        assert neg_G.x == G.x
        assert neg_G.y == (-G.y) % SECP256K1.p

    def test_neg_identity(self, identity: ECPoint) -> None:
        assert -identity == identity

    def test_double_negation(self, G: ECPoint) -> None:
        neg_G = -G
        assert -(neg_G) == G


class TestPointAddition:
    def test_add_identity_left(self, G: ECPoint, identity: ECPoint) -> None:
        assert identity + G == G

    def test_add_identity_right(self, G: ECPoint, identity: ECPoint) -> None:
        assert G + identity == G

    def test_commutativity(self, G: ECPoint) -> None:
        P2 = G * 2
        P3 = G * 3
        assert P2 + P3 == P3 + P2

    def test_associativity(self, G: ECPoint) -> None:
        P2 = G * 2
        P3 = G * 3
        P4 = G * 4
        assert (P2 + P3) + P4 == P2 + (P3 + P4)

    def test_G_plus_neg_G_is_identity(self, G: ECPoint) -> None:
        assert SECP256K1.identity == G + (-G)

    def test_add_different_curves_raises(self, G: ECPoint) -> None:
        other_curve = ECCurve(p=17, a=2, b=3, Gx=5, Gy=1, n=19)
        Q = ECPoint(5, 1, other_curve)
        with pytest.raises(ECCError, match="different curves"):
            _ = G + Q

    def test_point_operator_protocol_rejects_unrelated_types(self, G: ECPoint) -> None:
        assert G.__add__(object()) is NotImplemented
        assert G.__rmul__(object()) is NotImplemented


class TestScalarMultiplication:
    def test_multiply_by_one(self, G: ECPoint) -> None:
        assert G * 1 == G

    def test_multiply_by_zero(self, G: ECPoint) -> None:
        assert (G * 0).is_identity

    def test_multiply_by_order(self, G: ECPoint) -> None:
        assert (G * SECP256K1.n).is_identity

    def test_double_vs_add_self(self, G: ECPoint) -> None:
        assert G * 2 == G + G

    def test_additive_property(self, G: ECPoint) -> None:
        assert G * 7 == G * 3 + G * 4

    def test_negative_scalar(self, G: ECPoint) -> None:
        assert G * (-1) == -G

    def test_rmul(self, G: ECPoint) -> None:
        assert 5 * G == G * 5

    def test_multiply_large_random(self, G: ECPoint) -> None:
        import secrets

        k = secrets.randbelow(SECP256K1.n - 1) + 1
        P = G * k
        assert P.on_curve()
        assert not P.is_identity


class TestECDH:
    def test_shared_secret_match(self) -> None:
        alice = generate_keypair()
        bob = generate_keypair()
        s1 = ecdh_shared_secret(alice.private, bob.public)
        s2 = ecdh_shared_secret(bob.private, alice.public)
        assert s1 == s2

    def test_shared_secret_nonzero(self) -> None:
        alice = generate_keypair()
        bob = generate_keypair()
        secret = ecdh_shared_secret(alice.private, bob.public)
        assert len(secret) > 0

    def test_custom_curve_exchange_and_identity_rejection(self) -> None:
        curve = ECCurve(p=17, a=2, b=2, Gx=5, Gy=1, n=19)
        alice = generate_keypair(curve)
        bob = generate_keypair(curve)

        assert ecdh_shared_secret(alice.private, bob.public) == ecdh_shared_secret(
            bob.private,
            alice.public,
        )
        with pytest.raises(ECCError, match="identity"):
            ecdh_shared_secret(alice.private, curve.identity)


class TestECDSA:
    def test_sign_and_verify(self) -> None:
        key = generate_keypair()
        msg = b"hello elliptic world"
        h = hashlib.sha256(msg).digest()
        sig = ecdsa_sign(h, key.private)
        assert ecdsa_verify(h, sig, key.public)

    def test_wrong_message_fails(self) -> None:
        key = generate_keypair()
        sig = ecdsa_sign(hashlib.sha256(b"msg").digest(), key.private)
        assert not ecdsa_verify(hashlib.sha256(b"tampered").digest(), sig, key.public)

    def test_wrong_key_fails(self) -> None:
        alice = generate_keypair()
        bob = generate_keypair()
        h = hashlib.sha256(b"msg").digest()
        sig = ecdsa_sign(h, alice.private)
        assert not ecdsa_verify(h, sig, bob.public)

    def test_invalid_signature_params(self) -> None:
        key = generate_keypair()
        h = hashlib.sha256(b"msg").digest()
        assert not ecdsa_verify(h, (0, 1), key.public)
        assert not ecdsa_verify(h, (1, 0), key.public)
        n = SECP256K1.n
        assert not ecdsa_verify(h, (n, 1), key.public)

    def test_rfc6979_deterministic(self) -> None:
        h = hashlib.sha256(b"test").digest()
        private = 0x519B423D715F8B581F4FA8EE59F4771A5B44C8130B4E3EACCA54A56DDA72B464
        k1 = _nonce_rfc6979(h, private, SECP256K1)
        k2 = _nonce_rfc6979(h, private, SECP256K1)
        assert k1 == k2

    def test_multiple_signatures_all_verify(self) -> None:
        key = generate_keypair()
        h = hashlib.sha256(b"multiple signatures test").digest()
        sig1 = ecdsa_sign(h, key.private)
        sig2 = ecdsa_sign(h, key.private)
        assert ecdsa_verify(h, sig1, key.public)
        assert ecdsa_verify(h, sig2, key.public)

    @pytest.mark.parametrize(
        ("digest_size", "algorithm_name"),
        [(32, "sha256"), (48, "sha384"), (64, "sha512")],
    )
    def test_hash_algorithm_matches_digest_size(
        self,
        digest_size: int,
        algorithm_name: str,
    ) -> None:
        assert _hash_alg_for_msg(b"\x00" * digest_size).name == algorithm_name

    def test_custom_curve_signature_round_trip(self) -> None:
        curve = ECCurve(p=17, a=2, b=2, Gx=5, Gy=1, n=19)
        key = generate_keypair(curve)
        digest = hashlib.sha256(b"custom curve").digest()
        signature = ecdsa_sign(digest, key.private, curve)

        assert ecdsa_verify(digest, signature, key.public)


class TestKeyGeneration:
    def test_keypair_bounds(self) -> None:
        kp = generate_keypair()
        assert 1 <= kp.private < SECP256K1.n
        assert kp.public.on_curve()

    def test_public_equals_G_times_private(self) -> None:
        kp = generate_keypair()
        G = ECPoint(SECP256K1.Gx, SECP256K1.Gy, SECP256K1)
        assert kp.public == G * kp.private

    def test_custom_curve_keygen(self) -> None:
        curve = ECCurve(p=17, a=2, b=2, Gx=5, Gy=1, n=19)
        kp = generate_keypair(curve=curve)
        assert kp.curve is curve
        assert kp.public.on_curve()

    def test_backend_rejects_custom_curves_and_identity_points(self) -> None:
        curve = ECCurve(p=17, a=2, b=2, Gx=5, Gy=1, n=19)
        with pytest.raises(ECCError, match="custom curve"):
            _crypto_curve(curve)
        with pytest.raises(ECCError, match="identity"):
            _public_key_from_point(SECP256K1.identity)
