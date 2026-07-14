"""Structural tests for src/general_ludd/infra/qemu_detect.py."""

from __future__ import annotations

import dataclasses
import typing
from unittest import mock

import pytest


class TestQemuConfigFields:
    def test_is_dataclass(self):
        from general_ludd.infra.qemu_detect import QemuConfig
        assert dataclasses.is_dataclass(QemuConfig)

    def test_is_frozen(self):
        from general_ludd.infra.qemu_detect import QemuConfig
        assert QemuConfig.__dataclass_params__.frozen

    def test_field_names(self):
        from general_ludd.infra.qemu_detect import QemuConfig
        fields = {f.name for f in dataclasses.fields(QemuConfig)}
        assert fields == {"platform", "arch", "binary_path", "acceleration"}

    def test_platform_type(self):
        from general_ludd.infra.qemu_detect import QemuConfig
        hints = typing.get_type_hints(QemuConfig)
        actual = set(typing.get_args(hints["platform"]))
        assert actual == {"darwin", "linux", "unknown"}

    def test_arch_type(self):
        from general_ludd.infra.qemu_detect import QemuConfig
        hints = typing.get_type_hints(QemuConfig)
        actual = set(typing.get_args(hints["arch"]))
        assert actual == {"arm64", "amd64", "unknown"}

    def test_binary_path_type_is_optional_str(self):
        from general_ludd.infra.qemu_detect import QemuConfig
        hints = typing.get_type_hints(QemuConfig)
        assert hints["binary_path"] == str | None

    def test_acceleration_type(self):
        from general_ludd.infra.qemu_detect import QemuConfig
        hints = typing.get_type_hints(QemuConfig)
        actual = set(typing.get_args(hints["acceleration"]))
        assert actual == {"hvf", "kvm", "none"}

    def test_construct_darwin_arm64(self):
        from general_ludd.infra.qemu_detect import QemuConfig
        cfg = QemuConfig(
            platform="darwin", arch="arm64",
            binary_path="/opt/homebrew/bin/qemu-system-aarch64",
            acceleration="hvf",
        )
        assert cfg.platform == "darwin"
        assert cfg.arch == "arm64"
        assert cfg.binary_path == "/opt/homebrew/bin/qemu-system-aarch64"
        assert cfg.acceleration == "hvf"

    def test_construct_linux_amd64_kvm(self):
        from general_ludd.infra.qemu_detect import QemuConfig
        cfg = QemuConfig(
            platform="linux", arch="amd64",
            binary_path="/usr/bin/qemu-system-x86_64",
            acceleration="kvm",
        )
        assert cfg.platform == "linux"
        assert cfg.arch == "amd64"
        assert cfg.acceleration == "kvm"

    def test_construct_unknown_no_accel(self):
        from general_ludd.infra.qemu_detect import QemuConfig
        cfg = QemuConfig(
            platform="unknown", arch="unknown",
            binary_path=None, acceleration="none",
        )
        assert cfg.platform == "unknown"
        assert cfg.arch == "unknown"
        assert cfg.binary_path is None
        assert cfg.acceleration == "none"

    def test_frozen_prevents_mutation(self):
        from general_ludd.infra.qemu_detect import QemuConfig
        cfg = QemuConfig(platform="darwin", arch="arm64", binary_path=None, acceleration="hvf")
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.platform = "linux"  # type: ignore[misc]


class TestDetectPlatformDetection:
    def test_darwin(self, monkeypatch):
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.system", lambda: "Darwin")
        from general_ludd.infra.qemu_detect import _detect_platform
        assert _detect_platform() == "darwin"

    def test_linux(self, monkeypatch):
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.system", lambda: "Linux")
        from general_ludd.infra.qemu_detect import _detect_platform
        assert _detect_platform() == "linux"

    def test_windows_unknown(self, monkeypatch):
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.system", lambda: "Windows")
        from general_ludd.infra.qemu_detect import _detect_platform
        assert _detect_platform() == "unknown"

    def test_freebsd_unknown(self, monkeypatch):
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.system", lambda: "FreeBSD")
        from general_ludd.infra.qemu_detect import _detect_platform
        assert _detect_platform() == "unknown"


