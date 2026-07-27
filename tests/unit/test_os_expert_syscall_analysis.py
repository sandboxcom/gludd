"""Unit tests for os_expert syscall_analysis module.

Tests PLATFORM_SYSCALLS dict, SYSCALL_TRACE_TOOLS list, and their
TypedDict shapes for cross-platform completeness and data integrity.
"""

from __future__ import annotations

from typing import get_type_hints

from general_ludd.os_expert.syscall_analysis import (
    PLATFORM_SYSCALLS,
    SYSCALL_TRACE_TOOLS,
    SyscallEntry,
    SyscallTraceTool,
)

VALID_CATEGORIES = frozenset({"io", "fs", "memory", "process", "network", "signal", "debug"})


def test_syscall_entry_typeddict_has_all_fields():
    hints = get_type_hints(SyscallEntry)
    for field in ("platform", "name", "number", "category", "description"):
        assert field in hints, f"SyscallEntry missing field: {field}"


def test_platform_syscalls_is_dict():
    assert isinstance(PLATFORM_SYSCALLS, dict), "PLATFORM_SYSCALLS must be a dict"


def test_platform_syscalls_not_empty():
    assert len(PLATFORM_SYSCALLS) > 0, "PLATFORM_SYSCALLS must have entries"


def test_platform_syscalls_keys_are_strings():
    for key in PLATFORM_SYSCALLS:
        assert isinstance(key, str), f"PLATFORM_SYSCALLS key {key!r} is not a string"


def test_platform_syscalls_has_expected_platforms():
    expected = frozenset(
        {
            "linux_x86_64",
            "linux_aarch64",
            "macos_x86_64",
            "macos_arm64",
            "windows",
            "android",
            "ios",
        }
    )
    actual = set(PLATFORM_SYSCALLS.keys())
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"PLATFORM_SYSCALLS missing platforms: {missing}"
    assert not extra, f"PLATFORM_SYSCALLS has unexpected platforms: {extra}"


def test_platform_syscalls_values_are_lists_of_typeddicts():
    for platform_key, entries in PLATFORM_SYSCALLS.items():
        assert isinstance(entries, list), f"PLATFORM_SYSCALLS[{platform_key!r}] must be a list"
        assert len(entries) > 0, f"PLATFORM_SYSCALLS[{platform_key!r}] must have entries"
        for entry in entries:
            for field in ("platform", "name", "number", "category", "description"):
                assert field in entry, f"PLATFORM_SYSCALLS[{platform_key!r}] entry missing field: {field}"


def test_syscall_numbers_are_nonnegative():
    for platform_key, entries in PLATFORM_SYSCALLS.items():
        for entry in entries:
            num = entry["number"]
            assert isinstance(num, int), f"{platform_key} {entry['name']!r} number is not int: {num!r}"
            assert num >= 0, f"{platform_key} {entry['name']!r} has negative number: {num}"


def test_syscall_no_duplicate_names_per_platform():
    for platform_key, entries in PLATFORM_SYSCALLS.items():
        names = [e["name"] for e in entries]
        seen = set()
        for name in names:
            assert name not in seen, f"Duplicate syscall {name!r} in {platform_key}"
            seen.add(name)


def test_syscall_categories_are_valid():
    for platform_key, entries in PLATFORM_SYSCALLS.items():
        for entry in entries:
            cat = entry["category"]
            assert cat in VALID_CATEGORIES, f"{platform_key} {entry['name']!r} has bad category: {cat!r}"


def test_syscall_platform_field_matches_containing_key():
    for platform_key, entries in PLATFORM_SYSCALLS.items():
        for entry in entries:
            assert entry["platform"] == platform_key, (
                f"Entry {entry['name']!r} has platform {entry['platform']!r} but is in key {platform_key!r}"
            )


def test_syscall_trace_tool_typeddict_has_all_fields():
    hints = get_type_hints(SyscallTraceTool)
    for field in ("platform", "tool_name", "trace_command", "parse_command", "output_format"):
        assert field in hints, f"SyscallTraceTool missing field: {field}"


def test_syscall_trace_tools_is_list():
    assert isinstance(SYSCALL_TRACE_TOOLS, list), "SYSCALL_TRACE_TOOLS must be a list"


def test_syscall_trace_tools_not_empty():
    assert len(SYSCALL_TRACE_TOOLS) > 0, "SYSCALL_TRACE_TOOLS must have entries"


def test_syscall_trace_tools_entries_are_typeddicts():
    for entry in SYSCALL_TRACE_TOOLS:
        for field in ("platform", "tool_name", "trace_command", "parse_command", "output_format"):
            assert field in entry, f"SYSCALL_TRACE_TOOLS entry missing field: {field}"


def test_syscall_trace_tools_valid_output_formats():
    for entry in SYSCALL_TRACE_TOOLS:
        fmt = entry["output_format"]
        assert fmt in frozenset({"text", "csv", "json", "xml"}), (
            f"Trace tool {entry['tool_name']!r} has bad output_format: {fmt!r}"
        )


def test_syscall_trace_tools_no_duplicate_tool_names_per_platform():
    seen: dict[tuple[str, str], int] = {}
    for i, entry in enumerate(SYSCALL_TRACE_TOOLS):
        key = (entry["platform"], entry["tool_name"])
        assert key not in seen, f"Duplicate trace tool {entry['tool_name']!r} on {entry['platform']!r}"
        seen[key] = i


def test_syscall_trace_tools_platform_coverage():
    platforms = {e["platform"] for e in SYSCALL_TRACE_TOOLS}
    expected = frozenset({"linux", "macos", "windows", "android", "ios"})
    missing = expected - platforms
    assert not missing, f"SYSCALL_TRACE_TOOLS missing platforms: {missing}"
