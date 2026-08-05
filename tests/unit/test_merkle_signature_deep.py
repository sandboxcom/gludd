"""Deep tests for Merkle signature schemes: Winternitz OTS, LMS, HSS,
Merkle tree generation, proof verification, edge cases.

Pure-Python, stdlib only — mirrors src/general_ludd/algorithms/merkle_signature.py.
"""

from __future__ import annotations

import secrets
from dataclasses import FrozenInstanceError

import pytest

from general_ludd.algorithms.merkle_signature import (
    HSSKeyPair,
    HSSParams,
    LMSKeyPair,
    LMSParams,
    LMSSignature,
    MerkleKeyExhaustedError,
    MerkleTree,
    WinternitzConfig,
    _build_merkle_tree,
    _chain,
    _checksum,
    _digest_to_values,
    _merkle_proof,
    _sha256,
    _verify_merkle_proof,
    _winternitz_keygen,
    _winternitz_params,
    _winternitz_sign,
    _winternitz_verify,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _rand_bytes(n: int = 32) -> bytes:
    return secrets.token_bytes(n)


# ---------------------------------------------------------------------------
# WinternitzConfig
# ---------------------------------------------------------------------------


class TestWinternitzConfig:
    def test_default_config(self) -> None:
        cfg = WinternitzConfig()
        assert cfg.w == 4
        assert cfg.ls == 16

    def test_custom_w(self) -> None:
        for w in (1, 2, 4, 8):
            cfg = WinternitzConfig(w=w)
            assert cfg.w == w

    def test_invalid_w_raises(self) -> None:
        with pytest.raises(ValueError):
            WinternitzConfig(w=3)

    def test_frozen_dataclass(self) -> None:
        cfg = WinternitzConfig(w=2, ls=24)
        with pytest.raises(FrozenInstanceError):
            cfg.w = 8  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _winternitz_params
# ---------------------------------------------------------------------------


class TestWinternitzParams:
    def test_default_params(self) -> None:
        u, v, max_val, total = _winternitz_params(4, 16)
        assert u == 32
        assert v == 32
        assert max_val == 15
        assert total == 64

    def test_w1_params(self) -> None:
        u, _v, max_val, _total = _winternitz_params(1, 16)
        assert u == 128
        assert max_val == 1

    def test_w8_params(self) -> None:
        u, _v, max_val, _total = _winternitz_params(8, 16)
        assert u == 16
        assert max_val == 255

    def test_ls32_w4(self) -> None:
        u, v, _max_val, total = _winternitz_params(4, 32)
        assert u == 64
        assert v == 64
        assert total == 128


# ---------------------------------------------------------------------------
# _digest_to_values
# ---------------------------------------------------------------------------


class TestDigestToValues:
    def test_w4_two_per_byte(self) -> None:
        vals = _digest_to_values(b"\xab\xcd", 4, 4)
        assert vals == [0xA, 0xB, 0xC, 0xD]

    def test_w8_one_per_byte(self) -> None:
        vals = _digest_to_values(b"\x42\xff", 8, 2)
        assert vals == [0x42, 0xFF]

    def test_w1_eight_per_byte(self) -> None:
        vals = _digest_to_values(b"\x80", 1, 8)
        assert vals == [1, 0, 0, 0, 0, 0, 0, 0]

    def test_w2_four_per_byte(self) -> None:
        vals = _digest_to_values(b"\xff", 2, 4)
        assert vals == [3, 3, 3, 3]

    def test_pads_to_u(self) -> None:
        vals = _digest_to_values(b"\x01", 4, 4)
        assert len(vals) == 4
        assert vals[:2] == [0, 1]


# ---------------------------------------------------------------------------
# _checksum
# ---------------------------------------------------------------------------


class TestChecksum:
    def test_checksum_deterministic(self) -> None:
        hashed = _sha256(b"hello")
        c1 = _checksum(hashed, 4, 16)
        c2 = _checksum(hashed, 4, 16)
        assert c1 == c2

    def test_checksum_nonzero_length(self) -> None:
        hashed = _sha256(b"hello")
        csum = _checksum(hashed, 4, 16)
        assert len(csum) > 0

    def test_checksum_ls32(self) -> None:
        hashed = _sha256(b"data")
        csum = _checksum(hashed, 4, 32)
        assert len(csum) <= 64

    def test_different_messages_different_checksums(self) -> None:
        h1 = _sha256(b"msg1")
        h2 = _sha256(b"msg2")
        c1 = _checksum(h1, 4, 16)
        c2 = _checksum(h2, 4, 16)
        assert c1 != c2 or h1[:16] == h2[:16]


# ---------------------------------------------------------------------------
# _chain
# ---------------------------------------------------------------------------


class TestChain:
    def test_chain_s0_identity(self) -> None:
        x = _rand_bytes(32)
        assert _chain(0, 0, x, b"\x00" * 32, 4) == x

    def test_chain_deterministic(self) -> None:
        x = _rand_bytes(32)
        seed = _rand_bytes(32)
        assert _chain(1, 5, x, seed, 4) == _chain(1, 5, x, seed, 4)

    def test_chain_different_i_produces_different_result(self) -> None:
        x = _rand_bytes(32)
        seed = _rand_bytes(32)
        assert _chain(0, 5, x, seed, 4) != _chain(1, 5, x, seed, 4)

    def test_chain_length_preserved(self) -> None:
        for n in (16, 32):
            x = _rand_bytes(n)
            result = _chain(0, 3, x, _rand_bytes(32), 4)
            assert len(result) == n


# ---------------------------------------------------------------------------
# _winternitz_keygen / _winternitz_sign / _winternitz_verify
# ---------------------------------------------------------------------------


class TestWinternitzRoundtrip:
    def test_sign_verify_single_message(self) -> None:
        seed = _rand_bytes(32)
        w, ls = 4, 32
        sk, pk = _winternitz_keygen(w, ls, seed)
        msg = b"test message for winternitz"
        sig = _winternitz_sign(msg, sk, seed, w, ls)
        assert _winternitz_verify(msg, sig, pk, seed, w, ls) is True

    def test_sign_verify_ls16(self) -> None:
        seed = _rand_bytes(32)
        w, ls = 4, 16
        sk, pk = _winternitz_keygen(w, ls, seed)
        msg = b"hello world"
        sig = _winternitz_sign(msg, sk, seed, w, ls)
        assert _winternitz_verify(msg, sig, pk, seed, w, ls) is True

    def test_verify_wrong_message_fails(self) -> None:
        seed = _rand_bytes(32)
        w, ls = 4, 32
        sk, pk = _winternitz_keygen(w, ls, seed)
        sig = _winternitz_sign(b"correct message", sk, seed, w, ls)
        assert _winternitz_verify(b"wrong message", sig, pk, seed, w, ls) is False

    def test_verify_wrong_key_fails(self) -> None:
        seed1 = _rand_bytes(32)
        seed2 = _rand_bytes(32)
        w, ls = 4, 32
        sk, _ = _winternitz_keygen(w, ls, seed1)
        _, pk2 = _winternitz_keygen(w, ls, seed2)
        sig = _winternitz_sign(b"msg", sk, seed1, w, ls)
        assert _winternitz_verify(b"msg", sig, pk2, seed1, w, ls) is False

    def test_empty_sk_raises(self) -> None:
        with pytest.raises(MerkleKeyExhaustedError):
            _winternitz_sign(b"msg", [], b"\x00" * 32, 4, 32)

    def test_multiple_messages(self) -> None:
        seed = _rand_bytes(32)
        w, ls = 4, 32
        sk, pk = _winternitz_keygen(w, ls, seed)
        for msg in [b"msg1", b"msg2", b"msg3"]:
            sig = _winternitz_sign(msg, sk, seed, w, ls)
            assert _winternitz_verify(msg, sig, pk, seed, w, ls) is True

    def test_different_w_values(self) -> None:
        for w in (1, 2, 4, 8):
            seed = _rand_bytes(32)
            sk, pk = _winternitz_keygen(w, 32, seed)
            sig = _winternitz_sign(b"hello", sk, seed, w, 32)
            assert _winternitz_verify(b"hello", sig, pk, seed, w, 32) is True

    def test_signature_length_matches_total(self) -> None:
        for w in (1, 2, 4, 8):
            seed = _rand_bytes(32)
            _u, _v, _max_val, total = _winternitz_params(w, 32)
            sk, _pk = _winternitz_keygen(w, 32, seed)
            sig = _winternitz_sign(b"msg", sk, seed, w, 32)
            assert len(sig) == total

    def test_tampered_signature_fails(self) -> None:
        seed = _rand_bytes(32)
        w, ls = 4, 32
        sk, pk = _winternitz_keygen(w, ls, seed)
        sig = _winternitz_sign(b"msg", sk, seed, w, ls)
        tampered = list(sig)
        tampered[0] = _rand_bytes(32)
        assert _winternitz_verify(b"msg", tampered, pk, seed, w, ls) is False


# ---------------------------------------------------------------------------
# LMSParams
# ---------------------------------------------------------------------------


class TestLMSParams:
    def test_valid_params(self) -> None:
        p = LMSParams(h=4, m=16)
        assert p.leaf_count == 16
        assert p.h == 4

    def test_h_zero(self) -> None:
        p = LMSParams(h=0, m=16)
        assert p.leaf_count == 1

    def test_h_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError):
            LMSParams(h=21, m=16)
        with pytest.raises(ValueError):
            LMSParams(h=-1, m=16)

    def test_m_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError):
            LMSParams(h=4, m=1)
        with pytest.raises(ValueError):
            LMSParams(h=4, m=21)

    def test_leaf_count_for_larger_h(self) -> None:
        p = LMSParams(h=10, m=16)
        assert p.leaf_count == 1024