class TestArchDetection:
    def test_arm64(self, monkeypatch):
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.machine", lambda: "arm64")
        from general_ludd.infra.qemu_detect import _detect_arch
        assert _detect_arch() == "arm64"

    def test_aarch64_aliased_to_arm64(self, monkeypatch):
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.machine", lambda: "aarch64")
        from general_ludd.infra.qemu_detect import _detect_arch
        assert _detect_arch() == "arm64"

    def test_x86_64_aliased_to_amd64(self, monkeypatch):
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.machine", lambda: "x86_64")
        from general_ludd.infra.qemu_detect import _detect_arch
        assert _detect_arch() == "amd64"

    def test_amd64(self, monkeypatch):
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.machine", lambda: "amd64")
        from general_ludd.infra.qemu_detect import _detect_arch
        assert _detect_arch() == "amd64"

    def test_unknown(self, monkeypatch):
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.machine", lambda: "s390x")
        from general_ludd.infra.qemu_detect import _detect_arch
        assert _detect_arch() == "unknown"


class TestFindQemuBinary:
    def test_arm64_maps_to_qemu_system_aarch64(self, monkeypatch):
        from general_ludd.infra.qemu_detect import _find_qemu_binary
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            lambda n: "/usr/bin/" + n,
        )
        result = _find_qemu_binary("arm64")
        assert result == "/usr/bin/qemu-system-aarch64"

    def test_amd64_maps_to_qemu_system_x86_64(self, monkeypatch):
        from general_ludd.infra.qemu_detect import _find_qemu_binary
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            lambda n: "/usr/bin/" + n,
        )
        result = _find_qemu_binary("amd64")
        assert result == "/usr/bin/qemu-system-x86_64"

    def test_unknown_arch_returns_none(self, monkeypatch):
        from general_ludd.infra.qemu_detect import _find_qemu_binary
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            lambda n: "/bin/" + n,
        )
        assert _find_qemu_binary("unknown") is None

    def test_binary_not_found_returns_none(self, monkeypatch):
        from general_ludd.infra.qemu_detect import _find_qemu_binary
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            mock.Mock(return_value=None),
        )
        assert _find_qemu_binary("arm64") is None


class TestAccelerationDetection:
    def test_darwin_returns_hvf(self, monkeypatch):
        from general_ludd.infra.qemu_detect import _detect_acceleration
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            lambda n: "/usr/bin/" + n,
        )
        assert _detect_acceleration("darwin") == "hvf"

    def test_linux_with_kvm_ok_returns_kvm(self, monkeypatch):
        from general_ludd.infra.qemu_detect import _detect_acceleration
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            mock.Mock(return_value="/usr/sbin/kvm-ok"),
        )
        assert _detect_acceleration("linux") == "kvm"

    def test_linux_without_kvm_ok_returns_none(self, monkeypatch):
        from general_ludd.infra.qemu_detect import _detect_acceleration
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            mock.Mock(return_value=None),
        )
        assert _detect_acceleration("linux") == "none"

    def test_unknown_platform_returns_none(self, monkeypatch):
        from general_ludd.infra.qemu_detect import _detect_acceleration
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            lambda n: "/usr/bin/" + n,
        )
        assert _detect_acceleration("unknown") == "none"


