"""Deep tests for MinHash and LSH — Jaccard estimation, LSH banding, and edge cases."""

from __future__ import annotations

import pytest

from general_ludd.probabilistic.minhash import LSH, MinHash, _murmur64


def _tokenize(text: str) -> list[str]:
    return text.lower().split()


class TestMurmur64:
    def test_deterministic(self) -> None:
        a = _murmur64(b"hello", 0)
        b = _murmur64(b"hello", 0)
        assert a == b

    def test_different_keys_produce_different_hashes(self) -> None:
        a = _murmur64(b"hello", 0)
        b = _murmur64(b"world", 0)
        assert a != b

    def test_different_seeds_produce_different_hashes(self) -> None:
        a = _murmur64(b"data", 1)
        b = _murmur64(b"data", 2)
        assert a != b

    def test_output_is_nonnegative(self) -> None:
        for i in range(100):
            h = _murmur64(str(i).encode(), i)
            assert 0 <= h < 2**63


class TestMinHash:
    def test_construct_and_properties(self) -> None:
        mh = MinHash(num_perm=64)
        assert mh.num_perm == 64
        assert len(mh) == 64
        assert mh.seed == 42

    def test_default_construct(self) -> None:
        mh = MinHash()
        assert mh.num_perm == 128

    def test_num_perm_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="num_perm must be >= 1"):
            MinHash(num_perm=0)

    def test_num_perm_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="num_perm must be >= 1"):
            MinHash(num_perm=-5)

    def test_empty_signature_is_all_max_value(self) -> None:
        mh = MinHash(num_perm=32)
        sig = mh.signature
        assert len(sig) == 32
        assert all(s == 0x7FFFFFFFFFFFFFFF for s in sig)

    def test_signature_is_immutable_tuple(self) -> None:
        mh = MinHash(num_perm=8)
        sig = mh.signature
        assert isinstance(sig, tuple)
        with pytest.raises((TypeError, AttributeError)):
            sig[0] = 0  # type: ignore[index]

    def test_single_element_update(self) -> None:
        mh = MinHash(num_perm=32)
        mh.update("hello")
        sig = mh.signature
        assert all(s < 0x7FFFFFFFFFFFFFFF for s in sig)

    def test_identical_sets_estimate_one(self) -> None:
        mh1 = MinHash(num_perm=256)
        mh2 = MinHash(num_perm=256)
        words = _tokenize("the quick brown fox jumps over the lazy dog")
        mh1.add_many(words)
        mh2.add_many(words)
        jac = mh1.jaccard(mh2)
        assert jac == 1.0

    def test_disjoint_sets_estimate_near_zero(self) -> None:
        mh1 = MinHash(num_perm=256)
        mh2 = MinHash(num_perm=256)
        mh1.add_many(["a", "b", "c", "d", "e"])
        mh2.add_many(["f", "g", "h", "i", "j"])
        jac = mh1.jaccard(mh2)
        assert jac <= 0.10

    def test_partial_overlap_approximates_true_jaccard(self) -> None:
        set_a = {"cat", "dog", "mouse", "rabbit", "hamster"}
        set_b = {"dog", "mouse", "parrot", "snake", "lizard"}
        true_jaccard = len(set_a & set_b) / len(set_a | set_b)
        mh1 = MinHash(num_perm=512)
        mh2 = MinHash(num_perm=512)
        mh1.add_many(list(set_a))
        mh2.add_many(list(set_b))
        est = mh1.jaccard(mh2)
        assert abs(est - true_jaccard) < 0.12

    def test_large_set_approximation(self) -> None:
        import random as rng

        rng.seed(0)
        words_a = [f"word_{rng.randint(1, 2000)}" for _ in range(500)]
        words_b = [f"word_{rng.randint(1000, 3000)}" for _ in range(500)]
        set_a = set(words_a)
        set_b = set(words_b)
        true_jac = len(set_a & set_b) / len(set_a | set_b)
        mh1 = MinHash(num_perm=512)
        mh2 = MinHash(num_perm=512)
        mh1.add_many(words_a)
        mh2.add_many(words_b)
        est = mh1.jaccard(mh2)
        assert abs(est - true_jac) < 0.10

    def test_bytes_vs_str_ascii_yield_same_signature(self) -> None:
        mh_str = MinHash(num_perm=64)
        mh_bytes = MinHash(num_perm=64)
        mh_str.update("hello")
        mh_bytes.update(b"hello")
        assert mh_str.jaccard(mh_bytes) == 1.0

    def test_non_ascii_str_roundtrips_in_signature(self) -> None:
        mh = MinHash(num_perm=64)
        mh.update("café")
        assert all(s < 0x7FFFFFFFFFFFFFFF for s in mh.signature)

    def test_different_seeds_produce_different_signatures(self) -> None:
        mh1 = MinHash(num_perm=64, seed=1)
        mh2 = MinHash(num_perm=64, seed=2)
        mh1.update("test")
        mh2.update("test")
        assert mh1.signature != mh2.signature
        with pytest.raises(ValueError, match="seeds differ"):
            mh1.jaccard(mh2)

    def test_add_many_equals_individual_updates(self) -> None:
        mh1 = MinHash(num_perm=128)
        mh2 = MinHash(num_perm=128)
        items = ["x", "y", "z", "w"]
        mh1.add_many(items)
        for item in items:
            mh2.update(item)
        assert mh1.jaccard(mh2) == 1.0

    def test_merge_is_pairwise_min(self) -> None:
        mh1 = MinHash(num_perm=128)
        mh2 = MinHash(num_perm=128)
        mh1.add_many(["a", "b"])
        mh2.add_many(["b", "c"])
        merged = mh1.merge(mh2)
        for i in range(128):
            expected = min(mh1.signature[i], mh2.signature[i])
            assert merged.signature[i] == expected

    def test_merge_incompatible_sizes_raises(self) -> None:
        mh1 = MinHash(num_perm=64)
        mh2 = MinHash(num_perm=128)
        with pytest.raises(ValueError, match="incompatible MinHash sizes"):
            mh1.merge(mh2)

    def test_jaccard_incompatible_sizes_raises(self) -> None:
        mh1 = MinHash(num_perm=64)
        mh2 = MinHash(num_perm=128)
        with pytest.raises(ValueError, match="incompatible MinHash sizes"):
            mh1.jaccard(mh2)

    def test_serialization_roundtrip(self) -> None:
        mh = MinHash(num_perm=128, seed=99)
        mh.add_many(["alpha", "beta", "gamma", "delta"])
        raw = mh.to_bytes()
        restored = MinHash.from_bytes(raw)
        assert restored.num_perm == mh.num_perm
        assert restored.seed == mh.seed
        assert restored.signature == mh.signature
        assert mh.jaccard(restored) == 1.0

    def test_from_bytes_raises_on_truncated(self) -> None:
        with pytest.raises(ValueError, match="truncated MinHash data"):
            MinHash.from_bytes(b"\x00" * 3)

    def test_from_bytes_raises_on_body_too_short(self) -> None:
        header = b"\x00\x00\x00\x20\x00\x00\x00\x2a\x00\x00\x00\x00"
        with pytest.raises(ValueError, match="signature body truncated"):
            MinHash.from_bytes(header + b"\x00" * 10)

    def test_repr(self) -> None:
        mh = MinHash(num_perm=64, seed=7)
        r = repr(mh)
        assert "MinHash" in r
        assert "64" in r
        assert "7" in r

    def test_update_int_and_float(self) -> None:
        mh = MinHash(num_perm=64)
        mh.update(42)
        mh.update(3.14)
        sig = mh.signature
        assert all(s < 0x7FFFFFFFFFFFFFFF for s in sig)

    def test_jaccard_symmetric(self) -> None:
        mh1 = MinHash(num_perm=128)
        mh2 = MinHash(num_perm=128)
        mh1.add_many(["x", "y"])
        mh2.add_many(["y", "z"])
        j1 = mh1.jaccard(mh2)
        j2 = mh2.jaccard(mh1)
        assert j1 == pytest.approx(j2)