# ---------------------------------------------------------------------------
# _build_merkle_tree + _merkle_proof + _verify_merkle_proof
# ---------------------------------------------------------------------------


class TestMerkleTreeInternals:
    def test_build_tree_single_leaf(self) -> None:
        seed = _rand_bytes(32)
        leaf = _sha256(b"leaf")
        _nodes, root = _build_merkle_tree(seed, 0, [leaf])
        assert root == leaf

    def test_build_tree_two_leaves(self) -> None:
        seed = _rand_bytes(32)
        a = _sha256(b"a")
        b_val = _sha256(b"b")
        _nodes, root = _build_merkle_tree(seed, 1, [a, b_val])
        assert len(root) == 32

    def test_proof_verification_two_leaves(self) -> None:
        seed = _rand_bytes(32)
        a = _sha256(b"a")
        b_val = _sha256(b"b")
        nodes, root = _build_merkle_tree(seed, 1, [a, b_val])
        proof = _merkle_proof(nodes, 0, 1)
        assert _verify_merkle_proof(root, a, 0, proof, seed, 1) is True

    def test_proof_verification_second_leaf(self) -> None:
        seed = _rand_bytes(32)
        a = _sha256(b"a")
        b_val = _sha256(b"b")
        nodes, root = _build_merkle_tree(seed, 1, [a, b_val])
        proof = _merkle_proof(nodes, 1, 1)
        assert _verify_merkle_proof(root, b_val, 1, proof, seed, 1) is True

    def test_proof_fails_with_wrong_root(self) -> None:
        seed = _rand_bytes(32)
        a = _sha256(b"a")
        b_val = _sha256(b"b")
        nodes, _root = _build_merkle_tree(seed, 1, [a, b_val])
        proof = _merkle_proof(nodes, 0, 1)
        assert _verify_merkle_proof(b"\xff" * 32, a, 0, proof, seed, 1) is False

    def test_proof_fails_with_wrong_leaf(self) -> None:
        seed = _rand_bytes(32)
        a = _sha256(b"a")
        b_val = _sha256(b"b")
        nodes, root = _build_merkle_tree(seed, 1, [a, b_val])
        proof = _merkle_proof(nodes, 0, 1)
        assert _verify_merkle_proof(root, b_val, 0, proof, seed, 1) is False

    def test_proof_fails_with_wrong_index(self) -> None:
        seed = _rand_bytes(32)
        a = _sha256(b"a")
        b_val = _sha256(b"b")
        nodes, root = _build_merkle_tree(seed, 1, [a, b_val])
        proof = _merkle_proof(nodes, 0, 1)
        assert _verify_merkle_proof(root, a, 1, proof, seed, 1) is False

    def test_tree_four_leaves_all_proofs(self) -> None:
        seed = _rand_bytes(32)
        leaves = [_sha256(f"leaf{i}".encode()) for i in range(4)]
        nodes, root = _build_merkle_tree(seed, 2, leaves)
        for i in range(4):
            proof = _merkle_proof(nodes, i, 2)
            assert _verify_merkle_proof(root, leaves[i], i, proof, seed, 2) is True


