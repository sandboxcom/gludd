"""Extended structural tests for security/sandboxes/freebsd_jail.py — FreeBSD jail backend."""

from __future__ import annotations

from general_ludd.security.sandboxes import Capability, PermissionSpec, SandboxTarget
from general_ludd.security.sandboxes.freebsd_jail import (
    JailBackend,
    _devfs_rule_for,
    _jail_path,
    _pf_rules,
    render_jail_command,
    render_pf_rules,
)


def _make_cap(resource="file:/tmp/gludd/", actions=None):
    return Capability(resource=resource, actions=set(actions or ["read"]))


def _make_spec(agent_type="test-agent", capabilities=None, denied=None):
    return PermissionSpec(agent_type=agent_type, capabilities=capabilities or [], denied=denied or [])


class TestPfRules:
    def test_pf_rules_no_net_capabilities(self):
        spec = _make_spec(capabilities=[_make_cap("file:/tmp/gludd/", {"read"})])
        rules = _pf_rules(spec, "test-anchor")
        assert "# pf anchor test-anchor" in rules

    def test_pf_rules_with_net_capability(self):
        cap = Capability(
            resource="net:egress:api.example.com:443",
            actions={"connect"},
            constraints={"allowed_hosts": ["api.example.com"], "allowed_ports": [443]},
        )
        spec = _make_spec(capabilities=[cap])
        rules = _pf_rules(spec, "test-anchor")
        assert "api.example.com" in rules
        assert "block out quick proto tcp" in rules

    def test_pf_rules_with_hosts_no_ports(self):
        cap = Capability(
            resource="net:egress:api.example.com",
            actions={"connect"},
            constraints={"allowed_hosts": ["api.example.com"], "allowed_ports": []},
        )
        spec = _make_spec(capabilities=[cap])
        rules = _pf_rules(spec, "test-anchor")
        assert "api.example.com" in rules

    def test_pf_rules_multiple_hosts(self):
        cap = Capability(
            resource="net:egress",
            actions={"connect"},
            constraints={
                "allowed_hosts": ["host1.com", "host2.com"],
                "allowed_ports": [443],
            },
        )
        spec = _make_spec(capabilities=[cap])
        rules = _pf_rules(spec, "test-anchor")
        assert "host1.com" in rules
        assert "host2.com" in rules


class TestDevfsRule:
    def test_devfs_rule_structure(self):
        spec = _make_spec()
        rules = _devfs_rule_for(spec)
        assert "gludd_rules=10" in rules
        assert "add hide" in rules
        assert "random" in rules
        assert "urandom" in rules
        assert "zero" in rules


class TestRenderJailCommand:
    def test_render_jail_command_basics(self):
        spec = _make_spec()
        target = SandboxTarget()
        cmd = render_jail_command(spec, target)
        assert "jail" in cmd[0]
        assert "-c" in cmd
        assert "host.hostname" in " ".join(cmd)
        assert "ip4=inherit" in cmd

    def test_render_jail_command_with_agent_type(self):
        spec = _make_spec(agent_type="custom-agent")
        target = SandboxTarget()
        cmd = render_jail_command(spec, target)
        joined = " ".join(cmd)
        assert "gludd-custom-agent" in joined

    def test_render_jail_command_includes_devfs_ruleset(self):
        spec = _make_spec()
        target = SandboxTarget()
        cmd = render_jail_command(spec, target)
        assert "devfs_ruleset=10" in cmd

    def test_render_jail_command_includes_persist(self):
        spec = _make_spec()
        target = SandboxTarget()
        cmd = render_jail_command(spec, target)
        assert "persist" in cmd


class TestRenderPfRules:
    def test_render_pf_rules_default_anchor(self):
        spec = _make_spec()
        rules = render_pf_rules(spec)
        assert "# pf anchor gludd" in rules

    def test_render_pf_rules_custom_anchor(self):
        spec = _make_spec()
        rules = render_pf_rules(spec, anchor="custom")
        assert "# pf anchor custom" in rules


class TestJailPath:
    def test_jail_path_defaults(self):
        spec = _make_spec()
        target = SandboxTarget()
        path = _jail_path(spec, target)
        assert "test-agent" in path

    def test_jail_path_with_target_directory(self):
        spec = _make_spec()
        target = SandboxTarget(directory="/custom/jail")
        path = _jail_path(spec, target)
        assert path == "/custom/jail"

    def test_jail_path_from_file_capability(self):
        cap = Capability(
            resource="file:/tmp/workdir/",
            actions={"read"},
            constraints={"path_prefix": "/tmp/workdir"},
        )
        spec = _make_spec(capabilities=[cap])
        target = SandboxTarget()
        path = _jail_path(spec, target)
        assert path == "/tmp/workdir"


class TestBackend:
    def test_backend_name(self):
        assert JailBackend.name == "jail"

    def test_available_returns_bool(self):
        result = JailBackend.available()
        assert isinstance(result, bool)
