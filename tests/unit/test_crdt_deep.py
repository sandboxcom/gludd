"""Deep CRDT tests: merge commutativity, associativity, idempotence,
state-based and op-based replication for GCounter, PNCounter, GSet,
TwoPhaseSet, LWWRegister, ORSet, ORMap.
"""

from __future__ import annotations

import copy

from general_ludd.distributed.crdt import (
    GCounter,
    GSet,
    LWWRegister,
    ORMap,
    ORSet,
    PNCounter,
    TwoPhaseSet,
)

# ── helpers ────────────────────────────────────────────────────────────────────


def _merge(a: object, b: object) -> object:
    """State-based merge: deep-copy a, merge b into it, return a."""
    a = copy.deepcopy(a)
    a.merge(copy.deepcopy(b))  # type: ignore[union-attr]
    return a


def _merged(*crdts: object) -> object:
    """Merge a sequence into the first, return the first."""
    a = copy.deepcopy(crdts[0])
    for c in crdts[1:]:
        a.merge(copy.deepcopy(c))  # type: ignore[union-attr]
    return a


# ── GCounter ───────────────────────────────────────────────────────────────────


class TestGCounter:
    def test_increment(self) -> None:
        c = GCounter("a")
        c.increment(3)
        assert c.value == 3

    def test_merge_commutativity(self) -> None:
        a, b = GCounter("a"), GCounter("b")
        a.increment(5)
        b.increment(3)
        ab = _merge(a, b)
        ba = _merge(b, a)
        assert ab.value == ba.value == 8  # type: ignore[attr-defined]

    def test_merge_associativity(self) -> None:
        x, y, z = GCounter("a"), GCounter("b"), GCounter("c")
        x.increment(1)
        y.increment(2)
        z.increment(4)
        xy_z = _merged(_merge(x, y), z)
        x_yz = _merged(x, _merge(y, z))
        assert xy_z.value == 7  # type: ignore[attr-defined]
        assert x_yz.value == 7  # type: ignore[attr-defined]

    def test_merge_idempotence(self) -> None:
        a = GCounter("a")
        a.increment(10)
        aa = _merge(a, a)
        assert aa.value == 10  # type: ignore[attr-defined]

    def test_op_based_increment(self) -> None:
        a, b = GCounter("a"), GCounter("b")
        a.increment(2)
        b.increment(3)
        b.merge(copy.deepcopy(a))
        a.merge(copy.deepcopy(b))
        assert a.value == 5
        assert b.value == 5

    def test_state_roundtrip(self) -> None:
        a = GCounter("x")
        a.increment(7)
        b = GCounter("y")
        b.merge(GCounter._restore(a.state()))
        assert b.value == 7

    def test_multiple_replicas(self) -> None:
        r = [GCounter(chr(ord("a") + i)) for i in range(5)]
        for i, c in enumerate(r):
            c.increment(i + 1)
        merged = _merged(*r)
        assert merged.value == 15  # type: ignore[attr-defined]


# ── PNCounter ──────────────────────────────────────────────────────────────────


class TestPNCounter:
    def test_increment_decrement(self) -> None:
        p = PNCounter("a")
        p.increment(5)
        p.decrement(2)
        assert p.value == 3

    def test_merge_commutativity(self) -> None:
        a, b = PNCounter("x"), PNCounter("y")
        a.increment(10)
        a.decrement(3)
        b.increment(4)
        b.decrement(1)
        ab = _merge(a, b)
        ba = _merge(b, a)
        assert ab.value == ba.value == 10  # type: ignore[attr-defined]  # (10-3)+(4-1)=10

    def test_merge_associativity(self) -> None:
        a, b, c = PNCounter("a"), PNCounter("b"), PNCounter("c")
        a.increment(1)
        a.decrement(1)
        b.increment(3)
        c.increment(2)
        c.decrement(1)
        ab_c = _merged(_merge(a, b), c)
        a_bc = _merged(a, _merge(b, c))
        assert ab_c.value == a_bc.value == 4  # type: ignore[attr-defined]  # 0+3+(2-1)=4

    def test_merge_idempotence(self) -> None:
        p = PNCounter("z")
        p.increment(7)
        p.decrement(4)
        pp = _merge(p, p)
        assert pp.value == 3  # type: ignore[attr-defined]

    def test_negative_value(self) -> None:
        p = PNCounter("a")
        p.decrement(5)
        assert p.value == -5


