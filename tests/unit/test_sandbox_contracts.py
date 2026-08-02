"""Tests for sandbox/contracts.py — isolation levels, backend protocol,
configuration contracts, and resource limits.

Covers:
  - IsolationLevel enum values and comparison ordering
  - SandboxBackend Protocol structural conformance
  - SandboxConfig contract validation
  - ResourceLimits contract validation
  - Helper functions (is_valid_isolation_level, isolation_rank, …)
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.sandbox.contracts import (
    ISOLATION_RANK,
    MINIMAL_SANDBOX_CONFIG,
    STRICT_SANDBOX_CONFIG,
    IsolationLevel,
    SandboxBackend,
    SandboxConfig,
    SandboxResult,
    is_valid_isolation_level,
    isolation_exceeds,
    isolation_rank,
    validate_config,
)
from general_ludd.sandbox.resource_limits import ResourceLimits

# ---------------------------------------------------------------------------
# IsolationLevel
# ---------------------------------------------------------------------------


class TestIsolationLevelEnum:
    def test_members_exist(self) -> None:
        assert hasattr(IsolationLevel, "NONE")
        assert hasattr(IsolationLevel, "PROCESS")
        assert hasattr(IsolationLevel, "CONTAINER")
        assert hasattr(IsolationLevel, "VM_USERSPACE")
        assert hasattr(IsolationLevel, "VM_HARDWARE")

    def test_values_are_unique_strings(self) -> None:
        values = {m.value for m in IsolationLevel}
        assert len(values) == 5

    def test_default_is_none(self) -> None:
        assert IsolationLevel("none") == IsolationLevel.NONE

    def test_from_string_case_insensitive(self) -> None:
        assert IsolationLevel("process") == IsolationLevel.PROCESS
        assert IsolationLevel("PROCESS") == IsolationLevel.PROCESS
        assert IsolationLevel("vm_hardware") == IsolationLevel.VM_HARDWARE
        assert IsolationLevel("VM_HARDWARE") == IsolationLevel.VM_HARDWARE

    def test_from_unknown_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            IsolationLevel("hypervisor")

    def test_str_representation(self) -> None:
        assert str(IsolationLevel.CONTAINER) == "container"
        assert repr(IsolationLevel.VM_HARDWARE) == "IsolationLevel.VM_HARDWARE"


class TestIsolationRank:
    def test_rank_ordering(self) -> None:
        assert isolation_rank(IsolationLevel.NONE) == 2
        assert isolation_rank(IsolationLevel.PROCESS) == 4
        assert isolation_rank(IsolationLevel.CONTAINER) == 6
        assert isolation_rank(IsolationLevel.VM_USERSPACE) == 8
        assert isolation_rank(IsolationLevel.VM_HARDWARE) == 10

    def test_rank_monotonic(self) -> None:
        levels = list(IsolationLevel)
        ranks = [isolation_rank(lev) for lev in levels]
        assert ranks == sorted(ranks)

    def test_isolation_exceeds_strict(self) -> None:
        assert isolation_exceeds(IsolationLevel.VM_HARDWARE, IsolationLevel.CONTAINER)
        assert isolation_exceeds(IsolationLevel.CONTAINER, IsolationLevel.PROCESS)
        assert not isolation_exceeds(IsolationLevel.PROCESS, IsolationLevel.CONTAINER)
        assert not isolation_exceeds(IsolationLevel.CONTAINER, IsolationLevel.VM_HARDWARE)

    def test_isolation_exceeds_equal_is_false(self) -> None:
        assert not isolation_exceeds(IsolationLevel.CONTAINER, IsolationLevel.CONTAINER)
        assert not isolation_exceeds(IsolationLevel.VM_HARDWARE, IsolationLevel.VM_HARDWARE)


class TestIsValidIsolationLevel:
    def test_valid_strings(self) -> None:
        assert is_valid_isolation_level("none") is True
        assert is_valid_isolation_level("process") is True
        assert is_valid_isolation_level("container") is True
        assert is_valid_isolation_level("vm_userspace") is True
        assert is_valid_isolation_level("vm_hardware") is True

    def test_valid_enum_members(self) -> None:
        assert is_valid_isolation_level(IsolationLevel.NONE) is True
        assert is_valid_isolation_level(IsolationLevel.VM_HARDWARE) is True

    def test_invalid_values(self) -> None:
        assert is_valid_isolation_level("hypervisor") is False
        assert is_valid_isolation_level("") is False
        assert is_valid_isolation_level(42) is False
        assert is_valid_isolation_level(None) is False


# ---------------------------------------------------------------------------
# SandboxBackend Protocol
# ---------------------------------------------------------------------------


class _DummyBackend:
    """Concrete backend for protocol conformance tests."""

    name: str = "dummy"

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config

    def available(self) -> bool:
        return True

    def execute(self, command: str, **kwargs: Any) -> SandboxResult:
        return SandboxResult(returncode=0, stdout="ok", stderr="")

    def cleanup(self) -> None:
        pass


class TestSandboxBackendProtocol:
    def test_isinstance_check_passes_for_conforming(self) -> None:
        backend = _DummyBackend(SandboxConfig())
        assert isinstance(backend, SandboxBackend)

    def test_isinstance_check_fails_for_non_conforming(self) -> None:
        class MissingMethods:
            name: str = "bad"

        assert not isinstance(MissingMethods(), SandboxBackend)

    def test_missing_name_fails(self) -> None:
        class NoName:
            def available(self) -> bool:
                return True

            def execute(self, command: str, **kwargs: Any) -> SandboxResult:
                return SandboxResult(returncode=0, stdout="", stderr="")

            def cleanup(self) -> None:
                pass

        assert not isinstance(NoName(), SandboxBackend)

    def test_missing_execute_fails(self) -> None:
        class NoExecute:
            name: str = "bad"

            def available(self) -> bool:
                return True

            def cleanup(self) -> None:
                pass

        assert not isinstance(NoExecute(), SandboxBackend)

    def test_optional_methods_not_required(self) -> None:
        class MinimalBackend:
            name: str = "minimal"

            def available(self) -> bool:
                return True

            def execute(self, command: str, **kwargs: Any) -> SandboxResult:
                return SandboxResult(returncode=0, stdout="", stderr="")

            def cleanup(self) -> None:
                pass

        assert isinstance(MinimalBackend(), SandboxBackend)


# ---------------------------------------------------------------------------
# SandboxConfig contract
# ---------------------------------------------------------------------------


class TestSandboxConfig:
    def test_defaults(self) -> None:
        cfg = SandboxConfig()
        assert cfg.backend == "auto"
        assert cfg.isolation == IsolationLevel.NONE
        assert cfg.memory_mb == 512
        assert cfg.cpu_seconds == 300
        assert cfg.timeout == 300
        assert cfg.max_output_bytes == 1_000_000
        assert cfg.max_processes == 50
        assert cfg.allow_network is False
        assert cfg.fail_open is False
        assert cfg.jail_dir == ""
        assert cfg.image_path == ""
        assert cfg.vsock_port == 0

    def test_custom_config(self) -> None:
        cfg = SandboxConfig(
            backend="firecracker",
            isolation=IsolationLevel.VM_HARDWARE,
            memory_mb=1024,
            cpu_seconds=600,
            timeout=600,
            max_output_bytes=5_000_000,
            max_processes=100,
            allow_network=True,
            fail_open=True,
            jail_dir="/tmp/jail",
            image_path="/var/lib/gludd/rootfs.ext4",
            vsock_port=5000,
        )
        assert cfg.backend == "firecracker"
        assert cfg.isolation == IsolationLevel.VM_HARDWARE
        assert cfg.memory_mb == 1024
        assert cfg.cpu_seconds == 600
        assert cfg.timeout == 600
        assert cfg.max_output_bytes == 5_000_000
        assert cfg.max_processes == 100
        assert cfg.allow_network is True
        assert cfg.fail_open is True
        assert cfg.jail_dir == "/tmp/jail"
        assert cfg.image_path == "/var/lib/gludd/rootfs.ext4"
        assert cfg.vsock_port == 5000

    def test_to_resource_limits(self) -> None:
        cfg = SandboxConfig(memory_mb=256, cpu_seconds=120, max_processes=30)
        limits = cfg.to_resource_limits()
        assert isinstance(limits, ResourceLimits)
        assert limits.memory_bytes == 256 * 1024 * 1024
        assert limits.timeout_seconds == 120
        assert limits.pids_limit == 30

    def test_to_resource_limits_none_memory(self) -> None:
        cfg = SandboxConfig(memory_mb=0)
        limits = cfg.to_resource_limits()
        assert limits.memory_bytes is None

    def test_from_resource_limits(self) -> None:
        limits = ResourceLimits(
            memory_bytes=512 * 1024 * 1024,
            timeout_seconds=180,
            pids_limit=20,
        )
        cfg = SandboxConfig.from_resource_limits(limits, backend="docker", isolation=IsolationLevel.CONTAINER)
        assert cfg.memory_mb == 512
        assert cfg.timeout == 180
        assert cfg.max_processes == 20
        assert cfg.backend == "docker"
        assert cfg.isolation == IsolationLevel.CONTAINER

    def test_from_resource_limits_none_values(self) -> None:
        limits = ResourceLimits()
        cfg = SandboxConfig.from_resource_limits(limits)
        assert cfg.memory_mb == 512
        assert cfg.timeout == 300
        assert cfg.max_processes == 50

    def test_allowed_hosts_list(self) -> None:
        cfg = SandboxConfig(allowed_hosts=["api.github.com", "pypi.org"])
        assert cfg.allowed_hosts == ["api.github.com", "pypi.org"]

    def test_immutable(self) -> None:
        cfg = SandboxConfig(backend="firecracker")
        import dataclasses

        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.backend = "docker"


# ---------------------------------------------------------------------------
# SandboxResult contract
# ---------------------------------------------------------------------------


class TestSandboxResult:
    def test_success_result(self) -> None:
        result = SandboxResult(returncode=0, stdout="hello", stderr="")
        assert result.success is True
        assert result.returncode == 0
        assert result.stdout == "hello"
        assert result.stderr == ""

    def test_failure_result(self) -> None:
        result = SandboxResult(returncode=1, stdout="", stderr="error occurred")
        assert result.success is False
        assert result.returncode == 1
        assert result.stderr == "error occurred"

    def test_with_resource_usage(self) -> None:
        result = SandboxResult(
            returncode=0,
            stdout="done",
            stderr="",
            memory_used_bytes=50 * 1024 * 1024,
            cpu_time_ms=1200,
        )
        assert result.memory_used_bytes == 50 * 1024 * 1024
        assert result.cpu_time_ms == 1200

    def test_json_serializable(self) -> None:
        import json

        result = SandboxResult(returncode=0, stdout="ok", stderr="")
        serialized = json.dumps(
            {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "success": result.success,
            }
        )
        assert '"returncode": 0' in serialized


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------


class TestValidateConfig:
    def test_valid_default_config(self) -> None:
        errors = validate_config(SandboxConfig())
        assert errors == []

    def test_invalid_backend_with_wrong_isolation_does_not_block(self) -> None:
        cfg = SandboxConfig(backend="unknown", isolation=IsolationLevel.CONTAINER)
        errors = validate_config(cfg)
        assert len(errors) == 0

    def test_negative_memory_errors(self) -> None:
        cfg = SandboxConfig(memory_mb=-1)
        errors = validate_config(cfg)
        assert len(errors) > 0
        assert any("memory" in e.lower() for e in errors)

    def test_negative_cpu_errors(self) -> None:
        cfg = SandboxConfig(cpu_seconds=-10)
        errors = validate_config(cfg)
        assert len(errors) > 0
        assert any("cpu" in e.lower() for e in errors)

    def test_zero_timeout_ok(self) -> None:
        cfg = SandboxConfig(timeout=0)
        errors = validate_config(cfg)
        assert len(errors) == 0

    def test_negative_timeout_errors(self) -> None:
        cfg = SandboxConfig(timeout=-300)
        errors = validate_config(cfg)
        assert len(errors) > 0
        assert any("timeout" in e.lower() for e in errors)

    def test_negative_max_processes_errors(self) -> None:
        cfg = SandboxConfig(max_processes=-5)
        errors = validate_config(cfg)
        assert len(errors) > 0
        assert any("process" in e.lower() for e in errors)

    def test_multiple_errors(self) -> None:
        cfg = SandboxConfig(memory_mb=-1, cpu_seconds=-10, max_processes=-5)
        errors = validate_config(cfg)
        assert len(errors) >= 3


# ---------------------------------------------------------------------------
# Preset configurations
# ---------------------------------------------------------------------------


class TestPresetConfigurations:
    def test_minimal_sandbox_config(self) -> None:
        cfg = MINIMAL_SANDBOX_CONFIG
        assert cfg.isolation == IsolationLevel.NONE
        assert cfg.fail_open is True
        assert cfg.backend == "process"

    def test_strict_sandbox_config(self) -> None:
        cfg = STRICT_SANDBOX_CONFIG
        assert cfg.isolation == IsolationLevel.VM_HARDWARE
        assert cfg.fail_open is False
        assert cfg.backend == "firecracker"


# ---------------------------------------------------------------------------
# ISOLATION_RANK constant
# ---------------------------------------------------------------------------


class TestIsolationRankConstant:
    def test_all_members_present(self) -> None:
        for level in IsolationLevel:
            assert level in ISOLATION_RANK

    def test_no_extra_members(self) -> None:
        assert len(ISOLATION_RANK) == len(IsolationLevel)
