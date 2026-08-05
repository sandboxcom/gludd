"""FROST and tECDSA threshold signature tests using the ``cryptography`` library.

Tests cover:
  - FROST key generation, commitments, signing, aggregation, verification
  - tECDSA key generation, commitments, signing, verification
  - Edge cases: invalid threshold, wrong participants, tampered messages
  - Multiple threshold configurations (2-of-3, 3-of-5, 5-of-7)
  - secp256k1 and secp256r1 curves
"""

from __future__ import annotations

import hashlib
import secrets
from itertools import combinations

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from general_ludd.algorithms.frost import (
    ThresholdError,
    _compressed_point_bytes,
    _curve_order,
    _curve_params,
    _hash_binding_factors,
    _int_from_hash,
    _lagrange_coeff,
    _mod_inv,
    _point_add,
    _point_mul,
    _poly_eval,
    _scalar_mult_curve,
    frost_aggregate,
    frost_commit,
    frost_sign,
    frost_sign_share,
    frost_verify,
    generate_threshold_keys,
    tedcsa_commit,
    tedcsa_keygen,
    tedcsa_sign,
    tedcsa_verify,
)

_K1 = ec.SECP256K1()
_R1 = ec.SECP256R1()

_K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_R1_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


# ═══════════════════════════════════════════════════════════════════════════
# Point arithmetic tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPointArithmetic:
    def test_point_add_identity(self) -> None:
        p, a, _b, gx, gy = _curve_params(_K1)
        G = (gx, gy)
        assert _point_add(G, (0, 0), p, a) == G
        assert _point_add((0, 0), G, p, a) == G

    def test_point_add_inverse_yields_identity(self) -> None:
        p, a, _b, gx, gy = _curve_params(_K1)
        G = (gx, gy)
        negG = (gx, (-gy) % p)
        assert _point_add(G, negG, p, a) == (0, 0)

    def test_point_add_commutative(self) -> None:
        p, a, _b, _gx, _gy = _curve_params(_K1)
        result = generate_threshold_keys(2, 3, _K1)
        G_pt = _pubkey_to_tuple(result.group_public_key)
        R = _scalar_mult_curve(42, _K1)
        assert _point_add(G_pt, R, p, a) == _point_add(R, G_pt, p, a)

    def test_scalar_mult_doubling_equals_add(self) -> None:
        p, a, _b, gx, gy = _curve_params(_K1)
        G = (gx, gy)
        assert _point_mul(2, G, p, a) == _point_add(G, G, p, a)

    def test_scalar_mult_distributive(self) -> None:
        a_val, b_val = 7, 11
        G = _scalar_mult_curve(1, _K1)
        p, a, _b, _gx, _gy = _curve_params(_K1)
        sum_pt = _point_mul(a_val + b_val, G, p, a)
        ga = _point_mul(a_val, G, p, a)
        gb = _point_mul(b_val, G, p, a)
        assert sum_pt == _point_add(ga, gb, p, a)

    def test_scalar_mult_mod_order(self) -> None:
        p, a, _b, gx, gy = _curve_params(_K1)
        G = (gx, gy)
        result = _point_mul(_K1_ORDER, G, p, a)
        assert result == (0, 0)

    def test_compressed_point_roundtrip(self) -> None:
        G = _scalar_mult_curve(1, _K1)
        cbytes = _compressed_point_bytes(*G)
        assert len(cbytes) == 33
        assert cbytes[0:1] in (b"\x02", b"\x03")


# ═══════════════════════════════════════════════════════════════════════════
# Lagrange / polynomial tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPolynomial:
    def test_poly_eval_constant(self) -> None:
        assert _poly_eval([42], 5, _K1_ORDER) == 42 % _K1_ORDER

    def test_poly_eval_linear(self) -> None:
        assert _poly_eval([3, 2], 4, _K1_ORDER) == (3 + 2 * 4) % _K1_ORDER

    def test_poly_eval_quadratic(self) -> None:
        assert _poly_eval([1, 2, 3], 2, _K1_ORDER) == (1 + 2 * 2 + 3 * 4) % _K1_ORDER

    def test_lagrange_recovery(self) -> None:
        secret = 123456789
        subjects = [1, 2, 3, 5]
        coeffs = [secret, 888, 777]
        order = _K1_ORDER
        shares = {i: _poly_eval(coeffs, i, order) for i in subjects}
        subset = [2, 3, 5]
        recovered = sum(_lagrange_coeff(j, subset, order) * shares[j] for j in subset) % order
        assert recovered == secret

    def test_lagrange_incorrect_subset_fails(self) -> None:
        secret = 987654321
        subjects = [1, 2, 3, 4]
        coeffs = [secret, 111, 222]
        order = _K1_ORDER
        shares = {i: _poly_eval(coeffs, i, order) for i in subjects}
        wrong = sum(_lagrange_coeff(j, [1, 2], order) * shares[j] for j in [1, 2]) % order
        assert wrong != secret

    def test_mod_inv(self) -> None:
        assert _mod_inv(3, _K1_ORDER) * 3 % _K1_ORDER == 1


