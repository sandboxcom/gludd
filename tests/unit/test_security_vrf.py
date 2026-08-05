"""Verifiable Random Function tests (Ed25519-signature based VRF)."""

from __future__ import annotations

import hashlib
import os

import pytest


class TestVRFKeyGeneration:
    def test_generate_keypair_produces_valid_keys(self) -> None:
        from general_ludd.security.vrf import generate_keypair

        sk, pk = generate_keypair()
        assert len(sk) == 32
        assert len(pk) == 32
        assert sk != pk
        pk2 = generate_keypair()[1]
        assert pk != pk2

    def test_generate_keypair_is_deterministic_from_seed(self) -> None:
        from general_ludd.security.vrf import generate_keypair_from_seed

        seed = hashlib.sha256(b"deterministic test seed").digest()
        sk1, pk1 = generate_keypair_from_seed(seed)
        sk2, pk2 = generate_keypair_from_seed(seed)
        assert sk1 == sk2
        assert pk1 == pk2

    def test_generate_keypair_from_seed_rejects_short_seed(self) -> None:
        from general_ludd.security.vrf import VRFError, generate_keypair_from_seed

        with pytest.raises(VRFError, match="32 bytes"):
            generate_keypair_from_seed(b"short")

    def test_generate_keypair_from_seed_rejects_long_seed(self) -> None:
        from general_ludd.security.vrf import VRFError, generate_keypair_from_seed

        with pytest.raises(VRFError, match="32 bytes"):
            generate_keypair_from_seed(b"x" * 64)


