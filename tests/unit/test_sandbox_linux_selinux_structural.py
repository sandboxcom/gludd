"""Structural tests for security/sandboxes/linux_selinux.py — Linux SELinux backend."""

from __future__ import annotations

from general_ludd.security.sandboxes.linux_selinux import (
    SELinuxBackend,
    _is_file_family,
    _is_net_family,
    render_fc,
    render_te,
)


class TestSELinuxHelpers:
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

    def test_render_te(self):
        from general_ludd.security.sandboxes import PermissionSpec

        spec = PermissionSpec(agent_type="test-agent", capabilities=[])
        text = render_te(spec)
        assert isinstance(text, str)
        assert "module gludd_test_agent" in text
        assert "require" in text

    def test_render_fc(self):
        from general_ludd.security.sandboxes import PermissionSpec

        spec = PermissionSpec(agent_type="test-agent", capabilities=[])
        text = render_fc(spec)
        assert isinstance(text, str)
        assert "test-agent" in text

    def test_render_te_with_file_cap(self):
        from general_ludd.security.sandboxes import Capability, PermissionSpec

        spec = PermissionSpec(
            agent_type="test-agent",
            capabilities=[Capability(resource="file:/tmp/gludd", actions=["read", "write"])],
        )
        text = render_te(spec)
        assert "allow" in text
        assert "gludd_test_agent_t" in text

    def test_render_te_with_net_cap(self):
        from general_ludd.security.sandboxes import Capability, PermissionSpec

        spec = PermissionSpec(
            agent_type="test-agent",
            capabilities=[Capability(resource="net:egress", actions=["connect"])],
        )
        text = render_te(spec)
        assert "tcp_socket" in text


class TestSELinuxBackend:
    def test_backend_name(self):
        assert SELinuxBackend.name == "selinux"

    def test_available_returns_bool(self):
        result = SELinuxBackend.available()
        assert isinstance(result, bool)
