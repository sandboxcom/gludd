"""Structural tests for security/sandboxes/freebsd_jail.py — FreeBSD jail backend."""

from __future__ import annotations

from general_ludd.security.sandboxes.freebsd_jail import (
    JailBackend,
    _is_file_family,
    _is_net_family,
    _jail_path,
    _pf_rules,
    _devfs_rule_for,
    render_jail_command,
    render_pf_rules,
)


class TestFreebsdJailHelpers:
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

    def test_jail_path_defaults(self):
        from general_ludd.security.sandboxes import PermissionSpec, SandboxTarget

        spec = PermissionSpec(agent_type="test-agent", capabilities=[])
        target = SandboxTarget()
        path = _jail_path(spec, target)
        assert isinstance(path, str)
        assert "test-agent" in path

    def test_jail_path_with_directory(self):
        from general_ludd.security.sandboxes import PermissionSpec, SandboxTarget

        spec = PermissionSpec(agent_type="test-agent", capabilities=[])
        target = SandboxTarget(directory="/custom/path")
        path = _jail_path(spec, target)
        assert path == "/custom/path"


class TestJailBackend:
    def test_backend_name(self):
        assert JailBackend.name == "jail"

    def test_available_returns_bool(self):
        result = JailBackend.available()
        assert isinstance(result, bool)
