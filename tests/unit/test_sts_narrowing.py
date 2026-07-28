"""Unit tests for CapabilityNarrowing, OpenBaoPolicyRenderer, and PolicyFragment."""

from __future__ import annotations

from general_ludd.permissions.tool_permissions import CapabilityLattice, ToolAction
from general_ludd.sts.narrowing import (
    CapabilityNarrowing,
    OpenBaoPolicyRenderer,
    PolicyFragment,
)


class TestCapabilityNarrowingNarrow:
    def test_intersection_produces_subset(self):
        lattice = CapabilityLattice()
        narrowing = CapabilityNarrowing(lattice)
        child_actions = {ToolAction.READ, ToolAction.WRITE}
        result = narrowing.narrow(child_actions, parent_role="admin")
        assert isinstance(result, set)
        assert ToolAction.READ.value in result
        assert ToolAction.WRITE.value in result
        assert result.issubset(lattice.all_actions("admin"))

    def test_narrowing_is_never_wider_than_parent(self):
        lattice = CapabilityLattice()
        narrowing = CapabilityNarrowing(lattice)
        child_actions = {
            ToolAction.READ,
            ToolAction.WRITE,
            ToolAction.EXECUTE,
            ToolAction.DELETE,
        }
        result = narrowing.narrow(child_actions, parent_role="reader")
        parent_all = lattice.all_actions("reader")
        assert result.issubset(parent_all)
        assert ToolAction.READ.value in result
        assert ToolAction.WRITE.value not in result

    def test_empty_intersection_produces_empty(self):
        lattice = CapabilityLattice()
        narrowing = CapabilityNarrowing(lattice)
        child_actions = {ToolAction.DELETE}
        result = narrowing.narrow(child_actions, parent_role="reader")
        assert result == set()

    def test_parent_with_custom_empty_role_blocks_all(self):
        chain = {"readonly": frozenset()}
        lattice = CapabilityLattice(chain=chain)
        narrowing = CapabilityNarrowing(lattice)
        child_actions = {ToolAction.READ, ToolAction.WRITE}
        result = narrowing.narrow(child_actions, parent_role="readonly")
        assert result == set()

    def test_custom_chain_narrowing_inherits_parents(self):
        chain = {"admin": frozenset({"coder"}), "coder": frozenset({"writer"}), "writer": frozenset()}
        lattice = CapabilityLattice(chain=chain)
        narrowing = CapabilityNarrowing(lattice)
        parent_all = lattice.all_actions("admin")
        result = narrowing.narrow(parent_all, parent_role="admin")
        assert result == parent_all

    def test_string_action_input(self):
        lattice = CapabilityLattice()
        narrowing = CapabilityNarrowing(lattice)
        child_actions = {"read", "write"}
        result = narrowing.narrow(child_actions, parent_role="admin")
        assert "read" in result
        assert "write" in result

    def test_narrow_defaults_to_admin_role(self):
        lattice = CapabilityLattice()
        narrowing = CapabilityNarrowing(lattice)
        result = narrowing.narrow({ToolAction.READ}, parent_role="admin")
        assert ToolAction.READ.value in result


class TestValidateNarrowing:
    def test_subset_is_valid(self):
        parent = {ToolAction.READ, ToolAction.WRITE, ToolAction.EXECUTE}
        child = {ToolAction.READ, ToolAction.WRITE}
        assert CapabilityNarrowing.validate_narrowing(parent, child) is True

    def test_equal_is_valid(self):
        parent = {ToolAction.READ, ToolAction.WRITE}
        child = {ToolAction.READ, ToolAction.WRITE}
        assert CapabilityNarrowing.validate_narrowing(parent, child) is True

    def test_superset_is_invalid(self):
        parent = {ToolAction.READ}
        child = {ToolAction.READ, ToolAction.WRITE}
        assert CapabilityNarrowing.validate_narrowing(parent, child) is False

    def test_disjoint_is_invalid(self):
        parent = {ToolAction.READ}
        child = {ToolAction.WRITE}
        assert CapabilityNarrowing.validate_narrowing(parent, child) is False

    def test_string_input(self):
        parent = {"read", "write", "execute"}
        child = {"read", "write"}
        assert CapabilityNarrowing.validate_narrowing(parent, child) is True

    def test_string_superset_is_invalid(self):
        parent = {"read"}
        child = {"read", "delete"}
        assert CapabilityNarrowing.validate_narrowing(parent, child) is False

    def test_empty_child_is_valid(self):
        parent = {ToolAction.READ, ToolAction.WRITE}
        child: set[ToolAction] = set()
        assert CapabilityNarrowing.validate_narrowing(parent, child) is True

    def test_empty_parent_empty_child_valid(self):
        parent: set[ToolAction] = set()
        child: set[ToolAction] = set()
        assert CapabilityNarrowing.validate_narrowing(parent, child) is True

    def test_empty_parent_nonempty_child_invalid(self):
        parent: set[ToolAction] = set()
        child = {ToolAction.READ}
        assert CapabilityNarrowing.validate_narrowing(parent, child) is False


