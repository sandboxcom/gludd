"""Structural tests for security/sandboxes/linux_bubblewrap.py."""

from __future__ import annotations

from unittest.mock import patch

from general_ludd.security.sandboxes import (
    Capability,
    PermissionSpec,
    SandboxHandle,
    SandboxTarget,
)
from general_ludd.security.sandboxes.linux_bubblewrap import BubblewrapBackend, render_argv


def _make_spec(capabilities=None, denied=None, agent_type="test-agent"):
    return PermissionSpec(
        agent_type=agent_type,
        capabilities=capabilities or [],
        denied=denied or [],
    )


def _make_target(directory=None, pid=None):
    return SandboxTarget(directory=directory, pid=pid)


def test_render_argv_no_capabilities():
    spec = _make_spec()
    target = _make_target()
    argv = render_argv(spec, target)
    assert "bwrap" in argv
    assert "--unshare-all" in argv
    assert "--unshare-net" in argv
    assert "--die-with-parent" in argv


def test_render_argv_with_file_capability():
    spec = _make_spec(capabilities=[
        Capability(resource="file:/tmp/test/", actions={"read", "write"}),
    ])
    target = _make_target()
    argv = render_argv(spec, target)
    assert "--bind" in argv
    assert "/tmp/test/" in argv


def test_render_argv_with_net_capability():
    spec = _make_spec(capabilities=[
        Capability(resource="net:egress:api.example.com:443", actions={"connect"}),
    ])
    target = _make_target()
    argv = render_argv(spec, target)
    assert "--share-net" in argv


def test_render_argv_with_target_directory():
    spec = _make_spec()
    target = _make_target(directory="/opt/work")
    argv = render_argv(spec, target)
    assert "--chdir" in argv
    assert "/opt/work" in argv


def test_render_argv_custom_cmd():
    spec = _make_spec()
    target = _make_target()
    argv = render_argv(spec, target, cmd=["/usr/bin/python3", "-c", "print(1)"])
    assert "/usr/bin/python3" in argv


def test_bubblewrap_backend_name():
    assert BubblewrapBackend.name == "bubblewrap"


def test_bubblewrap_available_returns_bool():
    result = BubblewrapBackend.available()
    assert isinstance(result, bool)


def test_bubblewrap_apply_missing_bwrap():
    spec = _make_spec()
    target = _make_target()
    handle = BubblewrapBackend.apply(spec, target)
    assert handle.backend == "bubblewrap"
    assert isinstance(handle.applied, bool)


def test_bubblewrap_apply_renders_dispatch_prefix_and_network_warning():
    spec = _make_spec(capabilities=[
        Capability(
            resource="net:egress",
            actions={"connect"},
            constraints={"allowed_hosts": ["api.example.com"]},
        ),
    ])
    with patch("shutil.which", return_value="/usr/bin/bwrap"):
        handle = BubblewrapBackend.apply(spec, _make_target("/workspace"))

    assert handle.applied is True
    assert handle.extra["argv"][0] == "bwrap"
    assert "--share-net" in handle.extra["argv"]
    assert handle.extra["unhandled_net_hosts"] == ["api.example.com"]
    findings = BubblewrapBackend.verify(spec, handle)
    assert any(f.severity == "ok" for f in findings)
    assert any("NOT enforced" in f.message for f in findings)


def test_bubblewrap_verify_reports_unapplied_and_incomplete_argv():
    spec = _make_spec(capabilities=[
        Capability(
            resource="file:workspace",
            actions={"read"},
            constraints={"path_prefix": "/workspace"},
        ),
    ])
    unapplied = SandboxHandle(
        backend="bubblewrap",
        token="missing",
        applied=False,
        extra={"reason": "not installed"},
    )
    assert BubblewrapBackend.verify(spec, unapplied)[0].severity == "warn"

    incomplete = SandboxHandle(
        backend="bubblewrap",
        token="incomplete",
        applied=True,
        extra={"argv": [], "unhandled_net_hosts": []},
    )
    findings = BubblewrapBackend.verify(spec, incomplete)
    assert any("--unshare-all" in f.message for f in findings)
    assert any("--die-with-parent" in f.message for f in findings)
    assert any("missing --bind" in f.message for f in findings)


def test_bubblewrap_release_is_noop():
    handle = SandboxHandle(
        backend="bubblewrap",
        token="test-token",
        applied=False,
    )
    BubblewrapBackend.release(handle)