# ── GSet ───────────────────────────────────────────────────────────────────────


class TestGSet:
    def test_add(self) -> None:
        s: GSet[str] = GSet()
        s.add("x")
        s.add("y")
        assert s.value == frozenset({"x", "y"})

    def test_merge_commutativity(self) -> None:
        a: GSet[int] = GSet()
        b: GSet[int] = GSet()
        a.add(1)
        a.add(2)
        b.add(2)
        b.add(3)
        ab = _merge(a, b)
        ba = _merge(b, a)
        assert ab.value == ba.value == frozenset({1, 2, 3})  # type: ignore[attr-defined]

    def test_merge_associativity(self) -> None:
        x: GSet[str] = GSet()
        y: GSet[str] = GSet()
        z: GSet[str] = GSet()
        x.add("a")
        y.add("b")
        z.add("c")
        xy_z = _merged(_merge(x, y), z)
        x_yz = _merged(x, _merge(y, z))
        assert xy_z.value == frozenset({"a", "b", "c"})  # type: ignore[attr-defined]
        assert x_yz.value == frozenset({"a", "b", "c"})  # type: ignore[attr-defined]

    def test_merge_idempotence(self) -> None:
        s: GSet[int] = GSet()
        s.add(42)
        ss = _merge(s, s)
        assert ss.value == frozenset({42})  # type: ignore[attr-defined]


# ── TwoPhaseSet ────────────────────────────────────────────────────────────────


class TestTwoPhaseSet:
    def test_add_remove(self) -> None:
        s: TwoPhaseSet[str] = TwoPhaseSet()
        s.add("a")
        s.add("b")
        s.remove("a")
        assert s.value == frozenset({"b"})

    def test_merge_commutativity(self) -> None:
        a: TwoPhaseSet[int] = TwoPhaseSet()
        b: TwoPhaseSet[int] = TwoPhaseSet()
        a.add(1)
        a.add(2)
        a.remove(1)
        b.add(2)
        b.add(3)
        ab = _merge(a, b)
        ba = _merge(b, a)
        expected = frozenset({2, 3})
        assert ab.value == expected  # type: ignore[attr-defined]
        assert ba.value == expected  # type: ignore[attr-defined]

    def test_merge_associativity(self) -> None:
        x: TwoPhaseSet[str] = TwoPhaseSet()
        y: TwoPhaseSet[str] = TwoPhaseSet()
        z: TwoPhaseSet[str] = TwoPhaseSet()
        x.add("a")
        x.add("b")
        y.add("b")
        y.remove("b")
        z.add("a")
        z.add("c")
        xy_z = _merged(_merge(x, y), z)
        x_yz = _merged(x, _merge(y, z))
        expected = frozenset({"a", "c"})
        assert xy_z.value == expected  # type: ignore[attr-defined]
        assert x_yz.value == expected  # type: ignore[attr-defined]

    def test_merge_idempotence(self) -> None:
        s: TwoPhaseSet[int] = TwoPhaseSet()
        s.add(1)
        s.add(2)
        s.remove(1)
        ss = _merge(s, s)
        assert ss.value == frozenset({2})  # type: ignore[attr-defined]

    def test_remove_wins(self) -> None:
        s: TwoPhaseSet[str] = TwoPhaseSet()
        s.add("x")
        s.remove("x")
        s.add("x")
        s.remove("x")
        assert s.value == frozenset()


# ── LWWRegister ────────────────────────────────────────────────────────────────


