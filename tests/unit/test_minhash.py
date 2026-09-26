"""Unit tests for MinHash and LSH."""

import pytest

from general_ludd.probabilistic.minhash import LSH, MinHash


class TestMinHashInit:
    def test_default(self):
        mh = MinHash()
        assert mh.num_perm == 128
        assert mh.seed == 42
        assert len(mh.signature) == 128

    def test_invalid_num_perm(self):
        with pytest.raises(ValueError, match="num_perm must be >= 1"):
            MinHash(num_perm=0)

    def test_properties(self):
        mh = MinHash(num_perm=64, seed=7)
        assert mh.num_perm == 64
        assert mh.seed == 7
        assert len(mh) == 64


class TestMinHashUpdate:
    def test_update_and_jaccard(self):
        mh1 = MinHash(num_perm=64)
        mh2 = MinHash(num_perm=64)
        for item in ["a", "b", "c"]:
            mh1.update(item)
            mh2.update(item)
        assert mh1.jaccard(mh2) == 1.0

    def test_jaccard_similarity_estimate(self):
        mh1 = MinHash(num_perm=128)
        mh2 = MinHash(num_perm=128)
        for item in ["a", "b", "c", "d", "e"]:
            mh1.update(item)
        for item in ["a", "b", "c", "x", "y"]:
            mh2.update(item)
        j = mh1.jaccard(mh2)
        assert 0.0 <= j <= 1.0

    def test_add_many(self):
        mh = MinHash(num_perm=64)
        mh.add_many(["a", "b", "c"])
        mh2 = MinHash(num_perm=64)
        for item in ["a", "b", "c"]:
            mh2.update(item)
        assert mh.jaccard(mh2) == 1.0

    def test_incompatible_sizes(self):
        mh1 = MinHash(num_perm=64)
        mh2 = MinHash(num_perm=128)
        with pytest.raises(ValueError, match="incompatible MinHash sizes"):
            mh1.jaccard(mh2)

    def test_incompatible_seeds(self):
        mh1 = MinHash(seed=1)
        mh2 = MinHash(seed=2)
        with pytest.raises(ValueError, match="incompatible MinHash seeds"):
            mh1.jaccard(mh2)


class TestMinHashMerge:
    def test_merge(self):
        mh1 = MinHash(num_perm=64)
        mh2 = MinHash(num_perm=64)
        mh1.update("a")
        mh1.update("b")
        mh2.update("b")
        mh2.update("c")
        merged = mh1.merge(mh2)
        assert merged.num_perm == 64
        assert 0.0 <= mh1.jaccard(merged) <= 1.0


class TestMinHashSerialization:
    def test_roundtrip(self):
        mh = MinHash(num_perm=64, seed=7)
        mh.update("hello")
        raw = mh.to_bytes()
        mh2 = MinHash.from_bytes(raw)
        assert mh2.num_perm == 64
        assert mh2.seed == 7
        assert mh.jaccard(mh2) == 1.0

    def test_from_bytes_truncated(self):
        with pytest.raises(ValueError, match="truncated MinHash data"):
            MinHash.from_bytes(b"\x00")

    def test_from_bytes_body_truncated(self):
        header = (64).to_bytes(4, "big") + (1).to_bytes(4, "big") + (0).to_bytes(4, "big", signed=True)
        with pytest.raises(ValueError, match="signature body truncated"):
            MinHash.from_bytes(header + b"\x00")


class TestMinHashRepr:
    def test_repr(self):
        mh = MinHash(num_perm=64, seed=7)
        assert "MinHash" in repr(mh)


class TestLSH:
    def test_insert_and_query(self):
        lsh = LSH(num_perm=64, bands=8)
        mh = MinHash(num_perm=64)
        mh.update("hello")
        lsh.insert("a", mh)
        assert lsh.item_count == 1
        assert "a" in lsh.query(mh)

    def test_query_empty_index(self):
        lsh = LSH(num_perm=64, bands=8)
        mh = MinHash(num_perm=64)
        mh.update("hello")
        assert lsh.query(mh) == []

    def test_remove(self):
        lsh = LSH(num_perm=64, bands=8)
        mh = MinHash(num_perm=64)
        mh.update("hello")
        lsh.insert("a", mh)
        lsh.remove("a")
        assert lsh.item_count == 0
        assert "a" not in lsh.query(mh)

    def test_remove_missing(self):
        lsh = LSH(num_perm=64, bands=8)
        with pytest.raises(KeyError):
            lsh.remove("missing")

    def test_incompatible_num_perm(self):
        lsh = LSH(num_perm=64, bands=8)
        mh = MinHash(num_perm=128)
        with pytest.raises(ValueError, match="MinHash num_perm"):
            lsh.insert("a", mh)

    def test_incompatible_seed(self):
        lsh = LSH(num_perm=64, bands=8)
        mh1 = MinHash(num_perm=64, seed=1)
        mh2 = MinHash(num_perm=64, seed=2)
        lsh.insert("a", mh1)
        with pytest.raises(ValueError, match="incompatible MinHash seeds"):
            lsh.insert("b", mh2)

    def test_invalid_bands(self):
        with pytest.raises(ValueError, match="bands"):
            LSH(num_perm=64, bands=5)

    def test_similarity_threshold(self):
        lsh = LSH(num_perm=64, bands=8)
        assert 0.0 < lsh.similarity_threshold() < 1.0