def _pubkey_to_tuple(pk: ec.EllipticCurvePublicKey) -> tuple[int, int]:
    nums = pk.public_numbers()
    return (nums.x, nums.y)


# ═══════════════════════════════════════════════════════════════════════════
# Threshold key generation tests
# ═══════════════════════════════════════════════════════════════════════════


class TestKeyGeneration:
    @pytest.mark.parametrize("t,n", [(1, 3), (2, 3), (3, 5), (5, 7), (3, 10)])
    def test_generate_valid_shares(self, t: int, n: int) -> None:
        result = generate_threshold_keys(t, n, _K1)
        assert result.threshold == t
        assert result.num_participants == n
        assert len(result.shares) == n
        assert result.group_public_key is not None
        assert result.group_private_key is not None

    def test_generate_secp256r1(self) -> None:
        result = generate_threshold_keys(2, 3, _R1)
        assert len(result.shares) == 3
        assert isinstance(result.curve, ec.SECP256R1)

    def test_threshold_exceeds_participants_raises(self) -> None:
        with pytest.raises(ThresholdError, match="threshold"):
            generate_threshold_keys(5, 3, _K1)

    def test_threshold_zero_raises(self) -> None:
        with pytest.raises(ThresholdError, match="threshold"):
            generate_threshold_keys(0, 3, _K1)

    def test_shares_have_unique_indices(self) -> None:
        result = generate_threshold_keys(3, 7, _K1)
        assert sorted(result.shares.keys()) == list(range(1, 8))

    def test_each_share_has_verification_key(self) -> None:
        result = generate_threshold_keys(2, 4, _K1)
        for idx, share in result.shares.items():
            assert isinstance(share.verification_key, ec.EllipticCurvePublicKey)
            assert share.index == idx

    def test_keys_deterministic_by_curve(self) -> None:
        result_k1 = generate_threshold_keys(2, 3, _K1)
        result_r1 = generate_threshold_keys(2, 3, _R1)
        assert _pubkey_to_tuple(result_k1.group_public_key) != _pubkey_to_tuple(result_r1.group_public_key)

    def test_shares_can_sign_collectively(self) -> None:
        result = generate_threshold_keys(2, 5, _K1)
        sig = frost_sign(result, b"hello")
        assert frost_verify(result.group_public_key, b"hello", sig, _K1)


# ═══════════════════════════════════════════════════════════════════════════
# FROST commitment tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFrostCommit:
    def test_commit_produces_non_identity_points(self) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        commit = frost_commit(result.shares[1], _K1)
        assert commit.D != (0, 0)
        assert commit.E != (0, 0)

    def test_commit_different_each_call(self) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        c1 = frost_commit(result.shares[1], _K1)
        c2 = frost_commit(result.shares[1], _K1)
        assert c1.d != c2.d or c1.e != c2.e

    def test_commit_correct_index(self) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        for i in [1, 2, 3]:
            commit = frost_commit(result.shares[i], _K1)
            assert commit.index == i

    def test_commit_points_on_curve(self) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        p, _a, _b, _gx, _gy = _curve_params(_K1)
        for i in range(1, 4):
            c = frost_commit(result.shares[i], _K1)
            xD, yD = c.D
            xE, yE = c.E
            assert (yD * yD) % p == (xD * xD * xD + 7) % p
            assert (yE * yE) % p == (xE * xE * xE + 7) % p