class TestLWWRegister:
    def test_assign(self) -> None:
        r: LWWRegister[str] = LWWRegister("a")
        r.assign("hello")
        assert r.value == "hello"

    def test_merge_commutativity(self) -> None:
        a: LWWRegister[str] = LWWRegister("a")
        b: LWWRegister[str] = LWWRegister("b")
        a.assign("first")
        b.assign("second")
        ab = _merge(a, b)
        ba = _merge(b, a)
        assert ab.value == ba.value  # type: ignore[attr-defined]

    def test_merge_associativity(self) -> None:
        x: LWWRegister[int] = LWWRegister("x")
        y: LWWRegister[int] = LWWRegister("y")
        z: LWWRegister[int] = LWWRegister("z")
        x.assign(1)
        y.assign(2)
        z.assign(3)
        xy_z = _merged(_merge(x, y), z)
        x_yz = _merged(x, _merge(y, z))
        assert xy_z.value == x_yz.value  # type: ignore[attr-defined]

    def test_merge_idempotence(self) -> None:
        r: LWWRegister[str] = LWWRegister("r")
        r.assign("val")
        rr = _merge(r, r)
        assert rr.value == "val"  # type: ignore[attr-defined]

    def test_lww_most_recent_wins(self) -> None:
        a: LWWRegister[str] = LWWRegister("a")
        b: LWWRegister[str] = LWWRegister("b")
        import time

        a.assign("old")
        time.sleep(0.005)
        b.assign("new")
        merged = _merge(a, b)
        assert merged.value == "new"  # type: ignore[attr-defined]

    def test_lww_tie_breaker(self) -> None:
        a: LWWRegister[str] = LWWRegister("a")
        b: LWWRegister[str] = LWWRegister("z")
        a._ts = 1000
        b._ts = 1000
        a._value = "loser"
        b._value = "winner"
        merged = _merge(a, b)
        assert merged.value == "winner"  # type: ignore[attr-defined]


# ── ORSet ──────────────────────────────────────────────────────────────────────


class TestORSet:
    def test_add_remove(self) -> None:
        s: ORSet[str] = ORSet()
        s.add("x")
        s.add("y")
        s.remove("x")
        assert s.value == frozenset({"y"})

    def test_add_after_remove_concurrent(self) -> None:
        a: ORSet[int] = ORSet()
        b: ORSet[int] = ORSet()
        a.add(1, "tag_a")
        b.add(1, "tag_b")
        b.remove(1)
        ab = _merge(a, b)
        assert ab.value == frozenset({1})  # type: ignore[attr-defined]

    def test_merge_commutativity(self) -> None:
        a: ORSet[str] = ORSet()
        b: ORSet[str] = ORSet()
        a.add("shared")
        a.add("only_a")
        b.add("shared")
        b.add("only_b")
        a.remove("only_a")
        ab = _merge(a, b)
        ba = _merge(b, a)
        expected = frozenset({"shared", "only_b"})
        assert ab.value == expected  # type: ignore[attr-defined]
        assert ba.value == expected  # type: ignore[attr-defined]

    def test_merge_associativity(self) -> None:
        x: ORSet[int] = ORSet()
        y: ORSet[int] = ORSet()
        z: ORSet[int] = ORSet()
        x.add(1)
        y.add(1)
        y.remove(1)
        z.add(1)
        xy_z = _merged(_merge(x, y), z)
        x_yz = _merged(x, _merge(y, z))
        assert xy_z.value == x_yz.value  # type: ignore[attr-defined]

    def test_merge_idempotence(self) -> None:
        s: ORSet[str] = ORSet()
        s.add("x")
        s.add("y")
        s.remove("x")
        ss = _merge(s, s)
        assert ss.value == frozenset({"y"})  # type: ignore[attr-defined]

    def test_concurrent_add_remove_same_element(self) -> None:
        a: ORSet[int] = ORSet()
        b: ORSet[int] = ORSet()
        a.add(1)
        b.add(1, "tag_from_b")
        b.remove(1)
        ab = _merge(a, b)
        assert ab.value == frozenset({1})  # type: ignore[attr-defined]

    def test_op_based_add(self) -> None:
        a: ORSet[str] = ORSet()
        b: ORSet[str] = ORSet()
        a.add("x", "t1")
        a.add("y", "t2")
        b.add("z", "t3")
        b.merge(copy.deepcopy(a))
        a.merge(copy.deepcopy(b))
        assert a.value == frozenset({"x", "y", "z"})
        assert b.value == frozenset({"x", "y", "z"})