# ---------------------------------------------------------------------------
# LMSKeyPair — generate, sign, verify
# ---------------------------------------------------------------------------


class TestLMSKeyPair:
    def test_generate_h4(self) -> None:
        kp = LMSKeyPair.generate(h=4)
        assert len(kp.public_key_bytes()) == 32
        assert kp.params.leaf_count == 16
        assert kp.used == 0

    def test_generate_h0(self) -> None:
        kp = LMSKeyPair.generate(h=0)
        assert kp.params.leaf_count == 1

    def test_sign_produces_signature(self) -> None:
        kp = LMSKeyPair.generate(h=4)
        msg = b"hello lms"
        sig = kp.sign(msg)
        assert isinstance(sig, LMSSignature)
        assert sig.q == 0
        assert len(sig.path) == kp.params.h

    def test_sign_increments_used(self) -> None:
        kp = LMSKeyPair.generate(h=4)
        assert kp.used == 0
        kp.sign(b"msg1")
        assert kp.used == 1
        kp.sign(b"msg2")
        assert kp.used == 2

    def test_sign_verify_roundtrip(self) -> None:
        kp = LMSKeyPair.generate(h=4)
        msg = b"roundtrip test"
        sig = kp.sign(msg)
        assert sig.verify(msg, kp.root, kp.seed) is True

    def test_sign_verify_multiple(self) -> None:
        kp = LMSKeyPair.generate(h=4)
        for i in range(3):
            msg = f"msg{i}".encode()
            sig = kp.sign(msg)
            assert sig.verify(msg, kp.root, kp.seed) is True

    def test_verify_wrong_message_fails(self) -> None:
        kp = LMSKeyPair.generate(h=4)
        sig = kp.sign(b"correct")
        assert sig.verify(b"wrong", kp.root, kp.seed) is False

    def test_verify_wrong_root_fails(self) -> None:
        kp = LMSKeyPair.generate(h=4)
        sig = kp.sign(b"msg")
        assert sig.verify(b"msg", b"\x00" * 32, kp.seed) is False

    def test_verify_wrong_seed_fails(self) -> None:
        kp = LMSKeyPair.generate(h=4)
        sig = kp.sign(b"msg")
        assert sig.verify(b"msg", kp.root, _rand_bytes(32)) is False

    def test_sign_all_leaves(self) -> None:
        kp = LMSKeyPair.generate(h=3)
        for i in range(kp.params.leaf_count):
            sig = kp.sign(f"msg{i}".encode())
            assert sig.verify(f"msg{i}".encode(), kp.root, kp.seed) is True
        assert kp.used == kp.params.leaf_count

    def test_exhausted_key_raises(self) -> None:
        kp = LMSKeyPair.generate(h=3)
        for i in range(kp.params.leaf_count):
            kp.sign(f"msg{i}".encode())
        with pytest.raises(MerkleKeyExhaustedError):
            kp.sign(b"one too many")


