"""Extended structural tests for security/sandboxes/linux_bubblewrap.py — bubblewrap backend."""

from __future__ import annotations

from general_ludd.security.sandboxes import Capability, PermissionSpec, SandboxTarget
from general_ludd.security.sandboxes.linux_bubblewrap import (
    BubblewrapBackend,
    _is_file_family,
    _is_net_family,
    render_argv,
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


class TestRenderArgv:
    def test_minimal_argv_starts_with_bwrap(self):
        spec = _make_spec()
        target = SandboxTarget()
        argv = render_argv(spec, target)
        assert argv[0] == "bwrap"

    def test_argv_includes_ro_binds(self):
        spec = _make_spec()
        target = SandboxTarget()
        argv = render_argv(spec, target)
        assert "/usr" in argv
        assert "/lib" in argv

    def test_argv_includes_unshare_all(self):
        spec = _make_spec()
        target = SandboxTarget()
        argv = render_argv(spec, target)
        assert "--unshare-all" in argv

    def test_argv_includes_die_with_parent(self):
        spec = _make_spec()
        target = SandboxTarget()
        argv = render_argv(spec, target)
        assert "--die-with-parent" in argv

    def test_argv_includes_proc_dev(self):
        spec = _make_spec()
        target = SandboxTarget()
        argv = render_argv(spec, target)
        assert "/proc" in argv
        assert "/dev" in argv

    def test_argv_default_command_is_shell(self):
        spec = _make_spec()
        target = SandboxTarget()
        argv = render_argv(spec, target)
        assert argv[-1] == "/bin/sh"

    def test_argv_custom_command(self):
        spec = _make_spec()
        target = SandboxTarget()
        argv = render_argv(spec, target, cmd=["/usr/bin/python3", "-c", "pass"])
        assert argv[-4:] == ["--", "/usr/bin/python3", "-c", "pass"]

    def test_argv_with_file_capability(self):
        cap = Capability(
            resource="file:/tmp/gludd/",
            actions={"read", "write"},
            constraints={"path_prefix": "/tmp/gludd/"},
        )
        spec = _make_spec(capabilities=[cap])
        target = SandboxTarget()
        argv = render_argv(spec, target)
        assert "--bind" in argv
        assert "/tmp/gludd/" in argv

    def test_argv_no_duplicate_binds(self):
        cap = Capability(
            resource="file:/tmp/gludd/",
            actions={"read"},
            constraints={"path_prefix": "/tmp/gludd/"},
        )
        spec = _make_spec(capabilities=[cap, cap])
        target = SandboxTarget()
        argv = render_argv(spec, target)
        bind_count = sum(1 for a in argv if a == "--bind")
        assert bind_count == 1

    def test_argv_with_net_capability_share_net(self):
        cap = Capability(resource="net:egress", actions={"connect"})
        spec = _make_spec(capabilities=[cap])
        target = SandboxTarget()
        argv = render_argv(spec, target)
        assert "--share-net" in argv
        assert "--unshare-net" not in argv

    def test_argv_without_net_capability_unshare_net(self):
        spec = _make_spec(capabilities=[_make_cap("file:/tmp/gludd/", {"read"})])
        target = SandboxTarget()
        argv = render_argv(spec, target)
        assert "--unshare-net" in argv
        assert "--share-net" not in argv

    def test_argv_with_target_directory(self):
        spec = _make_spec()
        target = SandboxTarget(directory="/tmp/jail")
        argv = render_argv(spec, target)
        assert "--chdir" in argv
        assert "/tmp/jail" in argv

    def test_argv_resolv_conf_bound(self):
        spec = _make_spec()
        target = SandboxTarget()
        argv = render_argv(spec, target)
        assert "/etc/resolv.conf" in argv


class TestBackend:
    def test_backend_name(self):
        assert BubblewrapBackend.name == "bubblewrap"

    def test_available_returns_bool(self):
        result = BubblewrapBackend.available()
        assert isinstance(result, bool)
