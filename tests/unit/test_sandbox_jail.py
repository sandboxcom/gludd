"""Structural tests for security/sandboxes/freebsd_jail.py."""

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


def _make_spec(capabilities=None, denied=None, agent_type="test-agent"):
    return PermissionSpec(
        agent_type=agent_type,
        capabilities=capabilities or [],
        denied=denied or [],
    )


def test_jail_path_from_target_directory():
    spec = _make_spec()
    target = SandboxTarget(directory="/opt/jail", pid=None)
    path = _jail_path(spec, target)
    assert path == "/opt/jail"


def test_jail_path_from_file_capability():
    spec = _make_spec(capabilities=[
        Capability(resource="file:/var/data/", actions={"read"}),
    ])
    target = SandboxTarget(directory=None, pid=None)
    path = _jail_path(spec, target)
    assert path == "/var/data"


def test_jail_path_fallback():
    spec = _make_spec(agent_type="redis-worker")
    target = SandboxTarget(directory=None, pid=None)
    path = _jail_path(spec, target)
    assert path == "/tmp/gludd/redis-worker"


def test_pf_rules_no_net_capabilities():
    spec = _make_spec(capabilities=[
        Capability(resource="file:/tmp/", actions={"read"}),
    ])
    rules = _pf_rules(spec, anchor="mytest")
    assert "pf anchor mytest" in rules
    assert "block out" not in rules


def test_pf_rules_with_net_capability():
    spec = _make_spec(capabilities=[
        Capability(resource="net:egress:api.example.com:443", actions={"connect"}),
    ])
    rules = _pf_rules(spec, anchor="mytest")
    assert 'pass out quick proto tcp to "api.example.com" port 443' in rules
    assert "block out quick proto tcp from any to any" in rules


def test_pf_rules_net_without_port():
    spec = _make_spec(capabilities=[
        Capability(resource="net:egress:api.example.com", actions={"connect"}),
    ])
    rules = _pf_rules(spec, anchor="mytest")
    assert 'pass out quick proto tcp to "api.example.com"' in rules


def test_devfs_rule_for():
    rules = _devfs_rule_for(_make_spec())
    assert "gludd_rules=10" in rules
    assert "hide" in rules
    assert "null" in rules
    assert "random" in rules


def test_render_jail_command():
    spec = _make_spec(agent_type="test-agent")
    target = SandboxTarget(directory="/opt/jail", pid=None)
    cmd = render_jail_command(spec, target)
    assert cmd[0] == "jail"
    assert cmd[1] == "-c"
    assert "path=/opt/jail" in cmd
    assert "host.hostname=gludd-test-agent" in cmd
    assert "persist" in cmd


def test_render_pf_rules_delegates():
    spec = _make_spec()
    result = render_pf_rules(spec, "custom-anchor")
    assert "pf anchor custom-anchor" in result


def test_jail_backend_name():
    assert JailBackend.name == "jail"


def test_jail_available_returns_bool():
    result = JailBackend.available()
    assert isinstance(result, bool)


def test_jail_release_noop_when_not_applied():
    from general_ludd.security.sandboxes import SandboxHandle
    handle = SandboxHandle(
        backend="jail",
        token="test-jail",
        applied=False,
    )
    JailBackend.release(handle)
