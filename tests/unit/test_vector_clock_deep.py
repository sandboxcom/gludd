"""Deep vector clock / version vector tests: causality, concurrency, merge associativity."""

from __future__ import annotations

import json

import pytest

from general_ludd.distributed.vector_clock import VectorClock

# ── helpers ────────────────────────────────────────────────────────────────────


class TestVectorClockIncrement:
    """Single-node increment semantics."""

    def test_empty_increment(self) -> None:
        vc = VectorClock()
        vc2 = vc.increment("A")
        assert vc2["A"] == 1
        assert "A" not in vc  # original is immutable

    def test_increment_inplace_raises(self) -> None:
        vc = VectorClock()
        with pytest.raises(TypeError):
            vc["A"] = 1  # type: ignore[index]

    def test_repeated_increment(self) -> None:
        vc = VectorClock().increment("A").increment("A")  # type: ignore[func-returns-value]
        assert vc["A"] == 2

    def test_increment_different_keys(self) -> None:
        vc = VectorClock().increment("A").increment("B")  # type: ignore[func-returns-value]
        assert vc["A"] == 1
        assert vc["B"] == 1

    def test_increment_tracks_timestamp(self) -> None:
        vc = VectorClock().increment("X").increment("X").increment("Y")  # type: ignore[func-returns-value]
        assert dict(vc) == {"X": 2, "Y": 1}


class TestVectorClockMerge:
    """Lattice merge (entrywise max) semantics."""

    def test_merge_disjoint_keys(self) -> None:
        a = VectorClock({"A": 3})
        b = VectorClock({"B": 5})
        m = a.merge(b)
        assert dict(m) == {"A": 3, "B": 5}

    def test_merge_keeps_max(self) -> None:
        a = VectorClock({"A": 3, "B": 1})
        b = VectorClock({"A": 1, "B": 4})
        m = a.merge(b)
        assert dict(m) == {"A": 3, "B": 4}

    def test_merge_commutative(self) -> None:
        a = VectorClock({"A": 3, "B": 1, "C": 7})
        b = VectorClock({"A": 1, "B": 4, "C": 2})
        assert dict(a.merge(b)) == dict(b.merge(a))

    def test_merge_associative(self) -> None:
        a = VectorClock({"A": 3, "B": 1})
        b = VectorClock({"A": 1, "B": 4, "C": 2})
        c = VectorClock({"A": 2, "B": 2, "C": 5, "D": 9})
        assert dict(a.merge(b).merge(c)) == dict(a.merge(b.merge(c)))

    def test_merge_idempotent(self) -> None:
        a = VectorClock({"A": 3, "B": 1})
        assert dict(a.merge(a)) == dict(a)

    def test_merge_does_not_mutate_originals(self) -> None:
        a = VectorClock({"A": 3})
        b = VectorClock({"B": 2})
        a.merge(b)
        assert dict(a) == {"A": 3}
        assert dict(b) == {"B": 2}


class TestVectorClockCompare:
    """Happens-before / concurrent comparison."""

    def test_equal_clocks(self) -> None:
        a = VectorClock({"A": 2, "B": 4})
        b = VectorClock({"A": 2, "B": 4})
        assert a == b
        assert not (a < b)
        assert not (b < a)

    def test_happens_before_subset(self) -> None:
        a = VectorClock({"A": 1, "B": 1})
        b = VectorClock({"A": 2, "B": 2, "C": 1})
        assert a < b
        assert not (b < a)

    def test_concurrent_disjoint_keys(self) -> None:
        a = VectorClock({"A": 3})
        b = VectorClock({"B": 3})
        assert not (a < b)
        assert not (b < a)

    def test_concurrent_equal_then_diverge(self) -> None:
        a = VectorClock({"A": 2, "B": 1})
        b = VectorClock({"A": 1, "B": 2})
        assert not (a < b)
        assert not (b < a)

    def test_reflexive_not_happens_before(self) -> None:
        vc = VectorClock({"A": 3})
        assert not (vc < vc)
        assert vc == vc

    def test_transitive_happens_before(self) -> None:
        a = VectorClock({"A": 1})
        b = VectorClock({"A": 2})
        c = VectorClock({"A": 3})
        assert a < b
        assert b < c
        assert a < c

    def test_concurrent_partial_overlap(self) -> None:
        a = VectorClock({"A": 5, "B": 2})
        b = VectorClock({"A": 3, "B": 4})
        assert not (a < b)
        assert not (b < a)

    def test_compare_with_missing_keys_defaults_zero(self) -> None:
        a = VectorClock({"A": 1})
        b = VectorClock({"A": 1, "B": 1})
        assert a < b