class TestFrostBindingFactors:
    def test_binding_factors_deterministic(self) -> None:
        result = generate_threshold_keys(3, 5, _K1)
        commits = [frost_commit(result.shares[i], _K1) for i in [1, 3, 4]]
        order = _curve_order(_K1)
        rho1 = _hash_binding_factors(b"test", commits, order)
        rho2 = _hash_binding_factors(b"test", commits, order)
        assert rho1 == rho2

    def test_binding_factors_differ_by_msg(self) -> None:
        result = generate_threshold_keys(3, 5, _K1)
        commits = [frost_commit(result.shares[i], _K1) for i in [1, 3, 4]]
        order = _curve_order(_K1)
        rho1 = _hash_binding_factors(b"aaa", commits, order)
        rho2 = _hash_binding_factors(b"bbb", commits, order)
        assert rho1 != rho2


# ═══════════════════════════════════════════════════════════════════════════
# FROST full signing and verification
# ═══════════════════════════════════════════════════════════════════════════


class TestFrostSignVerify:
    @pytest.mark.parametrize(
        "t,n,subset",
        [
            (2, 3, [1, 2]),
            (2, 3, [1, 3]),
            (3, 5, [2, 3, 5]),
            (3, 5, [1, 4, 5]),
            (5, 7, [1, 2, 3, 5, 7]),
        ],
    )
    def test_frost_sign_and_verify(self, t: int, n: int, subset: list[int]) -> None:
        result = generate_threshold_keys(t, n, _K1)
        msg = b"FROST threshold test message"
        sig = frost_sign(result, msg, subset)
        assert frost_verify(result.group_public_key, msg, sig, _K1)

    @pytest.mark.parametrize(
        "msg",
        [
            b"",
            b"a",
            b"hello world",
            b"\x00" * 64,
            b"The quick brown fox jumps over the lazy dog",
        ],
    )
    def test_frost_various_messages(self, msg: bytes) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        sig = frost_sign(result, msg)
        assert frost_verify(result.group_public_key, msg, sig, _K1)

    def test_frost_single_threshold(self) -> None:
        result = generate_threshold_keys(1, 1, _K1)
        sig = frost_sign(result, b"single")
        assert frost_verify(result.group_public_key, b"single", sig, _K1)

    def test_verify_rejects_wrong_message(self) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        sig = frost_sign(result, b"correct message")
        assert not frost_verify(result.group_public_key, b"wrong message", sig, _K1)

    def test_verify_rejects_wrong_public_key(self) -> None:
        result1 = generate_threshold_keys(2, 3, _K1)
        result2 = generate_threshold_keys(2, 3, _K1)
        sig = frost_sign(result1, b"test")
        assert not frost_verify(result2.group_public_key, b"test", sig, _K1)

    def test_verify_rejects_tampered_z(self) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        R_bytes, z = frost_sign(result, b"test")
        tampered = (R_bytes, (z + 1) % _K1_ORDER)
        assert not frost_verify(result.group_public_key, b"test", tampered, _K1)

    def test_verify_rejects_tampered_R(self) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        R_bytes, z = frost_sign(result, b"test")
        tampered_R = bytearray(R_bytes)
        tampered_R[1] ^= 0x01
        tampered = (bytes(tampered_R), z)
        assert not frost_verify(result.group_public_key, b"test", tampered, _K1)

    def test_frost_sign_different_subsets_same_msg(self) -> None:
        result = generate_threshold_keys(3, 5, _K1)
        msg = b"same message"
        sig_a = frost_sign(result, msg, [1, 2, 3])
        sig_b = frost_sign(result, msg, [3, 4, 5])
        assert frost_verify(result.group_public_key, msg, sig_a, _K1)
        assert frost_verify(result.group_public_key, msg, sig_b, _K1)
        assert sig_a != sig_b

    def test_frost_too_few_participants_raises(self) -> None:
        result = generate_threshold_keys(3, 5, _K1)
        with pytest.raises(ThresholdError):
            frost_sign(result, b"test", [1, 2])

    def test_frost_invalid_participant_skipped(self) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        sig = frost_sign(result, b"test", [1, 7, 2])
        assert frost_verify(result.group_public_key, b"test", sig, _K1)

    def test_frost_sign_aggregate_deterministic_given_commits(self) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        msg = b"deterministic"
        curve = result.curve
        order = _curve_order(curve)
        commits = [frost_commit(result.shares[i], curve) for i in [1, 2]]
        rho = _hash_binding_factors(msg, commits, order)
        pk = result.group_public_key
        s1 = {
            1: frost_sign_share(result.shares[1], msg, commits, pk, rho, curve),
            2: frost_sign_share(result.shares[2], msg, commits, pk, rho, curve),
        }
        R1, z1 = frost_aggregate(commits, rho, s1, curve)
        s2 = {
            1: frost_sign_share(result.shares[1], msg, commits, pk, rho, curve),
            2: frost_sign_share(result.shares[2], msg, commits, pk, rho, curve),
        }
        R2, z2 = frost_aggregate(commits, rho, s2, curve)
        assert (R1, z1) == (R2, z2)
        assert frost_verify(pk, msg, (R1, z1), curve)