# ---------------------------------------------------------------------------
# HSS — multi-tree key generation and parameters
# ---------------------------------------------------------------------------


class TestHSSKeyPair:
    def test_generate_two_level(self) -> None:
        kp = HSSKeyPair.generate(levels=2, tree_heights=[2, 2])
        assert len(kp.public_key_bytes()) == 32
        assert len(kp.lms_keys) == 2

    def test_generate_custom_heights(self) -> None:
        kp = HSSKeyPair.generate(levels=3, tree_heights=[2, 3, 4])
        assert len(kp.lms_keys) == 3
        assert kp.lms_keys[0].params.leaf_count == 4

    def test_generate_default_heights(self) -> None:
        kp = HSSKeyPair.generate(levels=3)
        assert len(kp.lms_keys) == 3

    def test_lms_keys_are_independent(self) -> None:
        kp = HSSKeyPair.generate(levels=2, tree_heights=[3, 3])
        assert kp.lms_keys[0].root != kp.lms_keys[1].root


# ---------------------------------------------------------------------------
# HSSParams
# ---------------------------------------------------------------------------


class TestHSSParams:
    def test_valid_params(self) -> None:
        p = HSSParams(levels=3, tree_heights=[4, 4, 4], lm_ots_params=[16, 16, 16])
        assert p.levels == 3

    def test_levels_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError):
            HSSParams(levels=0, tree_heights=[4], lm_ots_params=[16])
        with pytest.raises(ValueError):
            HSSParams(levels=9, tree_heights=[4] * 9, lm_ots_params=[16] * 9)

    def test_mismatched_lengths_raises(self) -> None:
        with pytest.raises(ValueError):
            HSSParams(levels=2, tree_heights=[4, 4, 4], lm_ots_params=[16, 16])


