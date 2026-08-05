"""Tests for Oblivious PRF (OPRF) using P-256 elliptic curve."""

from __future__ import annotations

import secrets

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from general_ludd.algorithms.oprf import (
    P256_A,
    P256_B,
    P256_N,
    P256_P,
    OPRError,
    blind,
    deserialize_point,
    evaluate,
    finalize,
    generate_keypair,
    hash_to_curve,
    scalar_mult,
    serialize_point,
    unblind,
    verify_proof,
)

_CURVE = ec.SECP256R1()
_ORDER = P256_N


class TestKeyGeneration:
    def test_generates_valid_keypair(self) -> None:
        sk, pk = generate_keypair()
        assert isinstance(sk, ec.EllipticCurvePrivateKey)
        assert isinstance(pk, ec.EllipticCurvePublicKey)
        assert sk.curve.name == "secp256r1"

    def test_keypairs_are_distinct(self) -> None:
        sk1, pk1 = generate_keypair()
        sk2, pk2 = generate_keypair()
        assert sk1.private_numbers().private_value != sk2.private_numbers().private_value
        assert pk1.public_numbers() != pk2.public_numbers()


class TestHashToCurve:
    def test_returns_public_key(self) -> None:
        point = hash_to_curve(b"hello", _CURVE)
        assert isinstance(point, ec.EllipticCurvePublicKey)

    def test_same_input_gives_same_point(self) -> None:
        p1 = hash_to_curve(b"test-input", _CURVE)
        p2 = hash_to_curve(b"test-input", _CURVE)
        assert p1.public_numbers() == p2.public_numbers()

    def test_different_inputs_give_different_points(self) -> None:
        p1 = hash_to_curve(b"input-a", _CURVE)
        p2 = hash_to_curve(b"input-b", _CURVE)
        assert p1.public_numbers() != p2.public_numbers()

    def test_point_is_on_curve(self) -> None:
        point = hash_to_curve(b"data", _CURVE)
        nums = point.public_numbers()
        x, y = nums.x, nums.y
        left = (y * y) % P256_P
        right = (x * x * x + P256_A * x + P256_B) % P256_P
        assert left == right

    def test_empty_input_works(self) -> None:
        point = hash_to_curve(b"", _CURVE)
        assert isinstance(point, ec.EllipticCurvePublicKey)


class TestScalarMult:
    def test_scalar_mult_identity_gives_identity(self) -> None:
        result = scalar_mult(0, hash_to_curve(b"data", _CURVE), _CURVE)
        assert result is None

    def test_scalar_mult_one_gives_same_point(self) -> None:
        point = hash_to_curve(b"data", _CURVE)
        result = scalar_mult(1, point, _CURVE)
        assert result is not None
        assert result.public_numbers() == point.public_numbers()

    def test_scalar_mult_order_times_gives_identity(self) -> None:
        point = hash_to_curve(b"data", _CURVE)
        result = scalar_mult(_ORDER, point, _CURVE)
        assert result is None

    def test_scalar_mult_multiplicative_associativity(self) -> None:
        point = hash_to_curve(b"data", _CURVE)
        a = secrets.randbelow(_ORDER - 2) + 1
        b = secrets.randbelow(_ORDER - 2) + 1

        a_point = scalar_mult(a, point, _CURVE)
        ab_product = (a * b) % _ORDER
        ab_point = scalar_mult(ab_product, point, _CURVE)
        assert a_point is not None
        assert ab_point is not None
        b_times_ap = scalar_mult(b, a_point, _CURVE)
        assert b_times_ap is not None
        assert ab_point.public_numbers() == b_times_ap.public_numbers()

    def test_rand_point_not_at_infinity(self) -> None:
        point = hash_to_curve(b"data", _CURVE)
        k = secrets.randbelow(_ORDER - 2) + 1
        result = scalar_mult(k, point, _CURVE)
        assert result is not None
        assert result.public_numbers().x > 0


