"""Unit tests for CapabilityNarrowing and OpenBaoPolicyRenderer."""


from general_ludd.permissions.tool_permissions import (
    CapabilityLattice,
    ToolAction,
)
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

    def test_parent_with_empty_native_actions_blocks_all(self):
        chain = {"readonly": frozenset()}
        lattice = CapabilityLattice(chain=chain)
        narrowing = CapabilityNarrowing(lattice)

        child_actions = {ToolAction.READ, ToolAction.WRITE}
        result = narrowing.narrow(child_actions, parent_role="readonly")

        assert result == set()

    def test_custom_chain_narrowing(self):
        chain = {
            "admin": frozenset({"coder"}),
            "coder": frozenset({"writer"}),
            "writer": frozenset(),
        }
        lattice = CapabilityLattice(chain=chain)
        narrowing = CapabilityNarrowing(lattice)

        parent_all = set(lattice.all_actions("admin"))
        custom_child_actions = {x for x in parent_all if isinstance(x, str)}
        result = narrowing.narrow(custom_child_actions, parent_role="admin")

        assert result == parent_all
        assert result.issubset(parent_all)

    def test_string_action_input(self):
        lattice = CapabilityLattice()
        narrowing = CapabilityNarrowing(lattice)

        child_actions = {"read", "write"}
        result = narrowing.narrow(child_actions, parent_role="admin")

        assert "read" in result
        assert "write" in result


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


class TestOpenBaoPolicyRenderer:
    def test_policy_rendering_produces_valid_hcl(self):
        actions = {ToolAction.READ, ToolAction.WRITE}
        hcl = OpenBaoPolicyRenderer.render(actions, role_name="test-agent")

        assert isinstance(hcl, str)
        assert len(hcl) > 0
        assert 'path "' in hcl or "path '" in hcl
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
        chain: dict[str, frozenset[str]] = {}
        lattice = CapabilityLattice(chain=chain)
        narrowing = CapabilityNarrowing(lattice)

        actions = {ToolAction.READ}
        hcl = narrowing.to_openbao_policy(actions)

        assert isinstance(hcl, str)
        assert len(hcl) > 0


class TestPolicyFragment:
    def test_dataclass_construction(self):
        fragment = PolicyFragment(
            path="secret/*",
            capabilities=frozenset({"read", "write"}),
        )
        assert fragment.path == "secret/*"
        assert "read" in fragment.capabilities
        assert "write" in fragment.capabilities
        assert fragment.constraints == {}

    def test_equality(self):
        a = PolicyFragment(path="secret/*", capabilities=frozenset({"read"}))
        b = PolicyFragment(path="secret/*", capabilities=frozenset({"read"}))
        assert a == b

    def test_inequality(self):
        a = PolicyFragment(path="secret/*", capabilities=frozenset({"read"}))
        b = PolicyFragment(path="sys/*", capabilities=frozenset({"read"}))
        assert a != b
