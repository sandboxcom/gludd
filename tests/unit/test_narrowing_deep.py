"""Deep tests for sts/narrowing.py — PolicyFragment, OpenBaoPolicyRenderer, CapabilityNarrowing.

Covers all branches: empty input, path dedup, verb merging, unknown-actions
fallback, anti-escalation, lattice chain/no-chain paths, and frozen dataclass
semantics.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from general_ludd.sts.narrowing import (
    CapabilityNarrowing,
    OpenBaoPolicyRenderer,
    PolicyFragment,
)


class FakeAction:
    def __init__(self, value: str) -> None:
        self.value = value

    def __repr__(self) -> str:
        return f"FakeAction({self.value!r})"


class MockLattice:
    def __init__(self, chain: bool = True) -> None:
        self._actions: dict[str, set[str]] = {
            "admin": {"read", "write", "execute", "delete", "create"},
            "viewer": {"read"},
        }
        self.chain = chain

    def all_actions(self, role: str) -> set[str]:
        return self._actions.get(role, set())


# ── PolicyFragment ──────────────────────────────────────────────────────


class TestPolicyFragment:
    def test_create_with_minimal_args(self) -> None:
        frag = PolicyFragment(path="secret/*", capabilities=frozenset(["read"]))
        assert frag.path == "secret/*"
        assert frag.capabilities == frozenset(["read"])
        assert frag.constraints == {}

    def test_create_with_constraints(self) -> None:
        frag = PolicyFragment(
            path="secret/app",
            capabilities=frozenset(["read", "write"]),
            constraints={"max_ttl": "1h"},
        )
        assert frag.constraints == {"max_ttl": "1h"}

    def test_frozen_raises_on_mutation(self) -> None:
        frag = PolicyFragment(path="secret/*", capabilities=frozenset(["read"]))
        with pytest.raises(FrozenInstanceError):
            frag.path = "other"  # type: ignore[misc]

    def test_equality_by_path_and_capabilities(self) -> None:
        a = PolicyFragment(path="secret/*", capabilities=frozenset(["read"]))
        b = PolicyFragment(path="secret/*", capabilities=frozenset(["read"]))
        assert a == b

    def test_inequality_on_different_path(self) -> None:
        a = PolicyFragment(path="secret/a", capabilities=frozenset(["read"]))
        b = PolicyFragment(path="secret/b", capabilities=frozenset(["read"]))
        assert a != b

    def test_inequality_on_different_capabilities(self) -> None:
        a = PolicyFragment(path="secret/*", capabilities=frozenset(["read"]))
        b = PolicyFragment(path="secret/*", capabilities=frozenset(["write"]))
        assert a != b

    def test_hash_stable(self) -> None:
        a = PolicyFragment(path="secret/*", capabilities=frozenset(["read"]))
        b = PolicyFragment(path="secret/*", capabilities=frozenset(["read"]))
        assert hash(a) == hash(b)
        s = {a}
        assert b in s

    def test_constraints_excluded_from_equality(self) -> None:
        a = PolicyFragment(
            path="secret/*",
            capabilities=frozenset(["read"]),
            constraints={"x": 1},
        )
        b = PolicyFragment(
            path="secret/*",
            capabilities=frozenset(["read"]),
            constraints={"y": 2},
        )
        assert a == b

    def test_constraints_excluded_from_hash(self) -> None:
        a = PolicyFragment(
            path="secret/*",
            capabilities=frozenset(["read"]),
            constraints={"x": 1},
        )
        b = PolicyFragment(
            path="secret/*",
            capabilities=frozenset(["read"]),
            constraints={"y": 2},
        )
        assert hash(a) == hash(b)


# ── OpenBaoPolicyRenderer ───────────────────────────────────────────────


class TestOpenBaoPolicyRenderer:
    def test_empty_actions_returns_empty_string(self) -> None:
        result = OpenBaoPolicyRenderer.render([])
        assert result == ""

    def test_empty_iterable_safe(self) -> None:
        result = OpenBaoPolicyRenderer.render(iter(()))
        assert isinstance(result, str)

    def test_single_read_action_renders_hcl(self) -> None:
        result = OpenBaoPolicyRenderer.render(["read"])
        assert 'path "secret/*"' in result
        assert 'capabilities = ["read"]' in result

    def test_single_write_action_renders_combined_verbs(self) -> None:
        result = OpenBaoPolicyRenderer.render(["write"])
        assert "create, update" in result

    def test_multiple_actions_merges_verbs_same_path(self) -> None:
        result = OpenBaoPolicyRenderer.render(["read", "write"])
        assert "create, read, update" in result
        assert result.count("path") == 1

    def test_execute_action_maps_to_sys_path(self) -> None:
        result = OpenBaoPolicyRenderer.render(["execute"])
        assert 'path "sys/*"' in result
        assert 'capabilities = ["sudo"]' in result

    def test_read_and_execute_produce_two_path_blocks(self) -> None:
        result = OpenBaoPolicyRenderer.render(["read", "execute"])
        assert result.count("path") == 2

    def test_unknown_action_uses_literal_as_verb_and_default_path(self) -> None:
        result = OpenBaoPolicyRenderer.render(["unknown_action"])
        assert 'path "secret/*"' in result
        assert 'capabilities = ["unknown_action"]' in result

    def test_fake_action_objects_with_value_attr(self) -> None:
        result = OpenBaoPolicyRenderer.render(
            [
                FakeAction("read"),
                FakeAction("write"),
            ]
        )
        assert "create, read, update" in result

    def test_mixed_strings_and_objects(self) -> None:
        result = OpenBaoPolicyRenderer.render([FakeAction("read"), "delete"])
        assert "delete, read" in result

    def test_custom_role_name_in_header(self) -> None:
        result = OpenBaoPolicyRenderer.render(["read"], role_name="my-role")
        assert 'role "my-role"' in result

    def test_default_role_name_in_header(self) -> None:
        result = OpenBaoPolicyRenderer.render(["read"])
        assert 'role "default"' in result

    def test_duplicate_actions_are_deduped(self) -> None:
        result = OpenBaoPolicyRenderer.render(["read", "read", "read"])
        assert 'capabilities = ["read"]' in result

    def test_single_entry_verbs_sorted(self) -> None:
        result = OpenBaoPolicyRenderer.render(["write", "read"])
        assert "create, read, update" in result

    def test_overwrite_maps_to_update_verb(self) -> None:
        result = OpenBaoPolicyRenderer.render(["overwrite"])
        assert 'capabilities = ["update"]' in result

    def test_create_maps_to_create_verb(self) -> None:
        result = OpenBaoPolicyRenderer.render(["create"])
        assert 'capabilities = ["create"]' in result

    def test_hcl_ends_with_trailing_newline(self) -> None:
        result = OpenBaoPolicyRenderer.render(["read"])
        assert result.endswith("\n")

    def test_no_trailing_newline_on_empty(self) -> None:
        result = OpenBaoPolicyRenderer.render([])
        assert result == ""


# ── CapabilityNarrowing ─────────────────────────────────────────────────


class TestCapabilityNarrowingInit:
    def test_stores_parent_lattice(self) -> None:
        lattice = MockLattice()
        cn = CapabilityNarrowing(lattice)
        assert cn.parent_lattice is lattice

    def test_parent_lattice_property(self) -> None:
        lattice = MockLattice()
        cn = CapabilityNarrowing(lattice)
        assert cn.parent_lattice is lattice
        assert cn.parent_lattice.all_actions("admin")


class TestCapabilityNarrowingNarrow:
    def test_exact_match_returns_all(self) -> None:
        cn = CapabilityNarrowing(MockLattice())
        result = cn.narrow({"read", "write"})
        assert result == {"read", "write"}

    def test_child_subset_of_parent(self) -> None:
        cn = CapabilityNarrowing(MockLattice())
        result = cn.narrow({"read"})
        assert result == {"read"}

    def test_child_superset_drops_extra(self) -> None:
        cn = CapabilityNarrowing(MockLattice())
        result = cn.narrow({"read", "write", "super_admin"})
        assert result == {"read", "write"}

    def test_no_overlap_returns_empty(self) -> None:
        cn = CapabilityNarrowing(MockLattice())
        result = cn.narrow({"super_admin", "sudo_all"})
        assert result == set()

    def test_empty_child_actions(self) -> None:
        cn = CapabilityNarrowing(MockLattice())
        result = cn.narrow(set())
        assert result == set()

    def test_viewer_role_restricts_to_read_only(self) -> None:
        cn = CapabilityNarrowing(MockLattice())
        result = cn.narrow({"read", "write", "delete"}, parent_role="viewer")
        assert result == {"read"}

    def test_unknown_role_returns_empty(self) -> None:
        cn = CapabilityNarrowing(MockLattice())
        result = cn.narrow({"read"}, parent_role="nonexistent")
        assert result == set()

    def test_with_actions_that_have_value_attr(self) -> None:
        cn = CapabilityNarrowing(MockLattice())
        result = cn.narrow(
            {
                FakeAction("read"),
                FakeAction("write"),
                FakeAction("super_admin"),
            }
        )
        assert result == {"read", "write"}

    def test_dropped_actions_do_not_crash(self) -> None:
        cn = CapabilityNarrowing(MockLattice())
        result = cn.narrow({"super_admin"})
        assert result == set()


class TestCapabilityNarrowingValidateNarrowing:
    def test_child_is_subset_returns_true(self) -> None:
        result = CapabilityNarrowing.validate_narrowing(
            parent_actions={FakeAction("read"), FakeAction("write")},
            child_actions={FakeAction("read")},
        )
        assert result is True

    def test_child_equals_parent_returns_true(self) -> None:
        result = CapabilityNarrowing.validate_narrowing(
            parent_actions={"read", "write"},
            child_actions={"read", "write"},
        )
        assert result is True

    def test_child_is_superset_returns_false(self) -> None:
        result = CapabilityNarrowing.validate_narrowing(
            parent_actions={"read", "write"},
            child_actions={"read", "write", "delete"},
        )
        assert result is False

    def test_disjoint_returns_false(self) -> None:
        result = CapabilityNarrowing.validate_narrowing(
            parent_actions={"read"},
            child_actions={"delete"},
        )
        assert result is False

    def test_empty_child_returns_true(self) -> None:
        result = CapabilityNarrowing.validate_narrowing(
            parent_actions={"read", "write"},
            child_actions=set(),
        )
        assert result is True

    def test_empty_parent_and_empty_child_returns_true(self) -> None:
        result = CapabilityNarrowing.validate_narrowing(
            parent_actions=set(),
            child_actions=set(),
        )
        assert result is True

    def test_empty_parent_with_child_returns_false(self) -> None:
        result = CapabilityNarrowing.validate_narrowing(
            parent_actions=set(),
            child_actions={"read"},
        )
        assert result is False

    def test_string_actions_work(self) -> None:
        result = CapabilityNarrowing.validate_narrowing(
            parent_actions={"a", "b", "c"},
            child_actions={"a", "b"},
        )
        assert result is True


class TestCapabilityNarrowingToOpenBaoPolicy:
    def test_with_chain_narrows_before_rendering(self) -> None:
        lattice = MockLattice(chain=True)
        cn = CapabilityNarrowing(lattice)
        result = cn.to_openbao_policy({"read", "write", "super_admin"})
        assert "read" in result
        assert "super_admin" not in result

    def test_without_chain_renders_all_actions(self) -> None:
        lattice = MockLattice(chain=False)
        cn = CapabilityNarrowing(lattice)
        result = cn.to_openbao_policy({"super_admin"})
        assert "super_admin" in result

    def test_custom_role_name(self) -> None:
        lattice = MockLattice(chain=True)
        cn = CapabilityNarrowing(lattice)
        result = cn.to_openbao_policy({"read"}, role_name="child-agent")
        assert 'role "child-agent"' in result

    def test_default_role_name(self) -> None:
        lattice = MockLattice(chain=True)
        cn = CapabilityNarrowing(lattice)
        result = cn.to_openbao_policy({"read"})
        assert 'role "subagent"' in result
