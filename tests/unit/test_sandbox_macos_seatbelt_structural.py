"""Structural tests for security/sandboxes/macos_seatbelt.py — macOS Seatbelt sandbox backend."""

from __future__ import annotations

from general_ludd.security.sandboxes.macos_seatbelt import (
    SeatbeltBackend,
    _is_deprecated_host,
    _is_file_family,
    _is_net_family,
    _macos_version_tuple,
    render_profile,
)


class TestSeatbeltHelpers:
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

    def test_macos_version_tuple_returns_tuple(self):
        result = _macos_version_tuple()
        assert isinstance(result, tuple)

    def test_is_deprecated_host_returns_bool(self):
        result = _is_deprecated_host()
        assert isinstance(result, bool)

    def test_render_profile(self):
        from general_ludd.security.sandboxes import PermissionSpec

        spec = PermissionSpec(agent_type="test-agent", capabilities=[])
        text = render_profile(spec)
        assert isinstance(text, str)
        assert "(version 1)" in text
        assert "(deny default)" in text


class TestSeatbeltBackend:
    def test_backend_name(self):
        assert SeatbeltBackend.name == "seatbelt"

    def test_available_returns_bool(self):
        result = SeatbeltBackend.available()
        assert isinstance(result, bool)
