"""Unit tests for os_expert kernel_config module.

Tests KERNEL_PARAMETERS and KERNEL_FEATURES TypedDict data structures
for shape validity, cross-platform coverage, and data integrity.
"""

from __future__ import annotations

from typing import get_type_hints

from general_ludd.os_expert.kernel_config import (
    KERNEL_FEATURES,
    KERNEL_PARAMETERS,
    KernelFeature,
    KernelParameter,
)

VALID_EXPECTED_TYPES = frozenset({"str", "int", "bool", "float", "dict", "list"})


def test_kernel_parameter_typeddict_has_all_fields():
    hints = get_type_hints(KernelParameter)
    for field in ("platform", "name", "path", "expected_type", "default", "description"):
        assert field in hints, f"KernelParameter missing field: {field}"


def test_kernel_parameters_is_list():
    assert isinstance(KERNEL_PARAMETERS, list), "KERNEL_PARAMETERS must be a list"


def test_kernel_parameters_not_empty():
    assert len(KERNEL_PARAMETERS) > 0, "KERNEL_PARAMETERS must have entries"


def test_kernel_parameters_entries_are_typeddicts():
    for entry in KERNEL_PARAMETERS:
        for field in ("platform", "name", "path", "expected_type", "default", "description"):
            assert field in entry, f"KERNEL_PARAMETERS entry missing field: {field}"


def test_kernel_parameters_expected_types_are_valid():
    for entry in KERNEL_PARAMETERS:
        etype = entry["expected_type"]
        assert etype in VALID_EXPECTED_TYPES, f"Kernel param {entry['name']!r} has bad expected_type: {etype!r}"


def test_kernel_parameters_no_duplicate_names_per_platform():
    seen: dict[tuple[str, str], str] = {}
    for entry in KERNEL_PARAMETERS:
        key = (entry["platform"], entry["name"])
        assert key not in seen, f"Duplicate kernel param {entry['name']!r} on {entry['platform']!r}"
        seen[key] = entry["path"]


def test_kernel_parameters_platform_coverage():
    platforms = {e["platform"] for e in KERNEL_PARAMETERS}
    expected = frozenset({"linux", "macos", "windows", "android"})
    missing = expected - platforms
    assert not missing, f"KERNEL_PARAMETERS missing platforms: {missing}"


def test_kernel_parameters_default_not_none():
    for entry in KERNEL_PARAMETERS:
        assert entry["default"] is not None, f"Kernel param {entry['name']!r} default is None"


def test_kernel_feature_typeddict_has_all_fields():
    hints = get_type_hints(KernelFeature)
    for field in ("platform", "feature_name", "detection_path", "detection_command", "description"):
        assert field in hints, f"KernelFeature missing field: {field}"


def test_kernel_features_is_list():
    assert isinstance(KERNEL_FEATURES, list), "KERNEL_FEATURES must be a list"


def test_kernel_features_not_empty():
    assert len(KERNEL_FEATURES) > 0, "KERNEL_FEATURES must have entries"


def test_kernel_features_entries_are_typeddicts():
    for entry in KERNEL_FEATURES:
        for field in ("platform", "feature_name", "detection_path", "detection_command", "description"):
            assert field in entry, f"KERNEL_FEATURES entry missing field: {field}"


def test_kernel_features_no_duplicate_names_per_platform():
    seen: dict[tuple[str, str], int] = {}
    for i, entry in enumerate(KERNEL_FEATURES):
        key = (entry["platform"], entry["feature_name"])
        assert key not in seen, f"Duplicate feature {entry['feature_name']!r} on {entry['platform']!r}"
        seen[key] = i


def test_kernel_features_platform_coverage():
    platforms = {e["platform"] for e in KERNEL_FEATURES}
    expected = frozenset({"linux", "macos", "windows", "android", "ios"})
    missing = expected - platforms
    assert not missing, f"KERNEL_FEATURES missing platforms: {missing}"


def test_kernel_features_have_descriptions():
    for entry in KERNEL_FEATURES:
        assert len(entry["description"]) > 0, (
            f"Feature {entry['feature_name']!r} on {entry['platform']!r} has empty description"
        )


def test_kernel_parameters_known_linux_keys_exist():
    names = {e["name"] for e in KERNEL_PARAMETERS if e["platform"] == "linux"}
    expected = frozenset(
        {
            "kernel.hostname",
            "kernel.osrelease",
            "kernel.randomize_va_space",
            "kernel.kptr_restrict",
            "kernel.dmesg_restrict",
            "net.ipv4.ip_forward",
            "vm.swappiness",
            "vm.overcommit_memory",
            "fs.file-max",
            "kernel.perf_event_paranoid",
            "kernel.unprivileged_bpf_disabled",
            "kernel.yama.ptrace_scope",
            "net.core.somaxconn",
        }
    )
    missing = expected - names
    assert not missing, f"Missing known Linux params: {missing}"
