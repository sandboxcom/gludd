"""Unit tests for new os_expert knowledge modules.

Covers os_detection, syscall_analysis, and kernel_config modules —
verifying TypedDict shapes, Enum values, and global data lists
are importable and correctly structured.
"""

from __future__ import annotations

from typing import get_type_hints

from general_ludd.os_expert.kernel_config import (
    KERNEL_FEATURES,
    KERNEL_PARAMETERS,
    KernelFeature,
    KernelParameter,
)
from general_ludd.os_expert.os_detection import (
    OS_DETECTION_TABLE,
    OSFamily,
    OSInfo,
    detect_os_family,
)
from general_ludd.os_expert.syscall_analysis import (
    PLATFORM_SYSCALLS,
    SYSCALL_TRACE_TOOLS,
    SyscallEntry,
    SyscallTraceTool,
)

# ── os_detection ────────────────────────────────────────────────────────


def test_os_family_enum_has_expected_members():
    expected = {"LINUX", "MACOS", "WINDOWS", "ANDROID", "IOS", "BSD", "UNKNOWN"}
    actual = {m.name for m in OSFamily}
    assert actual == expected, f"OSFamily members mismatch: {actual - expected}"


def test_os_family_values_match_names():
    for member in OSFamily:
        assert member.value == member.name.lower(), (
            f"OSFamily.{member.name} value {member.value!r} != {member.name.lower()!r}"
        )


def test_os_info_typeddict_has_all_fields():
    hints = get_type_hints(OSInfo)
    for field in ("platform", "name", "version", "arch", "kernel_version", "detection_method"):
        assert field in hints, f"OSInfo missing field: {field}"


def test_os_detection_table_is_list():
    assert isinstance(OS_DETECTION_TABLE, list), "OS_DETECTION_TABLE must be a list"


def test_os_detection_table_entries_are_typeddicts():
    for entry in OS_DETECTION_TABLE:
        for field in ("platform", "name", "version", "arch", "kernel_version", "detection_method"):
            assert field in entry, f"OS_DETECTION_TABLE entry missing field: {field}"


def test_os_detection_table_not_empty():
    assert len(OS_DETECTION_TABLE) > 0, "OS_DETECTION_TABLE must have entries"


def test_detect_os_family_is_callable():
    assert callable(detect_os_family)


def test_detect_os_family_fallback_to_unknown():
    assert detect_os_family("beos") == OSFamily.UNKNOWN
    assert detect_os_family("haiku") == OSFamily.UNKNOWN
    assert detect_os_family("") == OSFamily.UNKNOWN


# ── syscall_analysis ─────────────────────────────────────────────────────


def test_syscall_entry_typeddict_has_all_fields():
    hints = get_type_hints(SyscallEntry)
    for field in ("platform", "name", "number", "category", "description"):
        assert field in hints, f"SyscallEntry missing field: {field}"


def test_platform_syscalls_is_dict():
    assert isinstance(PLATFORM_SYSCALLS, dict), "PLATFORM_SYSCALLS must be a dict"


def test_platform_syscalls_keys_are_strings():
    for key in PLATFORM_SYSCALLS:
        assert isinstance(key, str), f"PLATFORM_SYSCALLS key {key!r} is not a string"


def test_platform_syscalls_values_are_lists_of_typeddicts():
    for platform_key, entries in PLATFORM_SYSCALLS.items():
        assert isinstance(entries, list), f"PLATFORM_SYSCALLS[{platform_key!r}] must be a list"
        for entry in entries:
            for field in ("platform", "name", "number", "category", "description"):
                assert field in entry, f"PLATFORM_SYSCALLS[{platform_key!r}] entry missing field: {field}"


def test_platform_syscalls_not_empty():
    assert len(PLATFORM_SYSCALLS) > 0, "PLATFORM_SYSCALLS must have entries"


def test_platform_syscalls_has_linux():
    linux_keys = [k for k in PLATFORM_SYSCALLS if k.startswith("linux_")]
    assert len(linux_keys) > 0, "PLATFORM_SYSCALLS must have at least one 'linux_*' key"


def test_syscall_trace_tool_typeddict_has_all_fields():
    hints = get_type_hints(SyscallTraceTool)
    for field in ("platform", "tool_name", "trace_command", "parse_command", "output_format"):
        assert field in hints, f"SyscallTraceTool missing field: {field}"


def test_syscall_trace_tools_is_list():
    assert isinstance(SYSCALL_TRACE_TOOLS, list), "SYSCALL_TRACE_TOOLS must be a list"


def test_syscall_trace_tools_entries_are_typeddicts():
    for entry in SYSCALL_TRACE_TOOLS:
        for field in ("platform", "tool_name", "trace_command", "parse_command", "output_format"):
            assert field in entry, f"SYSCALL_TRACE_TOOLS entry missing field: {field}"


def test_syscall_trace_tools_not_empty():
    assert len(SYSCALL_TRACE_TOOLS) > 0, "SYSCALL_TRACE_TOOLS must have entries"


# ── kernel_config ────────────────────────────────────────────────────────


def test_kernel_parameter_typeddict_has_all_fields():
    hints = get_type_hints(KernelParameter)
    for field in ("platform", "name", "path", "expected_type", "default", "description"):
        assert field in hints, f"KernelParameter missing field: {field}"


def test_kernel_parameters_is_list():
    assert isinstance(KERNEL_PARAMETERS, list), "KERNEL_PARAMETERS must be a list"


def test_kernel_parameters_entries_are_typeddicts():
    for entry in KERNEL_PARAMETERS:
        for field in ("platform", "name", "path", "expected_type", "default", "description"):
            assert field in entry, f"KERNEL_PARAMETERS entry missing field: {field}"


def test_kernel_parameters_not_empty():
    assert len(KERNEL_PARAMETERS) > 0, "KERNEL_PARAMETERS must have entries"


def test_kernel_feature_typeddict_has_all_fields():
    hints = get_type_hints(KernelFeature)
    for field in ("platform", "feature_name", "detection_path", "detection_command", "description"):
        assert field in hints, f"KernelFeature missing field: {field}"


def test_kernel_features_is_list():
    assert isinstance(KERNEL_FEATURES, list), "KERNEL_FEATURES must be a list"


def test_kernel_features_entries_are_typeddicts():
    for entry in KERNEL_FEATURES:
        for field in ("platform", "feature_name", "detection_path", "detection_command", "description"):
            assert field in entry, f"KERNEL_FEATURES entry missing field: {field}"


def test_kernel_features_not_empty():
    assert len(KERNEL_FEATURES) > 0, "KERNEL_FEATURES must have entries"