# ═══════════════════════════════════════════════════════════════════════════
# FROST incrementality
# ═══════════════════════════════════════════════════════════════════════════


class TestFrostIncremental:
    def test_manual_two_step_signing(self) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        msg = b"incremental frost"
        curve = result.curve
        order = _curve_order(curve)

        c1 = frost_commit(result.shares[1], curve)
        c2 = frost_commit(result.shares[2], curve)
        commits = [c1, c2]
        rho = _hash_binding_factors(msg, commits, order)

        z1 = frost_sign_share(result.shares[1], msg, commits, result.group_public_key, rho, curve)
        z2 = frost_sign_share(result.shares[2], msg, commits, result.group_public_key, rho, curve)

        R_bytes, z = frost_aggregate(commits, rho, {1: z1, 2: z2}, curve)
        assert frost_verify(result.group_public_key, msg, (R_bytes, z), curve)

    def test_any_t_subset_works(self) -> None:
        result = generate_threshold_keys(3, 5, _K1)
        msg = b"any 3 of 5"
        for subset in combinations(range(1, 6), 3):
            sig = frost_sign(result, msg, list(subset))
            assert frost_verify(result.group_public_key, msg, sig, _K1)


# ═══════════════════════════════════════════════════════════════════════════
# Signature format tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFrostSignatureFormat:
    def test_R_bytes_format(self) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        R_bytes, _z = frost_sign(result, b"format test")
        assert len(R_bytes) == 33
        assert R_bytes[0:1] in (b"\x02", b"\x03")

    def test_z_in_range(self) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        _R_bytes, z = frost_sign(result, b"range test")
        assert 0 <= z < _K1_ORDER

    def test_large_input_message(self) -> None:
        result = generate_threshold_keys(2, 3, _K1)
        msg = secrets.token_bytes(4096)
        sig = frost_sign(result, msg)
        assert frost_verify(result.group_public_key, msg, sig, _K1)


# ═══════════════════════════════════════════════════════════════════════════
# tECDSA key generation tests
# ═══════════════════════════════════════════════════════════════════════════


class TestTEcdsaKeygen:
    @pytest.mark.parametrize("t,n", [(1, 3), (2, 3), (3, 5), (5, 7)])
    def test_keygen_valid_shares(self, t: int, n: int) -> None:
        pk, shares = tedcsa_keygen(t, n, _K1)
        assert isinstance(pk, ec.EllipticCurvePublicKey)
        assert len(shares) == n
        for idx, sh in shares.items():
            assert sh.index == idx
            assert isinstance(sh.X_i, ec.EllipticCurvePublicKey)

    def test_keygen_secp256r1(self) -> None:
        _pk, shares = tedcsa_keygen(2, 3, _R1)
        assert len(shares) == 3

    def test_keygen_unique_public_keys(self) -> None:
        _pk, shares = tedcsa_keygen(2, 5, _K1)
        x_vals = {share.X_i.public_numbers().x for share in shares.values()}
        assert len(x_vals) == 5


# ═══════════════════════════════════════════════════════════════════════════
# tECDSA commit tests
# ═══════════════════════════════════════════════════════════════════════════


class TestTEcdsaCommit:
    def test_commit_non_identity(self) -> None:
        _pk, shares = tedcsa_keygen(2, 3, _K1)
        commit = tedcsa_commit(shares[1], _K1)
        assert commit.R_i != (0, 0)
        assert commit.index == 1

    def test_commit_point_on_curve(self) -> None:
        _pk, shares = tedcsa_keygen(2, 3, _K1)
        p, _a, _b, _gx, _gy = _curve_params(_K1)
        c = tedcsa_commit(shares[2], _K1)
        x, y = c.R_i
        assert (y * y) % p == (x * x * x + 7) % p

    def test_commit_non_deterministic(self) -> None:
        _pk, shares = tedcsa_keygen(2, 3, _K1)
        c1 = tedcsa_commit(shares[1], _K1)
        c2 = tedcsa_commit(shares[1], _K1)
        assert c1.k_i != c2.k_i or c1.R_i != c2.R_i


