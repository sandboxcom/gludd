"""Unit tests for os_expert os_detection module.

Tests OSFamily enum, OSInfo TypedDict, OS_DETECTION_TABLE, and
detect_os_family function across all supported platforms.
"""

from __future__ import annotations

from typing import get_type_hints

from general_ludd.os_expert.os_detection import (
    OS_DETECTION_TABLE,
    OSFamily,
    OSInfo,
    detect_os_family,
)


def test_os_family_enum_has_expected_members():
    expected = {"LINUX", "MACOS", "WINDOWS", "ANDROID", "IOS", "BSD", "UNKNOWN"}
    actual = {m.name for m in OSFamily}
    assert actual == expected, f"OSFamily members mismatch: {actual - expected}"


def test_os_family_values_match_names():
    for member in OSFamily:
        assert member.value == member.name.lower(), (
            f"OSFamily.{member.name} value {member.value!r} != {member.name.lower()!r}"
        )


def test_os_family_enum_is_comparable():
    assert OSFamily.LINUX == OSFamily.LINUX
    assert OSFamily.LINUX != OSFamily.MACOS
    assert OSFamily.UNKNOWN != OSFamily.LINUX


def test_os_info_typeddict_has_all_fields():
    hints = get_type_hints(OSInfo)
    for field in ("platform", "name", "version", "arch", "kernel_version", "detection_method"):
        assert field in hints, f"OSInfo missing field: {field}"


def test_os_detection_table_is_list():
    assert isinstance(OS_DETECTION_TABLE, list), "OS_DETECTION_TABLE must be a list"


def test_os_detection_table_not_empty():
    assert len(OS_DETECTION_TABLE) > 0, "OS_DETECTION_TABLE must have entries"


def test_os_detection_table_entries_are_typeddicts():
    for entry in OS_DETECTION_TABLE:
        for field in ("platform", "name", "version", "arch", "kernel_version", "detection_method"):
            assert field in entry, f"OS_DETECTION_TABLE entry missing field: {field}"


def test_os_detection_table_no_duplicate_platforms():
    platforms = [e["platform"] for e in OS_DETECTION_TABLE]
    assert len(platforms) == len(set(platforms)), f"Duplicate platforms in OS_DETECTION_TABLE: {platforms}"


def test_os_detection_table_covers_expected_platforms():
    platforms = {e["platform"] for e in OS_DETECTION_TABLE}
    expected = frozenset({"linux", "macos", "windows", "android", "ios", "freebsd", "openbsd", "netbsd"})
    missing = expected - platforms
    assert not missing, f"OS_DETECTION_TABLE missing platforms: {missing}"


def test_detect_os_family_linux_exact():
    assert detect_os_family("linux") == OSFamily.LINUX


def test_detect_os_family_darwin():
    assert detect_os_family("darwin") == OSFamily.MACOS


def test_detect_os_family_macos_aliases():
    assert detect_os_family("macos") == OSFamily.MACOS
    assert detect_os_family("mac os x") == OSFamily.MACOS


def test_detect_os_family_windows_variants():
    assert detect_os_family("windows") == OSFamily.WINDOWS
    assert detect_os_family("win32") == OSFamily.WINDOWS
    assert detect_os_family("win64") == OSFamily.WINDOWS


def test_detect_os_family_android():
    assert detect_os_family("android") == OSFamily.ANDROID


def test_detect_os_family_ios():
    assert detect_os_family("ios") == OSFamily.IOS


def test_detect_os_family_bsd_variants():
    assert detect_os_family("freebsd") == OSFamily.BSD
    assert detect_os_family("openbsd") == OSFamily.BSD
    assert detect_os_family("netbsd") == OSFamily.BSD
    assert detect_os_family("dragonfly") == OSFamily.BSD


def test_detect_os_family_fallback_to_unknown():
    assert detect_os_family("beos") == OSFamily.UNKNOWN
    assert detect_os_family("haiku") == OSFamily.UNKNOWN
    assert detect_os_family("") == OSFamily.UNKNOWN
    assert detect_os_family("solaris") == OSFamily.UNKNOWN
    assert detect_os_family("amigaos") == OSFamily.UNKNOWN


def test_detect_os_family_case_insensitive():
    assert detect_os_family("LINUX") == OSFamily.LINUX
    assert detect_os_family("Darwin") == OSFamily.MACOS
    assert detect_os_family("Windows") == OSFamily.WINDOWS


def test_detect_os_family_none_uses_platform():
    result = detect_os_family()
    assert isinstance(result, OSFamily)
    assert result != OSFamily.UNKNOWN
