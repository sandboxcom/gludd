"""Structural tests for security/sandboxes/linux_apparmor.py — Linux AppArmor backend."""

from __future__ import annotations

from general_ludd.security.sandboxes.linux_apparmor import (
    AppArmorBackend,
    _is_file_family,
    _is_net_family,
    render_profile,
)


class TestAppArmorHelpers:
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

    def test_render_profile_basic(self):
        from general_ludd.security.sandboxes import PermissionSpec, SandboxTarget

        spec = PermissionSpec(agent_type="test-agent", capabilities=[])
        target = SandboxTarget()
        text = render_profile(spec, target)
        assert isinstance(text, str)
        assert "#include <tunables/global>" in text
        assert "profile gludd-test-agent" in text

    def test_render_profile_with_file_cap(self):
        from general_ludd.security.sandboxes import (
            Capability,
            PermissionSpec,
            SandboxTarget,
        )

        spec = PermissionSpec(
            agent_type="test-agent",
            capabilities=[Capability(resource="file:/tmp/gludd", actions=["read"])],
        )
        target = SandboxTarget()
        text = render_profile(spec, target)
        assert "profile gludd-test-agent" in text
        assert "read" in text

    def test_render_profile_with_pid(self):
        from general_ludd.security.sandboxes import PermissionSpec, SandboxTarget

        spec = PermissionSpec(agent_type="test-agent", capabilities=[])
        target = SandboxTarget(pid=1234)
        text = render_profile(spec, target)
        assert "audit deny ptrace" in text


class TestAppArmorBackend:
    def test_backend_name(self):
        assert AppArmorBackend.name == "apparmor"

    def test_available_returns_bool(self):
        result = AppArmorBackend.available()
        assert isinstance(result, bool)