# ═══════════════════════════════════════════════════════════════════════════
# tECDSA signing and verification
# ═══════════════════════════════════════════════════════════════════════════


class TestTEcdsaSignVerify:
    @pytest.mark.parametrize(
        "t,n,subset",
        [
            (2, 3, [1, 2]),
            (2, 3, [2, 3]),
            (3, 5, [1, 3, 5]),
            (3, 5, [2, 4, 5]),
            (5, 7, [1, 2, 3, 5, 7]),
        ],
    )
    def test_tedcsa_sign_and_verify(self, t: int, n: int, subset: list[int]) -> None:
        pk, shares = tedcsa_keygen(t, n, _K1)
        msg = b"tECDSA threshold test"
        sig = tedcsa_sign(shares, msg, subset, t, _K1)
        assert tedcsa_verify(pk, msg, sig, _K1)

    @pytest.mark.parametrize(
        "msg",
        [
            b"",
            b"x",
            b"Hello, tECDSA!",
            b"\x00" * 256,
        ],
    )
    def test_various_messages(self, msg: bytes) -> None:
        pk, shares = tedcsa_keygen(2, 3, _K1)
        sig = tedcsa_sign(shares, msg, [1, 2], 2, _K1)
        assert tedcsa_verify(pk, msg, sig, _K1)

    def test_single_signer(self) -> None:
        pk, shares = tedcsa_keygen(1, 1, _K1)
        sig = tedcsa_sign(shares, b"solo", None, 1, _K1)
        assert tedcsa_verify(pk, b"solo", sig, _K1)

    def test_wrong_message_rejected(self) -> None:
        pk, shares = tedcsa_keygen(2, 3, _K1)
        sig = tedcsa_sign(shares, b"correct", [1, 2], 2, _K1)
        assert not tedcsa_verify(pk, b"wrong", sig, _K1)

    def test_wrong_key_rejected(self) -> None:
        _pk1, shares1 = tedcsa_keygen(2, 3, _K1)
        pk2, _shares2 = tedcsa_keygen(2, 3, _K1)
        sig = tedcsa_sign(shares1, b"msg", [1, 2], 2, _K1)
        assert not tedcsa_verify(pk2, b"msg", sig, _K1)

    def test_tampered_signature_rejected(self) -> None:
        pk, shares = tedcsa_keygen(2, 3, _K1)
        sig = tedcsa_sign(shares, b"msg", [1, 2], 2, _K1)
        tampered = bytearray(sig)
        tampered[5] ^= 0xFF
        assert not tedcsa_verify(pk, b"msg", bytes(tampered), _K1)

    def test_default_participants(self) -> None:
        pk, shares = tedcsa_keygen(2, 3, _K1)
        sig = tedcsa_sign(shares, b"default", threshold=2, curve=_K1)
        assert tedcsa_verify(pk, b"default", sig, _K1)

    def test_too_few_participants_raises(self) -> None:
        _pk, shares = tedcsa_keygen(3, 5, _K1)
        with pytest.raises(ThresholdError):
            tedcsa_sign(shares, b"test", [1, 2], threshold=3, curve=_K1)

    def test_der_encoding_valid(self) -> None:
        _pk, shares = tedcsa_keygen(2, 3, _K1)
        sig = tedcsa_sign(shares, b"der test", [1, 2], 2, _K1)
        r, s = decode_dss_signature(sig)
        assert r > 0
        assert s > 0

    def test_any_t_subset_works_tedcsa(self) -> None:
        pk, shares = tedcsa_keygen(3, 5, _K1)
        msg = b"any 3 of 5 tECDSA"
        for subset in combinations(range(1, 6), 3):
            sig = tedcsa_sign(shares, msg, list(subset), 3, _K1)
            assert tedcsa_verify(pk, msg, sig, _K1)

    def test_secp256r1_sign_verify(self) -> None:
        pk, shares = tedcsa_keygen(2, 3, _R1)
        msg = b"secp256r1 test"
        sig = tedcsa_sign(shares, msg, [1, 2], 2, _R1)
        assert tedcsa_verify(pk, msg, sig, _R1)

    def test_different_sigs_for_different_msgs(self) -> None:
        _pk, shares = tedcsa_keygen(2, 3, _K1)
        sig1 = tedcsa_sign(shares, b"msg1", [1, 2], 2, _K1)
        sig2 = tedcsa_sign(shares, b"msg2", [1, 2], 2, _K1)
        assert sig1 != sig2