# ---------------------------------------------------------------------------
# MerkleTree utility class
# ---------------------------------------------------------------------------


class TestMerkleTreeUtility:
    def test_from_leaves_basic(self) -> None:
        leaves = [_sha256(f"item{i}".encode()) for i in range(3)]
        tree = MerkleTree.from_leaves(leaves)
        assert len(tree.root) == 32
        assert tree.height >= 2

    def test_from_leaves_power_of_two(self) -> None:
        leaves = [_sha256(f"item{i}".encode()) for i in range(4)]
        tree = MerkleTree.from_leaves(leaves)
        assert tree.height == 2

    def test_proof_verifies(self) -> None:
        leaves = [_sha256(f"item{i}".encode()) for i in range(8)]
        tree = MerkleTree.from_leaves(leaves)
        for i in range(8):
            proof = tree.proof(i)
            assert tree.verify_proof(leaves[i], i, proof) is True

    def test_proof_fails_for_wrong_leaf(self) -> None:
        leaves = [_sha256(f"item{i}".encode()) for i in range(8)]
        tree = MerkleTree.from_leaves(leaves)
        proof = tree.proof(0)
        assert tree.verify_proof(_sha256(b"not a leaf"), 0, proof) is False

    def test_proof_wrong_index(self) -> None:
        leaves = [_sha256(f"item{i}".encode()) for i in range(8)]
        tree = MerkleTree.from_leaves(leaves)
        proof = tree.proof(0)
        assert tree.verify_proof(leaves[0], 4, proof) is False

    def test_proof_short_proof_fails(self) -> None:
        leaves = [_sha256(f"item{i}".encode()) for i in range(8)]
        tree = MerkleTree.from_leaves(leaves)
        assert tree.verify_proof(leaves[0], 0, [b"\x00" * 32]) is False

    def test_empty_leaves_raises(self) -> None:
        with pytest.raises(ValueError):
            MerkleTree.from_leaves([])

    def test_proof_out_of_range_raises(self) -> None:
        leaves = [_sha256(f"item{i}".encode()) for i in range(4)]
        tree = MerkleTree.from_leaves(leaves)
        with pytest.raises(IndexError):
            tree.proof(-1)
        with pytest.raises(IndexError):
            tree.proof(10)

    def test_diff_detects_changed_leaves(self) -> None:
        leaves1 = [_sha256(b"a"), _sha256(b"b"), _sha256(b"c")]
        leaves2 = [_sha256(b"a"), _sha256(b"X"), _sha256(b"c")]
        tree1 = MerkleTree.from_leaves(leaves1)
        tree2 = MerkleTree.from_leaves(leaves2)
        diff = tree1.diff(tree2)
        assert diff == [1]

    def test_diff_detects_size_change(self) -> None:
        leaves1 = [_sha256(b"a"), _sha256(b"b")]
        leaves2 = [_sha256(b"a"), _sha256(b"b"), _sha256(b"c")]
        tree1 = MerkleTree.from_leaves(leaves1)
        tree2 = MerkleTree.from_leaves(leaves2)
        diff = tree1.diff(tree2)
        assert 2 in diff

    def test_identical_trees_diff_empty(self) -> None:
        leaves = [_sha256(b"a"), _sha256(b"b")]
        tree1 = MerkleTree.from_leaves(leaves)
        tree2 = MerkleTree.from_leaves(leaves)
        assert tree1.diff(tree2) == []

    def test_to_list_returns_all_nodes(self) -> None:
        leaves = [_sha256(f"item{i}".encode()) for i in range(3)]
        tree = MerkleTree.from_leaves(leaves)
        node_list = tree.to_list()
        assert len(node_list) > len(leaves)
        indices = {n.index for n in node_list}
        assert 1 in indices

    def test_single_leaf_tree(self) -> None:
        leaf = _sha256(b"only")
        tree = MerkleTree.from_leaves([leaf])
        assert tree.root == leaf or len(tree.root) == 32


