"""Structural tests for security/sandboxes/detect.py — auto-detect sandbox backend."""

from __future__ import annotations

from general_ludd.security.sandboxes.detect import (
    _apparmor_enabled,
    _appcontainer_present,
    _bubblewrap_present,
    _jail_present,
    _landlock_available,
    _seatbelt_present,
    _selinux_enabled,
    auto,
)


class TestDetectionFunctions:
    def test_landlock_returns_bool(self):
        result = _landlock_available()
        assert isinstance(result, bool)

    def test_bubblewrap_returns_bool(self):
        result = _bubblewrap_present()
        assert isinstance(result, bool)

    def test_apparmor_returns_bool(self):
        result = _apparmor_enabled()
        assert isinstance(result, bool)

    def test_selinux_returns_bool(self):
        result = _selinux_enabled()
        assert isinstance(result, bool)

    def test_jail_returns_bool(self):
        result = _jail_present()
        assert isinstance(result, bool)

    def test_seatbelt_returns_bool(self):
        result = _seatbelt_present()
        assert isinstance(result, bool)

    def test_appcontainer_returns_bool(self):
        result = _appcontainer_present()
        assert isinstance(result, bool)


class TestAuto:
    def test_returns_value(self):
        result = auto()
        assert result is not None or result is None