# ═══════════════════════════════════════════════════════════════════════════
# tECDSA incremental tests
# ═══════════════════════════════════════════════════════════════════════════


class TestTEcdsaIncremental:
    def test_manual_commit_then_sign(self) -> None:
        pk, shares = tedcsa_keygen(2, 3, _K1)
        msg = b"manual tECDSA"
        curve = _K1
        c1 = tedcsa_commit(shares[1], curve)
        c2 = tedcsa_commit(shares[2], curve)
        assert c1.R_i != c2.R_i
        sig = tedcsa_sign(shares, msg, [1, 2], 2, curve)
        assert tedcsa_verify(pk, msg, sig, curve)


# ═══════════════════════════════════════════════════════════════════════════
# Curve constants
# ═══════════════════════════════════════════════════════════════════════════


class TestCurveConstants:
    def test_secp256k1_order(self) -> None:
        assert _curve_order(_K1) == _K1_ORDER

    def test_secp256r1_order(self) -> None:
        assert _curve_order(_R1) == _R1_ORDER

    def test_int_from_hash_range(self) -> None:
        h = hashlib.sha256(b"test").digest()
        result = _int_from_hash(h, _K1_ORDER)
        assert 0 <= result < _K1_ORDER

    def test_generator_is_on_curve(self) -> None:
        p, _a, _b, gx, gy = _curve_params(_K1)
        assert (gy * gy) % p == (gx * gx * gx + 7) % p

    def test_secp256r1_params_loaded(self) -> None:
        p, _a, _b, gx, gy = _curve_params(_R1)
        assert p > 0
        assert 0 <= gx < p
        assert 0 <= gy < p

    def test_secp256r1_scalar_mult(self) -> None:
        G = _scalar_mult_curve(1, _R1)
        assert G != (0, 0)
        G2 = _scalar_mult_curve(2, _R1)
        assert G2 != (0, 0)
        assert G != G2


# ═══════════════════════════════════════════════════════════════════════════
# Large threshold configurations
# ═══════════════════════════════════════════════════════════════════════════


class TestLargeThreshold:
    def test_7_of_10_frost(self) -> None:
        result = generate_threshold_keys(7, 10, _K1)
        msg = b"7 of 10 FROST"
        sig = frost_sign(result, msg, [1, 2, 4, 5, 7, 9, 10])
        assert frost_verify(result.group_public_key, msg, sig, _K1)

    def test_5_of_10_tedcsa(self) -> None:
        pk, shares = tedcsa_keygen(5, 10, _K1)
        msg = b"5 of 10 tECDSA"
        sig = tedcsa_sign(shares, msg, [1, 3, 5, 7, 9], 5, _K1)
        assert tedcsa_verify(pk, msg, sig, _K1)

    def test_10_of_15_frost(self) -> None:
        result = generate_threshold_keys(10, 15, _K1)
        msg = b"10 of 15 FROST"
        subset = list(range(1, 11))
        sig = frost_sign(result, msg, subset)
        assert frost_verify(result.group_public_key, msg, sig, _K1)

    def test_3_of_10_tedcsa_any_subset(self) -> None:
        pk, shares = tedcsa_keygen(3, 10, _K1)
        msg = b"any 3 of 10"
        for subset in list(combinations(range(1, 11), 3))[:4]:
            sig = tedcsa_sign(shares, msg, list(subset), 3, _K1)
            assert tedcsa_verify(pk, msg, sig, _K1)

    def test_2_of_3_secp256r1_frost(self) -> None:
        result = generate_threshold_keys(2, 3, _R1)
        msg = b"secp256r1 frost"
        sig = frost_sign(result, msg, [1, 2])
        assert frost_verify(result.group_public_key, msg, sig, _R1)
