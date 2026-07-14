"""Structural tests for security/sandboxes/linux_apparmor.py — AppArmor backend."""

from __future__ import annotations

from general_ludd.security.sandboxes import Capability, PermissionSpec, SandboxTarget
from general_ludd.security.sandboxes.linux_apparmor import (
    AppArmorBackend,
    _allow_rule_for,
    _deny_rule_for,
    _is_file_family,
    _is_net_family,
    render_profile,
)


def _make_cap(resource="file:/tmp/gludd/", actions=None):
    return Capability(resource=resource, actions=set(actions or ["read"]))


def _make_spec(agent_type="test-agent", capabilities=None, denied=None):
    return PermissionSpec(agent_type=agent_type, capabilities=capabilities or [], denied=denied or [])


class TestHelpers:
    def test_is_file_family_true(self):
        assert _is_file_family(_make_cap("file:/tmp/gludd")) is True

    def test_is_file_family_false(self):
        assert _is_file_family(_make_cap("net:egress")) is False

    def test_is_net_family_true(self):
        assert _is_net_family(_make_cap("net:egress")) is True

    def test_is_net_family_false(self):
        assert _is_net_family(_make_cap("file:/tmp")) is False


class TestDenyRule:
    def test_deny_rule_file(self):
        cap = _make_cap("file:/tmp/secret/", {"read"})
        rule = _deny_rule_for(cap)
        assert 'deny' in rule

    def test_deny_rule_net(self):
        cap = _make_cap("net:egress", {"connect"})
        rule = _deny_rule_for(cap)
        assert 'deny network inet stream' in rule

    def test_deny_rule_other(self):
        cap = _make_cap("other:resource")
        rule = _deny_rule_for(cap)
        assert 'deny other:resource' in rule


class TestAllowRule:
    def test_allow_rule_file(self):
        cap = _make_cap("file:/tmp/gludd/", {"read"})
        rule = _allow_rule_for(cap)
        assert 'read' in rule or '**' in rule

    def test_allow_rule_file_with_write(self):
        cap = _make_cap("file:/tmp/gludd/", {"write"})
        rule = _allow_rule_for(cap)
        assert len(rule) > 0

    def test_allow_rule_net(self):
        cap = _make_cap("net:egress", {"connect"})
        rule = _allow_rule_for(cap)
        assert 'network inet stream' in rule

    def test_allow_rule_other(self):
        cap = _make_cap("other:resource")
        rule = _allow_rule_for(cap)
        assert 'other:resource' in rule


class TestRenderProfile:
    def test_render_profile_header(self):
        spec = _make_spec()
        target = SandboxTarget()
        profile = render_profile(spec, target)
        assert '#include <tunables/global>' in profile
        assert 'profile gludd-test-agent' in profile
        assert 'flags=(attach_disconnected)' in profile

    def test_render_profile_with_file_capability(self):
        spec = _make_spec(capabilities=[_make_cap("file:/tmp/gludd/", {"read"})])
        target = SandboxTarget()
        profile = render_profile(spec, target)
        assert '#include <tunables/global>' in profile

    def test_render_profile_with_denied(self):
        spec = _make_spec(denied=[_make_cap("file:/tmp/forbidden/", {"read"})])
        target = SandboxTarget()
        profile = render_profile(spec, target)
        assert 'deny' in profile

    def test_render_profile_empty_spec(self):
        spec = _make_spec()
        target = SandboxTarget()
        profile = render_profile(spec, target)
        assert 'empty spec' in profile or '}' in profile

    def test_render_profile_uses_agent_type_in_name(self):
        spec = _make_spec(agent_type="custom-agent")
        target = SandboxTarget()
        profile = render_profile(spec, target)
        assert 'profile gludd-custom-agent' in profile

    def test_render_profile_with_pid_adds_attach(self):
        spec = _make_spec()
        target = SandboxTarget(pid=12345)
        profile = render_profile(spec, target)
        assert 'ptrace' in profile


class TestBackend:
    def test_backend_name(self):
        assert AppArmorBackend.name == "apparmor"

    def test_available_returns_bool(self):
        result = AppArmorBackend.available()
        assert isinstance(result, bool)