class TestVectorClockSerialisation:
    """Round-trip through dict and JSON."""

    def test_from_dict_round_trip(self) -> None:
        original = VectorClock({"A": 3, "B": 5, "C": 1})
        reconstructed = VectorClock(dict(original))
        assert original == reconstructed

    def test_to_json_and_back(self) -> None:
        vc = VectorClock({"node-1": 7, "node-2": 3})
        data = json.dumps(dict(vc))
        back = VectorClock(json.loads(data))
        assert vc == back

    def test_repr(self) -> None:
        vc = VectorClock({"A": 1, "B": 2})
        r = repr(vc)
        assert "VectorClock" in r
        assert "A" in r
        assert "B" in r

    def test_str(self) -> None:
        vc = VectorClock({"A": 1, "B": 2})
        s = str(vc)
        assert "A" in s
        assert "B" in s

    def test_len_and_bool(self) -> None:
        assert len(VectorClock()) == 0
        assert len(VectorClock({"A": 1, "B": 2})) == 2
        assert not bool(VectorClock())
        assert bool(VectorClock({"A": 1}))

    def test_iter_yields_sorted_keys(self) -> None:
        vc = VectorClock({"B": 2, "A": 1, "C": 3})
        assert list(vc) == ["A", "B", "C"]


class TestVectorClockCausalityChains:
    """Multi-node causality propagation."""

    def test_three_node_causality(self) -> None:
        a = VectorClock().increment("A")
        b = VectorClock().increment("B")
        ab = a.merge(b)
        c = VectorClock().increment("C")
        abc = ab.merge(c)
        assert a < abc
        assert b < abc
        assert c < abc

    def test_replica_update_pattern(self) -> None:
        vc_a = VectorClock({"A": 1})
        vc_b = VectorClock({"B": 1})
        merged = vc_a.merge(vc_b)
        assert vc_a < merged
        assert vc_b < merged

    def test_empty_clock_is_bottom(self) -> None:
        empty = VectorClock()
        a = VectorClock({"A": 1})
        assert empty < a
        assert empty == VectorClock()
        merged = empty.merge(a)
        assert merged == a

    def test_empty_clock_is_bottom_merge_left(self) -> None:
        a = VectorClock({"A": 1})
        assert a.merge(VectorClock()) == a

    def test_deep_copy_via_constructor(self) -> None:
        vc = VectorClock({"A": 1, "B": 2})
        dup = VectorClock(dict(vc))
        assert vc == dup
        assert vc is not dup


class TestVectorClockEdgeCases:
    """Corner-case and error-path behaviour."""

    def test_negative_count_raises(self) -> None:
        with pytest.raises(ValueError, match="Non-negative"):
            VectorClock({"A": -1})

    def test_zero_count_omitted_from_view(self) -> None:
        vc = VectorClock({"A": 0, "B": 1})
        assert "A" not in list(vc)
        assert vc["B"] == 1

    def test_contains_zero_acts_absent(self) -> None:
        vc = VectorClock({"A": 0})
        assert "A" not in vc

    def test_hash_equality(self) -> None:
        a = VectorClock({"A": 1, "B": 2})
        b = VectorClock({"A": 1, "B": 2})
        assert hash(a) == hash(b)

    def test_hash_different(self) -> None:
        a = VectorClock({"A": 1})
        b = VectorClock({"A": 2})
        assert hash(a) != hash(b)

    def test_missing_key_returns_zero(self) -> None:
        vc = VectorClock({"A": 3})
        assert vc["B"] == 0

    def test_increment_new_ctor(self) -> None:
        vc = VectorClock({"A": 0}).increment("A")
        assert vc["A"] == 1

    def test_increment_included_in_iterator(self) -> None:
        vc = VectorClock().increment("X")
        assert list(vc) == ["X"]

    def test_large_merge_is_correct(self) -> None:
        a = VectorClock({str(i): i for i in range(1, 51)})
        b = VectorClock({str(i): 50 - i for i in range(1, 51)})
        m = a.merge(b)
        assert m["1"] == 49
        assert m["25"] == 25
        assert m["49"] == 49

    def test_concurrent_from_shared_ancestor(self) -> None:
        ancestor = VectorClock({"A": 1, "B": 1})
        left = ancestor.increment("A")
        right = ancestor.increment("B")
        assert ancestor < left
        assert ancestor < right
        assert not (left < right)
        assert not (right < left)