class TestDetectIntegration:
    def test_darwin_arm64_with_qemu(self, monkeypatch):
        from general_ludd.infra.qemu_detect import detect
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.system", lambda: "Darwin")
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.machine", lambda: "arm64")
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            lambda n: "/opt/homebrew/bin/qemu-system-aarch64"
            if n == "qemu-system-aarch64"
            else None,
        )
        cfg = detect()
        assert cfg.platform == "darwin"
        assert cfg.arch == "arm64"
        assert cfg.binary_path == "/opt/homebrew/bin/qemu-system-aarch64"
        assert cfg.acceleration == "hvf"

    def test_linux_amd64_with_qemu_and_kvm_ok(self, monkeypatch):
        from general_ludd.infra.qemu_detect import detect
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.system", lambda: "Linux")
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.machine", lambda: "x86_64")
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            lambda n: "/usr/bin/qemu-system-x86_64" if n == "qemu-system-x86_64"
            else "/usr/sbin/kvm-ok" if n == "kvm-ok"
            else None,
        )
        cfg = detect()
        assert cfg.platform == "linux"
        assert cfg.arch == "amd64"
        assert cfg.binary_path == "/usr/bin/qemu-system-x86_64"
        assert cfg.acceleration == "kvm"

    def test_linux_amd64_qemu_missing_kvm_absent(self, monkeypatch):
        from general_ludd.infra.qemu_detect import detect
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.system", lambda: "Linux")
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.machine", lambda: "amd64")
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            mock.Mock(return_value=None),
        )
        cfg = detect()
        assert cfg.platform == "linux"
        assert cfg.arch == "amd64"
        assert cfg.binary_path is None
        assert cfg.acceleration == "none"

    def test_unknown_platform_unknown_arch(self, monkeypatch):
        from general_ludd.infra.qemu_detect import detect
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.system", lambda: "Windows")
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.machine", lambda: "s390x")
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            lambda n: "/bin/" + n,
        )
        cfg = detect()
        assert cfg.platform == "unknown"
        assert cfg.arch == "unknown"
        assert cfg.binary_path is None
        assert cfg.acceleration == "none"

    def test_darwin_arm64_qemu_missing(self, monkeypatch):
        from general_ludd.infra.qemu_detect import detect
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.system", lambda: "Darwin")
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.machine", lambda: "aarch64")
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            mock.Mock(return_value=None),
        )
        cfg = detect()
        assert cfg.platform == "darwin"
        assert cfg.arch == "arm64"
        assert cfg.binary_path is None
        assert cfg.acceleration == "hvf"

    def test_darwin_amd64(self, monkeypatch):
        from general_ludd.infra.qemu_detect import detect
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.system", lambda: "Darwin")
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.machine", lambda: "x86_64")
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            lambda n: "/usr/local/bin/qemu-system-x86_64"
            if n == "qemu-system-x86_64"
            else None,
        )
        cfg = detect()
        assert cfg.platform == "darwin"
        assert cfg.arch == "amd64"
        assert cfg.binary_path == "/usr/local/bin/qemu-system-x86_64"
        assert cfg.acceleration == "hvf"

    def test_linux_arm64_kvm_missing(self, monkeypatch):
        from general_ludd.infra.qemu_detect import detect
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.system", lambda: "Linux")
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.machine", lambda: "aarch64")
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            lambda n: "/usr/bin/qemu-system-aarch64"
            if n == "qemu-system-aarch64"
            else None,
        )
        cfg = detect()
        assert cfg.platform == "linux"
        assert cfg.arch == "arm64"
        assert cfg.binary_path == "/usr/bin/qemu-system-aarch64"
        assert cfg.acceleration == "none"


class TestDetectReturnsQemuConfig:
    def test_detect_returns_qemu_config_instance(self, monkeypatch):
        from general_ludd.infra.qemu_detect import QemuConfig, detect
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.system", lambda: "Darwin")
        monkeypatch.setattr("general_ludd.infra.qemu_detect.platform.machine", lambda: "arm64")
        monkeypatch.setattr(
            "general_ludd.infra.qemu_detect.shutil.which",
            lambda n: "/usr/bin/" + n,
        )
        cfg = detect()
        assert isinstance(cfg, QemuConfig)


class TestExports:
    def test_all_exports(self):
        from general_ludd.infra import qemu_detect
        assert "QemuConfig" in qemu_detect.__all__
        assert "detect" in qemu_detect.__all__
        assert len(qemu_detect.__all__) == 2
