"""Deep tests for persistent vector — conj, pop, assoc, peek, transient, structural sharing."""

from __future__ import annotations

import random

import pytest

from general_ludd.algorithms.persistent_vector import (
    PersistentVector,
    TransientVector,
)


class TestPersistentVectorCore:
    def test_empty_has_len_zero(self) -> None:
        v = PersistentVector.empty()
        assert len(v) == 0
        assert v.peek() is None

    def test_conj_and_nth(self) -> None:
        v = PersistentVector.empty()
        for i in range(100):
            v = v.conj(i)
        assert len(v) == 100
        for i in range(100):
            assert v[i] == i

    def test_negative_index(self) -> None:
        v = PersistentVector.empty()
        for i in range(50):
            v = v.conj(i * 2)
        assert v[-1] == 98
        assert v[-3] == 94
        assert v[-50] == 0

    def test_peek(self) -> None:
        v = PersistentVector.empty()
        assert v.peek() is None
        v = v.conj("a")
        assert v.peek() == "a"
        v = v.conj("b").conj("c")
        assert v.peek() == "c"

    def test_pop(self) -> None:
        v = PersistentVector.empty()
        for i in range(5):
            v = v.conj(i)
        v = v.pop()
        assert len(v) == 4
        assert list(v) == [0, 1, 2, 3]
        v = v.pop().pop()
        assert len(v) == 2
        assert list(v) == [0, 1]

    def test_pop_to_empty(self) -> None:
        v = PersistentVector.empty().conj(42)
        v = v.pop()
        assert len(v) == 0
        assert list(v) == []
        with pytest.raises(IndexError):
            v.pop()

    def test_assoc(self) -> None:
        v = PersistentVector.empty()
        for i in range(10):
            v = v.conj(i)
        v2 = v.assoc(3, 99).assoc(7, 77)
        assert v[3] == 3
        assert v2[3] == 99
        assert v2[7] == 77
        assert len(v2) == 10

    def test_assoc_negative_index(self) -> None:
        v = PersistentVector.empty()
        for i in range(10):
            v = v.conj(i)
        v2 = v.assoc(-1, 99)
        assert v2[9] == 99
        assert v2[0] == 0

    def test_assoc_out_of_bounds_raises(self) -> None:
        v = PersistentVector.empty().conj(1).conj(2)
        with pytest.raises(IndexError):
            v.assoc(5, 99)
        with pytest.raises(IndexError):
            v.assoc(-3, 99)

    def test_structural_sharing_after_conj(self) -> None:
        v1 = PersistentVector.empty()
        for i in range(100):
            v1 = v1.conj(i)
        v2 = v1.conj(100)
        assert len(v1) == 100
        assert len(v2) == 101
        for i in range(100):
            assert v1[i] == i
            assert v2[i] == i
        assert v2[100] == 100

    def test_structural_sharing_after_assoc(self) -> None:
        v1 = PersistentVector.empty()
        for i in range(100):
            v1 = v1.conj(i)
        v2 = v1.assoc(50, 999)
        assert v1[50] == 50
        assert v2[50] == 999
        for i in range(100):
            if i != 50:
                assert v1[i] == v2[i]

    def test_large_vector_correctness(self) -> None:
        v = PersistentVector.empty()
        n = 5000
        for i in range(n):
            v = v.conj(i)
        assert len(v) == n
        for idx in [0, 31, 32, 63, 64, 1023, 1024, n - 1]:
            assert v[idx] == idx

    def test_large_vector_pop_roundtrip(self) -> None:
        v = PersistentVector.empty()
        for i in range(2000):
            v = v.conj(i)
        for _ in range(1000):
            v = v.pop()
        assert len(v) == 1000
        for i in range(1000):
            assert v[i] == i

    def test_iteration(self) -> None:
        v = PersistentVector.empty()
        for i in range(33):
            v = v.conj(chr(65 + i))
        assert list(v) == [chr(65 + i) for i in range(33)]

    def test_equality(self) -> None:
        v1 = PersistentVector.empty()
        v2 = PersistentVector.empty()
        for i in range(20):
            v1 = v1.conj(i)
            v2 = v2.conj(i)
        assert v1 == v2
        v2 = v2.assoc(5, 999)
        assert v1 != v2

    def test_from_iterable(self) -> None:
        v = PersistentVector.from_iterable(range(100))
        assert len(v) == 100
        assert v[0] == 0
        assert v[99] == 99
        assert list(v) == list(range(100))


