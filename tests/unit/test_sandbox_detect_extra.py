"""Additional structural tests for security/sandboxes/detect.py."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest import mock

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
from general_ludd.security.sandboxes.freebsd_jail import JailBackend
from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend
from general_ludd.security.sandboxes.linux_bubblewrap import BubblewrapBackend
from general_ludd.security.sandboxes.linux_landlock import LandlockBackend
from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend
from general_ludd.security.sandboxes.macos_seatbelt import SeatbeltBackend
from general_ludd.security.sandboxes.windows_appcontainer import AppContainerBackend


class TestAutoOnDarwin:
    def test_returns_seatbelt_when_present_and_not_deprecated(self):
        with mock.patch.object(sys, "platform", "darwin"), mock.patch(
            "general_ludd.security.sandboxes.detect._seatbelt_present", return_value=True
        ), mock.patch(
            "general_ludd.security.sandboxes.macos_seatbelt._is_deprecated_host",
            return_value=False,
        ):
            result = auto()
            assert result is SeatbeltBackend

    def test_returns_none_when_seatbelt_deprecated(self):
        with mock.patch.object(sys, "platform", "darwin"), mock.patch(
            "general_ludd.security.sandboxes.detect._seatbelt_present", return_value=True
        ), mock.patch(
            "general_ludd.security.sandboxes.macos_seatbelt._is_deprecated_host",
            return_value=True,
        ):
            result = auto()
            assert result is None

    def test_returns_none_when_seatbelt_absent(self):
        with mock.patch.object(sys, "platform", "darwin"), mock.patch(
            "general_ludd.security.sandboxes.detect._seatbelt_present", return_value=False
        ):
            result = auto()
            assert result is None


class TestAutoOnLinux:
    def test_returns_landlock_when_available(self):
        with mock.patch.object(sys, "platform", "linux"), mock.patch(
            "general_ludd.security.sandboxes.detect._landlock_available", return_value=True
        ):
            result = auto()
            assert result is LandlockBackend

    def test_returns_bubblewrap_when_landlock_unavailable(self):
        with mock.patch.object(sys, "platform", "linux"), mock.patch(
            "general_ludd.security.sandboxes.detect._landlock_available", return_value=False
        ), mock.patch(
            "general_ludd.security.sandboxes.detect._bubblewrap_present", return_value=True
        ):
            result = auto()
            assert result is BubblewrapBackend

    def test_returns_apparmor_when_others_unavailable(self):
        with mock.patch.object(sys, "platform", "linux"), mock.patch(
            "general_ludd.security.sandboxes.detect._landlock_available", return_value=False
        ), mock.patch(
            "general_ludd.security.sandboxes.detect._bubblewrap_present", return_value=False
        ), mock.patch(
            "general_ludd.security.sandboxes.detect._apparmor_enabled", return_value=True
        ):
            result = auto()
            assert result is AppArmorBackend

    def test_returns_selinux_when_all_others_unavailable(self):
        with mock.patch.object(sys, "platform", "linux"), mock.patch(
            "general_ludd.security.sandboxes.detect._landlock_available", return_value=False
        ), mock.patch(
            "general_ludd.security.sandboxes.detect._bubblewrap_present", return_value=False
        ), mock.patch(
            "general_ludd.security.sandboxes.detect._apparmor_enabled", return_value=False
        ), mock.patch(
            "general_ludd.security.sandboxes.detect._selinux_enabled", return_value=True
        ):
            result = auto()
            assert result is SELinuxBackend

    def test_returns_none_when_nothing_available(self):
        with mock.patch.object(sys, "platform", "linux"), mock.patch(
            "general_ludd.security.sandboxes.detect._landlock_available", return_value=False
        ), mock.patch(
            "general_ludd.security.sandboxes.detect._bubblewrap_present", return_value=False
        ), mock.patch(
            "general_ludd.security.sandboxes.detect._apparmor_enabled", return_value=False
        ), mock.patch(
            "general_ludd.security.sandboxes.detect._selinux_enabled", return_value=False
        ):
            result = auto()
            assert result is None


class TestAutoOnFreeBSD:
    def test_returns_jail_when_present(self):
        with mock.patch.object(sys, "platform", "freebsd13"), mock.patch(
            "general_ludd.security.sandboxes.detect._jail_present", return_value=True
        ):
            result = auto()
            assert result is JailBackend


class TestAutoOnWindows:
    def test_returns_appcontainer_when_present(self):
        with mock.patch.object(sys, "platform", "win32"), mock.patch(
            "general_ludd.security.sandboxes.detect._appcontainer_present", return_value=True
        ):
            result = auto()
            assert result is AppContainerBackend

    def test_returns_none_when_appcontainer_absent(self):
        with mock.patch.object(sys, "platform", "win32"), mock.patch(
            "general_ludd.security.sandboxes.detect._appcontainer_present", return_value=False
        ):
            result = auto()
            assert result is None


class TestAutoUnknownPlatform:
    def test_returns_none(self):
        with mock.patch.object(sys, "platform", "haiku"):
            result = auto()
            assert result is None


class TestDetectionHelpersReturnTypes:
    def test_landlock_available(self):
        assert isinstance(_landlock_available(), bool)

    def test_bubblewrap_present(self):
        assert isinstance(_bubblewrap_present(), bool)

    def test_apparmor_enabled(self):
        assert isinstance(_apparmor_enabled(), bool)

    def test_selinux_enabled(self):
        assert isinstance(_selinux_enabled(), bool)

    def test_jail_present(self):
        assert isinstance(_jail_present(), bool)

    def test_seatbelt_present(self):
        assert isinstance(_seatbelt_present(), bool)

    def test_appcontainer_present(self):
        assert isinstance(_appcontainer_present(), bool)

    def test_landlock_available_with_positive_abi(self):
        landlock = SimpleNamespace(
            Ruleset=lambda: SimpleNamespace(abi=4),
        )
        with mock.patch("importlib.import_module", return_value=landlock):
            assert _landlock_available() is True

    def test_selinux_enabled_with_python_binding_and_toolchain(self):
        selinux = SimpleNamespace(is_selinux_enabled=lambda: True)
        with (
            mock.patch("shutil.which", return_value="/usr/bin/checkmodule"),
            mock.patch("importlib.import_module", return_value=selinux),
        ):
            assert _selinux_enabled() is True

    def test_apparmor_enabled_when_tools_and_kernel_are_ready(self):
        with (
            mock.patch("shutil.which", return_value="/usr/bin/tool"),
            mock.patch(
                "subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ),
        ):
            assert _apparmor_enabled() is True

    def test_appcontainer_present_on_windows_with_pywin32(self):
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch("importlib.import_module", return_value=object()),
        ):
            assert _appcontainer_present() is True
