"""Tests for vm/contracts.py — ImageConfig, BootConfig, UnikernelBackend Protocol.

Covers:
  - ImageConfig dataclass (fields, defaults, immutability, to_manifest)
  - BootConfig dataclass (fields, defaults, immutability, validation)
  - UnikernelBackend Protocol conformance (structural isinstance checks)
  - UnikernelBackend integration with SandboxBackend Protocol
  - validate_image_config and validate_boot_config helpers
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from general_ludd.sandbox.contracts import (
    SandboxBackend,
    SandboxConfig,
    SandboxResult,
)


class TestImageConfig:
    def test_defaults(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig

        cfg = ImageConfig(name="gludd-sandbox-default")
        assert cfg.name == "gludd-sandbox-default"
        assert cfg.architecture == "x86_64"
        assert cfg.kernel_version == "5.10"
        assert cfg.packages == ()
        assert cfg.custom_files == ()
        assert cfg.image_type == "firecracker"
        assert cfg.kernel_path == "/var/lib/gludd/vmlinux"
        assert cfg.rootfs_path == "/var/lib/gludd/rootfs.ext4"
        assert cfg.extra == {}

    def test_custom_config(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig

        cfg = ImageConfig(
            name="my-sandbox",
            packages=("python3", "ansible"),
            architecture="aarch64",
            kernel_version="6.1",
            custom_files=(("/etc/hostname", b"myhost"),),
            image_type="gvisor",
            kernel_path="/opt/kernel/vmlinux",
            rootfs_path="/opt/images/rootfs.ext4",
            extra={"label": "production"},
        )
        assert cfg.name == "my-sandbox"
        assert cfg.packages == ("python3", "ansible")
        assert cfg.architecture == "aarch64"
        assert cfg.kernel_version == "6.1"
        assert cfg.custom_files == (("/etc/hostname", b"myhost"),)
        assert cfg.image_type == "gvisor"
        assert cfg.kernel_path == "/opt/kernel/vmlinux"
        assert cfg.rootfs_path == "/opt/images/rootfs.ext4"
        assert cfg.extra == {"label": "production"}

    def test_immutable(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig

        cfg = ImageConfig(name="test")
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.name = "changed"

    def test_to_manifest(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig
        from general_ludd.security.sandboxes.vm.image_builder import ImageManifest

        cfg = ImageConfig(
            name="gludd-sandbox-default",
            packages=("python3", "ansible", "git"),
        )
        manifest = cfg.to_manifest()
        assert isinstance(manifest, ImageManifest)
        assert manifest.name == "gludd-sandbox-default"
        assert manifest.packages == ("python3", "ansible", "git")
        assert manifest.architecture == "x86_64"
        assert manifest.kernel_version == "5.10"

    def test_to_manifest_preserves_custom_files(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig

        cfg = ImageConfig(
            name="custom",
            custom_files=(("/usr/bin/agent", b"#!/bin/sh\n"),),
        )
        manifest = cfg.to_manifest()
        assert manifest.custom_files == (("/usr/bin/agent", b"#!/bin/sh\n"),)

    def test_to_manifest_preserves_extra(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig

        cfg = ImageConfig(name="extra-test", extra={"foo": "bar"})
        manifest = cfg.to_manifest()
        assert manifest.extra == {"foo": "bar"}

    def test_from_manifest_round_trip(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig
        from general_ludd.security.sandboxes.vm.image_builder import ImageManifest

        manifest = ImageManifest(
            name="roundtrip",
            packages=("python3", "git"),
            architecture="aarch64",
            kernel_version="6.1",
            custom_files=(("/etc/motd", b"hello"),),
            extra={"build_id": "abc123"},
        )
        cfg = ImageConfig.from_manifest(
            manifest,
            kernel_path="/custom/kernel",
            rootfs_path="/custom/rootfs",
        )
        assert cfg.name == "roundtrip"
        assert cfg.packages == ("python3", "git")
        assert cfg.architecture == "aarch64"
        assert cfg.kernel_version == "6.1"
        assert cfg.custom_files == (("/etc/motd", b"hello"),)
        assert cfg.extra == {"build_id": "abc123"}
        assert cfg.kernel_path == "/custom/kernel"
        assert cfg.rootfs_path == "/custom/rootfs"
        assert cfg.image_type == "firecracker"


class TestBootConfig:
    def test_defaults(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import BootConfig

        cfg = BootConfig()
        assert cfg.vcpu_count == 1
        assert cfg.mem_size_mib == 128
        assert cfg.boot_args == "console=ttyS0 reboot=k panic=1 pci=off"
        assert cfg.guest_cid == 3
        assert cfg.timeout_seconds == 30
        assert cfg.network_enabled is False
        assert cfg.vsock_enabled is True
        assert cfg.read_only_rootfs is False

    def test_custom_config(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import BootConfig

        cfg = BootConfig(
            vcpu_count=4,
            mem_size_mib=2048,
            boot_args="console=ttyS0 panic=1",
            guest_cid=7,
            timeout_seconds=120,
            network_enabled=True,
            vsock_enabled=False,
            read_only_rootfs=True,
        )
        assert cfg.vcpu_count == 4
        assert cfg.mem_size_mib == 2048
        assert cfg.boot_args == "console=ttyS0 panic=1"
        assert cfg.guest_cid == 7
        assert cfg.timeout_seconds == 120
        assert cfg.network_enabled is True
        assert cfg.vsock_enabled is False
        assert cfg.read_only_rootfs is True

    def test_immutable(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import BootConfig

        cfg = BootConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.vcpu_count = 8

    def test_to_firecracker_machine_config(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import BootConfig

        cfg = BootConfig(vcpu_count=2, mem_size_mib=1024)
        machine = cfg.to_firecracker_machine_config()
        assert machine == {"vcpu_count": 2, "mem_size_mib": 1024}

    def test_to_firecracker_boot_source(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import BootConfig

        cfg = BootConfig(boot_args="console=ttyS0 panic=1")
        boot = cfg.to_firecracker_boot_source(kernel_path="/vmlinux")
        assert boot["kernel_image_path"] == "/vmlinux"
        assert boot["boot_args"] == "console=ttyS0 panic=1"

    def test_to_firecracker_drive_config(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import BootConfig

        cfg = BootConfig(read_only_rootfs=True)
        drive = cfg.to_firecracker_drive_config(rootfs_path="/rootfs.ext4")
        assert drive["drive_id"] == "rootfs"
        assert drive["path_on_host"] == "/rootfs.ext4"
        assert drive["is_root_device"] is True
        assert drive["is_read_only"] is True

    def test_to_firecracker_vsock_config(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import BootConfig

        cfg = BootConfig(guest_cid=5)
        vsock = cfg.to_firecracker_vsock_config(uds_path="/tmp/vsock.sock")
        assert vsock == {"guest_cid": 5, "uds_path": "/tmp/vsock.sock"}

    def test_to_firecracker_vsock_disabled_returns_none(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import BootConfig

        cfg = BootConfig(vsock_enabled=False)
        assert cfg.to_firecracker_vsock_config("/tmp/vsock.sock") is None


class TestValidateImageConfig:
    def test_valid_default(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            ImageConfig,
            validate_image_config,
        )

        errors = validate_image_config(ImageConfig(name="test"))
        assert errors == []

    def test_empty_name(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            ImageConfig,
            validate_image_config,
        )

        errors = validate_image_config(ImageConfig(name=""))
        assert len(errors) > 0
        assert any("name" in e.lower() for e in errors)

    def test_unknown_architecture(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            ImageConfig,
            validate_image_config,
        )

        errors = validate_image_config(ImageConfig(name="test", architecture="mips"))
        assert len(errors) > 0
        assert any("architecture" in e.lower() for e in errors)

    def test_unknown_image_type(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            ImageConfig,
            validate_image_config,
        )

        errors = validate_image_config(ImageConfig(name="test", image_type="qemu"))
        assert len(errors) > 0
        assert any("image_type" in e.lower() for e in errors)

    def test_both_firecracker_and_gvisor_valid(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            ImageConfig,
            validate_image_config,
        )

        assert validate_image_config(ImageConfig(name="test", image_type="firecracker")) == []
        assert validate_image_config(ImageConfig(name="test", image_type="gvisor")) == []

    def test_valid_architectures_pass(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            ImageConfig,
            validate_image_config,
        )

        for arch in ("x86_64", "aarch64"):
            errors = validate_image_config(ImageConfig(name="test", architecture=arch))
            assert errors == [], f"architecture {arch} should be valid"


class TestValidateBootConfig:
    def test_valid_default(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            BootConfig,
            validate_boot_config,
        )

        errors = validate_boot_config(BootConfig())
        assert errors == []

    def test_zero_vcpu_count(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            BootConfig,
            validate_boot_config,
        )

        errors = validate_boot_config(BootConfig(vcpu_count=0))
        assert len(errors) > 0
        assert any("vcpu" in e.lower() for e in errors)

    def test_negative_vcpu_count(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            BootConfig,
            validate_boot_config,
        )

        errors = validate_boot_config(BootConfig(vcpu_count=-1))
        assert len(errors) > 0

    def test_zero_mem_size(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            BootConfig,
            validate_boot_config,
        )

        errors = validate_boot_config(BootConfig(mem_size_mib=0))
        assert len(errors) > 0
        assert any("mem" in e.lower() for e in errors)

    def test_negative_timeout(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            BootConfig,
            validate_boot_config,
        )

        errors = validate_boot_config(BootConfig(timeout_seconds=-10))
        assert len(errors) > 0
        assert any("timeout" in e.lower() for e in errors)

    def test_multiple_errors(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            BootConfig,
            validate_boot_config,
        )

        errors = validate_boot_config(BootConfig(vcpu_count=0, mem_size_mib=0))
        assert len(errors) >= 2


class TestUnikernelBackendProtocol:
    def test_conforming_implementation_passes_isinstance(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            BootConfig,
            ImageConfig,
            UnikernelBackend,
        )

        class ConformingUnikernel:
            name: str = "test-unikernel"

            def __init__(self, config: SandboxConfig) -> None:
                self.config = config

            def available(self) -> bool:
                return True

            def configure_image(self, image: ImageConfig) -> None:
                self.image = image

            def configure_boot(self, boot: BootConfig) -> None:
                self.boot = boot

            def execute(self, command: str, **kwargs: Any) -> SandboxResult:
                return SandboxResult(returncode=0, stdout="ok", stderr="")

            def cleanup(self) -> None:
                pass

        backend = ConformingUnikernel(SandboxConfig())
        assert isinstance(backend, UnikernelBackend)
        assert isinstance(backend, SandboxBackend)

    def test_missing_configure_image_fails(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import UnikernelBackend

        class NoImageConfig:
            name: str = "partial"

            def __init__(self, config: SandboxConfig) -> None:
                self.config = config

            def available(self) -> bool:
                return True

            def configure_boot(self, boot: Any) -> None:
                pass

            def execute(self, command: str, **kwargs: Any) -> SandboxResult:
                return SandboxResult(returncode=0, stdout="", stderr="")

            def cleanup(self) -> None:
                pass

        assert not isinstance(NoImageConfig(SandboxConfig()), UnikernelBackend)

    def test_missing_configure_boot_fails(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import UnikernelBackend

        class NoBootConfig:
            name: str = "partial"

            def __init__(self, config: SandboxConfig) -> None:
                self.config = config

            def available(self) -> bool:
                return True

            def configure_image(self, image: Any) -> None:
                pass

            def execute(self, command: str, **kwargs: Any) -> SandboxResult:
                return SandboxResult(returncode=0, stdout="", stderr="")

            def cleanup(self) -> None:
                pass

        assert not isinstance(NoBootConfig(SandboxConfig()), UnikernelBackend)

    def test_unikernel_subtypes_sandbox_backend(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            BootConfig,
            ImageConfig,
            UnikernelBackend,
        )

        class FullBackend:
            name: str = "full"

            def __init__(self, config: SandboxConfig) -> None:
                self.config = config

            def available(self) -> bool:
                return True

            def configure_image(self, image: ImageConfig) -> None:
                pass

            def configure_boot(self, boot: BootConfig) -> None:
                pass

            def execute(self, command: str, **kwargs: Any) -> SandboxResult:
                return SandboxResult(returncode=0, stdout="", stderr="")

            def cleanup(self) -> None:
                pass

        fb = FullBackend(SandboxConfig())
        assert isinstance(fb, UnikernelBackend)
        assert isinstance(fb, SandboxBackend)

    def test_missing_available_fails(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            BootConfig,
            ImageConfig,
            UnikernelBackend,
        )

        class NoAvailable:
            name: str = "no-avail"

            def __init__(self, config: SandboxConfig) -> None:
                self.config = config

            def configure_image(self, image: ImageConfig) -> None:
                pass

            def configure_boot(self, boot: BootConfig) -> None:
                pass

            def execute(self, command: str, **kwargs: Any) -> SandboxResult:
                return SandboxResult(returncode=0, stdout="", stderr="")

            def cleanup(self) -> None:
                pass

        assert not isinstance(NoAvailable(SandboxConfig()), UnikernelBackend)
        assert not isinstance(NoAvailable(SandboxConfig()), SandboxBackend)


class TestImageConfigContentHash:
    def test_same_config_same_hash(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig

        a = ImageConfig(name="test", packages=("a", "b"))
        b = ImageConfig(name="test", packages=("a", "b"))
        assert a.content_hash() == b.content_hash()

    def test_different_name_different_hash(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig

        a = ImageConfig(name="a")
        b = ImageConfig(name="b")
        assert a.content_hash() != b.content_hash()

    def test_different_packages_different_hash(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig

        a = ImageConfig(name="test", packages=("python3",))
        b = ImageConfig(name="test", packages=("python3", "git"))
        assert a.content_hash() != b.content_hash()

    def test_hash_is_stable_string(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig

        cfg = ImageConfig(name="stable")
        h = cfg.content_hash()
        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_extra_not_in_hash_does_not_affect_equality(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig

        a = ImageConfig(name="test", extra={"build": "1"})
        b = ImageConfig(name="test", extra={"build": "2"})
        assert a.content_hash() == b.content_hash()


class TestBootConfigIntegration:
    def test_boot_config_can_construct_firecracker_rest_body(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import BootConfig

        boot = BootConfig(vcpu_count=2, mem_size_mib=512)
        machine = boot.to_firecracker_machine_config()
        boot_src = boot.to_firecracker_boot_source("/vmlinux")
        drive = boot.to_firecracker_drive_config("/rootfs.ext4")
        vsock = boot.to_firecracker_vsock_config("/tmp/vsock.sock")

        assert machine["vcpu_count"] == 2
        assert machine["mem_size_mib"] == 512
        assert boot_src["kernel_image_path"] == "/vmlinux"
        assert drive["path_on_host"] == "/rootfs.ext4"
        assert vsock["guest_cid"] == 3
        assert vsock["uds_path"] == "/tmp/vsock.sock"


class TestImageConfigCachePath:
    def test_cache_path_is_under_cache_dir(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig
        from general_ludd.security.sandboxes.vm.image_builder import CACHE_DIR

        cfg = ImageConfig(name="cache-test")
        cp = cfg.cache_path()
        assert str(cp).startswith(str(CACHE_DIR))

    def test_different_configs_different_cache_paths(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig

        a = ImageConfig(name="a")
        b = ImageConfig(name="b")
        assert a.cache_path() != b.cache_path()

    def test_same_config_same_cache_path(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import ImageConfig

        a = ImageConfig(name="same", packages=("x", "y"))
        b = ImageConfig(name="same", packages=("x", "y"))
        assert a.cache_path() == b.cache_path()


class TestBootConfigToSandboxConfig:
    def test_merges_into_sandbox_config(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import BootConfig

        boot = BootConfig(vcpu_count=4, mem_size_mib=2048)
        sc = boot.to_sandbox_config()
        assert sc.memory_mb == 2048
        assert sc.cpu_seconds == boot.timeout_seconds
        assert sc.timeout == boot.timeout_seconds


class TestPresetConfigurations:
    def test_default_image_config(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            DEFAULT_IMAGE_CONFIG,
            ImageConfig,
        )

        assert isinstance(DEFAULT_IMAGE_CONFIG, ImageConfig)
        assert DEFAULT_IMAGE_CONFIG.name == "gludd-sandbox-default"
        assert DEFAULT_IMAGE_CONFIG.image_type == "firecracker"

    def test_default_boot_config(self) -> None:
        from general_ludd.security.sandboxes.vm.contracts import (
            DEFAULT_BOOT_CONFIG,
            BootConfig,
        )

        assert isinstance(DEFAULT_BOOT_CONFIG, BootConfig)
        assert DEFAULT_BOOT_CONFIG.vcpu_count == 1
        assert DEFAULT_BOOT_CONFIG.mem_size_mib == 128
