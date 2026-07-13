from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from general_ludd.permissions.tool_permissions import (
    CapabilityLattice,
    PermissionEvaluator,
    ToolAction,
    ToolPermission,
    ToolPermissionSpec,
    _action_in,
    _infer_action,
    _matches_scope,
)


class TestToolAction:
    def test_all_actions_are_strings(self) -> None:
        for action in ToolAction:
            assert isinstance(action.value, str)

    def test_known_actions_present(self) -> None:
        actions = {a.value for a in ToolAction}
        assert "read" in actions
        assert "write" in actions
        assert "create" in actions
        assert "execute" in actions
        assert "delete" in actions


class TestToolPermission:
    def test_default_construction(self) -> None:
        tp = ToolPermission(tool="read_file")
        assert tp.tool == "read_file"
        assert tp.allowed_actions == ()
        assert tp.denied_actions == ()
        assert tp.scope is None

    def test_with_actions(self) -> None:
        tp = ToolPermission(
            tool="bash",
            allowed_actions=("execute", "read"),
            denied_actions=("write",),
            scope="project:gludd",
        )
        assert tp.allowed_actions == ("execute", "read")
        assert tp.denied_actions == ("write",)
        assert tp.scope == "project:gludd"

    def test_frozen(self) -> None:
        tp = ToolPermission(tool="*", allowed_actions=("read",))
        with pytest.raises(FrozenInstanceError):
            tp.tool = "bash"  # type: ignore[misc]


class TestToolPermissionSpec:
    def test_default_construction(self) -> None:
        spec = ToolPermissionSpec(role="coder")
        assert spec.role == "coder"
        assert spec.permissions == ()

    def test_permissions_for_exact_match(self) -> None:
        spec = ToolPermissionSpec(
            role="coder",
            permissions=(
                ToolPermission(tool="read_file", allowed_actions=("read",)),
                ToolPermission(tool="bash", allowed_actions=("execute",)),
            ),
        )
        result = spec.permissions_for("read_file")
        assert len(result) == 1
        assert result[0].tool == "read_file"

    def test_permissions_for_wildcard(self) -> None:
        spec = ToolPermissionSpec(
            role="coder",
            permissions=(
                ToolPermission(tool="*", allowed_actions=("read",)),
                ToolPermission(tool="read_file", allowed_actions=("write",)),
            ),
        )
        result = spec.permissions_for("unknown_tool")
        assert len(result) == 1
        assert result[0].tool == "*"


class TestInferAction:
    def test_strips_er_suffix(self) -> None:
        assert _infer_action("viewer") == "view"
        assert _infer_action("reader") == "read"
        assert _infer_action("writer") == "writ"

    def test_strips_or_suffix(self) -> None:
        assert _infer_action("editor") == "edit"

    def test_no_match_returns_original(self) -> None:
        assert _infer_action("admin") == "admin"
        assert _infer_action("x") == "x"


class TestMatchesScope:
    def test_global_rule_matches_everything(self) -> None:
        assert _matches_scope(None, None) is True
        assert _matches_scope(None, "project:gludd") is True

    def test_exact_match(self) -> None:
        assert _matches_scope("project:gludd", "project:gludd") is True

    def test_exact_mismatch(self) -> None:
        assert _matches_scope("project:a", "project:b") is False

    def test_request_scope_none_no_match(self) -> None:
        assert _matches_scope("project:gludd", None) is False

    def test_wildcard_prefix(self) -> None:
        assert _matches_scope("project:*", "project:gludd") is True
        assert _matches_scope("project:*", "project:other") is True

    def test_wildcard_prefix_no_match_other_type(self) -> None:
        assert _matches_scope("project:*", "team:gludd") is False


class TestActionIn:
    def test_wildcard_matches(self) -> None:
        assert _action_in(("*",), "read") is True
        assert _action_in(("*",), "write") is True

    def test_exact_match(self) -> None:
        assert _action_in(("read", "write"), "read") is True

    def test_no_match(self) -> None:
        assert _action_in(("read",), "execute") is False

    def test_empty_actions(self) -> None:
        assert _action_in((), "read") is False


