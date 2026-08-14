"""Deep capability lattice + routing tests.

Covers: lattice traversal (parent→child, child→parent), capability inheritance
chains, role-based access gating, dynamic capability registration, circular
dependency detection, and capability version compatibility.

Spans four source modules:
- general_ludd.security.capability_lattice  (daemon-side per-role dispatch gating)
- general_ludd.permissions.tool_permissions  (CapabilityLattice, PermissionEvaluator)
- general_ludd.dispatch.capabilities         (CapabilityRegistry, CollectionMeta)
- general_ludd.dispatch.router               (CapabilityRouter)
- general_ludd.dispatch.dynamic_dispatcher   (DynamicDispatcher)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from general_ludd.dispatch.capabilities import (
    CapabilityRegistry,
    CollectionMeta,
    discover_capabilities,
)
from general_ludd.dispatch.dynamic_dispatcher import (
    UNRESTRICTED_ROLE,
    DynamicDispatcher,
    ToolCall,
    parse_tool_calls,
    structured_tool_calls_to_calls,
)
from general_ludd.dispatch.router import CapabilityRouter
from general_ludd.permissions.tool_permissions import (
    CapabilityLattice,
    PermissionEvaluator,
    ToolAction,
    ToolPermission,
    ToolPermissionSpec,
)
from general_ludd.security.capability_lattice import (
    _BUILTIN,
    _KIND_REQUIRES_SELF_MODIFY,
    CapabilityError,
    ProtectedPathError,
    RoleCapabilities,
    capabilities_for,
    check_dispatch,
    check_self_modification,
    is_collections_path,
    is_protected_path,
    role_may_dispatch,
)


def _attempt_frozen_mutation(instance: object, attribute: str, value: object) -> None:
    """Attempt mutation through the public Python attribute protocol."""
    setattr(instance, attribute, value)


# ============================================================================
# 1. Lattice traversal — parent→child and child→parent
# ============================================================================


class TestLatticeTraversalParentChild:
    """Tests for parent→child navigation and child→parent ancestry lookups."""

    def test_admin_parent_of_coder(self) -> None:
        lattice = CapabilityLattice()
        parents = lattice._all_parents("admin")
        assert "coder" in parents
        assert "writer" in parents
        assert "reader" in parents

    def test_coder_parents_include_writer_and_reader(self) -> None:
        lattice = CapabilityLattice()
        parents = lattice._all_parents("coder")
        assert "writer" in parents
        assert "reader" in parents
        assert "admin" not in parents

    def test_writer_parents_only_reader(self) -> None:
        lattice = CapabilityLattice()
        parents = lattice._all_parents("writer")
        assert parents == frozenset({"reader"})

    def test_reader_has_no_parents(self) -> None:
        lattice = CapabilityLattice()
        assert lattice._all_parents("reader") == frozenset()

    def test_viewer_is_standalone_no_parents(self) -> None:
        lattice = CapabilityLattice()
        parents = lattice._all_parents("viewer")
        assert parents == frozenset()
        assert "reader" not in parents

    def test_all_parents_returns_frozenset(self) -> None:
        lattice = CapabilityLattice()
        result = lattice._all_parents("admin")
        assert isinstance(result, frozenset)

    def test_child_does_not_appear_in_own_parents(self) -> None:
        lattice = CapabilityLattice()
        assert "admin" not in lattice._all_parents("admin")
        assert "coder" not in lattice._all_parents("coder")

    # -- Custom chain traversal --

    def test_custom_chain_parent_traversal(self) -> None:
        lattice = CapabilityLattice(
            chain={
                "senior": frozenset({"junior"}),
                "junior": frozenset({"intern"}),
                "intern": frozenset(),
            }
        )
        parents = lattice._all_parents("senior")
        assert parents == frozenset({"junior", "intern"})

    def test_custom_chain_leaf_no_parents(self) -> None:
        lattice = CapabilityLattice(
            chain={
                "senior": frozenset({"junior"}),
                "junior": frozenset(),
            }
        )
        assert lattice._all_parents("junior") == frozenset()

    def test_custom_chain_unknown_role_no_parents(self) -> None:
        lattice = CapabilityLattice(chain={"manager": frozenset({"worker"}), "worker": frozenset()})
        assert lattice._all_parents("nobody") == frozenset()


# ============================================================================
# 2. Capability inheritance chains
# ============================================================================


class TestCapabilityInheritanceChains:
    """Tests for transitive capability inheritance through chain traversal."""

    def test_admin_inherits_reader_read(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("admin", ToolAction.READ) is True

    def test_admin_inherits_writer_write_create(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("admin", ToolAction.WRITE) is True
        assert lattice.is_granted("admin", ToolAction.CREATE) is True

    def test_admin_inherits_coder_execute(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("admin", ToolAction.EXECUTE) is True

    def test_admin_native_delete(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("admin", ToolAction.DELETE) is True

    def test_reader_has_only_read(self) -> None:
        lattice = CapabilityLattice()
        actions = lattice.all_actions("reader")
        assert actions == frozenset({"read"})
        assert lattice.is_granted("reader", "read") is True
        for action in ToolAction:
            if action != ToolAction.READ:
                assert lattice.is_granted("reader", action.value) is False

    def test_custom_chain_transitive_inheritance(self) -> None:
        lattice = CapabilityLattice(
            chain={
                "publisher": frozenset({"editor"}),
                "editor": frozenset({"viewer"}),
                "viewer": frozenset(),
            }
        )
        assert lattice.is_granted("publisher", "edit") is True
        assert lattice.is_granted("publisher", "view") is True
        assert lattice.is_granted("editor", "view") is True
        assert lattice.is_granted("editor", "publish") is False

    def test_diamond_inheritance_does_not_duplicate(self) -> None:
        """A role with a shared ancestor via two paths must not double-count."""
        lattice = CapabilityLattice(
            chain={
                "ceo": frozenset({"eng_lead", "sales_lead"}),
                "eng_lead": frozenset({"worker"}),
                "sales_lead": frozenset({"worker"}),
                "worker": frozenset(),
            }
        )
        actions = lattice.all_actions("ceo")
        assert actions == frozenset({"ceo", "eng_lead", "sales_lead", "work"})

    def test_native_actions_return_frozenset(self) -> None:
        lattice = CapabilityLattice()
        assert isinstance(lattice.native_actions("admin"), frozenset)

    def test_all_actions_return_frozenset(self) -> None:
        lattice = CapabilityLattice()
        assert isinstance(lattice.all_actions("admin"), frozenset)


# ============================================================================
# 3. Role-based access gating (dispatch + self-modification)
# ============================================================================


class TestRoleBasedAccessGating:
    """Tests for per-role dispatch and modification access control."""

    # -- capabilities_for --

    def test_known_role_returns_correct_caps(self) -> None:
        caps = capabilities_for("coder")
        assert isinstance(caps, RoleCapabilities)
        assert caps.role == "coder"
        assert caps.collections_self_modify is False
        assert "role" in caps.dispatch_kinds
        assert "collection" not in caps.dispatch_kinds

    def test_self_improve_agent_has_collections_self_modify(self) -> None:
        caps = capabilities_for("self_improve_agent")
        assert caps.collections_self_modify is True
        assert "collection" in caps.dispatch_kinds

    def test_unknown_role_is_baseline(self) -> None:
        caps = capabilities_for("nonexistent")
        assert caps.role == "nonexistent"
        assert caps.collections_self_modify is False
        assert caps.dispatch_kinds == frozenset()

    def test_none_role_is_baseline(self) -> None:
        caps = capabilities_for(None)
        assert caps.role == "<unknown>"
        assert caps.collections_self_modify is False

    def test_empty_string_role_is_baseline(self) -> None:
        caps = capabilities_for("")
        assert caps.role == "<unknown>"
        assert caps.collections_self_modify is False

    def test_non_string_role_is_baseline(self) -> None:
        caps = capabilities_for(cast(Any, 42))
        assert caps.role == "<unknown>"

    # -- role_may_dispatch --

    def test_coder_may_dispatch_role(self) -> None:
        assert role_may_dispatch("coder", "role") is True

    def test_coder_may_dispatch_mcp(self) -> None:
        assert role_may_dispatch("coder", "mcp") is True

    def test_coder_may_dispatch_skill(self) -> None:
        assert role_may_dispatch("coder", "skill") is True

    def test_coder_may_not_dispatch_collection(self) -> None:
        assert role_may_dispatch("coder", "collection") is False

    def test_operator_without_self_modify_may_not_dispatch_collection(self) -> None:
        caps = capabilities_for("operator")
        assert "collection" in caps.dispatch_kinds
        assert caps.collections_self_modify is False
        assert role_may_dispatch("operator", "collection") is False

    def test_none_role_may_not_dispatch_anything(self) -> None:
        for kind in ("role", "collection", "mcp", "skill"):
            assert role_may_dispatch(None, kind) is False

    def test_unknown_role_may_not_dispatch_anything(self) -> None:
        assert role_may_dispatch("unknown", "role") is False
        assert role_may_dispatch("unknown", "mcp") is False

    # -- check_dispatch (fail-closed variant) --

    def test_check_dispatch_passes_for_valid_role_kind(self) -> None:
        check_dispatch("coder", "role")  # must not raise

    def test_check_dispatch_raises_for_insufficient_capability(self) -> None:
        with pytest.raises(CapabilityError, match="lacks the capability"):
            check_dispatch("coder", "collection")

    # -- check_self_modification --

    def test_check_self_modification_passes_non_collections_path(self) -> None:
        check_self_modification("/some/arbitrary/file.py", "coder")

    def test_check_self_modification_raises_for_protected_path(self) -> None:
        with pytest.raises(ProtectedPathError, match="protected guard file"):
            check_self_modification("src/general_ludd/security/capability_lattice.py", "self_improve_agent")

    def test_check_self_modification_allows_self_improve_in_collections(self) -> None:
        check_self_modification("collections/ansible_collections/x/y.py", "self_improve_agent")

    def test_check_self_modification_denies_coder_in_collections(self) -> None:
        with pytest.raises(CapabilityError, match="may not self-modify collections"):
            check_self_modification("collections/ansible_collections/x/y.py", "coder")

    # -- is_collections_path --

    def test_is_collections_path_true(self) -> None:
        assert is_collections_path("/some/path/collections/ansible_collections/x/y.py") is True

    def test_is_collections_path_false(self) -> None:
        assert is_collections_path("/some/path/other/x/y.py") is False

    def test_is_protected_path_for_permissions_file(self) -> None:
        assert is_protected_path("src/general_ludd/security/capability_lattice.py") is True

    def test_is_protected_path_for_ordinary_file(self) -> None:
        assert is_protected_path("src/general_ludd/utils/helpers.py") is False


# ============================================================================
# 4. Dynamic capability registration (CapabilityRegistry + Router)
# ============================================================================


class TestDynamicCapabilityRegistration:
    """Tests for CapabilityRegistry, CollectionMeta, and routing."""

    def test_add_collection_and_lookup_by_tag(self) -> None:
        reg = CapabilityRegistry()
        meta = CollectionMeta(
            name="my_coll",
            namespace="ns",
            version="1.2.3",
            tags=frozenset({"game_logic", "ai"}),
        )
        reg.add_collection(meta)
        assert "my_coll" in reg.collections
        assert reg.lookup_by_tag("game_logic") == frozenset({"my_coll"})
        assert reg.lookup_by_tag("ai") == frozenset({"my_coll"})

    def test_multiple_collections_under_same_tag(self) -> None:
        reg = CapabilityRegistry()
        reg.add_collection(CollectionMeta(name="a", namespace="ns", tags=frozenset({"shared"})))
        reg.add_collection(CollectionMeta(name="b", namespace="ns", tags=frozenset({"shared"})))
        assert reg.lookup_by_tag("shared") == frozenset({"a", "b"})

    def test_lookup_unknown_tag_returns_empty(self) -> None:
        reg = CapabilityRegistry()
        assert reg.lookup_by_tag("nope") == frozenset()

    def test_serialization_roundtrip(self) -> None:
        reg = CapabilityRegistry()
        meta = CollectionMeta(
            name="coll",
            namespace="ns",
            version="0.1.0",
            description="test",
            tags=frozenset({"tag1", "tag2"}),
            raw_tags=["tag1", "tag2"],
            model_capabilities=[
                {
                    "name": "mc1",
                    "description": "desc",
                    "quality_class": "high",
                    "roles": ["role1"],
                    "model_needs": [],
                    "aliases": [],
                }
            ],
            role_capabilities={"role1": ["cap1"]},
        )
        reg.add_collection(meta)
        d = reg.to_dict()
        restored = CapabilityRegistry.from_dict(d)
        assert restored.lookup_by_tag("tag1") == frozenset({"coll"})
        assert restored.lookup_by_tag("tag2") == frozenset({"coll"})
        assert restored.collections["coll"].version == "0.1.0"

    def test_from_dict_empty_data(self) -> None:
        reg = CapabilityRegistry.from_dict({})
        assert reg.collections == {}
        assert reg.tag_index == {}

    def test_from_dict_non_dict_collections_skipped(self) -> None:
        reg = CapabilityRegistry.from_dict({"collections": "not_a_dict"})
        assert reg.collections == {}

    def test_from_dict_missing_tags_defaults(self) -> None:
        reg = CapabilityRegistry.from_dict({"collections": {"c": {"name": "c", "namespace": "ns"}}})
        assert "c" in reg.collections
        assert reg.collections["c"].tags == frozenset()

    # -- CollectionMeta --

    def test_collection_meta_to_dict(self) -> None:
        meta = CollectionMeta(name="c", namespace="ns", version="1.0")
        d = meta.to_dict()
        assert d["name"] == "c"
        assert d["namespace"] == "ns"
        assert d["version"] == "1.0"

    def test_collection_meta_from_galaxy_minimal(self) -> None:
        meta = CollectionMeta.from_galaxy({"name": "c", "namespace": "ns"})
        assert meta.name == "c"
        assert meta.namespace == "ns"
        assert meta.version == "unknown"
        assert meta.tags == frozenset()

    def test_collection_meta_from_galaxy_tags_str(self) -> None:
        meta = CollectionMeta.from_galaxy({"name": "c", "namespace": "ns", "tags": ["t1", "t2"]})
        assert meta.tags == frozenset({"t1", "t2"})


# ============================================================================
# 5. Circular dependency detection
# ============================================================================


class TestCircularDependencyDetection:
    """CapabilityLattice must not infinite-loop on circular chains."""

    def test_circular_chain_terminates(self) -> None:
        lattice = CapabilityLattice(
            chain={
                "a": frozenset({"b"}),
                "b": frozenset({"c"}),
                "c": frozenset({"a"}),
            }
        )
        actions = lattice.all_actions("a")
        assert isinstance(actions, frozenset)

    def test_self_referential_parent_terminates(self) -> None:
        lattice = CapabilityLattice(chain={"a": frozenset({"a"})})
        actions = lattice.all_actions("a")
        assert isinstance(actions, frozenset)
        assert "a" in actions

    def test_two_node_cycle_terminates(self) -> None:
        lattice = CapabilityLattice(
            chain={
                "x": frozenset({"y"}),
                "y": frozenset({"x"}),
            }
        )
        actions = lattice.all_actions("x")
        assert "x" in actions
        assert "y" in actions


# ============================================================================
# 6. Capability version compatibility
# ============================================================================


class TestCapabilityVersionCompatibility:
    """Collection versioning and capability stability."""

    def test_registry_preserves_version_on_roundtrip(self) -> None:
        reg = CapabilityRegistry()
        meta = CollectionMeta(name="c", namespace="ns", version="2.0.1")
        reg.add_collection(meta)
        restored = CapabilityRegistry.from_dict(reg.to_dict())
        assert restored.collections["c"].version == "2.0.1"

    def test_version_unknown_when_not_in_galaxy(self) -> None:
        meta = CollectionMeta.from_galaxy({"name": "c", "namespace": "ns"})
        assert meta.version == "unknown"

    def test_version_preserved_in_capability_route(self) -> None:
        reg = CapabilityRegistry()
        meta = CollectionMeta(name="c", namespace="ns", version="3.2.1", tags=frozenset({"foo"}))
        reg.add_collection(meta)
        router = CapabilityRouter(reg)
        result = router.route("foo")
        assert result.ok
        assert result.matches[0].collection.version == "3.2.1"

    def test_discover_capabilities_returns_registry(self) -> None:
        reg = discover_capabilities()
        assert isinstance(reg, CapabilityRegistry)

    # -- Router --

    def test_router_empty_capability_returns_error(self) -> None:
        reg = CapabilityRegistry()
        router = CapabilityRouter(reg)
        result = router.route("")
        assert result.ok is False
        assert result.error == "empty capability string"

    def test_router_unknown_capability_returns_error(self) -> None:
        reg = CapabilityRegistry()
        reg.add_collection(CollectionMeta(name="c", namespace="ns", tags=frozenset({"known"})))
        router = CapabilityRouter(reg)
        result = router.route("unknown")
        assert result.ok is False
        assert "no collection found" in (result.error or "")

    def test_router_route_by_collection(self) -> None:
        reg = CapabilityRegistry()
        reg.add_collection(CollectionMeta(name="c", namespace="ns"))
        router = CapabilityRouter(reg)
        result = router.route_by_collection("c")
        assert result.ok is True
        assert result.matches[0].name == "c"

    def test_router_route_by_collection_not_found(self) -> None:
        reg = CapabilityRegistry()
        router = CapabilityRouter(reg)
        result = router.route_by_collection("nope")
        assert result.ok is False
        assert "collection not found" in (result.error or "")

    def test_router_get_collection(self) -> None:
        reg = CapabilityRegistry()
        meta = CollectionMeta(name="c", namespace="ns")
        reg.add_collection(meta)
        router = CapabilityRouter(reg)
        assert router.get_collection("c") is meta
        assert router.get_collection("nope") is None

    def test_router_list_capabilities(self) -> None:
        reg = CapabilityRegistry()
        reg.add_collection(CollectionMeta(name="c", namespace="ns", tags=frozenset({"a", "b"})))
        router = CapabilityRouter(reg)
        caps = router.list_capabilities()
        assert sorted(caps) == ["a", "b"]


# ============================================================================
# 7. PermissionEvaluator — deep coverage
# ============================================================================


class TestPermissionEvaluatorDeep:
    """Additional PermissionEvaluator edge cases."""

    def test_deny_overrides_allow(self) -> None:
        spec = ToolPermissionSpec(
            role="coder",
            permissions=(ToolPermission(tool="file", allowed_actions=("read",), denied_actions=("read",)),),
        )
        ev = PermissionEvaluator(lattice=CapabilityLattice())
        assert ev.may_use(spec, "file", "read") is False

    def test_role_without_action_in_lattice_denied(self) -> None:
        spec = ToolPermissionSpec(
            role="viewer",
            permissions=(ToolPermission(tool="file", allowed_actions=("execute",)),),
        )
        ev = PermissionEvaluator(lattice=CapabilityLattice())
        assert ev.may_use(spec, "file", "execute") is False

    def test_glob_deny_on_any_tool(self) -> None:
        spec = ToolPermissionSpec(
            role="coder",
            permissions=(
                ToolPermission(tool="*", denied_actions=("delete",)),
                ToolPermission(tool="bash", allowed_actions=("execute",)),
            ),
        )
        ev = PermissionEvaluator(lattice=CapabilityLattice())
        assert ev.may_use(spec, "bash", "delete") is False
        assert ev.may_use(spec, "bash", "execute") is True

    def test_multiple_tools_bulk_evaluate(self) -> None:
        spec = ToolPermissionSpec(
            role="writer",
            permissions=(
                ToolPermission(tool="file", allowed_actions=("read", "write")),
                ToolPermission(tool="db", allowed_actions=("read",)),
            ),
        )
        ev = PermissionEvaluator(lattice=CapabilityLattice())
        results = ev.evaluate_all(
            spec,
            [
                ("file", "read"),
                ("file", "write"),
                ("file", "delete"),
                ("db", "read"),
                ("db", "write"),
            ],
        )
        assert results[("file", "read")] is True
        assert results[("file", "write")] is True
        assert results[("file", "delete")] is False  # not allowed
        assert results[("db", "read")] is True
        assert results[("db", "write")] is False  # not allowed + writer lacks execute


# ============================================================================
# 8. DynamicDispatcher — capability-gated dispatch
# ============================================================================


class TestDynamicDispatcherDeep:
    """DynamicDispatcher with role-based capability gating."""

    @staticmethod
    def _ok_handler() -> Callable[[str, dict], str]:
        def h(name: str, args: dict) -> str:
            return f"handled:{name}"

        return h

    def test_dispatcher_none_role_denies_privileged_kinds(self) -> None:
        dd = DynamicDispatcher(role_handler=self._ok_handler(), role=None)
        result = asyncio.run(dd.dispatch(ToolCall(kind="role", name="do_thing")))
        assert result.ok is False
        assert result.error == "capability_denied"

    def test_dispatcher_coder_role_allows_role_kind(self) -> None:
        dd = DynamicDispatcher(role_handler=self._ok_handler(), role="coder")
        result = asyncio.run(dd.dispatch(ToolCall(kind="role", name="do_thing")))
        assert result.ok is True
        assert result.output == "handled:do_thing"

    def test_dispatcher_coder_role_denies_collection_kind(self) -> None:
        dd = DynamicDispatcher(collection_handler=self._ok_handler(), role="coder")
        result = asyncio.run(dd.dispatch(ToolCall(kind="collection", name="coll_op")))
        assert result.ok is False
        assert result.error == "capability_denied"

    def test_dispatcher_unrestricted_bypasses_all(self) -> None:
        dd = DynamicDispatcher(role_handler=self._ok_handler(), role=UNRESTRICTED_ROLE)
        result = asyncio.run(dd.dispatch(ToolCall(kind="role", name="do_thing")))
        assert result.ok is True

    def test_unrestricted_role_is_object_sentinel(self) -> None:
        assert UNRESTRICTED_ROLE is not None
        assert not isinstance(UNRESTRICTED_ROLE, str)

    def test_dispatcher_unknown_kind_fails_closed(self) -> None:
        dd = DynamicDispatcher(role=UNRESTRICTED_ROLE)
        result = asyncio.run(dd.dispatch(ToolCall(kind="bogus", name="x")))
        assert result.ok is False
        assert result.error is not None and "unknown_kind" in result.error

    def test_dispatcher_handler_error_fails_closed(self) -> None:
        def raiser(_name: str, _args: dict) -> str:
            raise RuntimeError("boom")

        dd = DynamicDispatcher(mcp_handler=raiser, role=UNRESTRICTED_ROLE)
        result = asyncio.run(dd.dispatch(ToolCall(kind="mcp", name="x")))
        assert result.ok is False
        assert result.error == "handler_error"

    def test_dispatcher_dispatch_all(self) -> None:
        dd = DynamicDispatcher(role_handler=self._ok_handler(), role="coder")
        results = asyncio.run(
            dd.dispatch_all(
                [
                    ToolCall(kind="role", name="a"),
                    ToolCall(kind="role", name="b"),
                ]
            )
        )
        assert len(results) == 2
        assert results[0].ok is True
        assert results[1].ok is True

    def test_dispatcher_list_available(self) -> None:
        dd = DynamicDispatcher(role_handler=self._ok_handler())
        info = dd.list_available()
        assert "role" in info["registered_kinds"]

    def test_report_status_cannot_dispatch_role(self) -> None:
        dd = DynamicDispatcher(role_handler=self._ok_handler(), role="report_status")
        result = asyncio.run(dd.dispatch(ToolCall(kind="role", name="x")))
        assert result.ok is False

    def test_report_status_can_dispatch_skill(self) -> None:
        dd = DynamicDispatcher(skill_handler=self._ok_handler(), role="report_status")
        result = asyncio.run(dd.dispatch(ToolCall(kind="skill", name="x")))
        assert result.ok is True

    def test_event_loop_cannot_dispatch_collection(self) -> None:
        from general_ludd.security.capability_lattice import _BUILTIN

        caps = _BUILTIN["event_loop"]
        assert "collection" not in caps.dispatch_kinds
        assert caps.collections_self_modify is False

    def test_security_auditor_can_dispatch_role_and_skill(self) -> None:
        dd = DynamicDispatcher(
            role_handler=self._ok_handler(),
            skill_handler=self._ok_handler(),
            role="security_auditor",
        )
        r1 = asyncio.run(dd.dispatch(ToolCall(kind="role", name="x")))
        r2 = asyncio.run(dd.dispatch(ToolCall(kind="skill", name="x")))
        r3 = asyncio.run(dd.dispatch(ToolCall(kind="mcp", name="x")))
        assert r1.ok is True
        assert r2.ok is True
        assert r3.ok is False


# ============================================================================
# 9. parse_tool_calls + structured_tool_calls_to_calls
# ============================================================================


class TestParseToolCalls:
    """Tool call parsing edge cases."""

    def test_parse_dict_single_call(self) -> None:
        calls = parse_tool_calls({"kind": "role", "name": "agent_a", "args": {"x": 1}})
        assert len(calls) == 1
        assert calls[0].kind == "role"
        assert calls[0].name == "agent_a"

    def test_parse_tool_calls_list(self) -> None:
        calls = parse_tool_calls(
            {
                "tool_calls": [
                    {"kind": "role", "name": "a"},
                    {"kind": "mcp", "name": "b"},
                ]
            }
        )
        assert len(calls) == 2

    def test_parse_json_string(self) -> None:
        calls = parse_tool_calls(json.dumps({"kind": "role", "name": "a"}))
        assert len(calls) == 1

    def test_parse_invalid_json_returns_empty(self) -> None:
        assert parse_tool_calls("not json") == []

    def test_parse_non_dict_returns_empty(self) -> None:
        assert parse_tool_calls(cast(Any, 123)) == []

    def test_parse_missing_name_skipped(self) -> None:
        calls = parse_tool_calls({"tool_calls": [{"kind": "role"}]})
        assert len(calls) == 0

    def test_parse_name_truncated_to_256(self) -> None:
        long_name = "x" * 500
        calls = parse_tool_calls({"kind": "role", "name": long_name})
        assert len(calls[0].name) == 256

    def test_structured_tool_calls_basic(self) -> None:
        raw = [
            {
                "id": "1",
                "type": "function",
                "function": {"name": "tool_x", "arguments": {"a": 1}},
            }
        ]
        calls = structured_tool_calls_to_calls(raw)
        assert len(calls) == 1
        assert calls[0].kind == "mcp"
        assert calls[0].name == "tool_x"

    def test_structured_tool_calls_json_args(self) -> None:
        raw = [
            {
                "id": "1",
                "type": "function",
                "function": {"name": "t", "arguments": json.dumps({"b": 2})},
            }
        ]
        calls = structured_tool_calls_to_calls(raw)
        assert calls[0].args == {"b": 2}

    def test_structured_tool_calls_none_returns_empty(self) -> None:
        assert structured_tool_calls_to_calls(None) == []

    def test_structured_tool_calls_skip_non_dict(self) -> None:
        assert structured_tool_calls_to_calls(cast(Any, ["bad"])) == []


# ============================================================================
# 10. Lattice frozen / immutability
# ============================================================================


class TestLatticeImmutability:
    """CapabilityLattice and related dataclasses must be frozen."""

    def test_lattice_is_frozen(self) -> None:
        lattice = CapabilityLattice()
        with pytest.raises(FrozenInstanceError):
            _attempt_frozen_mutation(lattice, "chain", {})

    def test_role_capabilities_is_frozen(self) -> None:
        caps = capabilities_for("admin")
        with pytest.raises(FrozenInstanceError):
            _attempt_frozen_mutation(caps, "collections_self_modify", True)

    def test_tool_permission_is_frozen(self) -> None:
        tp = ToolPermission(tool="*")
        with pytest.raises(FrozenInstanceError):
            _attempt_frozen_mutation(tp, "tool", "bash")


# ============================================================================
# 11. CapabilityLattice all_actions completeness
# ============================================================================


class TestAllActionsCompleteness:
    """Verify that all_actions correctly unions native + inherited."""

    def test_admin_all_actions_complete(self) -> None:
        lattice = CapabilityLattice()
        actions = lattice.all_actions("admin")
        assert actions >= frozenset({"read", "write", "create", "overwrite", "execute", "delete"})

    def test_coder_all_actions_has_read_write_execute(self) -> None:
        lattice = CapabilityLattice()
        actions = lattice.all_actions("coder")
        assert "read" in actions
        assert "write" in actions
        assert "create" in actions
        assert "overwrite" in actions
        assert "execute" in actions
        assert "delete" not in actions

    def test_custom_lattice_native_actions_inferred(self) -> None:
        lattice = CapabilityLattice(chain={"inspector": frozenset({"viewer"}), "viewer": frozenset()})
        assert lattice.native_actions("inspector") == frozenset({"inspect"})
        assert lattice.native_actions("viewer") == frozenset({"view"})


# ============================================================================
# 12. _KIND_REQUIRES_SELF_MODIFY constant
# ============================================================================


class TestKindRequiresSelfModify:
    """Verify the _KIND_REQUIRES_SELF_MODIFY guard constant."""

    def test_collection_kind_requires_self_modify(self) -> None:
        assert "collection" in _KIND_REQUIRES_SELF_MODIFY

    def test_role_kind_does_not_require_self_modify(self) -> None:
        assert "role" not in _KIND_REQUIRES_SELF_MODIFY

    def test_mcp_kind_does_not_require_self_modify(self) -> None:
        assert "mcp" not in _KIND_REQUIRES_SELF_MODIFY

    def test_skill_kind_does_not_require_self_modify(self) -> None:
        assert "skill" not in _KIND_REQUIRES_SELF_MODIFY


# ============================================================================
# 13. _BUILTIN completeness audit
# ============================================================================


class TestBuiltinCompleteness:
    """Every role in _BUILTIN must have a valid shape."""

    KNOWN_ROLES = frozenset(
        {
            "self_improve_agent",
            "self_research_agent",
            "coder",
            "operator",
            "report_status",
            "security_auditor",
            "event_loop",
        }
    )

    def test_all_known_roles_exist(self) -> None:
        for role in self.KNOWN_ROLES:
            assert role in _BUILTIN, f"Missing role: {role}"

    def test_every_role_has_string_role_attr(self) -> None:
        for name, caps in _BUILTIN.items():
            assert isinstance(caps.role, str)
            assert caps.role == name

    def test_only_self_roles_have_collections_self_modify(self) -> None:
        for name, caps in _BUILTIN.items():
            if name.startswith("self_"):
                assert caps.collections_self_modify is True
            else:
                assert caps.collections_self_modify is False

    def test_every_role_has_frozenset_dispatch_kinds(self) -> None:
        for caps in _BUILTIN.values():
            assert isinstance(caps.dispatch_kinds, frozenset)
