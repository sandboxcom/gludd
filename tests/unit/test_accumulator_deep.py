"""Deep tests for cryptographic accumulators: Merkle tree inclusion/exclusion
proofs, batch proofs, RSA universal accumulator membership/non-membership
witnesses, and edge cases.
"""

from __future__ import annotations

import copy
import hashlib
import random
import secrets

import pytest

from general_ludd.algorithms.accumulator import (
    AccumulatorError,
    MerkleTree,
    RSAConfig,
    RSAUniversalAccumulator,
    _leaf_hash,
    _pair_hash,
)

# ===========================================================================
# MerkleTree
# ===========================================================================

_SHA256_EMPTY = b""

_AB_LEAF_A = hashlib.sha256(b"\x00a").digest()
_AB_LEAF_B = hashlib.sha256(b"\x00b").digest()
_AB_ROOT = _pair_hash(_AB_LEAF_A, _AB_LEAF_B)


class TestMerkleTreeConstruction:
    def test_empty_tree(self) -> None:
        t = MerkleTree([])
        assert t.root == b""
        assert len(t) == 0
        assert t.leaf_count == 0
        assert t.leaves == []
        assert t.leaf_hashes == []

    def test_single_leaf(self) -> None:
        t = MerkleTree([b"hello"])
        assert len(t) == 1
        assert t.root == _leaf_hash(b"hello")
        assert t.leaves == [b"hello"]

    def test_two_leaves(self) -> None:
        t = MerkleTree([b"a", b"b"])
        assert len(t) == 2
        assert t.root == _AB_ROOT
        assert t.leaf_hashes == [_AB_LEAF_A, _AB_LEAF_B]

    def test_three_leaves(self) -> None:
        t = MerkleTree([b"a", b"b", b"c"])
        assert t.leaf_count == 3
        assert len(t.root) == 32
        l2 = _pair_hash(_AB_LEAF_A, _AB_LEAF_B)
        lc = _leaf_hash(b"c")
        lc_pair = _pair_hash(lc, lc)
        assert t.root == _pair_hash(l2, lc_pair)

    def test_four_leaves(self) -> None:
        t = MerkleTree([b"a", b"b", b"c", b"d"])
        assert t.leaf_count == 4
        l1 = _pair_hash(_leaf_hash(b"a"), _leaf_hash(b"b"))
        l2 = _pair_hash(_leaf_hash(b"c"), _leaf_hash(b"d"))
        assert t.root == _pair_hash(l1, l2)

    def test_many_leaves_produces_root(self) -> None:
        leaves = [secrets.token_bytes(32) for _ in range(100)]
        t = MerkleTree(leaves)
        assert len(t.root) == 32
        assert len(t) == 100

    def test_large_power_of_two(self) -> None:
        leaves = [str(i).encode() for i in range(256)]
        t = MerkleTree(leaves)
        assert len(t.root) == 32

    def test_custom_hash_functions(self) -> None:
        seen: list[bytes] = []

        def hlf(data: bytes) -> bytes:
            seen.append(data)
            return hashlib.sha256(data).digest()

        def hpr(left: bytes, right: bytes) -> bytes:
            seen.append(left + right)
            return hashlib.sha256(left + right).digest()

        t = MerkleTree([b"x", b"y"], hash_leaf=hlf, hash_pair=hpr)
        assert t.leaf_count == 2
        assert len(t.root) == 32


class TestMerkleTreeDeterminism:
    def test_same_leaves_same_root(self) -> None:
        leaves = [b"one", b"two", b"three"]
        t1 = MerkleTree(leaves)
        t2 = MerkleTree(leaves)
        assert t1.root == t2.root

    def test_different_leaves_different_root(self) -> None:
        t1 = MerkleTree([b"a", b"b"])
        t2 = MerkleTree([b"a", b"c"])
        assert t1.root != t2.root

    def test_duplicate_leaves_still_deterministic(self) -> None:
        t1 = MerkleTree([b"x", b"x"])
        t2 = MerkleTree([b"x", b"x"])
        assert t1.root == t2.root


