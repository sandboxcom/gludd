"""TDD tests for AG.4 — Tool permission scoping.

Covers: ToolPermission model, CapabilityLattice hierarchy, PermissionEvaluator
with allow/deny rules, wildcard matching, scope-based permissions.
"""

from __future__ import annotations

import pytest

from general_ludd.permissions.tool_permissions import (
    CapabilityLattice,
    PermissionEvaluator,
    ToolAction,
    ToolPermission,
    ToolPermissionSpec,
)

# ---------------------------------------------------------------------------
# ToolPermission + ToolPermissionSpec — data model
# ---------------------------------------------------------------------------

class TestToolPermissionModel:
    """ToolPermission and ToolPermissionSpec model tests."""

    def test_tool_permission_defaults(self) -> None:
        tp = ToolPermission(tool="read_file")
        assert tp.tool == "read_file"
        assert tp.allowed_actions == ()
        assert tp.denied_actions == ()
        assert tp.scope is None

    def test_tool_permission_with_actions(self) -> None:
        tp = ToolPermission(
            tool="write_file",
            allowed_actions=[ToolAction.CREATE, ToolAction.OVERWRITE],
            denied_actions=[ToolAction.DELETE],
            scope="project:gludd",
        )
        assert tp.tool == "write_file"
        assert ToolAction.CREATE in tp.allowed_actions
        assert ToolAction.DELETE in tp.denied_actions
        assert tp.scope == "project:gludd"

    def test_tool_permission_immutable(self) -> None:
        tp = ToolPermission(tool="bash", allowed_actions=[ToolAction.EXECUTE])
        with pytest.raises(AttributeError):
            tp.allowed_actions.append(ToolAction.READ)  # type: ignore[attr-defined]

    def test_spec_with_multiple_tools(self) -> None:
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(tool="read_file", allowed_actions=[ToolAction.READ]),
                ToolPermission(tool="write_file", allowed_actions=[ToolAction.CREATE, ToolAction.OVERWRITE]),
                ToolPermission(
                    tool="bash",
                    allowed_actions=[ToolAction.EXECUTE],
                    denied_actions=[ToolAction.EXECUTE],
                    scope="project:gludd",
                ),
            ],
        )
        assert len(spec.permissions) == 3
        assert spec.role == "coder"

    def test_spec_lookup_by_tool(self) -> None:
        spec = ToolPermissionSpec(
            role="viewer",
            permissions=[
                ToolPermission(tool="read_file", allowed_actions=[ToolAction.READ]),
                ToolPermission(tool="grep", allowed_actions=[ToolAction.READ]),
            ],
        )
        perms = spec.permissions_for("read_file")
        assert len(perms) == 1
        assert perms[0].tool == "read_file"
        assert spec.permissions_for("nonexistent") == []


# ---------------------------------------------------------------------------
# CapabilityLattice — hierarchical capabilities with inheritance
# ---------------------------------------------------------------------------

