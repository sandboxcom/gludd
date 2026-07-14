"""Structural tests for security/sandboxes/windows_appcontainer.py — Windows AppContainer backend."""

from __future__ import annotations

from general_ludd.security.sandboxes import Capability, PermissionSpec
from general_ludd.security.sandboxes.windows_appcontainer import (
    AppContainerBackend,
    _firewall_rule_name,
    _icacls_deny_all_except,
    _is_file_family,
    _is_net_family,
    _net_allow_rules,
    render_icacls,
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


class TestFirewallRuleName:
    def test_firewall_rule_name(self):
        spec = _make_spec(agent_type="test-agent")
        name = _firewall_rule_name(spec)
        assert "gludd-test-agent" in name
        assert "egress" in name

    def test_firewall_rule_name_custom_agent(self):
        spec = _make_spec(agent_type="custom-worker")
        name = _firewall_rule_name(spec)
        assert "gludd-custom-worker" in name


class TestIcacls:
    def test_icacls_deny_all_except(self):
        rules = _icacls_deny_all_except("C:\\workdir", "S-1-15-2-123")
        assert len(rules) >= 4
        assert "icacls" in rules[0]
        assert "C:\\workdir" in rules[1]
        assert "S-1-15-2-123" in rules[4]

    def test_render_icacls_delegates(self):
        spec = _make_spec()
        rules = render_icacls(spec, "C:\\workdir", "S-1-15-2-123")
        assert isinstance(rules, list)
        assert "icacls" in rules[0]


class TestNetAllowRules:
    def test_net_allow_rules_no_net_capabilities(self):
        spec = _make_spec(capabilities=[_make_cap("file:/tmp/gludd/", {"read"})])
        rules = _net_allow_rules(spec)
        assert len(rules) >= 1
        assert "block" in rules[-1][7]

    def test_net_allow_rules_with_net_capability(self):
        cap = Capability(
            resource="net:egress:api.example.com:443",
            actions={"connect"},
            constraints={"allowed_hosts": ["api.example.com"], "allowed_ports": [443]},
        )
        spec = _make_spec(capabilities=[cap])
        rules = _net_allow_rules(spec)
        assert len(rules) >= 2
        assert "allow" in rules[0][7]
        assert "block" in rules[-1][7]

    def test_net_allow_rules_deny_rule_last(self):
        cap = Capability(
            resource="net:egress:api.example.com:443",
            actions={"connect"},
            constraints={"allowed_hosts": ["api.example.com"], "allowed_ports": [443]},
        )
        spec = _make_spec(capabilities=[cap])
        rules = _net_allow_rules(spec)
        last_rule = rules[-1]
        assert "block" in last_rule[7]


class TestBackend:
    def test_backend_name(self):
        assert AppContainerBackend.name == "appcontainer"

    def test_available_returns_bool(self):
        result = AppContainerBackend.available()
        assert isinstance(result, bool)
