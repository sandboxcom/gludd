"""Deep audit of all NamedTuple subclasses in src/general_ludd/."""

from __future__ import annotations

import inspect
import types
import typing as t
from typing import NamedTuple

import pytest

from general_ludd.db.migrations import MigrationPlan
from general_ludd.regex_engine import BacktrackingDanger
from general_ludd.routing_roles.weights import RoleWeights

NAMEDTUPLE_CLASSES: list[type[NamedTuple]] = [
    RoleWeights,
    BacktrackingDanger,
    MigrationPlan,
]


# ---------------------------------------------------------------------------
# Detection: ensure we found all NamedTuples
# ---------------------------------------------------------------------------


def test_all_namedtuple_classes_are_actually_namedtuple_subclasses() -> None:
    for cls in NAMEDTUPLE_CLASSES:
        assert issubclass(cls, tuple), f"{cls.__name__} is not a tuple subclass"
        assert hasattr(cls, "_fields"), f"{cls.__name__} has no _fields"


# ---------------------------------------------------------------------------
# Field typing
# ---------------------------------------------------------------------------


def test_role_weights_fields_fully_typed() -> None:
    annots = t.get_type_hints(RoleWeights)
    assert annots == {"cost": float, "quality": float}


def test_backtracking_danger_fields_fully_typed() -> None:
    annots = t.get_type_hints(BacktrackingDanger)
    assert annots == {
        "construct": str,
        "location": int,
        "reason": str,
    }


def test_migration_plan_fields_fully_typed() -> None:
    annots = t.get_type_hints(MigrationPlan)
    assert annots["sql"] is str
    assert annots["pending_count"] is int
    assert annots["head_rev"] is str
    assert annots["current_rev"] == str | None


# ---------------------------------------------------------------------------
# No mutable defaults (NamedTuples structurally prevent this, but verify)
# ---------------------------------------------------------------------------


def test_no_namedtuple_has_mutable_default() -> None:
    mutable_types = (list, dict, set, bytearray)
    for cls in NAMEDTUPLE_CLASSES:
        hints = t.get_type_hints(cls)
        for field_name, field_type in hints.items():
            origin = t.get_origin(field_type)
            if origin is not None:
                assert origin not in mutable_types, f"{cls.__name__}.{field_name} has mutable type {field_type}"


def test_role_weights_no_mutable_fields() -> None:
    hints = t.get_type_hints(RoleWeights)
    for field_type in hints.values():
        assert field_type in (float, int, str, bool)


def test_backtracking_danger_no_mutable_fields() -> None:
    hints = t.get_type_hints(BacktrackingDanger)
    for field_type in hints.values():
        assert field_type in (str, int)


def test_migration_plan_no_mutable_fields() -> None:
    hints = t.get_type_hints(MigrationPlan)
    for field_type in hints.values():
        origin = t.get_origin(field_type)
        if origin is not None:
            assert origin in (
                t.Union,
                types.UnionType,
            ), f"MigrationPlan has unexpected mutable origin {origin}"
        else:
            assert field_type in (str, int), f"MigrationPlan has unexpected bare field type {field_type}"


# ---------------------------------------------------------------------------
# _make / from sequence
# ---------------------------------------------------------------------------


def test_role_weights_make_and_from_sequence() -> None:
    rw = RoleWeights(0.3, 0.7)
    from_seq = RoleWeights._make([0.3, 0.7])
    assert from_seq == rw
    assert from_seq.cost == 0.3
    assert from_seq.quality == 0.7


def test_backtracking_danger_make_and_from_sequence() -> None:
    bd = BacktrackingDanger("(a+)+", 10, "nested quantifier")
    from_seq = BacktrackingDanger._make(["(a+)+", 10, "nested quantifier"])
    assert from_seq == bd
    assert from_seq.construct == "(a+)+"
    assert from_seq.location == 10
    assert from_seq.reason == "nested quantifier"


