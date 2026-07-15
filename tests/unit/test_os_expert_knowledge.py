"""Unit tests for the os_expert collection knowledge modules.

Covers os_events, security_architectures, system_buses, package_management,
and logging_systems — verifying TypedDict shapes, Enum values, and global
data lists are importable and correctly structured.
"""

from __future__ import annotations

from typing import get_type_hints

from general_ludd.os_expert.logging_systems import LOGGING_SYSTEMS, LoggingSystem
from general_ludd.os_expert.os_events import OS_EVENT_MAP, OSEventSource
from general_ludd.os_expert.package_management import PACKAGE_MANAGERS, PackageManager
from general_ludd.os_expert.security_architectures import (
    SECURITY_ARCHITECTURES,
    SecurityArchitecture,
    SecurityLayer,
)
from general_ludd.os_expert.system_buses import SYSTEM_BUSES, SystemBus

# ---------------------------------------------------------------------------
# os_events
# ---------------------------------------------------------------------------

def test_os_event_source_typeddict_has_all_fields():
    hints = get_type_hints(OSEventSource)
    for field in ("platform", "category", "source_name", "log_path", "query_api"):
        assert field in hints, f"OSEventSource missing field: {field}"


def test_os_event_map_is_list():
    assert isinstance(OS_EVENT_MAP, list), "OS_EVENT_MAP must be a list"


def test_os_event_map_entries_are_typeddicts():
    for entry in OS_EVENT_MAP:
        for field in ("platform", "category", "source_name", "log_path", "query_api"):
            assert field in entry, f"OS_EVENT_MAP entry missing field: {field}"


def test_os_event_map_empty_is_valid():
    assert OS_EVENT_MAP == [], "OS_EVENT_MAP should be empty until populated"
    assert isinstance(OS_EVENT_MAP, list)


# ---------------------------------------------------------------------------
# security_architectures
# ---------------------------------------------------------------------------

def test_security_layer_enum_has_expected_members():
    expected = {
        "KERNEL",
        "MANDATORY_ACCESS",
        "CODE_SIGNING",
        "ANTI_MALWARE",
        "FIREWALL",
        "TRUSTED_EXECUTION",
    }
    actual = {m.name for m in SecurityLayer}
    assert actual == expected, f"SecurityLayer members mismatch: {actual - expected}"


def test_security_layer_values_match_names():
    for member in SecurityLayer:
        assert member.value == member.name.lower(), (
            f"SecurityLayer.{member.name} value {member.value!r} != {member.name.lower()!r}"
        )


def test_security_architecture_typeddict_has_all_fields():
    hints = get_type_hints(SecurityArchitecture)
    for field in ("platform", "layer", "name", "config_path", "audit_command"):
        assert field in hints, f"SecurityArchitecture missing field: {field}"


def test_security_architectures_is_list():
    assert isinstance(SECURITY_ARCHITECTURES, list), "SECURITY_ARCHITECTURES must be a list"


def test_security_architectures_entries_are_typeddicts():
    for entry in SECURITY_ARCHITECTURES:
        for field in ("platform", "layer", "name", "config_path", "audit_command"):
            assert field in entry, (
                f"SECURITY_ARCHITECTURES entry missing field: {field}"
            )


def test_security_architectures_empty_is_valid():
    assert SECURITY_ARCHITECTURES == [], (
        "SECURITY_ARCHITECTURES should be empty until populated"
    )
    assert isinstance(SECURITY_ARCHITECTURES, list)


# ---------------------------------------------------------------------------
# system_buses
# ---------------------------------------------------------------------------

def test_system_bus_typeddict_has_all_fields():
    hints = get_type_hints(SystemBus)
    for field in (
        "platform",
        "bus_name",
        "transport",
        "default_address",
        "introspection_tool",
    ):
        assert field in hints, f"SystemBus missing field: {field}"


def test_system_buses_is_list():
    assert isinstance(SYSTEM_BUSES, list), "SYSTEM_BUSES must be a list"


def test_system_buses_entries_are_typeddicts():
    for entry in SYSTEM_BUSES:
        for field in (
            "platform",
            "bus_name",
            "transport",
            "default_address",
            "introspection_tool",
        ):
            assert field in entry, f"SYSTEM_BUSES entry missing field: {field}"


def test_system_buses_empty_is_valid():
    assert SYSTEM_BUSES == [], "SYSTEM_BUSES should be empty until populated"
    assert isinstance(SYSTEM_BUSES, list)


# ---------------------------------------------------------------------------
# package_management
# ---------------------------------------------------------------------------

def test_package_manager_typeddict_has_all_fields():
    hints = get_type_hints(PackageManager)
    for field in (
        "platform",
        "name",
        "format",
        "install_command",
        "query_command",
        "update_command",
        "audit_command",
    ):
        assert field in hints, f"PackageManager missing field: {field}"


def test_package_managers_is_list():
    assert isinstance(PACKAGE_MANAGERS, list), "PACKAGE_MANAGERS must be a list"


def test_package_managers_entries_are_typeddicts():
    for entry in PACKAGE_MANAGERS:
        for field in (
            "platform",
            "name",
            "format",
            "install_command",
            "query_command",
            "update_command",
            "audit_command",
        ):
            assert field in entry, f"PACKAGE_MANAGERS entry missing field: {field}"


def test_package_managers_empty_is_valid():
    assert PACKAGE_MANAGERS == [], (
        "PACKAGE_MANAGERS should be empty until populated"
    )
    assert isinstance(PACKAGE_MANAGERS, list)


# ---------------------------------------------------------------------------
# logging_systems
# ---------------------------------------------------------------------------

def test_logging_system_typeddict_has_all_fields():
    hints = get_type_hints(LoggingSystem)
    for field in (
        "platform",
        "system_name",
        "log_path",
        "query_command",
        "stream_command",
    ):
        assert field in hints, f"LoggingSystem missing field: {field}"


def test_logging_systems_is_list():
    assert isinstance(LOGGING_SYSTEMS, list), "LOGGING_SYSTEMS must be a list"


def test_logging_systems_entries_are_typeddicts():
    for entry in LOGGING_SYSTEMS:
        for field in (
            "platform",
            "system_name",
            "log_path",
            "query_command",
            "stream_command",
        ):
            assert field in entry, f"LOGGING_SYSTEMS entry missing field: {field}"


def test_logging_systems_empty_is_valid():
    assert LOGGING_SYSTEMS == [], "LOGGING_SYSTEMS should be empty until populated"
    assert isinstance(LOGGING_SYSTEMS, list)