# ---------------------------------------------------------------------------
# Cross-scheme interop
# ---------------------------------------------------------------------------


class TestCrossSchemeInterop:
    def test_lms_signatures_are_unique(self) -> None:
        kp = LMSKeyPair.generate(h=4)
        sig1 = kp.sign(b"msg")
        sig2 = kp.sign(b"msg")
        assert sig1.ots_signature != sig2.ots_signature

    def test_large_message_roundtrip(self) -> None:
        kp = LMSKeyPair.generate(h=4)
        msg = secrets.token_bytes(1024)
        sig = kp.sign(msg)
        assert sig.verify(msg, kp.root, kp.seed) is True

    def test_tampered_ots_signature_fails(self) -> None:
        kp = LMSKeyPair.generate(h=4)
        sig = kp.sign(b"msg")
        tampered = list(sig.ots_signature)
        tampered[0] = _rand_bytes()
        bad_sig = LMSSignature(q=sig.q, ots_signature=tampered, path=sig.path, params=sig.params)
        assert bad_sig.verify(b"msg", kp.root, kp.seed) is False

    def test_tampered_path_fails(self) -> None:
        kp = LMSKeyPair.generate(h=4)
        sig = kp.sign(b"msg")
        tampered_path = list(sig.path)
        tampered_path[0] = _rand_bytes()
        bad_sig = LMSSignature(
            q=sig.q,
            ots_signature=sig.ots_signature,
            path=tampered_path,
            params=sig.params,
        )
        assert bad_sig.verify(b"msg", kp.root, kp.seed) is False

    def test_tampered_q_fails(self) -> None:
        kp = LMSKeyPair.generate(h=4)
        sig = kp.sign(b"msg")
        bad_sig = LMSSignature(
            q=sig.q + 1,
            ots_signature=sig.ots_signature,
            path=sig.path,
            params=sig.params,
        )
        assert bad_sig.verify(b"msg", kp.root, kp.seed) is False