def test_migration_plan_make_and_from_sequence() -> None:
    mp = MigrationPlan("SELECT 1", 2, "abc", "def")
    from_seq = MigrationPlan._make(["SELECT 1", 2, "abc", "def"])
    assert from_seq == mp
    assert from_seq.sql == "SELECT 1"
    assert from_seq.pending_count == 2
    assert from_seq.current_rev == "abc"
    assert from_seq.head_rev == "def"


# ---------------------------------------------------------------------------
# _asdict roundtrip
# ---------------------------------------------------------------------------


def test_role_weights_asdict_roundtrip() -> None:
    rw = RoleWeights(0.05, 0.95)
    d = rw._asdict()
    assert d == {"cost": 0.05, "quality": 0.95}
    rebuilt = RoleWeights(**d)
    assert rebuilt == rw


def test_backtracking_danger_asdict_roundtrip() -> None:
    bd = BacktrackingDanger("(a*)*", 5, "empty-string match")
    d = bd._asdict()
    assert d == {"construct": "(a*)*", "location": 5, "reason": "empty-string match"}
    rebuilt = BacktrackingDanger(**d)
    assert rebuilt == bd


def test_migration_plan_asdict_roundtrip() -> None:
    mp = MigrationPlan("ALTER TABLE ...", 3, "rev1", "rev2")
    d = mp._asdict()
    assert d == {
        "sql": "ALTER TABLE ...",
        "pending_count": 3,
        "current_rev": "rev1",
        "head_rev": "rev2",
    }
    rebuilt = MigrationPlan(**d)
    assert rebuilt == mp


def test_migration_plan_asdict_with_none_current_rev() -> None:
    mp = MigrationPlan("", 0, None, "head")
    d = mp._asdict()
    assert d["current_rev"] is None
    rebuilt = MigrationPlan(**d)
    assert rebuilt == mp


# ---------------------------------------------------------------------------
# _replace creates new instance
# ---------------------------------------------------------------------------


def test_role_weights_replace_creates_new() -> None:
    orig = RoleWeights(0.1, 0.9)
    updated = orig._replace(cost=0.2)
    assert updated is not orig
    assert updated == RoleWeights(0.2, 0.9)
    assert orig == RoleWeights(0.1, 0.9)


def test_backtracking_danger_replace_creates_new() -> None:
    orig = BacktrackingDanger("(a)+", 0, "nested")
    updated = orig._replace(reason="revised reason")
    assert updated is not orig
    assert updated == BacktrackingDanger("(a)+", 0, "revised reason")
    assert orig.reason == "nested"


def test_migration_plan_replace_creates_new() -> None:
    orig = MigrationPlan("sql", 1, "a", "b")
    updated = orig._replace(pending_count=0, head_rev="z")
    assert updated is not orig
    assert updated == MigrationPlan("sql", 0, "a", "z")
    assert orig == MigrationPlan("sql", 1, "a", "b")


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_role_weights_is_immutable() -> None:
    rw = RoleWeights(0.5, 0.5)
    with pytest.raises((TypeError, AttributeError)):
        rw.cost = 0.9  # type: ignore[misc]


def test_backtracking_danger_is_immutable() -> None:
    bd = BacktrackingDanger("x", 0, "y")
    with pytest.raises((TypeError, AttributeError)):
        bd.construct = "z"  # type: ignore[misc]


def test_migration_plan_is_immutable() -> None:
    mp = MigrationPlan("x", 0, None, "h")
    with pytest.raises((TypeError, AttributeError)):
        mp.pending_count = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Rich access — index, attribute, unpacking
# ---------------------------------------------------------------------------


def test_role_weights_index_and_unpacking() -> None:
    rw = RoleWeights(0.3, 0.7)
    assert rw[0] == 0.3
    assert rw[1] == 0.7
    cost, quality = rw
    assert cost == 0.3
    assert quality == 0.7


def test_backtracking_danger_index_and_unpacking() -> None:
    bd = BacktrackingDanger("p", 42, "r")
    assert bd[0] == "p"
    assert bd[1] == 42
    assert bd[2] == "r"
    c, label, r = bd
    assert c == "p" and label == 42 and r == "r"


