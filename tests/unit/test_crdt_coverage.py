"""State and tombstone branch coverage for CRDT implementations."""

from __future__ import annotations

from general_ludd.distributed import crdt


def test_crdt_state_snapshots_are_detached_values() -> None:
    """Every public state snapshot contains caller-owned serializable values."""
    positive = crdt.PNCounter("a")
    positive.increment(3)
    grow_set: crdt.GSet[str] = crdt.GSet()
    grow_set.add("x")
    two_phase: crdt.TwoPhaseSet[str] = crdt.TwoPhaseSet()
    two_phase.add("x")
    register: crdt.LWWRegister[str] = crdt.LWWRegister("a", "value")
    observed: crdt.ORSet[str] = crdt.ORSet()
    observed.add("x", "tag")

    assert crdt._merge_ints(1, 2) == 2
    assert positive.state() == {"inc": {"a": 3}, "dec": {"a": 0}}
    assert grow_set.state() == {"x"}
    assert two_phase.state() == {"A": {"x"}, "R": set()}
    assert register.state()["v"] == "value"
    assert observed.state() == {"adds": {"x": {"tag"}}, "rems": {}}


def test_ormap_missing_non_set_operation_is_a_noop() -> None:
    """An update operation cannot implicitly create a value without a factory set."""
    mapping: crdt.ORMap[str, crdt.GCounter] = crdt.ORMap(value_factory=lambda: crdt.GCounter("a"))

    mapping.put("missing", "increment")

    assert mapping.get("missing") is None
    assert mapping.state() == {"entries": {}, "deltas": {}}


def test_ormap_reuses_existing_value_for_non_set_operation() -> None:
    """A non-set operation on an existing key adds a fresh observed version tag."""
    mapping: crdt.ORMap[str, crdt.GCounter] = crdt.ORMap(value_factory=lambda: crdt.GCounter("a"))
    mapping.put("count")
    before = len(mapping._entries["count"][0])

    mapping.put("count", "increment")

    assert len(mapping._entries["count"][0]) == before + 1
    assert mapping.get("count") is not None


def test_ormap_tombstone_hides_and_merge_removes_fully_observed_key() -> None:
    """A tombstone covering every observed tag hides and removes the entry."""
    mapping: crdt.ORMap[str, crdt.GCounter] = crdt.ORMap(value_factory=lambda: crdt.GCounter("a"))
    mapping.put("count")
    tags = set(mapping._entries["count"][0])
    mapping._deltas["count"] = set(tags)

    assert mapping.value == {}
    assert mapping.get("count") is None

    remover: crdt.ORMap[str, crdt.GCounter] = crdt.ORMap(value_factory=lambda: crdt.GCounter("b"))
    remover._deltas["count"] = set(tags)
    mapping.merge(remover)

    assert "count" not in mapping._entries
