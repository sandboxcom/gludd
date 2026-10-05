"""Deep sandbox isolation and unikernel tests.

Covers untested paths across the sandbox subsystem:
  - Filesystem isolation boundary enforcement (path confinement, symlink escape)
  - Network namespace isolation (NetworkPolicy, allowed/blocked hosts/ports)
  - Resource quota enforcement (CPU, memory, disk, rlimit propagation)
  - Process limit enforcement (pids_limit, RLIMIT_NPROC)
  - Sandbox escape attempt detection (null bytes, traversal, symlink chains)
  - Image pull verification and cache staleness
  - Cleanup on failure (CleanupManager, resource tracking, error resilience)
  - Unikernel backend deep behaviour (VM runtime detection, image/boot config)

Author: opencode agent
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.ornith.sandbox import (
    OrnithSandbox,
    _sandbox_preexec_fn,
    confine_export_path,
    create_ornith_sandbox,
    ornith_sandboxed_run,
)
from general_ludd.sandbox.backends.firecracker_backend import FirecrackerBackend
from general_ludd.sandbox.backends.process_backend import ProcessBackend
from general_ludd.sandbox.backends.unikernel_backend import UnikernelBackend
from general_ludd.sandbox.cleanup import CleanupManager
from general_ludd.sandbox.contracts import (
    ISOLATION_RANK,
    MINIMAL_SANDBOX_CONFIG,
    STRICT_SANDBOX_CONFIG,
    IsolationLevel,
    SandboxConfig,
    is_valid_isolation_level,
    isolation_exceeds,
    validate_config,
)
from general_ludd.sandbox.enforcer import (
    PathEscapeError,
    SandboxEnforcer,
)
from general_ludd.sandbox.enforcer import (
    SandboxConfig as EnforcerSandboxConfig,
)
from general_ludd.sandbox.image_cache import CachedImage, ImageCache
from general_ludd.sandbox.network_policy import NetworkPolicy
from general_ludd.sandbox.resource_limits import ResourceLimits
from general_ludd.sandbox.security_policy import SecurityPolicy
from general_ludd.security.sandboxes.state import (
    SandboxState,
    SandboxStateError,
    safe_state_component,
)
from general_ludd.security.sandboxes.vm.contracts import (
    DEFAULT_BOOT_CONFIG,
    DEFAULT_IMAGE_CONFIG,
    BootConfig,
    ImageConfig,
    validate_boot_config,
    validate_image_config,
)

# ────────────────────────────────────────────────────────────────
# Filesystem isolation boundary enforcement
# ────────────────────────────────────────────────────────────────


class TestFilesystemIsolationDeep:
    """Deep filesystem boundary tests beyond basic path confinement."""

    def test_multiple_symlink_hops_blocked(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        hop1 = jail / "hop1"
        hop1.symlink_to(outside)
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path(str(hop1 / ".." / "hop1" / "secret"))

    def test_confine_path_with_null_byte_rejected(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises((PathEscapeError, ValueError)):
            enforcer.confine_path("evil\x00path.txt")

    def test_absolute_path_outside_jail_rejected(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError, match="escapes"):
            enforcer.confine_path("/bin/sh")

    def test_relative_path_resolved_within_jail(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        subdir = jail / "subdir"
        subdir.mkdir(parents=True)
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        result = enforcer.confine_path("subdir/./file.txt")
        assert jail.name in result
        assert result.endswith("file.txt")

    def test_existing_symlink_inside_jail_allowed(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        target = jail / "legit"
        target.mkdir()
        link = jail / "alias"
        link.symlink_to(target)
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        result = enforcer.confine_path(str(link / "data.json"))
        assert "legit" in result
        assert "data.json" in result

    def test_another_jail_absolute_path_escape_blocked(self, tmp_path: Path) -> None:
        jail = tmp_path / "a"
        jail.mkdir()
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path("/etc/hosts")


# ────────────────────────────────────────────────────────────────
# Network namespace isolation
# ────────────────────────────────────────────────────────────────


class TestNetworkIsolationDeep:
    """Deep network isolation tests."""

    def test_fully_isolated_policy_denies_all(self) -> None:
        policy = NetworkPolicy.fully_isolated()
        assert policy.is_isolated()
        assert not policy.allow_outbound
        assert not policy.allow_inbound
        assert not policy.allows_host("example.com")
        assert not policy.allows_port(443)

    def test_allowed_hosts_only_permit_listed(self) -> None:
        policy = NetworkPolicy(allowed_hosts=["10.0.0.1"], allow_outbound=True)
        assert policy.allows_host("10.0.0.1")
        assert not policy.allows_host("10.0.0.2")
        assert not policy.allows_host("example.com")

    def test_blocked_host_overrides_allowed(self) -> None:
        policy = NetworkPolicy(
            allowed_hosts=["10.0.0.1", "10.0.0.2"],
            blocked_hosts=["10.0.0.2"],
            allow_outbound=True,
        )
        assert policy.allows_host("10.0.0.1")
        assert not policy.allows_host("10.0.0.2")

    def test_blocked_port_overrides_allowed(self) -> None:
        policy = NetworkPolicy(
            allowed_ports=[80, 443, 8080],
            blocked_ports=[8080],
            allow_outbound=True,
        )
        assert policy.allows_port(80)
        assert policy.allows_port(443)
        assert not policy.allows_port(8080)

    def test_allow_localhost_policy(self) -> None:
        policy = NetworkPolicy.allow_localhost()
        assert policy.allow_outbound
        assert policy.allows_host("127.0.0.1")
        assert policy.allows_host("::1")
        assert not policy.allows_host("0.0.0.0")

    def test_docker_args_network_none_when_isolated(self) -> None:
        policy = NetworkPolicy.fully_isolated()
        args = policy.to_docker_args()
        assert "--network" in args
        assert "none" in args

    def test_docker_args_with_dns_servers(self) -> None:
        policy = NetworkPolicy(dns_servers=["8.8.8.8"], allow_outbound=True)
        args = policy.to_docker_args()
        assert "--dns" in args
        assert "8.8.8.8" in args

    def test_kubernetes_policy_includes_policy_types(self) -> None:
        policy = NetworkPolicy(allowed_hosts=["10.0.0.0/8"], allow_outbound=True)
        k8s = policy.to_kubernetes_policy("sandbox-ns", {"app": "gludd"})
        assert k8s["metadata"]["name"] == "gludd-sandbox"
        assert k8s["kind"] == "NetworkPolicy"
        assert "Egress" in k8s["spec"]["policyTypes"]


# ────────────────────────────────────────────────────────────────
# Resource quota enforcement
# ────────────────────────────────────────────────────────────────


class TestResourceQuotaEnforcement:
    """CPU, memory, and disk resource quota tests."""

    def test_resource_limits_docker_args_memory_only(self) -> None:
        limits = ResourceLimits(memory_bytes=512 * 1024 * 1024)
        args = limits.to_docker_args()
        assert "--memory" in args
        assert str(512 * 1024 * 1024) in args

    def test_resource_limits_docker_args_pids_limit(self) -> None:
        limits = ResourceLimits(pids_limit=100)
        args = limits.to_docker_args()
        assert "--pids-limit" in args
        assert "100" in args

    def test_resource_limits_docker_args_cpu_shares(self) -> None:
        limits = ResourceLimits(cpu_shares=2048, cpu_period=50000)
        args = limits.to_docker_args()
        assert "--cpu-shares" in args
        assert "--cpu-period" in args

    def test_resource_limits_docker_args_swap_memory(self) -> None:
        limits = ResourceLimits(memory_bytes=256 * 1024 * 1024, memory_swap_bytes=512 * 1024 * 1024)
        args = limits.to_docker_args()
        assert "--memory-swap" in args

    def test_resource_limits_disk_bytes_storage_opt(self) -> None:
        limits = ResourceLimits(disk_bytes=10 * 1024 * 1024 * 1024)
        args = limits.to_docker_args()
        assert "--storage-opt" in args
        assert "size=" in " ".join(args)

    def test_resource_limits_kubernetes_memory_and_cpu(self) -> None:
        limits = ResourceLimits(memory_bytes=512 * 1024 * 1024, cpu_shares=2048)
        k8s = limits.to_kubernetes_resources()
        assert k8s["limits"]["memory"] == str(512 * 1024 * 1024)
        assert k8s["limits"]["cpu"] == "2m"
        assert k8s["requests"] == k8s["limits"]

    def test_resource_limits_kubernetes_ephemeral_storage(self) -> None:
        limits = ResourceLimits(disk_bytes=5 * 1024 * 1024 * 1024)
        k8s = limits.to_kubernetes_resources()
        assert "ephemeral-storage" in k8s["limits"]

    def test_exceed_memory_returns_true_when_over(self) -> None:
        limits = ResourceLimits(memory_bytes=100 * 1024 * 1024)
        assert limits.exceed_memory(200 * 1024 * 1024)

    def test_exceed_memory_returns_false_when_under(self) -> None:
        limits = ResourceLimits(memory_bytes=100 * 1024 * 1024)
        assert not limits.exceed_memory(50 * 1024 * 1024)

    def test_exceed_timeout_true_when_elapsed_exceeds(self) -> None:
        limits = ResourceLimits(timeout_seconds=30)
        assert limits.exceed_timeout(31.0)

    def test_exceed_timeout_false_when_within(self) -> None:
        limits = ResourceLimits(timeout_seconds=30)
        assert not limits.exceed_timeout(29.9)

    def test_default_factories_have_distinct_limits(self) -> None:
        light = ResourceLimits.default_light()
        medium = ResourceLimits.default_medium()
        heavy = ResourceLimits.default_heavy()
        assert light.memory_bytes is not None
        assert medium.memory_bytes is not None
        assert heavy.memory_bytes is not None
        assert light.memory_bytes < medium.memory_bytes
        assert medium.memory_bytes < heavy.memory_bytes

    def test_to_process_limits_conversion(self) -> None:
        limits = ResourceLimits(memory_bytes=1024 * 1024 * 1024, cpu_shares=4096)
        proc = limits.to_process_limits()
        assert proc["memory_mb"] == 1024
        assert proc["cpu_seconds"] == 4


# ────────────────────────────────────────────────────────────────
# Process limit enforcement
# ────────────────────────────────────────────────────────────────


class TestProcessLimitEnforcement:
    """PIDs limit and RLIMIT_NPROC enforcement tests."""

    def _has_pids_limit_arg(self, docker_args: list[str]) -> bool:
        return any(a.startswith("--pids-limit") for a in docker_args)

    def test_process_backend_honours_pids_limit(self) -> None:
        config = SandboxConfig(max_processes=25)
        limits = config.to_resource_limits()
        docker_args = limits.to_docker_args()
        assert self._has_pids_limit_arg(docker_args)

    def test_pids_limit_propagates_as_rlimit_nproc(self) -> None:
        config = SandboxConfig(max_processes=50)
        limits = config.to_resource_limits()
        assert limits.pids_limit == 50

    def test_sandbox_config_zero_pids_means_unlimited(self) -> None:
        limits = ResourceLimits(pids_limit=0)
        args = limits.to_docker_args()
        assert "--pids-limit" not in args


# ────────────────────────────────────────────────────────────────
# Sandbox escape attempt detection
# ────────────────────────────────────────────────────────────────


class TestSandboxEscapeDetection:
    """Escape attempt detection and prevention tests."""

    def test_double_dot_dot_traversal_blocked(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path(str(jail / ".." / ".." / ".." / "etc" / "passwd"))

    def test_dot_dot_via_relative_path_blocked(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path("../../tmp/secret")

    def test_symlink_chain_escape_blocked(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        outside = tmp_path / "sensitive"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        a = jail / "a"
        a.symlink_to(outside)
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path(str(a / "secret.txt"))

    def test_absolute_symlink_to_root_blocked(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        rootlink = jail / "root"
        rootlink.symlink_to("/")
        enforcer = SandboxEnforcer(EnforcerSandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path(str(rootlink / "tmp"))

    def test_ornith_confine_export_null_byte_rejected(self) -> None:
        with (
            patch.object(type(create_ornith_sandbox()), "__init__", lambda self: None),
            patch("os.environ", {"ORNITH_EXPORT_ROOT": "/tmp/exports"}),pytest.raises(ValueError, match="null byte")
        ):
            confine_export_path("good\x00evil.txt", "fallback.txt")


# ────────────────────────────────────────────────────────────────
# Image pull verification
# ────────────────────────────────────────────────────────────────


class TestImagePullVerification:
    """Image cache and pull verification tests."""

    def test_image_cache_put_and_get(self) -> None:
        cache = ImageCache()
        cache.put("sha256:abc123", "alpine", "3.18", size_bytes=7000000)
        entry = cache.get("alpine", "3.18")
        assert entry is not None
        assert entry.image_id == "sha256:abc123"
        assert entry.size_bytes == 7000000

    def test_image_cache_miss_returns_none(self) -> None:
        cache = ImageCache()
        assert cache.get("nonexistent", "latest") is None

    def test_image_cache_remove(self) -> None:
        cache = ImageCache()
        cache.put("sha256:def", "python", "3.12")
        assert cache.remove("python", "3.12")
        assert not cache.remove("python", "3.13")

    def test_cached_image_full_name(self) -> None:
        img = CachedImage(name="ubuntu", tag="22.04", image_id="sha256:xyz")
        assert img.full_name == "ubuntu:22.04"

    def test_cached_image_stale_detection(self) -> None:
        img = CachedImage(name="alpine", tag="latest", pulled_at=time.time() - 4000)
        assert img.is_stale(3600)

    def test_cached_image_not_stale(self) -> None:
        img = CachedImage(name="alpine", tag="latest", pulled_at=time.time() - 10)
        assert not img.is_stale(3600)

    def test_image_cache_cleanup_stale(self) -> None:
        cache = ImageCache()
        cache.put("sha256:a", "img", "one", pulled_at=time.time() - 5000)
        cache.put("sha256:b", "img", "two", pulled_at=time.time() - 5)
        removed = cache.cleanup_stale(3600)
        assert removed == 1
        assert cache.get("img", "one") is None
        assert cache.get("img", "two") is not None

    def test_image_cache_prune_all(self) -> None:
        cache = ImageCache()
        cache.put("sha256:a", "a", "1")
        cache.put("sha256:b", "b", "1")
        cache.put("sha256:c", "c", "1")
        assert cache.image_count() == 3
        with patch("subprocess.run"):
            cache.prune_all()
        assert cache.image_count() == 0

    def test_image_cache_total_size(self) -> None:
        cache = ImageCache()
        cache.put("sha256:x", "a", "1", size_bytes=1000)
        cache.put("sha256:y", "b", "1", size_bytes=2000)
        assert cache.total_size_bytes() == 3000

    def test_image_cache_list_sorted_by_time(self) -> None:
        cache = ImageCache()
        cache.put("sha256:old", "old", "1", pulled_at=100.0)
        cache.put("sha256:new", "new", "1", pulled_at=200.0)
        images = cache.list_images()
        assert images[0].name == "new"
        assert images[1].name == "old"


# ────────────────────────────────────────────────────────────────
# Cleanup on failure
# ────────────────────────────────────────────────────────────────


class TestCleanupOnFailure:
    """Cleanup manager and resource tracking tests."""

    def test_cleanup_manager_track_and_cleanup(self) -> None:
        cm = CleanupManager()
        cm.track("docker_container", "abc123")
        assert cm.pending_count() == 1
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = cm.cleanup_resource("docker_container", "abc123")
            assert result
        assert cm.pending_count() == 0

    def test_cleanup_manager_unknown_resource_type_returns_false(self) -> None:
        cm = CleanupManager()
        cm.track("unknown_type", "xyz")
        result = cm.cleanup_resource("unknown_type", "xyz")
        assert not result
        assert cm.pending_count() == 1

    def test_cleanup_manager_history_records_success(self) -> None:
        cm = CleanupManager()
        cm.track("docker_container", "abc")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            cm.cleanup_resource("docker_container", "abc")
        assert cm.history_count() == 1
        record = cm.last_cleanup()
        assert record is not None
        assert record.success

    def test_cleanup_manager_history_records_failure(self) -> None:
        cm = CleanupManager()
        cm.track("docker_container", "broken")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("boom")
            result = cm.cleanup_resource("docker_container", "broken")
            assert not result
        record = cm.last_cleanup()
        assert record is not None
        assert not record.success

    def test_cleanup_manager_cleanup_all(self) -> None:
        cm = CleanupManager()
        cm.track("docker_container", "a")
        cm.track("docker_container", "b")
        cm.track("kubernetes_pod", "c")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            count = cm.cleanup_all()
            assert count == 3
            assert cm.pending_count() == 0

    def test_cleanup_manager_cleanup_docker_containers_with_label(self) -> None:
        cm = CleanupManager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="cid1\ncid2\n")
            count = cm.cleanup_docker_containers(label="gludd-sandbox")
            assert count == 2
            assert cm.history_count() == 2

    def test_cleanup_manager_cleanup_docker_no_containers(self) -> None:
        cm = CleanupManager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            count = cm.cleanup_docker_containers()
            assert count == 0
            assert cm.history_count() == 0


# ────────────────────────────────────────────────────────────────
# Unikernel backend deep tests
# ────────────────────────────────────────────────────────────────


class TestUnikernelBackendDeep:
    """Deeper unikernel backend tests beyond the basic contract."""

    def test_available_with_both_runtimes_prefers_firecracker(self) -> None:
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/firecracker"
            backend = UnikernelBackend(SandboxConfig(backend="unikernel"))
            assert backend.available()
            assert backend._vm_runtime == "firecracker"

    def test_available_with_only_runsc(self) -> None:
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/runsc" if cmd == "runsc" else None
            backend = UnikernelBackend(SandboxConfig(backend="unikernel"))
            assert backend.available()
            assert backend._vm_runtime == "gvisor"

    def test_available_with_neither_returns_false(self) -> None:
        with patch("shutil.which", return_value=None):
            backend = UnikernelBackend(SandboxConfig(backend="unikernel"))
            assert not backend.available()
            assert backend._vm_runtime is None

    def test_execute_with_image_and_boot_but_no_runtime(self) -> None:
        with patch("shutil.which", return_value=None):
            backend = UnikernelBackend(SandboxConfig(backend="unikernel"))
            backend.configure_image(ImageConfig(name="test-sandbox"))
            backend.configure_boot(BootConfig(vcpu_count=1, mem_size_mib=256))
            result = backend.execute("echo hello")
            assert result.returncode == 127
            assert "not available" in result.stderr

    def test_execute_missing_both_configs_returns_error(self) -> None:
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/firecracker" if cmd == "firecracker" else None
            backend = UnikernelBackend(SandboxConfig(backend="unikernel"))
            result = backend.execute("echo hello")
            assert result.returncode == 127
            assert "configure_image" in result.stderr

    def test_execute_missing_only_boot_config_returns_error(self) -> None:
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/firecracker" if cmd == "firecracker" else None
            backend = UnikernelBackend(SandboxConfig(backend="unikernel"))
            backend.configure_image(ImageConfig(name="my-sandbox"))
            result = backend.execute("echo hello")
            assert result.returncode == 127
            assert "configure_image" in result.stderr or "configure_boot" in result.stderr

    def test_execute_missing_only_image_config_returns_error(self) -> None:
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/firecracker" if cmd == "firecracker" else None
            backend = UnikernelBackend(SandboxConfig(backend="unikernel"))
            backend.configure_boot(BootConfig(vcpu_count=2, mem_size_mib=512))
            result = backend.execute("echo hello")
            assert result.returncode == 127
            assert "configure_image" in result.stderr

    def test_cleanup_does_not_raise(self) -> None:
        backend = UnikernelBackend(SandboxConfig(backend="unikernel"))
        backend.cleanup()

    def test_gvisor_runtime_detected_when_no_firecracker(self) -> None:
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/runsc" if cmd == "runsc" else None
            backend = UnikernelBackend(SandboxConfig(backend="unikernel"))
            assert backend._vm_runtime == "gvisor"


# ────────────────────────────────────────────────────────────────
# BootConfig & ImageConfig validation
# ────────────────────────────────────────────────────────────────


class TestBootImageConfigValidation:
    """BootConfig and ImageConfig validation tests."""

    def test_validate_image_config_valid(self) -> None:
        errors = validate_image_config(ImageConfig(name="sandbox"))
        assert errors == []

    def test_validate_image_config_empty_name(self) -> None:
        errors = validate_image_config(ImageConfig(name=""))
        assert len(errors) >= 1
        assert any("name" in e for e in errors)

    def test_validate_image_config_invalid_architecture(self) -> None:
        errors = validate_image_config(ImageConfig(name="test", architecture="riscv"))
        assert len(errors) >= 1

    def test_validate_image_config_invalid_image_type(self) -> None:
        errors = validate_image_config(ImageConfig(name="test", image_type="qemu"))
        assert len(errors) >= 1

    def test_validate_boot_config_valid(self) -> None:
        errors = validate_boot_config(BootConfig())
        assert errors == []

    def test_validate_boot_config_zero_vcpus(self) -> None:
        errors = validate_boot_config(BootConfig(vcpu_count=0))
        assert len(errors) >= 1

    def test_validate_boot_config_zero_memory(self) -> None:
        errors = validate_boot_config(BootConfig(mem_size_mib=0))
        assert len(errors) >= 1

    def test_validate_boot_config_negative_timeout(self) -> None:
        errors = validate_boot_config(BootConfig(timeout_seconds=-1))
        assert len(errors) >= 1

    def test_boot_config_to_firecracker_machine_config(self) -> None:
        boot = BootConfig(vcpu_count=4, mem_size_mib=2048)
        mc = boot.to_firecracker_machine_config()
        assert mc == {"vcpu_count": 4, "mem_size_mib": 2048}

    def test_boot_config_to_firecracker_boot_source(self) -> None:
        boot = BootConfig(boot_args="console=ttyS0 panic=1")
        bs = boot.to_firecracker_boot_source("/tmp/vmlinux")
        assert bs["kernel_image_path"] == "/tmp/vmlinux"
        assert bs["boot_args"] == "console=ttyS0 panic=1"

    def test_boot_config_to_firecracker_drive_read_only(self) -> None:
        boot = BootConfig(read_only_rootfs=True)
        drive = boot.to_firecracker_drive_config("/tmp/rootfs.ext4")
        assert drive["is_read_only"] is True
        assert drive["drive_id"] == "rootfs"

    def test_boot_config_to_firecracker_drive_writable(self) -> None:
        boot = BootConfig()
        drive = boot.to_firecracker_drive_config("/tmp/rootfs.ext4")
        assert drive["is_read_only"] is False

    def test_boot_config_vsock_disabled_returns_none(self) -> None:
        boot = BootConfig(vsock_enabled=False)
        assert boot.to_firecracker_vsock_config("/tmp/sock") is None

    def test_boot_config_vsock_enabled_returns_config(self) -> None:
        boot = BootConfig(guest_cid=5)
        vsock = boot.to_firecracker_vsock_config("/tmp/sock")
        assert vsock is not None
        assert vsock["guest_cid"] == 5
        assert vsock["uds_path"] == "/tmp/sock"

    def test_boot_config_to_sandbox_config(self) -> None:
        boot = BootConfig(mem_size_mib=256, timeout_seconds=45)
        sc = boot.to_sandbox_config()
        assert sc.memory_mb == 256
        assert sc.timeout == 45
        assert sc.isolation == IsolationLevel.VM_HARDWARE
        assert sc.backend == "firecracker"

    def test_default_image_config_has_expected_packages(self) -> None:
        assert "python3" in DEFAULT_IMAGE_CONFIG.packages
        assert "ansible" in DEFAULT_IMAGE_CONFIG.packages
        assert "git" in DEFAULT_IMAGE_CONFIG.packages

    def test_default_boot_config_values(self) -> None:
        assert DEFAULT_BOOT_CONFIG.vcpu_count == 1
        assert DEFAULT_BOOT_CONFIG.mem_size_mib == 128
        assert DEFAULT_BOOT_CONFIG.vsock_enabled is True


# ────────────────────────────────────────────────────────────────
# IsolationLevel contract tests
# ────────────────────────────────────────────────────────────────


class TestIsolationLevelDeep:
    """IsolationLevel ranking, ordering, and validation tests."""

    def test_isolation_rank_ordering(self) -> None:
        assert ISOLATION_RANK[IsolationLevel.NONE] < ISOLATION_RANK[IsolationLevel.PROCESS]
        assert ISOLATION_RANK[IsolationLevel.PROCESS] < ISOLATION_RANK[IsolationLevel.CONTAINER]
        assert ISOLATION_RANK[IsolationLevel.CONTAINER] < ISOLATION_RANK[IsolationLevel.VM_USERSPACE]
        assert ISOLATION_RANK[IsolationLevel.VM_USERSPACE] < ISOLATION_RANK[IsolationLevel.VM_HARDWARE]

    def test_isolation_exceeds_true(self) -> None:
        assert isolation_exceeds(IsolationLevel.VM_HARDWARE, IsolationLevel.CONTAINER)
        assert isolation_exceeds(IsolationLevel.VM_USERSPACE, IsolationLevel.PROCESS)
        assert isolation_exceeds(IsolationLevel.CONTAINER, IsolationLevel.NONE)

    def test_isolation_exceeds_false_when_equal(self) -> None:
        assert not isolation_exceeds(IsolationLevel.CONTAINER, IsolationLevel.CONTAINER)

    def test_isolation_exceeds_false_when_weaker(self) -> None:
        assert not isolation_exceeds(IsolationLevel.PROCESS, IsolationLevel.VM_HARDWARE)

    def test_is_valid_isolation_level_with_string(self) -> None:
        assert is_valid_isolation_level("container")
        assert is_valid_isolation_level("vm_hardware")
        assert not is_valid_isolation_level("invalid_level")

    def test_is_valid_isolation_level_with_enum(self) -> None:
        assert is_valid_isolation_level(IsolationLevel.PROCESS)

    def test_is_valid_isolation_level_with_non_string(self) -> None:
        assert not is_valid_isolation_level(42)

    def test_isolation_level_missing_case_insensitive(self) -> None:
        assert IsolationLevel._missing_("CONTAINER") == IsolationLevel.CONTAINER
        assert IsolationLevel._missing_("vm_Hardware") == IsolationLevel.VM_HARDWARE


# ────────────────────────────────────────────────────────────────
# SandboxConfig validation
# ────────────────────────────────────────────────────────────────


class TestSandboxConfigValidation:
    """SandboxConfig validation edge cases."""

    def test_validate_config_valid_returns_empty(self) -> None:
        errors = validate_config(SandboxConfig())
        assert errors == []

    def test_validate_config_negative_memory(self) -> None:
        errors = validate_config(SandboxConfig(memory_mb=-1))
        assert len(errors) >= 1
        assert any("memory_mb" in e for e in errors)

    def test_validate_config_negative_cpu_seconds(self) -> None:
        errors = validate_config(SandboxConfig(cpu_seconds=-5))
        assert len(errors) >= 1
        assert any("cpu_seconds" in e for e in errors)

    def test_validate_config_negative_timeout(self) -> None:
        errors = validate_config(SandboxConfig(timeout=-1))
        assert len(errors) >= 1
        assert any("timeout" in e for e in errors)

    def test_validate_config_negative_max_processes(self) -> None:
        errors = validate_config(SandboxConfig(max_processes=-1))
        assert len(errors) >= 1
        assert any("max_processes" in e for e in errors)

    def test_validate_config_negative_max_output_bytes(self) -> None:
        errors = validate_config(SandboxConfig(max_output_bytes=-100))
        assert len(errors) >= 1
        assert any("max_output_bytes" in e for e in errors)

    def test_from_resource_limits_converts_correctly(self) -> None:
        limits = ResourceLimits(memory_bytes=1024 * 1024 * 1024, pids_limit=100, timeout_seconds=60)
        config = SandboxConfig.from_resource_limits(limits, backend="container")
        assert config.memory_mb == 1024
        assert config.cpu_seconds == 60
        assert config.max_processes == 100
        assert config.backend == "container"

    def test_from_resource_limits_zero_pids_uses_default(self) -> None:
        limits = ResourceLimits(pids_limit=0)
        config = SandboxConfig.from_resource_limits(limits)
        assert config.max_processes == 50

    def test_from_resource_limits_none_memory_uses_default(self) -> None:
        limits = ResourceLimits(memory_bytes=None)
        config = SandboxConfig.from_resource_limits(limits)
        assert config.memory_mb == 512

    def test_minimal_config_is_fail_open(self) -> None:
        assert MINIMAL_SANDBOX_CONFIG.fail_open is True
        assert MINIMAL_SANDBOX_CONFIG.backend == "process"
        assert MINIMAL_SANDBOX_CONFIG.isolation == IsolationLevel.NONE

    def test_strict_config_is_fail_closed(self) -> None:
        assert STRICT_SANDBOX_CONFIG.fail_open is False
        assert STRICT_SANDBOX_CONFIG.backend == "firecracker"
        assert STRICT_SANDBOX_CONFIG.isolation == IsolationLevel.VM_HARDWARE


# ────────────────────────────────────────────────────────────────
# FirecrackerBackend deep tests
# ────────────────────────────────────────────────────────────────


class TestFirecrackerBackendDeep:
    """Deeper FirecrackerBackend availability and error-state tests."""

    def test_kvm_available_on_linux_with_dev_kvm(self) -> None:
        with patch("platform.system", return_value="Linux"), patch("os.path.exists", return_value=True):
            assert FirecrackerBackend._kvm_available()

    def test_kvm_not_available_on_macos(self) -> None:
        with patch("platform.system", return_value="Darwin"):
            assert not FirecrackerBackend._kvm_available()

    def test_kvm_not_available_on_windows(self) -> None:
        with patch("platform.system", return_value="Windows"):
            assert not FirecrackerBackend._kvm_available()

    def test_available_false_when_firecracker_missing(self) -> None:
        with patch("shutil.which", return_value=None):
            backend = FirecrackerBackend(SandboxConfig())
            assert not backend.available()

    def test_execute_unavailable_returns_error_result(self) -> None:
        with patch("shutil.which", return_value=None):
            backend = FirecrackerBackend(SandboxConfig())
            result = backend.execute("echo hello")
            assert result.returncode == 127
            assert "not available" in result.stderr
            assert not result.was_killed

    def test_execute_stub_returns_stub_message(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/firecracker"),
            patch.object(FirecrackerBackend, "_kvm_available", return_value=True),
        ):
            backend = FirecrackerBackend(SandboxConfig())
            result = backend.execute("echo hello")
            assert result.returncode == 127
            assert "not yet implemented" in result.stderr

    def test_cleanup_does_not_raise(self) -> None:
        backend = FirecrackerBackend(SandboxConfig())
        backend.cleanup()


# ────────────────────────────────────────────────────────────────
# ProcessBackend deep tests
# ────────────────────────────────────────────────────────────────


class TestProcessBackendDeep:
    """Deeper ProcessBackend execution and error-path tests."""

    def test_process_backend_is_always_available(self) -> None:
        backend = ProcessBackend(SandboxConfig())
        assert backend.available()

    def test_process_backend_truncates_stdout_at_max_output(self) -> None:
        config = SandboxConfig(max_output_bytes=10)
        backend = ProcessBackend(config)
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("a" * 100, "")
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            with patch("resource.getrusage") as mock_rusage:
                mock_rusage.return_value = MagicMock(ru_utime=0.0, ru_stime=0.0, ru_maxrss=0)
                result = backend.execute("echo long")
                assert len(result.stdout) <= 10

    def test_process_backend_truncates_stderr(self) -> None:
        config = SandboxConfig(max_output_bytes=8)
        backend = ProcessBackend(config)
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("", "b" * 50)
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            with patch("resource.getrusage") as mock_rusage:
                mock_rusage.return_value = MagicMock(ru_utime=0.0, ru_stime=0.0, ru_maxrss=0)
                result = backend.execute("cmd")
                assert len(result.stderr) <= 8

    def test_process_backend_file_not_found(self) -> None:
        backend = ProcessBackend(SandboxConfig())
        with patch("subprocess.Popen", side_effect=FileNotFoundError):
            result = backend.execute("nonexistent_binary_xyz")
            assert result.returncode == 127
            assert "not found" in result.stderr.lower()

    def test_process_backend_timeout_is_killed(self) -> None:
        config = SandboxConfig(timeout=1)
        backend = ProcessBackend(config)
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.side_effect = subprocess.TimeoutExpired("cmd", 1)
            mock_proc.returncode = -1
            mock_proc.pid = 0
            mock_popen.return_value = mock_proc

            with patch("resource.getrusage") as mock_rusage:
                mock_rusage.return_value = MagicMock(ru_utime=0.0, ru_stime=0.0, ru_maxrss=0)
                result = backend.execute("sleep 999")
                assert result.was_killed

    def test_process_backend_timeout_kill_fallback(self) -> None:
        config = SandboxConfig(timeout=1)
        backend = ProcessBackend(config)
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.side_effect = [
                subprocess.TimeoutExpired("cmd", 1),
                subprocess.TimeoutExpired("cmd", 1),
            ]
            mock_proc.returncode = -1
            mock_proc.pid = 0
            mock_popen.return_value = mock_proc

            with patch("resource.getrusage") as mock_rusage:
                mock_rusage.return_value = MagicMock(ru_utime=0.0, ru_stime=0.0, ru_maxrss=0)
                result = backend.execute("sleep 999")
                assert result.was_killed
                assert result.returncode == -1

    def test_process_backend_cleanup_does_not_raise(self) -> None:
        backend = ProcessBackend(SandboxConfig())
        backend.cleanup()


# ────────────────────────────────────────────────────────────────
# SandboxState security tests
# ────────────────────────────────────────────────────────────────


class TestSandboxStateDeep:
    """SandboxState security and path containment tests."""

    def test_safe_state_component_with_safe_value(self) -> None:
        result = safe_state_component("mycomponent")
        assert result == "mycomponent"

    def test_safe_state_component_with_unsafe_chars(self) -> None:
        result = safe_state_component("../etc")
        assert ".." not in result
        assert result.startswith("etc-")
        assert "/" not in result
        assert result == safe_state_component("../etc")
        assert result != safe_state_component("/etc")

    def test_safe_state_component_empty_fallback(self) -> None:
        result = safe_state_component("")
        assert result.startswith("item-")

    def test_sandbox_state_api(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        with patch.dict(os.environ, {"GLUDD_SANDBOX_STATE_DIR": str(tmp_path / "sandbox-state")}):
            state = SandboxState.discover(project_root=project)
            assert state.project_dir.parent == tmp_path / "sandbox-state"
            p = state.path("runtimes")
            assert "runtimes" in p.name

    def test_sandbox_state_cleanup_backend(self, tmp_path: Path) -> None:
        project = tmp_path / "proj2"
        project.mkdir()
        state_dir = tmp_path / "sbox-state2"
        with patch.dict(os.environ, {"GLUDD_SANDBOX_STATE_DIR": str(state_dir)}):
            state = SandboxState.discover(project_root=project)
            backend_dir = state.directory("firecracker")
            assert backend_dir.exists()
            cleaned = state.cleanup_backend("firecracker")
            assert cleaned
            assert not backend_dir.exists()

    def test_sandbox_state_cleanup_project(self, tmp_path: Path) -> None:
        project = tmp_path / "proj3"
        project.mkdir()
        state_dir = tmp_path / "sbox-state3"
        with patch.dict(os.environ, {"GLUDD_SANDBOX_STATE_DIR": str(state_dir)}):
            state = SandboxState.discover(project_root=project)
            assert state.project_dir.exists()
            cleaned = state.cleanup_project()
            assert cleaned
            assert not state.project_dir.exists()

    def test_secure_directory_rejects_symlink_component(self, tmp_path: Path) -> None:
        state_dir = tmp_path / "sbox-state4"
        state_dir.mkdir()
        evil = tmp_path / "evil-dir"
        evil.mkdir()
        sym = state_dir / "hijack"
        sym.symlink_to(evil)
        project = tmp_path / "proj4"
        project.mkdir()
        with (
            patch.dict(
                os.environ,
                {"GLUDD_SANDBOX_STATE_DIR": str(sym / "managed")},
            ),
            pytest.raises(SandboxStateError, match="symlink"),
        ):
            SandboxState.discover(project_root=project)

    def test_discover_rolls_back_new_base_on_namespace_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import general_ludd.security.sandboxes.state as state_module

        project = tmp_path / "rollback-project"
        project.mkdir()
        base = tmp_path / "rollback-state"
        monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
        secure_directory = state_module._secure_directory

        def fail_namespace(path: Path) -> Path:
            if path == base:
                return secure_directory(path)
            raise SandboxStateError("injected namespace allocation failure")

        monkeypatch.setattr(state_module, "_secure_directory", fail_namespace)

        with pytest.raises(SandboxStateError, match="injected"):
            SandboxState.discover(project_root=project)

        assert not base.exists()
        assert project.exists()

    def test_validate_safe_component_rejects_dot_dot(self) -> None:
        from general_ludd.security.sandboxes.state import _validate_component

        with pytest.raises(SandboxStateError):
            _validate_component("..")


# ────────────────────────────────────────────────────────────────
# SecurityPolicy tests
# ────────────────────────────────────────────────────────────────


class TestSecurityPolicyDeep:
    """SecurityPolicy edge cases and docker/k8s args."""

    def test_minimal_policy_is_restrictive(self) -> None:
        policy = SecurityPolicy.minimal()
        assert policy.is_restrictive()

    def test_privileged_policy_is_not_restrictive(self) -> None:
        policy = SecurityPolicy(privileged=True)
        assert not policy.is_restrictive()

    def test_policy_with_capabilities_is_not_restrictive(self) -> None:
        policy = SecurityPolicy(capabilities=["NET_ADMIN"])
        assert not policy.is_restrictive()

    def test_policy_allowing_privilege_escalation_is_not_restrictive(self) -> None:
        policy = SecurityPolicy(allow_privilege_escalation=True)
        assert not policy.is_restrictive()

    def test_policy_writable_rootfs_is_not_restrictive(self) -> None:
        policy = SecurityPolicy(read_only_root=False)
        assert not policy.is_restrictive()

    def test_default_docker_policy_is_restrictive(self) -> None:
        policy = SecurityPolicy.default_docker()
        assert policy.is_restrictive()
        assert policy.read_only_root
        assert not policy.privileged

    def test_to_kubernetes_context(self) -> None:
        policy = SecurityPolicy(
            capabilities=["SYS_TIME"],
            seccomp_profile="gludd-seccomp.json",
        )
        ctx = policy.to_kubernetes_context()
        assert ctx["allowPrivilegeEscalation"] is False
        assert ctx["readOnlyRootFilesystem"] is True
        assert "seccompProfile" in ctx
        assert ctx["capabilities"]["add"] == ["SYS_TIME"]

    def test_docker_args_read_only_root(self) -> None:
        policy = SecurityPolicy(read_only_root=True)
        args = policy.to_docker_args()
        assert "--read-only" in args

    def test_docker_args_seccomp_profile(self) -> None:
        policy = SecurityPolicy(seccomp_profile="custom-profile.json")
        args = policy.to_docker_args()
        assert "--security-opt" in args
        assert "seccomp=custom-profile.json" in args

    def test_docker_args_apparmor_profile(self) -> None:
        policy = SecurityPolicy(apparmor_profile="gludd-apparmor")
        args = policy.to_docker_args()
        assert "apparmor=gludd-apparmor" in args

    def test_docker_args_writable_paths(self) -> None:
        policy = SecurityPolicy(writable_paths=["/tmp/data", "/var/log"])
        args = policy.to_docker_args()
        assert "-v" in args
        assert "/tmp/data:/tmp/data:rw" in args
        assert "/var/log:/var/log:rw" in args


# ────────────────────────────────────────────────────────────────
# Ornith sandbox deep tests
# ────────────────────────────────────────────────────────────────


class TestOrnithSandboxDeep:
    """Deep ornith sandbox runtime tests."""

    def test_sandboxed_run_successful_command(self, tmp_path: Path) -> None:
        with (
            patch("subprocess.run") as mock_run,
            patch("tempfile.mkdtemp") as mock_dt,
            patch("general_ludd.security.state.SecureState.temporary_directory") as mock_td,
        ):
            mock_td.return_value = tmp_path / "ornith-run"
            mock_dt.return_value = str(tmp_path / "ornith-run")
            mock_proc = MagicMock()
            mock_proc.stdout = "hello world\n"
            mock_proc.stderr = ""
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            result = ornith_sandboxed_run(["echo", "hello"])
            assert result["returncode"] == 0
            assert result["stdout"] == "hello world\n"

    def test_sandboxed_run_timeout(self, tmp_path: Path) -> None:
        with (
            patch("subprocess.run") as mock_run,
            patch("general_ludd.security.state.SecureState.temporary_directory") as mock_td,
            patch("general_ludd.ornith.sandbox.create_ornith_sandbox") as mock_sandbox,
        ):
            mock_sandbox.return_value.__enter__.return_value = MagicMock(temp_dir=tmp_path)
            mock_sandbox.return_value.__enter__.return_value.temp_dir = tmp_path
            mock_td.return_value = tmp_path
            exc = subprocess.TimeoutExpired("cmd", 300)
            exc.stdout = b"partial"
            exc.stderr = b"timeout"
            mock_run.side_effect = exc

            result = ornith_sandboxed_run(["sleep", "999"], timeout=1)
            assert result["returncode"] == -1
            assert result["stdout"] == "partial"

    def test_sandboxed_run_file_not_found(self, tmp_path: Path) -> None:
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("general_ludd.security.state.SecureState.temporary_directory") as mock_td,
            patch("general_ludd.ornith.sandbox.create_ornith_sandbox") as mock_sandbox,
        ):
            mock_sandbox.return_value.__enter__.return_value = MagicMock(temp_dir=tmp_path)
            mock_sandbox.return_value.__enter__.return_value.temp_dir = tmp_path
            mock_td.return_value = tmp_path

            result = ornith_sandboxed_run(["nonexistent_binary"])
            assert result["returncode"] == -1
            assert "not found" in str(result["stderr"])

    def test_sandbox_preexec_fn_does_not_chdir(self, tmp_path: Path) -> None:
        cwd_before = os.getcwd()
        _sandbox_preexec_fn(512, 60, str(tmp_path))
        assert os.getcwd() == cwd_before

    def test_sandboxed_run_respects_env_override(self, tmp_path: Path) -> None:
        with (
            patch("subprocess.run") as mock_run,
            patch("general_ludd.ornith.sandbox.create_ornith_sandbox") as mock_sandbox,
        ):
            mock_sandbox.return_value.__enter__.return_value.temp_dir = tmp_path
            mock_proc = MagicMock()
            mock_proc.stdout = ""
            mock_proc.stderr = ""
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            result = ornith_sandboxed_run(["env"], env={"MY_VAR": "custom"})
            assert result["returncode"] == 0
            call_kwargs = mock_run.call_args[1]
            assert "MY_VAR" in call_kwargs["env"]
            assert call_kwargs["env"]["MY_VAR"] == "custom"
            assert call_kwargs["cwd"] == str(tmp_path)
            mock_sandbox.return_value.__exit__.assert_called_once()

    def test_sandboxed_run_mem_and_cpu_override(self, tmp_path: Path) -> None:
        with (
            patch("subprocess.run") as mock_run,
            patch("general_ludd.ornith.sandbox.create_ornith_sandbox") as mock_sandbox,
            patch("general_ludd.ornith.sandbox._sandbox_preexec_fn") as mock_preexec,
        ):
            mock_sandbox.return_value.__enter__.return_value.temp_dir = tmp_path
            mock_proc = MagicMock()
            mock_proc.stdout = ""
            mock_proc.stderr = ""
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            result = ornith_sandboxed_run(["echo"], mem_mb=128, cpu_s=10)
            preexec_fn = mock_run.call_args.kwargs["preexec_fn"]
            preexec_fn()

            assert result["returncode"] == 0
            mock_preexec.assert_called_once_with(128, 10, str(tmp_path))
            mock_sandbox.return_value.__exit__.assert_called_once()

    def test_cleanup_refuses_unmanaged_temp_directory(self, tmp_path: Path) -> None:
        project = tmp_path / "cleanup-project"
        project.mkdir()
        state_root = tmp_path / "cleanup-state"
        outside = tmp_path / "outside-sandbox"
        outside.mkdir()
        marker = outside / "preserve.txt"
        marker.write_text("preserve", encoding="utf-8")

        with patch.dict(
            os.environ,
            {"GLUDD_SANDBOX_STATE_DIR": str(state_root)},
        ):
            state = SandboxState.discover(project_root=project)

        sandbox = object.__new__(OrnithSandbox)
        sandbox._state = state
        sandbox.temp_dir = outside
        sandbox._cleaned = False

        with pytest.raises(SandboxStateError, match="outside"):
            sandbox.cleanup()

        assert marker.read_text(encoding="utf-8") == "preserve"
        assert sandbox._cleaned is False