class TestLSH:
    def test_construct_defaults(self) -> None:
        lsh = LSH()
        assert lsh.num_perm == 128
        assert lsh.bands == 16
        assert lsh.rows == 8
        assert lsh.item_count == 0

    def test_bands_must_divide_num_perm(self) -> None:
        with pytest.raises(ValueError, match="must evenly divide"):
            LSH(num_perm=100, bands=7)

    def test_bands_one_raises(self) -> None:
        with pytest.raises(ValueError, match="num_perm must be >= 1"):
            LSH(num_perm=0, bands=1)

    def test_insert_and_query_finds_similar(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        mh1 = MinHash(num_perm=128)
        mh2 = MinHash(num_perm=128)
        mh1.add_many(_tokenize("machine learning deep neural networks gradient optimization loss backprop"))
        mh2.add_many(_tokenize("machine learning deep neural networks gradient optimization loss backprop data"))
        lsh.insert("doc1", mh1)
        lsh.insert("doc2", mh2)
        candidates = lsh.query(mh1)
        assert "doc2" in candidates

    def test_query_on_dissimilar_finds_similar_only(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        mh_ml = MinHash(num_perm=128)
        mh_food = MinHash(num_perm=128)
        mh_query = MinHash(num_perm=128)
        mh_ml.add_many(_tokenize("gradient descent backpropagation optimizer loss function layer neuron"))
        mh_food.add_many(_tokenize("spaghetti carbonara bolognese recipe pasta sauce italian"))
        mh_query.add_many(_tokenize("gradient descent backpropagation optimizer loss function layer query"))
        lsh.insert("ml", mh_ml)
        lsh.insert("food", mh_food)
        candidates = lsh.query(mh_query)
        assert "ml" in candidates
        assert "food" not in candidates

    def test_insert_incompatible_num_perm_raises(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        mh = MinHash(num_perm=64)
        with pytest.raises(ValueError, match="num_perm"):
            lsh.insert("bad", mh)

    def test_query_incompatible_num_perm_raises(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        mh = MinHash(num_perm=64)
        with pytest.raises(ValueError, match="num_perm"):
            lsh.query(mh)

    def test_remove_removes_from_items(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        mh = MinHash(num_perm=128)
        mh.update("data")
        lsh.insert("key1", mh)
        assert lsh.item_count == 1
        lsh.remove("key1")
        assert lsh.item_count == 0

    def test_remove_nonexistent_raises(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        with pytest.raises(KeyError):
            lsh.remove("nope")

    def test_removed_key_not_in_query(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        mh1 = MinHash(num_perm=128)
        mh2 = MinHash(num_perm=128)
        mh1.add_many(_tokenize("hello world"))
        mh2.add_many(_tokenize("hello earth"))
        lsh.insert("a", mh1)
        lsh.insert("b", mh2)
        lsh.remove("a")
        candidates = lsh.query(mh1)
        assert "a" not in candidates

    def test_similarity_threshold_formula(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        expected = (1.0 / 16) ** (1.0 / 8)
        assert lsh.similarity_threshold() == pytest.approx(expected)

    def test_similar_items_above_threshold(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        threshold = lsh.similarity_threshold()
        mh1 = MinHash(num_perm=128)
        mh2 = MinHash(num_perm=128)
        words = [f"shared_{i}" for i in range(50)] + [f"uniq_{i}" for i in range(10)]
        mh1.add_many(words)
        mh2.add_many(words)
        jac = mh1.jaccard(mh2)
        assert jac > threshold

    def test_different_band_configs(self) -> None:
        for bands in [2, 4, 8, 32, 64]:
            lsh = LSH(num_perm=128, bands=bands)
            assert lsh.rows == 128 // bands
            assert lsh.bands == bands

    def test_empty_lsh_query_returns_empty(self) -> None:
        lsh = LSH(num_perm=128, bands=16)
        mh = MinHash(num_perm=128)
        mh.update("x")
        assert lsh.query(mh) == []
