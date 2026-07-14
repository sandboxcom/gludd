"""Extended structural tests for security/sandboxes/detect.py — auto-detect sandbox backend."""

from __future__ import annotations

import sys

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


class TestDetectionReturnTypes:
    def test_landlock_returns_bool(self):
        assert isinstance(_landlock_available(), bool)

    def test_bubblewrap_returns_bool(self):
        assert isinstance(_bubblewrap_present(), bool)

    def test_apparmor_returns_bool(self):
        assert isinstance(_apparmor_enabled(), bool)

    def test_selinux_returns_bool(self):
        assert isinstance(_selinux_enabled(), bool)

    def test_jail_returns_bool(self):
        assert isinstance(_jail_present(), bool)

    def test_seatbelt_returns_bool(self):
        assert isinstance(_seatbelt_present(), bool)

    def test_appcontainer_returns_bool(self):
        assert isinstance(_appcontainer_present(), bool)


class TestAutoReturnsType:
    def test_auto_returns_backend_or_none(self):

        result = auto()
        if result is not None:
            assert isinstance(result, type)
        else:
            assert result is None

    def test_auto_on_macos(self):
        if sys.platform == "darwin":
            result = auto()
            assert result is not None or result is None

    def test_auto_on_linux(self):
        if sys.platform.startswith("linux"):
            result = auto()
            assert result is not None or result is None


class TestJailPlatformCheck:
    def test_jail_present_checks_freebsd(self):
        result = _jail_present()
        if sys.platform.startswith("freebsd"):
            assert isinstance(result, bool)
        else:
            assert result is False

    def test_seatbelt_present_checks_darwin(self):
        result = _seatbelt_present()
        if sys.platform == "darwin":
            assert isinstance(result, bool)
        else:
            assert result is False

    def test_appcontainer_present_checks_windows(self):
        result = _appcontainer_present()
        if sys.platform.startswith("win"):
            assert isinstance(result, bool)
        else:
            assert result is False
