"""Structural tests for security/sandboxes/linux_selinux.py."""

from __future__ import annotations

from general_ludd.security.sandboxes import Capability, PermissionSpec, SandboxTarget
from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend, _fc_for, _te_for, render_fc, render_te


def _make_spec(capabilities=None, denied=None, agent_type="test-agent"):
    return PermissionSpec(
        agent_type=agent_type,
        capabilities=capabilities or [],
        denied=denied or [],
    )


def test_te_for_no_capabilities():
    spec = _make_spec()
    te = _te_for(spec)
    assert "module gludd_test_agent 1.0" in te
    assert "type gludd_test_agent_t" in te


def test_te_for_with_file_capability():
    spec = _make_spec(capabilities=[
        Capability(resource="file:/tmp/test/", actions={"read", "write"}),
    ])
    te = _te_for(spec)
    assert "allow gludd_test_agent_t usr_t:dir { read write }" in te


def test_te_for_with_net_capability():
    spec = _make_spec(capabilities=[
        Capability(resource="net:egress:api.example.com:443", actions={"connect"}),
    ])
    te = _te_for(spec)
    assert "allow gludd_test_agent_t unreserved_port_t:tcp_socket name_connect" in te


def test_te_for_type_name_sanitizes_dashes():
    spec = _make_spec(agent_type="my-custom-agent")
    te = _te_for(spec)
    assert "module gludd_my_custom_agent 1.0" in te
    assert "type gludd_my_custom_agent_t" in te


def test_fc_for_no_capabilities():
    spec = _make_spec()
    fc = _fc_for(spec)
    assert "gludd_test_agent_t" in fc


def test_fc_for_with_file_capability():
    spec = _make_spec(capabilities=[
        Capability(resource="file:/tmp/test/", actions={"read", "write"}),
    ])
    fc = _fc_for(spec)
    assert "/tmp/test/" in fc
    assert "gludd_test_agent_t" in fc


def test_render_te_delegates():
    spec = _make_spec()
    result = render_te(spec)
    assert result == _te_for(spec)


def test_render_fc_delegates():
    spec = _make_spec()
    result = render_fc(spec)
    assert result == _fc_for(spec)


def test_selinux_backend_name():
    assert SELinuxBackend.name == "selinux"


def test_selinux_available_returns_bool():
    result = SELinuxBackend.available()
    assert isinstance(result, bool)


def test_selinux_apply_returns_handle():
    spec = _make_spec()
    target = SandboxTarget(directory=None, pid=None)
    handle = SELinuxBackend.apply(spec, target)
    assert handle.backend == "selinux"
    assert isinstance(handle.applied, bool)


def test_selinux_release_noop_when_not_applied():
    from general_ludd.security.sandboxes import SandboxHandle
    handle = SandboxHandle(
        backend="selinux",
        token="test-token",
        applied=False,
    )
    SELinuxBackend.release(handle)