def test_migration_plan_index_and_unpacking() -> None:
    mp = MigrationPlan("S", 7, "x", "y")
    assert mp[0] == "S"
    assert mp[1] == 7
    assert mp[2] == "x"
    assert mp[3] == "y"
    s, pc, cr, hr = mp
    assert s == "S" and pc == 7 and cr == "x" and hr == "y"


# ---------------------------------------------------------------------------
# Equality / hashing
# ---------------------------------------------------------------------------


def test_namedtuples_equal_by_value() -> None:
    rw1 = RoleWeights(0.1, 0.9)
    rw2 = RoleWeights(0.1, 0.9)
    assert rw1 == rw2
    assert hash(rw1) == hash(rw2)
    assert rw1 != RoleWeights(0.2, 0.8)


def test_backtracking_danger_equality() -> None:
    bd1 = BacktrackingDanger("a", 0, "r")
    bd2 = BacktrackingDanger("a", 0, "r")
    assert bd1 == bd2
    assert hash(bd1) == hash(bd2)
    assert bd1 != BacktrackingDanger("b", 0, "r")


def test_migration_plan_equality() -> None:
    mp1 = MigrationPlan("sql", 1, None, "head")
    mp2 = MigrationPlan("sql", 1, None, "head")
    assert mp1 == mp2
    assert hash(mp1) == hash(mp2)
    assert mp1 != MigrationPlan("sql", 2, None, "head")


# ---------------------------------------------------------------------------
# Field count
# ---------------------------------------------------------------------------


def test_role_weights_field_count() -> None:
    assert len(RoleWeights._fields) == 2
    assert RoleWeights._fields == ("cost", "quality")


def test_backtracking_danger_field_count() -> None:
    assert len(BacktrackingDanger._fields) == 3
    assert BacktrackingDanger._fields == ("construct", "location", "reason")


def test_migration_plan_field_count() -> None:
    assert len(MigrationPlan._fields) == 4
    assert MigrationPlan._fields == ("sql", "pending_count", "current_rev", "head_rev")


# ---------------------------------------------------------------------------
# __repr__ is informative
# ---------------------------------------------------------------------------


def test_role_weights_repr() -> None:
    rw = RoleWeights(0.1, 0.9)
    r = repr(rw)
    assert "RoleWeights" in r
    assert "0.1" in r
    assert "0.9" in r


def test_backtracking_danger_repr() -> None:
    bd = BacktrackingDanger("(a+)+", 5, "nested")
    r = repr(bd)
    assert "BacktrackingDanger" in r
    assert "(a+)+" in r


def test_migration_plan_repr() -> None:
    mp = MigrationPlan("SELECT 1", 0, "abc", "def")
    r = repr(mp)
    assert "MigrationPlan" in r
    assert "SELECT 1" in r


# ---------------------------------------------------------------------------
# NamedTuple source locations (co-located with usage, not in a types module)
# ---------------------------------------------------------------------------


def test_namedtuples_defined_in_domain_modules() -> None:
    allowed_paths = {
        "routing_roles/weights.py",
        "regex_engine.py",
        "db/migrations.py",
    }
    for cls in NAMEDTUPLE_CLASSES:
        f = inspect.getfile(cls)
        assert any(p in f for p in allowed_paths), f"{cls.__name__} defined in {f}, expected one of {allowed_paths}"


# ---------------------------------------------------------------------------
# _make rejects wrong-length sequences
# ---------------------------------------------------------------------------


def test_role_weights_make_rejects_wrong_length() -> None:
    with pytest.raises(TypeError):
        RoleWeights._make([0.1])
    with pytest.raises(TypeError):
        RoleWeights._make([0.1, 0.2, 0.3])


def test_backtracking_danger_make_rejects_wrong_length() -> None:
    with pytest.raises(TypeError):
        BacktrackingDanger._make(["a", 1])
    with pytest.raises(TypeError):
        BacktrackingDanger._make(["a", 1, "b", "extra"])


def test_migration_plan_make_rejects_wrong_length() -> None:
    with pytest.raises(TypeError):
        MigrationPlan._make(["a"])
    with pytest.raises(TypeError):
        MigrationPlan._make(["a", "b", "c", "d", "e"])