class TestMerkleInclusionProof:
    def test_single_leaf_proof(self) -> None:
        t = MerkleTree([b"only"])
        proof = t.inclusion_proof(0)
        assert proof == []
        assert MerkleTree.verify_inclusion(t.leaf_hashes[0], 0, proof, t.root)

    def test_two_leaf_inclusion(self) -> None:
        t = MerkleTree([b"a", b"b"])
        p0 = t.inclusion_proof(0)
        assert len(p0) == 1
        assert p0[0][1] is True  # sibling is right child
        assert MerkleTree.verify_inclusion(t.leaf_hashes[0], 0, p0, t.root)

        p1 = t.inclusion_proof(1)
        assert len(p1) == 1
        assert p1[0][1] is False  # sibling is left child
        assert MerkleTree.verify_inclusion(t.leaf_hashes[1], 1, p1, t.root)

    def test_four_leaf_inclusion(self) -> None:
        leaves = [b"a", b"b", b"c", b"d"]
        t = MerkleTree(leaves)
        for i in range(4):
            proof = t.inclusion_proof(i)
            assert MerkleTree.verify_inclusion(t.leaf_hashes[i], i, proof, t.root)

    def test_proof_length_is_tree_height(self) -> None:
        for n in range(1, 33):
            t = MerkleTree([str(i).encode() for i in range(n)])
            for idx in [0, n // 2, n - 1]:
                proof = t.inclusion_proof(idx)
                assert len(proof) <= n.bit_length()

    def test_tampered_leaf_rejected(self) -> None:
        t = MerkleTree([b"a", b"b", b"c"])
        proof = t.inclusion_proof(0)
        fake_hash = hashlib.sha256(b"\x00WRONG").digest()
        assert not MerkleTree.verify_inclusion(fake_hash, 0, proof, t.root)

    def test_wrong_index_in_deep_tree_rejected(self) -> None:
        t = MerkleTree([b"a", b"b", b"c", b"d"])
        proof = t.inclusion_proof(0)
        assert MerkleTree.verify_inclusion(t.leaf_hashes[0], 0, proof, t.root)
        assert not MerkleTree.verify_inclusion(t.leaf_hashes[0], 2, proof, t.root)

    def test_tampered_sibling_rejected(self) -> None:
        t = MerkleTree([b"a", b"b", b"c"])
        proof = t.inclusion_proof(0)
        bad_proof = copy.deepcopy(proof)
        bad_proof[0] = (b"\x00" * 32, bad_proof[0][1])
        assert not MerkleTree.verify_inclusion(t.leaf_hashes[0], 0, bad_proof, t.root)

    def test_index_out_of_range(self) -> None:
        t = MerkleTree([b"a"])
        with pytest.raises(IndexError):
            t.inclusion_proof(1)
        with pytest.raises(IndexError):
            t.inclusion_proof(-1)

    def test_empty_tree_verification(self) -> None:
        t = MerkleTree([])
        assert t.root == b""
        assert MerkleTree.verify_inclusion(b"", 0, [], b"")


class TestMerkleBatchInclusion:
    def test_batch_empty_indices(self) -> None:
        t = MerkleTree([b"a", b"b"])
        assert t.inclusion_proof_batch([]) == []

    def test_batch_single_index(self) -> None:
        t = MerkleTree([b"a", b"b", b"c", b"d"])
        proof = t.inclusion_proof_batch([2])
        assert len(proof) >= 1

    def test_batch_all_indices(self) -> None:
        t = MerkleTree([b"a", b"b", b"c", b"d"])
        proof = t.inclusion_proof_batch([0, 1, 2, 3])
        assert len(proof) == 0


class TestMerkleExclusionProof:
    def test_exclusion_on_empty(self) -> None:
        t = MerkleTree([])
        assert t.exclusion_proof(b"anything") is None

    def test_exclusion_present_returns_none(self) -> None:
        t = MerkleTree([b"a", b"b"])
        assert t.exclusion_proof(b"a") is None

    def test_exclusion_absent_basic(self) -> None:
        t = MerkleTree([b"a", b"c"])
        result = t.exclusion_proof(b"b")
        assert result is not None
        index, _left, _right, _target = result
        assert index in (0, 1, 2)

    def test_exclusion_before_all(self) -> None:
        t = MerkleTree([b"b", b"c"])
        result = t.exclusion_proof(b"a")
        assert result is not None
        _index, left, right, _target = result
        assert left == b""
        assert right != b""

    def test_exclusion_after_all(self) -> None:
        t = MerkleTree([b"a", b"b"])
        result = t.exclusion_proof(b"z")
        assert result is not None
        _index, left, right, _target = result
        assert left != b""
        assert right == b""

    def test_exclude_batch(self) -> None:
        t = MerkleTree([b"a", b"b", b"c"])
        results = t.exclude_batch([b"a", b"x", b"z", b"b"])
        assert results == [False, True, True, False]


class TestMerklePropertyIdentity:
    def test_root_zero_length_input(self) -> None:
        t = MerkleTree([])
        assert t.root == b""

    def test_root_with_identical_leaves(self) -> None:
        t = MerkleTree([b"x"] * 10)
        assert len(t.root) == 32

    def test_root_changes_with_new_leaf(self) -> None:
        data = [f"leaf{i}".encode() for i in range(5)]
        before = MerkleTree(data).root
        after = MerkleTree([*data, b"extra"]).root
        assert before != after

    def test_repr(self) -> None:
        t = MerkleTree([b"a", b"b"])
        r = repr(t)
        assert "MerkleTree" in r
        assert str(len(t)) in r


# ===========================================================================
# RSA universal accumulator (small key for speed)
# ===========================================================================


@pytest.fixture(scope="module")
def rsa_config() -> RSAConfig:
    return RSAConfig(bits=512)


@pytest.fixture
def acc(rsa_config: RSAConfig) -> RSAUniversalAccumulator:
    return RSAUniversalAccumulator(rsa_config)


class TestRSAConfig:
    def test_config_generates_keys(self, rsa_config: RSAConfig) -> None:
        assert rsa_config.N > 0
        assert rsa_config.G > 1
        assert rsa_config.G < rsa_config.N
        assert rsa_config.p > 1
        assert rsa_config.q > 1

    def test_config_modulus_is_product(self, rsa_config: RSAConfig) -> None:
        assert rsa_config.p * rsa_config.q == rsa_config.N

    def test_min_bits_rejected(self) -> None:
        with pytest.raises(AccumulatorError, match="bits"):
            RSAConfig(bits=32)


class TestAccumulatorEmpty:
    def test_empty_value_is_generator(self, acc: RSAUniversalAccumulator) -> None:
        assert acc.value == acc.config.G
        assert acc.element_count() == 0

    def test_empty_elements(self, acc: RSAUniversalAccumulator) -> None:
        assert acc.elements == set()


class TestAccumulatorAdd:
    def test_add_increments_count(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"apple")
        assert acc.element_count() == 1
        assert acc.value != acc.config.G

    def test_add_multiple(self, acc: RSAUniversalAccumulator) -> None:
        for e in [b"a", b"b", b"c"]:
            acc.add(e)
        assert acc.element_count() == 3

    def test_add_duplicate_is_idempotent(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"x")
        before = acc.value
        acc.add(b"x")
        assert acc.element_count() == 1
        assert acc.value == before

    def test_add_commutative(self, rsa_config: RSAConfig) -> None:
        a1 = RSAUniversalAccumulator(rsa_config)
        a1.add(b"x")
        a1.add(b"y")
        a2 = RSAUniversalAccumulator(rsa_config)
        a2.add(b"y")
        a2.add(b"x")
        assert a1.value == a2.value


class TestAccumulatorRemove:
    def test_remove_present(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"x")
        acc.add(b"y")
        acc.remove(b"x")
        assert acc.element_count() == 1

    def test_remove_last(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"only")
        acc.remove(b"only")
        assert acc.element_count() == 0
        assert acc.value == acc.config.G

    def test_remove_missing_raises(self, acc: RSAUniversalAccumulator) -> None:
        with pytest.raises(AccumulatorError, match="not in"):
            acc.remove(b"nope")


class TestAccumulatorWitness:
    def test_witness_verifies(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"e1")
        acc.add(b"e2")
        w = acc.witness(b"e1")
        assert acc.verify_witness(b"e1", w)

    def test_witness_does_not_verify_wrong_element(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"e1")
        acc.add(b"e2")
        w = acc.witness(b"e1")
        assert not acc.verify_witness(b"e2", w)

    def test_witness_does_not_verify_non_member(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"e1")
        w = acc.witness(b"e1")
        assert not acc.verify_witness(b"outside", w)

    def test_witness_for_missing_raises(self, acc: RSAUniversalAccumulator) -> None:
        with pytest.raises(AccumulatorError, match="not in"):
            acc.witness(b"nope")

    def test_witness_single_element(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"single")
        w = acc.witness(b"single")
        assert w == acc.config.G
        assert acc.verify_witness(b"single", w)


class TestAccumulatorNonMembership:
    def test_non_member_get_proof(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"e1")
        acc.add(b"e2")
        proof = acc.non_membership_proof(b"outside")
        assert proof is not None
        assert acc.verify_non_membership(b"outside", proof)

    def test_member_gets_none(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"e1")
        assert acc.non_membership_proof(b"e1") is None

    def test_empty_accumulator_non_member(self, rsa_config: RSAConfig) -> None:
        a = RSAUniversalAccumulator(rsa_config)
        proof = a.non_membership_proof(b"anything")
        if proof is not None:
            assert a.verify_non_membership(b"anything", proof)

    def test_non_member_wrong_proof_fails(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"e1")
        acc.add(b"e2")
        proof_x = acc.non_membership_proof(b"x")
        assert proof_x is not None
        assert not acc.verify_non_membership(b"y", proof_x)

    def test_non_member_proof_and_add(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"e1")
        proof = acc.non_membership_proof(b"e2")
        assert proof is not None
        assert acc.verify_non_membership(b"e2", proof)
        acc.add(b"e2")
        assert acc.non_membership_proof(b"e2") is None


class TestAccumulatorRoundTrip:
    def test_add_remove_back_to_empty(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"a")
        acc.add(b"b")
        acc.remove(b"a")
        acc.remove(b"b")
        assert acc.element_count() == 0
        assert acc.value == acc.config.G

    def test_large_set_witness(self, rsa_config: RSAConfig) -> None:
        a = RSAUniversalAccumulator(rsa_config)
        for i in range(20):
            a.add(str(i).encode())
        for i in range(20):
            w = a.witness(str(i).encode())
            assert a.verify_witness(str(i).encode(), w)

    def test_large_set_non_membership(self, rsa_config: RSAConfig) -> None:
        a = RSAUniversalAccumulator(rsa_config)
        for i in range(15):
            a.add(str(i).encode())
        for outsider in [b"alpha", b"beta", b"gamma", b"delta"]:
            proof = a.non_membership_proof(outsider)
            assert proof is not None
            assert a.verify_non_membership(outsider, proof)

    def test_witness_after_remove(self, acc: RSAUniversalAccumulator) -> None:
        acc.add(b"a")
        acc.add(b"b")
        acc.add(b"c")
        acc.remove(b"b")
        w_a = acc.witness(b"a")
        assert acc.verify_witness(b"a", w_a)
        w_c = acc.witness(b"c")
        assert acc.verify_witness(b"c", w_c)


class TestAccumulatorRelational:
    def test_same_set_same_value(self, rsa_config: RSAConfig) -> None:
        a1 = RSAUniversalAccumulator(rsa_config, [b"x", b"y", b"z"])
        a2 = RSAUniversalAccumulator(rsa_config, [b"x", b"y", b"z"])
        assert a1.value == a2.value

    def test_subset_different_value(self, rsa_config: RSAConfig) -> None:
        a1 = RSAUniversalAccumulator(rsa_config, [b"x", b"y", b"z"])
        a2 = RSAUniversalAccumulator(rsa_config, [b"x", b"y"])
        assert a1.value != a2.value


class TestAccumulatorFuzzing:
    def test_random_add_remove_witness(self, rsa_config: RSAConfig) -> None:
        a = RSAUniversalAccumulator(rsa_config)
        pool = [secrets.token_bytes(8) for _ in range(30)]
        rng = random.Random(42)
        inserted: list[bytes] = []
        for e in pool:
            a.add(e)
            inserted.append(e)
            if rng.random() < 0.3 and inserted:
                removed = inserted.pop(rng.randrange(len(inserted)))
                a.remove(removed)
        for e in inserted:
            w = a.witness(e)
            assert a.verify_witness(e, w)

    def test_random_exclusion_proofs(self, rsa_config: RSAConfig) -> None:
        a = RSAUniversalAccumulator(rsa_config)
        pool = [secrets.token_bytes(8) for _ in range(15)]
        for e in pool[:8]:
            a.add(e)
        outsiders = pool[8:]
        for o in outsiders:
            proof = a.non_membership_proof(o)
            if proof is not None:
                assert a.verify_non_membership(o, proof)


# ===========================================================================
# Utility / edge
# ===========================================================================


class TestLeafHash:
    def test_leaf_hash_is_salted(self) -> None:
        h = _leaf_hash(b"dog")
        plain = hashlib.sha256(b"dog").digest()
        assert h != plain

    def test_leaf_hash_deterministic(self) -> None:
        assert _leaf_hash(b"cat") == _leaf_hash(b"cat")

    def test_leaf_hash_different_for_different_inputs(self) -> None:
        assert _leaf_hash(b"a") != _leaf_hash(b"b")


class TestPairHash:
    def test_pair_hash_commutative(self) -> None:
        a = b"\x01" * 32
        b = b"\x02" * 32
        assert _pair_hash(a, b) == _pair_hash(b, a)

    def test_pair_hash_different_inputs(self) -> None:
        a = _pair_hash(b"\x01" * 32, b"\x02" * 32)
        b_value = _pair_hash(b"\x03" * 32, b"\x04" * 32)
        assert a != b_value