class TestVRFProve:
    def test_prove_returns_64_byte_proof(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove

        sk, _ = generate_keypair()
        proof = prove(sk, b"hello world")
        assert isinstance(proof, bytes)
        assert len(proof) == 64

    def test_prove_deterministic_for_same_alpha(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove

        sk, _ = generate_keypair()
        alpha = b"test alpha"
        proof1 = prove(sk, alpha)
        proof2 = prove(sk, alpha)
        assert proof1 == proof2

    def test_prove_different_for_different_alpha(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove

        sk, _ = generate_keypair()
        proof1 = prove(sk, b"alpha one")
        proof2 = prove(sk, b"alpha two")
        assert proof1 != proof2

    def test_prove_accepts_string_alpha(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove

        sk, _ = generate_keypair()
        proof = prove(sk, "string alpha")
        assert len(proof) == 64

    def test_prove_different_key_different_proof_same_alpha(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove

        sk_a, _ = generate_keypair()
        sk_b, _ = generate_keypair()
        alpha = b"shared message"
        assert prove(sk_a, alpha) != prove(sk_b, alpha)

    def test_prove_empty_alpha(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove

        sk, _ = generate_keypair()
        proof = prove(sk, b"")
        assert len(proof) == 64

    def test_prove_large_alpha(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove

        sk, _ = generate_keypair()
        large = os.urandom(4096)
        proof = prove(sk, large)
        assert len(proof) == 64

    def test_prove_rejects_short_sk(self) -> None:
        from general_ludd.security.vrf import VRFError, prove

        with pytest.raises(VRFError, match="32 bytes"):
            prove(b"short", b"alpha")

    def test_prove_rejects_long_sk(self) -> None:
        from general_ludd.security.vrf import VRFError, prove

        with pytest.raises(VRFError, match="32 bytes"):
            prove(b"x" * 64, b"alpha")


class TestVRFVerify:
    def test_verify_valid_proof(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, pk = generate_keypair()
        alpha = b"verifiable message"
        proof = prove(sk, alpha)
        out = verify(pk, alpha, proof)
        assert out is not None
        assert len(out) == 64

    def test_verify_deterministic_output(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, pk = generate_keypair()
        alpha = b"deterministic output test"
        proof = prove(sk, alpha)
        out1 = verify(pk, alpha, proof)
        out2 = verify(pk, alpha, proof)
        assert out1 == out2
        assert out1 is not None

    def test_verify_different_output_for_different_alpha(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, pk = generate_keypair()
        a1 = b"alpha one"
        a2 = b"alpha two"
        p1 = prove(sk, a1)
        p2 = prove(sk, a2)
        o1 = verify(pk, a1, p1)
        o2 = verify(pk, a2, p2)
        assert o1 is not None
        assert o2 is not None
        assert o1 != o2

    def test_verify_rejects_wrong_alpha(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, pk = generate_keypair()
        alpha = b"original"
        proof = prove(sk, alpha)
        assert verify(pk, b"tampered", proof) is None

    def test_verify_rejects_wrong_public_key(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, pk_right = generate_keypair()
        _, pk_wrong = generate_keypair()
        alpha = b"test"
        proof = prove(sk, alpha)
        assert verify(pk_right, alpha, proof) is not None
        assert verify(pk_wrong, alpha, proof) is None

    def test_verify_rejects_tampered_proof(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, pk = generate_keypair()
        alpha = b"test"
        proof = prove(sk, alpha)
        tampered = bytearray(proof)
        tampered[0] ^= 0x01
        assert verify(pk, alpha, bytes(tampered)) is None

    def test_verify_rejects_tampered_mid_proof(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, pk = generate_keypair()
        alpha = b"test"
        proof = prove(sk, alpha)
        tampered = bytearray(proof)
        tampered[32] ^= 0xFF
        assert verify(pk, alpha, bytes(tampered)) is None

    def test_verify_rejects_truncated_proof(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, _ = generate_keypair()
        _, pk = generate_keypair()
        proof = prove(sk, b"msg")
        assert verify(pk, b"msg", proof[:40]) is None

    def test_verify_rejects_empty_alpha(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, pk = generate_keypair()
        proof = prove(sk, b"")
        assert verify(pk, b"", proof) is not None
        assert verify(pk, b" ", proof) is None

    def test_verify_rejects_short_public_key(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, _ = generate_keypair()
        proof = prove(sk, b"msg")
        assert verify(b"short_pk", b"msg", proof) is None


class TestVRFProofHashOutput:
    def test_proof_to_hash_is_deterministic(self) -> None:
        from general_ludd.security.vrf import generate_keypair, proof_to_hash, prove

        sk, _ = generate_keypair()
        proof = prove(sk, b"consistent")
        out1 = proof_to_hash(proof)
        out2 = proof_to_hash(proof)
        assert out1 == out2
        assert len(out1) == 64

    def test_proof_to_hash_empty(self) -> None:
        from general_ludd.security.vrf import generate_keypair, proof_to_hash, prove

        sk, _ = generate_keypair()
        proof = prove(sk, b"")
        result = proof_to_hash(proof)
        assert len(result) == 64

    def test_different_proofs_produce_different_hashes(self) -> None:
        from general_ludd.security.vrf import generate_keypair, proof_to_hash, prove

        sk, _ = generate_keypair()
        p1 = prove(sk, b"one")
        p2 = prove(sk, b"two")
        assert proof_to_hash(p1) != proof_to_hash(p2)


class TestVRFProofEncodeDecode:
    def test_roundtrip_encode_decode(self) -> None:
        from general_ludd.security.vrf import (
            decode_proof,
            encode_proof,
            generate_keypair,
            prove,
        )

        sk, _ = generate_keypair()
        alpha = b"roundtrip test"
        proof = prove(sk, alpha)
        encoded = encode_proof(proof)
        assert isinstance(encoded, bytes)
        assert len(encoded) == 64
        decoded = decode_proof(encoded)
        assert decoded == proof

    def test_decode_rejects_short_proof(self) -> None:
        from general_ludd.security.vrf import VRFError, decode_proof

        with pytest.raises(VRFError, match="64 bytes"):
            decode_proof(b"short")

    def test_decode_rejects_long_proof(self) -> None:
        from general_ludd.security.vrf import VRFError, decode_proof

        with pytest.raises(VRFError, match="64 bytes"):
            decode_proof(b"x" * 100)

    def test_encode_decode_multiple(self) -> None:
        from general_ludd.security.vrf import (
            decode_proof,
            encode_proof,
            generate_keypair,
            prove,
        )

        sk, _ = generate_keypair()
        for i in range(5):
            alpha = f"roundtrip {i}".encode()
            proof = prove(sk, alpha)
            assert decode_proof(encode_proof(proof)) == proof


class TestVRFHighLevelWorkflow:
    def test_full_workflow_prove_verify_output(self) -> None:
        from general_ludd.security.vrf import (
            generate_keypair,
            proof_to_hash,
            prove,
            verify,
        )

        sk, pk = generate_keypair()
        alpha = b"full workflow test"
        proof = prove(sk, alpha)
        vrf_output = verify(pk, alpha, proof)
        assert vrf_output is not None

        hash_from_proof = proof_to_hash(proof)
        assert vrf_output == hash_from_proof

    def test_proof_unique_per_input(self) -> None:
        from general_ludd.security.vrf import generate_keypair, proof_to_hash, prove

        sk, _ = generate_keypair()
        seen = set()
        for i in range(20):
            alpha = f"unique input {i}".encode()
            h = proof_to_hash(prove(sk, alpha))
            assert h not in seen, f"Collision at iteration {i}"
            seen.add(h)

    def test_verify_rejects_zero_public_key(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, _ = generate_keypair()
        proof = prove(sk, b"test")
        assert verify(b"\x00" * 32, b"test", proof) is None

    def test_verify_rejects_all_ones_public_key(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, _ = generate_keypair()
        proof = prove(sk, b"test")
        assert verify(b"\xff" * 32, b"test", proof) is None


class TestVRFEdgeCases:
    def test_binary_zero_alpha(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, pk = generate_keypair()
        alpha = b"\x00" * 64
        proof = prove(sk, alpha)
        assert verify(pk, alpha, proof) is not None

    def test_max_alpha_size(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, pk = generate_keypair()
        alpha = b"\xff" * 65536
        proof = prove(sk, alpha)
        assert verify(pk, alpha, proof) is not None

    def test_unicode_string_alpha(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk, pk = generate_keypair()
        alpha = "cafe\u0301 r\u00e9sum\u00e9 na\u00efve \U0001f600"
        proof = prove(sk, alpha)
        assert verify(pk, alpha, proof) is not None

    def test_multiple_outputs_different_keys_same_alpha(self) -> None:
        from general_ludd.security.vrf import generate_keypair, prove, verify

        sk_a, pk_a = generate_keypair()
        sk_b, pk_b = generate_keypair()
        alpha = b"shared input"
        proof_a = prove(sk_a, alpha)
        proof_b = prove(sk_b, alpha)
        out_a = verify(pk_a, alpha, proof_a)
        out_b = verify(pk_b, alpha, proof_b)
        assert out_a is not None
        assert out_b is not None
        assert out_a != out_b


class TestVRFDeterministicSeed:
    def test_seed_pair_produces_consistent_proofs(self) -> None:
        from general_ludd.security.vrf import generate_keypair_from_seed, prove, verify

        seed = hashlib.sha256(b"fixed seed").digest()
        sk_a, pk_a = generate_keypair_from_seed(seed)
        sk_b, pk_b = generate_keypair_from_seed(seed)
        assert sk_a == sk_b
        assert pk_a == pk_b
        p1 = prove(sk_a, b"test")
        p2 = prove(sk_b, b"test")
        assert p1 == p2
        assert verify(pk_a, b"test", p1) == verify(pk_b, b"test", p2)


class TestVRFSerialize:
    def test_serialize_proof_valid_roundtrip_with_verify(self) -> None:
        from general_ludd.security.vrf import (
            decode_proof,
            encode_proof,
            generate_keypair,
            prove,
            verify,
        )

        sk, pk = generate_keypair()
        alpha = b"serialization test"
        proof = prove(sk, alpha)
        serialized = encode_proof(proof)
        deserialized = decode_proof(serialized)
        assert proof == deserialized
        out1 = verify(pk, alpha, proof)
        out2 = verify(pk, alpha, deserialized)
        assert out1 == out2
        assert out1 is not None
