"""Structural tests for security/sandboxes/macos_seatbelt.py — macOS Seatbelt backend."""

from __future__ import annotations

from general_ludd.security.sandboxes import Capability, PermissionSpec
from general_ludd.security.sandboxes.macos_seatbelt import (
    SeatbeltBackend,
    _deny_clause,
    _file_clause,
    _is_deprecated_host,
    _is_file_family,
    _is_net_family,
    _macos_version_tuple,
    _net_clause,
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

    def test_macos_version_tuple_returns_tuple(self):
        result = _macos_version_tuple()
        assert isinstance(result, tuple)

    def test_is_deprecated_host_returns_bool(self):
        result = _is_deprecated_host()
        assert isinstance(result, bool)


class TestFileClause:
    def test_file_clause_read(self):
        cap = _make_cap("file:/tmp/gludd/", {"read"})
        clause = _file_clause(cap)
        assert 'file-read*' in clause
        assert '/tmp/gludd/' in clause

    def test_file_clause_write(self):
        cap = _make_cap("file:/tmp/gludd/", {"write"})
        clause = _file_clause(cap)
        assert 'file-write*' in clause

    def test_file_clause_read_write(self):
        cap = _make_cap("file:/tmp/gludd/", {"read", "write"})
        clause = _file_clause(cap)
        assert 'file-read*' in clause
        assert 'file-write*' in clause

    def test_file_clause_no_actions_defaults_read(self):
        cap = _make_cap("file:/tmp/gludd/", set())
        clause = _file_clause(cap)
        assert 'file-read*' in clause


class TestNetClause:
    def test_net_clause_with_hosts_and_ports(self):
        cap = Capability(
            resource="net:egress:api.example.com:443",
            actions={"connect"},
            constraints={
                "allowed_hosts": ["api.example.com"],
                "allowed_ports": [443],
            },
        )
        clause = _net_clause(cap)
        assert "network-outbound" in clause
        assert "api.example.com" in clause

    def test_net_clause_without_hosts(self):
        cap = _make_cap("net:egress", {"connect"})
        clause = _net_clause(cap)
        assert 'network-outbound' in clause


class TestDenyClause:
    def test_deny_clause_file(self):
        cap = _make_cap("file:/tmp/secret/", {"read"})
        clause = _deny_clause(cap)
        assert 'deny file-read*' in clause

    def test_deny_clause_net(self):
        cap = _make_cap("net:egress", {"connect"})
        clause = _deny_clause(cap)
        assert 'deny network-outbound' in clause

    def test_deny_clause_other(self):
        cap = _make_cap("other:resource", {"use"})
        clause = _deny_clause(cap)
        assert 'deny other:resource' in clause


class TestRenderProfile:
    def test_render_profile_header(self):
        spec = _make_spec()
        profile = render_profile(spec)
        assert "(version 1)" in profile
        assert "(deny default)" in profile
        assert "(allow process-fork)" in profile

    def test_render_profile_with_file_capability(self):
        spec = _make_spec(capabilities=[_make_cap("file:/tmp/gludd/", {"read"})])
        profile = render_profile(spec)
        assert '(allow file-read*' in profile
        assert '/tmp/gludd/' in profile

    def test_render_profile_with_denied_capability(self):
        spec = _make_spec(denied=[_make_cap("file:/tmp/forbidden/", {"read"})])
        profile = render_profile(spec)
        assert 'deny file-read*' in profile

    def test_render_profile_empty_spec(self):
        spec = _make_spec()
        profile = render_profile(spec)
        assert 'empty spec' in profile or profile.endswith('\n')


class TestBackend:
    def test_backend_name(self):
        assert SeatbeltBackend.name == "seatbelt"

    def test_available_returns_bool(self):
        result = SeatbeltBackend.available()
        assert isinstance(result, bool)