# ── ORMap ──────────────────────────────────────────────────────────────────────


class TestORMap:
    def test_put_get(self) -> None:
        def _make() -> GCounter:
            return GCounter("m")

        m: ORMap[str, GCounter] = ORMap(value_factory=_make)
        m.put("k", "set")
        val = m.get("k")
        assert val is not None
        val.increment(1)
        assert val.value == 1

    def test_remove(self) -> None:
        def _make() -> LWWRegister[str]:
            return LWWRegister("m")

        m: ORMap[str, LWWRegister[str]] = ORMap(value_factory=_make)
        m.put("key", "set")
        m.remove("key")
        assert m.get("key") is None
        assert "key" not in m.value

    def test_merge_commutativity(self) -> None:
        a: ORMap[str, GCounter] = ORMap(value_factory=lambda: GCounter("a"))
        b: ORMap[str, GCounter] = ORMap(value_factory=lambda: GCounter("b"))
        a.put("k1", "set")
        b.put("k1", "set")
        b.put("k2", "set")
        a.get("k1").increment(1)  # type: ignore[union-attr]
        b.get("k1").increment(2)  # type: ignore[union-attr]
        b.get("k2").increment(3)  # type: ignore[union-attr]
        ab = _merge(a, b)
        ba = _merge(b, a)
        assert ab.value["k1"] == 3  # type: ignore[attr-defined]
        assert ba.value["k1"] == 3  # type: ignore[attr-defined]
        assert ab.value["k2"] == 3  # type: ignore[attr-defined]
        assert ba.value["k2"] == 3  # type: ignore[attr-defined]

    def test_merge_associativity(self) -> None:
        x: ORMap[str, GCounter] = ORMap(value_factory=lambda: GCounter("x"))
        y: ORMap[str, GCounter] = ORMap(value_factory=lambda: GCounter("y"))
        z: ORMap[str, GCounter] = ORMap(value_factory=lambda: GCounter("z"))
        x.put("a", "set")
        x.get("a").increment(1)  # type: ignore[union-attr]
        y.put("a", "set")
        y.get("a").increment(2)  # type: ignore[union-attr]
        z.put("a", "set")
        z.get("a").increment(3)  # type: ignore[union-attr]
        xy_z = _merged(_merge(x, y), z)
        x_yz = _merged(x, _merge(y, z))
        assert xy_z.value["a"] == 6  # type: ignore[attr-defined]
        assert x_yz.value["a"] == 6  # type: ignore[attr-defined]

    def test_merge_idempotence(self) -> None:
        def _make() -> GCounter:
            return GCounter("m")

        m: ORMap[str, GCounter] = ORMap(value_factory=_make)
        m.put("x", "set")
        m.get("x").increment(7)  # type: ignore[union-attr]
        mm = _merge(m, m)
        assert mm.value["x"] == 7  # type: ignore[attr-defined]

    def test_concurrent_add_remove(self) -> None:
        def _make() -> LWWRegister[int]:
            return LWWRegister("m")

        a: ORMap[str, LWWRegister[int]] = ORMap(value_factory=_make)
        b: ORMap[str, LWWRegister[int]] = ORMap(value_factory=_make)
        a.put("key", "set")
        b.remove("key")
        b.put("key", "set")
        ab = _merge(a, b)
        assert "key" in ab.value  # type: ignore[attr-defined]