class TestCapabilityLatticeBuiltin:
    def test_default_chain_loaded(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("reader", "read") is True
        assert lattice.is_granted("reader", "write") is False

    def test_writer_inherits_reader(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("writer", "read") is True
        assert lattice.is_granted("writer", "write") is True
        assert lattice.is_granted("writer", "create") is True

    def test_admin_has_all(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("admin", "read") is True
        assert lattice.is_granted("admin", "write") is True
        assert lattice.is_granted("admin", "execute") is True
        assert lattice.is_granted("admin", "delete") is True

    def test_native_actions_for_known_role(self) -> None:
        lattice = CapabilityLattice()
        assert "read" in lattice.native_actions("reader")
        assert "write" in lattice.native_actions("writer")

    def test_all_actions_includes_inherited(self) -> None:
        lattice = CapabilityLattice()
        actions = lattice.all_actions("coder")
        assert "read" in actions
        assert "write" in actions
        assert "execute" in actions

    def test_unknown_role_has_no_actions(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("unknown", "read") is False

    def test_viewer_is_read_only(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("viewer", "read") is True
        assert lattice.is_granted("viewer", "write") is False
        assert lattice.is_granted("viewer", "execute") is False

    def test_viewer_inherits_no_parents(self) -> None:
        lattice = CapabilityLattice()
        assert lattice._all_parents("viewer") == frozenset()


class TestCapabilityLatticeCustom:
    def test_custom_chain_uses_inferred_actions(self) -> None:
        lattice = CapabilityLattice(chain={"editor": frozenset({"viewer"}), "viewer": frozenset()})
        assert "edit" in lattice.native_actions("editor")
        assert "view" in lattice.native_actions("viewer")

    def test_custom_chain_inheritance(self) -> None:
        lattice = CapabilityLattice(chain={"publisher": frozenset({"editor"}), "editor": frozenset()})
        assert lattice.is_granted("publisher", "edit") is True
        assert lattice.is_granted("editor", "publish") is False


class TestPermissionEvaluator:
    def _coder_spec(self) -> ToolPermissionSpec:
        return ToolPermissionSpec(
            role="coder",
            permissions=(
                ToolPermission(tool="read_file", allowed_actions=("read",)),
                ToolPermission(tool="bash", allowed_actions=("execute",)),
                ToolPermission(tool="write_file", allowed_actions=("write", "create")),
                ToolPermission(tool="delete_file", denied_actions=("delete",)),
            ),
        )

    def _evaluator(self) -> PermissionEvaluator:
        return PermissionEvaluator(lattice=CapabilityLattice())

    def test_read_file_allowed(self) -> None:
        ev = self._evaluator()
        assert ev.may_use(self._coder_spec(), "read_file", "read") is True

    def test_bash_execute_allowed(self) -> None:
        ev = self._evaluator()
        assert ev.may_use(self._coder_spec(), "bash", "execute") is True

    def test_deny_takes_precedence(self) -> None:
        ev = self._evaluator()
        assert ev.may_use(self._coder_spec(), "delete_file", "delete") is False

    def test_no_allow_rule_defaults_to_deny(self) -> None:
        ev = self._evaluator()
        assert ev.may_use(self._coder_spec(), "unknown_tool", "read") is False

    def test_may_read_convenience(self) -> None:
        ev = self._evaluator()
        assert ev.may_read(self._coder_spec(), "read_file") is True
        assert ev.may_read(self._coder_spec(), "bash") is False

    def test_may_write_convenience(self) -> None:
        ev = self._evaluator()
        assert ev.may_write(self._coder_spec(), "write_file") is True
        assert ev.may_write(self._coder_spec(), "read_file") is False

    def test_may_execute_convenience(self) -> None:
        ev = self._evaluator()
        assert ev.may_execute(self._coder_spec(), "bash") is True
        assert ev.may_execute(self._coder_spec(), "read_file") is False

    def test_scope_restriction(self) -> None:
        spec = ToolPermissionSpec(
            role="coder",
            permissions=(ToolPermission(tool="read_file", allowed_actions=("read",), scope="project:a"),),
        )
        ev = self._evaluator()
        assert ev.may_use(spec, "read_file", "read", scope="project:a") is True
        assert ev.may_use(spec, "read_file", "read", scope="project:b") is False

    def test_wildcard_allow_skips_lattice(self) -> None:
        spec = ToolPermissionSpec(
            role="viewer",
            permissions=(ToolPermission(tool="*", allowed_actions=("*",)),),
        )
        ev = self._evaluator()
        assert ev.may_use(spec, "any_tool", "execute") is True

    def test_evaluate_all_bulk(self) -> None:
        ev = self._evaluator()
        spec = self._coder_spec()
        results = ev.evaluate_all(spec, [("read_file", "read"), ("read_file", "write"), ("bash", "execute")])
        assert results[("read_file", "read")] is True
        assert results[("read_file", "write")] is False
        assert results[("bash", "execute")] is True