class TestCapabilityLattice:
    """Hierarchical capability lattice tests."""

    def test_root_is_all(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("admin", ToolAction.READ)
        assert lattice.is_granted("admin", ToolAction.WRITE)
        assert lattice.is_granted("admin", ToolAction.EXECUTE)
        assert lattice.is_granted("admin", ToolAction.DELETE)

    def test_reader_has_read_only(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("reader", ToolAction.READ)
        assert not lattice.is_granted("reader", ToolAction.WRITE)
        assert not lattice.is_granted("reader", ToolAction.EXECUTE)
        assert not lattice.is_granted("reader", ToolAction.DELETE)

    def test_writer_inherits_reader(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("writer", ToolAction.READ)
        assert lattice.is_granted("writer", ToolAction.WRITE)
        assert lattice.is_granted("writer", ToolAction.CREATE)
        assert not lattice.is_granted("writer", ToolAction.EXECUTE)

    def test_coder_inherits_writer(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("coder", ToolAction.READ)
        assert lattice.is_granted("coder", ToolAction.WRITE)
        assert lattice.is_granted("coder", ToolAction.EXECUTE)
        assert lattice.is_granted("coder", ToolAction.CREATE)
        assert lattice.is_granted("coder", ToolAction.OVERWRITE)

    def test_admin_inherits_coder(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("admin", ToolAction.READ)
        assert lattice.is_granted("admin", ToolAction.WRITE)
        assert lattice.is_granted("admin", ToolAction.EXECUTE)
        assert lattice.is_granted("admin", ToolAction.DELETE)

    def test_unknown_capability_denied(self) -> None:
        lattice = CapabilityLattice()
        assert not lattice.is_granted("reader", "nonexistent_action")
        assert not lattice.is_granted("unknown_role", ToolAction.READ)

    def test_custom_chain(self) -> None:
        lattice = CapabilityLattice(
            chain={
                "viewer": set(),
                "editor": {"viewer"},
                "publisher": {"editor"},
            }
        )
        assert lattice.is_granted("viewer", "view")
        assert not lattice.is_granted("viewer", "edit")
        assert lattice.is_granted("editor", "view")  # via inheritance
        assert lattice.is_granted("editor", "edit")
        assert lattice.is_granted("publisher", "view")  # via editor->viewer
        assert lattice.is_granted("publisher", "edit")  # via editor
        assert lattice.is_granted("publisher", "publish")
        assert not lattice.is_granted("editor", "publish")


# ---------------------------------------------------------------------------
# PermissionEvaluator — tool usage evaluation
# ---------------------------------------------------------------------------

class TestPermissionEvaluator:
    """End-to-end permission evaluation tests."""

    def _make_local_lattice(self) -> CapabilityLattice:
        return CapabilityLattice()

    def _make_evaluator(
        self,
        lattice: CapabilityLattice | None = None,
    ) -> PermissionEvaluator:
        return PermissionEvaluator(lattice=lattice or self._make_local_lattice())

    # -- role-based access (coder vs viewer) --

    def test_coder_can_use_read_write(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(tool="read_file", allowed_actions=[ToolAction.READ]),
                ToolPermission(tool="write_file", allowed_actions=[ToolAction.WRITE, ToolAction.CREATE]),
            ],
        )
        assert ev.may_use(spec, "read_file", ToolAction.READ)
        assert ev.may_use(spec, "write_file", ToolAction.WRITE)
        assert ev.may_use(spec, "write_file", ToolAction.CREATE)

    def test_viewer_only_read(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="viewer",
            permissions=[
                ToolPermission(tool="read_file", allowed_actions=[ToolAction.READ]),
            ],
        )
        assert ev.may_use(spec, "read_file", ToolAction.READ)
        assert not ev.may_use(spec, "write_file", ToolAction.WRITE)
        assert not ev.may_use(spec, "bash", ToolAction.EXECUTE)

    def test_viewer_cannot_write(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="viewer",
            permissions=[
                ToolPermission(tool="read_file", allowed_actions=[ToolAction.READ]),
                ToolPermission(tool="write_file", allowed_actions=[ToolAction.WRITE]),
            ],
        )
        assert not ev.may_use(spec, "write_file", ToolAction.WRITE)

    # -- deny takes precedence over allow --

    def test_deny_overrides_allow(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(
                    tool="bash",
                    allowed_actions=[ToolAction.EXECUTE],
                    denied_actions=[ToolAction.EXECUTE],
                ),
            ],
        )
        assert not ev.may_use(spec, "bash", ToolAction.EXECUTE)

    def test_deny_on_specific_action_allows_others(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="admin",
            permissions=[
                ToolPermission(
                    tool="file_manager",
                    allowed_actions=[ToolAction.READ, ToolAction.WRITE, ToolAction.DELETE],
                    denied_actions=[ToolAction.DELETE],
                ),
            ],
        )
        assert ev.may_use(spec, "file_manager", ToolAction.READ)
        assert ev.may_use(spec, "file_manager", ToolAction.WRITE)
        assert not ev.may_use(spec, "file_manager", ToolAction.DELETE)

    def test_deny_without_allow_is_still_denied(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(
                    tool="bash",
                    allowed_actions=[],
                    denied_actions=[ToolAction.EXECUTE],
                ),
            ],
        )
        assert not ev.may_use(spec, "bash", ToolAction.EXECUTE)

    # -- wildcard ("*") permissions --

    def test_wildcard_tool_allows_all_actions(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="admin",
            permissions=[
                ToolPermission(
                    tool="*",
                    allowed_actions=[ToolAction.READ, ToolAction.WRITE, ToolAction.EXECUTE],
                ),
            ],
        )
        assert ev.may_use(spec, "read_file", ToolAction.READ)
        assert ev.may_use(spec, "write_file", ToolAction.WRITE)
        assert ev.may_use(spec, "bash", ToolAction.EXECUTE)

    def test_wildcard_tool_deny_overrides_allow(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(tool="*", allowed_actions=[ToolAction.EXECUTE]),
                ToolPermission(tool="bash", denied_actions=[ToolAction.EXECUTE]),
            ],
        )
        assert ev.may_use(spec, "grep", ToolAction.EXECUTE)
        assert not ev.may_use(spec, "bash", ToolAction.EXECUTE)

    def test_wildcard_action_allows_all(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="admin",
            permissions=[
                ToolPermission(tool="read_file", allowed_actions=["*"]),
            ],
        )
        assert ev.may_use(spec, "read_file", ToolAction.READ)
        assert ev.may_use(spec, "read_file", ToolAction.WRITE)
        assert ev.may_use(spec, "read_file", "custom_action")

    # -- scope-based permissions --

    def test_scope_match_allows(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(
                    tool="write_file",
                    allowed_actions=[ToolAction.CREATE],
                    scope="project:gludd",
                ),
            ],
        )
        assert ev.may_use(spec, "write_file", ToolAction.CREATE, scope="project:gludd")
        assert not ev.may_use(spec, "write_file", ToolAction.CREATE, scope="project:other")

    def test_scope_mismatch_denies(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(
                    tool="write_file",
                    allowed_actions=[ToolAction.OVERWRITE],
                    scope="project:gludd",
                ),
            ],
        )
        assert not ev.may_use(spec, "write_file", ToolAction.OVERWRITE, scope="project:other")

    def test_no_scope_means_global(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(
                    tool="read_file",
                    allowed_actions=[ToolAction.READ],
                ),
            ],
        )
        assert ev.may_use(spec, "read_file", ToolAction.READ, scope="project:gludd")
        assert ev.may_use(spec, "read_file", ToolAction.READ, scope="project:other")
        assert ev.may_use(spec, "read_file", ToolAction.READ, scope=None)

    def test_scope_wildcard(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(
                    tool="write_file",
                    allowed_actions=[ToolAction.CREATE],
                    scope="project:*",
                ),
                ToolPermission(
                    tool="read_file",
                    allowed_actions=[ToolAction.READ],
                ),
            ],
        )
        assert ev.may_use(spec, "write_file", ToolAction.CREATE, scope="project:gludd")
        assert ev.may_use(spec, "write_file", ToolAction.CREATE, scope="project:other")
        assert not ev.may_use(spec, "write_file", ToolAction.CREATE, scope="global:data")

    # -- no permission defined -> default deny --

    def test_undefined_tool_default_deny(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(tool="read_file", allowed_actions=[ToolAction.READ]),
            ],
        )
        assert not ev.may_use(spec, "bash", ToolAction.EXECUTE)

    # -- convenience methods --

    def test_may_read_convenience(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(tool="read_file", allowed_actions=[ToolAction.READ]),
                ToolPermission(tool="write_file", allowed_actions=[ToolAction.WRITE]),
            ],
        )
        assert ev.may_read(spec, "read_file")
        assert not ev.may_read(spec, "write_file")

    def test_may_write_convenience(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(tool="write_file", allowed_actions=[ToolAction.WRITE, ToolAction.CREATE]),
            ],
        )
        assert ev.may_write(spec, "write_file")
        assert not ev.may_write(spec, "read_file")

    def test_may_execute_convenience(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(tool="bash", allowed_actions=[ToolAction.EXECUTE]),
            ],
        )
        assert ev.may_execute(spec, "bash")
        assert not ev.may_execute(spec, "read_file")

    # -- bulk evaluation --

    def test_evaluate_all(self) -> None:
        ev = self._make_evaluator()
        spec = ToolPermissionSpec(
            role="coder",
            permissions=[
                ToolPermission(tool="read_file", allowed_actions=[ToolAction.READ]),
                ToolPermission(
                    tool="write_file",
                    allowed_actions=[ToolAction.CREATE, ToolAction.OVERWRITE],
                    denied_actions=[ToolAction.DELETE],
                ),
                ToolPermission(tool="grep", allowed_actions=[ToolAction.READ]),
            ],
        )
        results = ev.evaluate_all(
            spec,
            queries=[
                ("read_file", ToolAction.READ),
                ("read_file", ToolAction.WRITE),
                ("write_file", ToolAction.CREATE),
                ("write_file", ToolAction.DELETE),
                ("grep", ToolAction.READ),
                ("bash", ToolAction.EXECUTE),
            ],
        )
        assert results == {
            ("read_file", ToolAction.READ): True,
            ("read_file", ToolAction.WRITE): False,
            ("write_file", ToolAction.CREATE): True,
            ("write_file", ToolAction.DELETE): False,
            ("grep", ToolAction.READ): True,
            ("bash", ToolAction.EXECUTE): False,
        }
