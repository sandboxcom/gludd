"""Structural tests for security/sandboxes/linux_bubblewrap.py — bubblewrap namespace sandbox."""

from __future__ import annotations

from general_ludd.security.sandboxes.linux_bubblewrap import (
    BubblewrapBackend,
    _is_file_family,
    _is_net_family,
    render_argv,
)


class TestBubblewrapHelpers:
    def test_is_file_family_true(self):
        from general_ludd.security.sandboxes import Capability

        cap = Capability(resource="file:/tmp/gludd", actions=["read"])
        assert _is_file_family(cap) is True

    def test_is_file_family_false(self):
        from general_ludd.security.sandboxes import Capability

        cap = Capability(resource="net:egress", actions=["connect"])
        assert _is_file_family(cap) is False

    def test_is_net_family_true(self):
        from general_ludd.security.sandboxes import Capability

        cap = Capability(resource="net:egress", actions=["connect"])
        assert _is_net_family(cap) is True

    def test_is_net_family_false(self):
        from general_ludd.security.sandboxes import Capability

        cap = Capability(resource="file:/tmp", actions=["read"])
        assert _is_net_family(cap) is False

    def test_render_argv_minimal(self):
        from general_ludd.security.sandboxes import PermissionSpec, SandboxTarget

        spec = PermissionSpec(agent_type="test-agent", capabilities=[])
        target = SandboxTarget()
        argv = render_argv(spec, target)
        assert isinstance(argv, list)
        assert "bwrap" in argv
        assert "--unshare-all" in argv
        assert "--die-with-parent" in argv

    def test_render_argv_with_net_cap(self):
        from general_ludd.security.sandboxes import Capability, PermissionSpec, SandboxTarget

        spec = PermissionSpec(
            agent_type="test-agent",
            capabilities=[Capability(resource="net:egress", actions=["connect"])],
        )
        target = SandboxTarget()
        argv = render_argv(spec, target)
        assert "--share-net" in argv


class TestBubblewrapBackend:
    def test_backend_name(self):
        assert BubblewrapBackend.name == "bubblewrap"

    def test_available_returns_bool(self):
        result = BubblewrapBackend.available()
        assert isinstance(result, bool)