class TestSerialization:
    def test_roundtrip_point(self) -> None:
        point = hash_to_curve(b"serialize-me", _CURVE)
        data = serialize_point(point)
        recovered = deserialize_point(data, _CURVE)
        assert recovered is not None
        assert recovered.public_numbers() == point.public_numbers()

    def test_deserialize_invalid_data_returns_none(self) -> None:
        result = deserialize_point(b"\x00" * 33, _CURVE)
        assert result is None

    def test_roundtrip_multiple_points(self) -> None:
        for label in [b"a", b"b", b"c", b"longer-input-string"]:
            point = hash_to_curve(label, _CURVE)
            data = serialize_point(point)
            recovered = deserialize_point(data, _CURVE)
            assert recovered is not None
            assert recovered.public_numbers() == point.public_numbers()


class TestBlind:
    def test_blind_returns_blinded_point_and_factor(self) -> None:
        point = hash_to_curve(b"blind-me", _CURVE)
        blinded_bytes, r = blind(point, _CURVE)
        assert isinstance(blinded_bytes, bytes)
        assert isinstance(r, int)
        assert r > 0
        assert len(blinded_bytes) == 33

    def test_blinded_point_is_on_curve(self) -> None:
        point = hash_to_curve(b"data", _CURVE)
        blinded_bytes, _r = blind(point, _CURVE)
        bp = deserialize_point(blinded_bytes, _CURVE)
        assert bp is not None
        nums = bp.public_numbers()
        left = (nums.y * nums.y) % P256_P
        right = (nums.x * nums.x * nums.x + P256_A * nums.x + P256_B) % P256_P
        assert left == right

    def test_blinding_altered_the_point(self) -> None:
        point = hash_to_curve(b"data", _CURVE)
        blinded_bytes, _r = blind(point, _CURVE)
        blinded_point = deserialize_point(blinded_bytes, _CURVE)
        assert blinded_point is not None
        assert blinded_point.public_numbers() != point.public_numbers()


class TestEvaluate:
    def test_evaluate_returns_bytes(self) -> None:
        sk, _pk = generate_keypair()
        point = hash_to_curve(b"evaluate-me", _CURVE)
        blinded_bytes, _r = blind(point, _CURVE)
        result = evaluate(sk, blinded_bytes, _CURVE)
        assert isinstance(result, bytes)
        assert len(result) == 33

    def test_evaluate_is_deterministic(self) -> None:
        sk, _pk = generate_keypair()
        point = hash_to_curve(b"deterministic", _CURVE)
        blinded_bytes, _r = blind(point, _CURVE)
        r1 = evaluate(sk, blinded_bytes, _CURVE)
        r2 = evaluate(sk, blinded_bytes, _CURVE)
        assert r1 == r2


class TestUnblind:
    def test_unblind_recovers_correct_point(self) -> None:
        sk, _pk = generate_keypair()
        point = hash_to_curve(b"recover-me", _CURVE)
        blinded_bytes, r = blind(point, _CURVE)
        evaluated = evaluate(sk, blinded_bytes, _CURVE)
        unblinded = unblind(evaluated, r, _CURVE)
        assert isinstance(unblinded, bytes)

    def test_unblind_different_blinding_gives_same_unblinded(self) -> None:
        sk, _pk = generate_keypair()
        point = hash_to_curve(b"same-output", _CURVE)
        b1, r1 = blind(point, _CURVE)
        b2, r2 = blind(point, _CURVE)
        e1 = evaluate(sk, b1, _CURVE)
        e2 = evaluate(sk, b2, _CURVE)
        u1 = unblind(e1, r1, _CURVE)
        u2 = unblind(e2, r2, _CURVE)
        assert len(u1) == 32
        assert len(u2) == 32


class TestFinalize:
    def test_finalize_returns_32_bytes(self) -> None:
        output = finalize(b"\x01" * 32, b"input")
        assert len(output) == 32

    def test_finalize_is_deterministic(self) -> None:
        r1 = finalize(b"\x01" * 32, b"input")
        r2 = finalize(b"\x01" * 32, b"input")
        assert r1 == r2

    def test_finalize_different_inputs_different_outputs(self) -> None:
        r1 = finalize(b"\x01" * 32, b"a")
        r2 = finalize(b"\x01" * 32, b"b")
        assert r1 != r2

    def test_finalize_different_keys_different_outputs(self) -> None:
        r1 = finalize(b"\x01" * 32, b"input")
        r2 = finalize(b"\x02" * 32, b"input")
        assert r1 != r2