class TestTransientVector:
    def test_transient_conj_and_persistent(self) -> None:
        tv = TransientVector.empty()
        for i in range(100):
            tv = tv.conj(i)
        pv = tv.persistent()
        assert len(pv) == 100
        for i in range(100):
            assert pv[i] == i

    def test_transient_conj_many(self) -> None:
        tv = TransientVector.empty()
        n = 10000
        for i in range(n):
            tv = tv.conj(i)
        pv = tv.persistent()
        assert len(pv) == n
        assert pv[0] == 0
        assert pv[n - 1] == n - 1
        assert pv[1234] == 1234
        assert pv[8191] == 8191

    def test_transient_roundtrip_persistent(self) -> None:
        v = PersistentVector.empty()
        for i in range(500):
            v = v.conj(i)
        tv = v.transient()
        for i in range(500, 600):
            tv = tv.conj(i)
        pv = tv.persistent()
        assert len(pv) == 600
        for i in range(600):
            assert pv[i] == i

    def test_transient_assoc(self) -> None:
        tv = TransientVector.empty()
        for i in range(10):
            tv = tv.conj(i * 10)
        tv = tv.assoc(3, 999).assoc(7, 777)
        pv = tv.persistent()
        assert pv[3] == 999
        assert pv[7] == 777
        assert pv[0] == 0
        assert pv[9] == 90

    def test_transient_pop(self) -> None:
        tv = TransientVector.empty()
        for i in range(10):
            tv = tv.conj(i)
        tv = tv.pop().pop()
        pv = tv.persistent()
        assert len(pv) == 8
        assert list(pv) == list(range(8))

    def test_sealed_transient_raises_on_conj(self) -> None:
        tv = TransientVector.empty().conj(1).conj(2)
        _ = tv.persistent()
        with pytest.raises(RuntimeError):
            tv.conj(3)

    def test_sealed_transient_raises_on_pop(self) -> None:
        tv = TransientVector.empty().conj(1)
        _ = tv.persistent()
        with pytest.raises(RuntimeError):
            tv.pop()


class TestPersistentVectorEdgeCases:
    def test_tail_boundary_conj(self) -> None:
        v = PersistentVector.empty()
        for i in range(32):
            v = v.conj(i)
        assert len(v) == 32
        assert v[31] == 31
        v = v.conj(32)
        assert len(v) == 33
        assert v[32] == 32
        for i in range(32):
            assert v[i] == i

    def test_tail_boundary_pop(self) -> None:
        v = PersistentVector.empty()
        for i in range(65):
            v = v.conj(i)
        v = v.pop()
        assert len(v) == 64
        assert v[63] == 63
        v = v.pop()
        assert len(v) == 63
        assert v[62] == 62

    def test_many_pops_from_deep(self) -> None:
        v = PersistentVector.empty()
        for i in range(1200):
            v = v.conj(i)
        for _ in range(800):
            v = v.pop()
        assert len(v) == 400
        assert v[0] == 0
        assert v[399] == 399

    def test_assoc_at_tail_boundary(self) -> None:
        v = PersistentVector.empty()
        for i in range(64):
            v = v.conj(i)
        v2 = v.assoc(32, 999)
        assert v[32] == 32
        assert v2[32] == 999

    def test_random_operations(self) -> None:
        rng = random.Random(42)
        ref: list[int] = []
        pv = PersistentVector.empty()
        for _ in range(500):
            op = rng.choice(["conj", "pop"])
            if op == "conj" or len(ref) == 0:
                x = rng.randint(0, 9999)
                ref.append(x)
                pv = pv.conj(x)
            else:
                ref.pop()
                pv = pv.pop()
            assert list(pv) == ref
            assert len(pv) == len(ref)

    def test_assoc_under_random_stress(self) -> None:
        rng = random.Random(99)
        tv = TransientVector.empty()
        ref: list[int] = []
        for _i in range(200):
            x = rng.randint(0, 9999)
            ref.append(x)
            tv = tv.conj(x)
        for _ in range(100):
            idx = rng.randint(0, len(ref) - 1)
            val = rng.randint(0, 9999)
            ref[idx] = val
            tv = tv.assoc(idx, val)
        pv = tv.persistent()
        assert list(pv) == ref

    def test_empty_pop_raises(self) -> None:
        with pytest.raises(IndexError):
            PersistentVector.empty().pop()

    def test_out_of_bounds_getitem_raises(self) -> None:
        v = PersistentVector.empty().conj(1).conj(2)
        with pytest.raises(IndexError):
            _ = v[5]
        with pytest.raises(IndexError):
            _ = v[-5]

    def test_single_element_roundtrip(self) -> None:
        v = PersistentVector.empty().conj("x")
        assert v.peek() == "x"
        assert v[0] == "x"
        v = v.pop()
        assert len(v) == 0

    def test_repr_small(self) -> None:
        v = PersistentVector.empty()
        for i in range(3):
            v = v.conj(i)
        r = repr(v)
        assert "0, 1, 2" in r

    def test_repr_large_has_ellipsis(self) -> None:
        v = PersistentVector.empty()
        for i in range(100):
            v = v.conj(i)
        r = repr(v)
        assert "..." in r