class TestOpenBaoPolicyRenderer:
    def test_policy_rendering_produces_valid_hcl(self):
        actions = {ToolAction.READ, ToolAction.WRITE}
        hcl = OpenBaoPolicyRenderer.render(actions, role_name="test-agent")
        assert isinstance(hcl, str)
        assert len(hcl) > 0
        assert "path" in hcl
        assert "capabilities" in hcl
        assert "read" in hcl

    def test_empty_actions_returns_empty(self):
        hcl = OpenBaoPolicyRenderer.render(set())
        assert hcl == ""

    def test_deduplicates_paths(self):
        actions = {ToolAction.READ, ToolAction.CREATE, ToolAction.OVERWRITE}
        hcl = OpenBaoPolicyRenderer.render(actions)
        assert isinstance(hcl, str)
        assert len(hcl) > 0
        assert "capabilities" in hcl

    def test_string_actions_accepted(self):
        actions = {"read", "write"}
        hcl = OpenBaoPolicyRenderer.render(actions)
        assert isinstance(hcl, str)
        assert len(hcl) > 0

    def test_execute_maps_to_sys_path(self):
        actions = {ToolAction.EXECUTE}
        hcl = OpenBaoPolicyRenderer.render(actions)
        assert "sys/*" in hcl
        assert "sudo" in hcl

    def test_role_name_in_header(self):
        actions = {ToolAction.READ}
        hcl = OpenBaoPolicyRenderer.render(actions, role_name="my-role")
        assert "my-role" in hcl

    def test_delete_action(self):
        actions = {ToolAction.DELETE}
        hcl = OpenBaoPolicyRenderer.render(actions)
        assert "delete" in hcl

    def test_default_role_name_used_when_not_provided(self):
        actions = {ToolAction.READ}
        hcl = OpenBaoPolicyRenderer.render(actions)
        assert "default" in hcl

    def test_unknown_action_uses_fallback_path_and_verb(self):
        actions = {"nonexistent_action"}
        hcl = OpenBaoPolicyRenderer.render(actions)
        assert "secret/*" in hcl
        assert "nonexistent_action" in hcl

    def test_merges_verbs_on_same_path(self):
        actions = {ToolAction.READ, ToolAction.WRITE}
        hcl = OpenBaoPolicyRenderer.render(actions)
        assert "read" in hcl
        assert "create" in hcl
        assert "update" in hcl


class TestCapabilityNarrowingToOpenbaoPolicy:
    def test_narrows_and_renders(self):
        lattice = CapabilityLattice()
        narrowing = CapabilityNarrowing(lattice)
        child_actions = {ToolAction.READ, ToolAction.WRITE, ToolAction.EXECUTE}
        hcl = narrowing.to_openbao_policy(child_actions, role_name="subagent-reader")
        assert isinstance(hcl, str)
        assert len(hcl) > 0
        assert "subagent-reader" in hcl
        assert "capabilities" in hcl

    def test_empty_lattice_falls_back_to_direct_render(self):
        lattice = CapabilityLattice(chain={})
        narrowing = CapabilityNarrowing(lattice)
        actions = {ToolAction.READ}
        hcl = narrowing.to_openbao_policy(actions)
        assert isinstance(hcl, str)
        assert len(hcl) > 0

    def test_lattice_with_chain_narrows_first(self):
        chain = {"admin": frozenset({"coder"}), "coder": frozenset({"writer"}), "writer": frozenset()}
        lattice = CapabilityLattice(chain=chain)
        narrowing = CapabilityNarrowing(lattice)
        child_actions = {"edit"}
        hcl = narrowing.to_openbao_policy(child_actions, role_name="narrowed")
        assert "narrowed" in hcl
        assert len(hcl) > 0


class TestPolicyFragment:
    def test_dataclass_construction(self):
        fragment = PolicyFragment(path="secret/*", capabilities=frozenset({"read", "write"}))
        assert fragment.path == "secret/*"
        assert "read" in fragment.capabilities
        assert "write" in fragment.capabilities
        assert fragment.constraints == {}

    def test_equality(self):
        a = PolicyFragment(path="secret/*", capabilities=frozenset({"read"}))
        b = PolicyFragment(path="secret/*", capabilities=frozenset({"read"}))
        assert a == b

    def test_inequality_path(self):
        a = PolicyFragment(path="secret/*", capabilities=frozenset({"read"}))
        b = PolicyFragment(path="sys/*", capabilities=frozenset({"read"}))
        assert a != b

    def test_inequality_capabilities(self):
        a = PolicyFragment(path="secret/*", capabilities=frozenset({"read"}))
        b = PolicyFragment(path="secret/*", capabilities=frozenset({"write"}))
        assert a != b

    def test_constraints_passed_through(self):
        fragment = PolicyFragment(path="x/*", capabilities=frozenset(), constraints={"ttl": 3600})
        assert fragment.constraints == {"ttl": 3600}

    def test_frozen_prevents_mutation(self):
        fragment = PolicyFragment(path="x/*", capabilities=frozenset({"read"}))
        try:
            fragment.path = "y/*"  # type: ignore[misc]
            raise AssertionError("PolicyFragment should be frozen")
        except Exception:
            pass

    def test_hashable(self):
        fragment = PolicyFragment(path="a", capabilities=frozenset({"b"}))
        assert hash(fragment) is not None