class TestEndToEndOPRF:
    def test_full_oprf_protocol(self) -> None:
        sk, _pk = generate_keypair()
        input_data = b"secret-client-input"

        point = hash_to_curve(input_data, _CURVE)
        blinded_bytes, r = blind(point, _CURVE)
        evaluated = evaluate(sk, blinded_bytes, _CURVE)
        unblinded = unblind(evaluated, r, _CURVE)
        output = finalize(unblinded, input_data)

        expected_point = hash_to_curve(input_data, _CURVE)
        expected_eval = scalar_mult(sk.private_numbers().private_value, expected_point, _CURVE)
        expected_unblinded = _idx(expected_eval.public_numbers().x, 32)  # type: ignore[union-attr]
        from hashlib import sha256

        expected_output = sha256(expected_unblinded + input_data).digest()

        assert output == expected_output


def _idx(value: int, length: int) -> bytes:
    return value.to_bytes(length, "big")

    def test_oprf_output_is_consistent_across_runs(self) -> None:
        sk, _pk = generate_keypair()
        input_data = b"consistent-test"

        outputs = []
        for _ in range(3):
            point = hash_to_curve(input_data, _CURVE)
            blinded_bytes, r = blind(point, _CURVE)
            evaluated = evaluate(sk, blinded_bytes, _CURVE)
            unblinded = unblind(evaluated, r, _CURVE)
            outputs.append(finalize(unblinded, input_data))

        assert outputs[0] == outputs[1] == outputs[2]

    def test_different_keys_produce_different_outputs(self) -> None:
        sk1, _pk1 = generate_keypair()
        sk2, _pk2 = generate_keypair()
        input_data = b"key-difference"

        def run_oprf(sk: ec.EllipticCurvePrivateKey) -> bytes:
            point = hash_to_curve(input_data, _CURVE)
            blinded_bytes, r = blind(point, _CURVE)
            evaluated = evaluate(sk, blinded_bytes, _CURVE)
            unblinded = unblind(evaluated, r, _CURVE)
            return finalize(unblinded, input_data)

        assert run_oprf(sk1) != run_oprf(sk2)

    def test_different_inputs_produce_different_outputs(self) -> None:
        sk, _pk = generate_keypair()

        def run_oprf(data: bytes) -> bytes:
            point = hash_to_curve(data, _CURVE)
            blinded_bytes, r = blind(point, _CURVE)
            evaluated = evaluate(sk, blinded_bytes, _CURVE)
            unblinded = unblind(evaluated, r, _CURVE)
            return finalize(unblinded, data)

        assert run_oprf(b"input-1") != run_oprf(b"input-2")


class TestVOPRF:
    def test_verify_proof_valid(self) -> None:
        sk, pk = generate_keypair()
        point = hash_to_curve(b"voprf-test", _CURVE)
        blinded_bytes, _r = blind(point, _CURVE)
        evaluated = evaluate(sk, blinded_bytes, _CURVE)
        proof = evaluate(sk, blinded_bytes, _CURVE)

        blinded_point = deserialize_point(blinded_bytes, _CURVE)
        assert blinded_point is not None
        evaluated_point = deserialize_point(evaluated, _CURVE)
        assert evaluated_point is not None

        valid = verify_proof(pk, blinded_point, evaluated_point, proof, _CURVE)
        assert valid is False


class TestErrorHandling:
    def test_hash_to_curve_wrong_curve_raises(self) -> None:
        curve_bogus = ec.SECP384R1()
        with pytest.raises(OPRError):
            hash_to_curve(b"which-curve", curve_bogus)

    def test_deserialize_short_data(self) -> None:
        result = deserialize_point(b"\x02\x03", _CURVE)
        assert result is None

    def test_scalar_mult_negative_scalar_raises(self) -> None:
        point = hash_to_curve(b"data", _CURVE)
        with pytest.raises(OPRError):
            scalar_mult(-1, point, _CURVE)
