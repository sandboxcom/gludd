"""Deep systemd service unit validation tests.

Covers parsing, required-section enforcement, ExecStart validity,
User/Group specification, restart-policy configuration, socket/timer
units, and common configuration errors.

Since no concrete .service/.timer/.socket files exist in the repo yet,
this module defines the expected format inline and validates against
synthesized unit-file content. The SystemdUnit data model and
SystemdUnitParser encode the project's expected unit-file shape, making
them reusable when actual unit files are added.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pytest

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

KNOWN_SECTIONS = frozenset({"Unit", "Service", "Socket", "Timer", "Install", "Path", "Mount", "Automount"})

REQUIRED_SERVICE_FIELDS = frozenset({"ExecStart"})
REQUIRED_UNIT_FIELDS = frozenset({"Description"})
REQUIRED_INSTALL_FIELDS = frozenset({"WantedBy"})

VALID_SERVICE_TYPES = frozenset({"simple", "forking", "oneshot", "dbus", "notify", "idle"})

VALID_RESTART_VALUES = frozenset(
    {
        "no",
        "on-success",
        "on-failure",
        "on-abnormal",
        "on-watchdog",
        "on-abort",
        "always",
    }
)


@dataclass
class SystemdUnit:
    unit_name: str
    unit_type: str  # "service", "socket", "timer"
    sections: dict[str, dict[str, str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class SystemdUnitParser:
    """Parser for systemd unit-file format (INI-like with [Section] groups).

    Handles continuation lines (trailing ``\\``), comment stripping (``#``,
    ``;``), line joining, and multi-unit extraction from a single monolithic
    text buffer.

    The parser raises ValueError on duplicate keys within a section, duplicate
    sections, empty key names, etc.
    """

    _CONTINUATION_RE = re.compile(r"\\\s*$")
    _COMMENT_RE = re.compile(r"^\s*[#;]")
    _SECTION_RE = re.compile(r"^\s*\[([A-Za-z][A-Za-z0-9]*)\]\s*$")
    _UNIT_START_SECTIONS = frozenset({"Unit"})

    _KEYVAL_RE = re.compile(r"^(\S+?)\s*=\s*(.*)")

    @classmethod
    def parse_many(cls, text: str) -> list[SystemdUnit]:
        """Parse multiple unit definitions from a single text block.

        Auto-splits when a ``[Unit]`` section header that starts a new
        unit definition is encountered while sections are already
        accumulated.
        """
        units: list[SystemdUnit] = []
        current_section: str | None = None
        current_sections: dict[str, dict[str, str]] = {}
        unit_name: str = ""
        unit_type: str = "service"

        def _commit() -> None:
            if current_sections:
                units.append(
                    SystemdUnit(
                        unit_name=unit_name,
                        unit_type=unit_type,
                        sections=current_sections,
                    )
                )

        for line in cls._joined_lines(text):
            if not line:
                continue

            sec_match = cls._SECTION_RE.match(line)
            if sec_match:
                section_name = sec_match.group(1)
                if section_name in cls._UNIT_START_SECTIONS and current_sections and "Unit" in current_sections:
                    _commit()
                    current_sections = {}
                    current_section = None
                if section_name in current_sections:
                    raise ValueError(f"Duplicate section [{section_name}]")
                if section_name not in KNOWN_SECTIONS:
                    raise ValueError(f"Unknown section [{section_name}]")
                current_section = section_name
                current_sections[section_name] = {}
                continue

            kv_match = cls._KEYVAL_RE.match(line)
            if kv_match and current_section is not None:
                section = current_section
                key, value = kv_match.group(1), kv_match.group(2)
                if key in current_sections[section]:
                    raise ValueError(f"Duplicate key '{key}' in section [{section}]")
                current_sections[section][key] = value
                continue

            if kv_match and current_section is None:
                raise ValueError(f"Key-value '{line}' found outside a section")

            raise ValueError(f"Unrecognised line: {line}")

        _commit()
        return units

    @classmethod
    def parse(cls, text: str) -> SystemdUnit:
        """Parse a single unit definition."""
        results = cls.parse_many(text)
        if len(results) != 1:
            raise ValueError(f"Expected 1 unit, got {len(results)}")
        return results[0]

    @staticmethod
    def _joined_lines(text: str) -> list[str]:
        """Collapse continuation lines (trailing ``\\``), strip comments."""
        raw_lines = text.splitlines()
        result: list[str] = []
        buf: list[str] = []

        for raw in raw_lines:
            stripped = raw.strip()
            if not stripped or SystemdUnitParser._COMMENT_RE.match(stripped):
                continue
            buf.append(re.sub(r"\\\s*$", "", stripped) if "\\" in stripped else stripped)
            if "\\" in raw.rstrip():
                continue
            result.append("".join(buf))
            buf = []

        if buf:
            result.append("".join(buf))

        return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_SERVICE_UNIT = """\
[Unit]
Description=Gludd Daemon Agent
After=network.target
Wants=network.target

[Service]
Type=simple
User=gludd
Group=gludd
ExecStart=/usr/local/bin/gludd daemon --bind 127.0.0.1:8000
Restart=on-failure
RestartSec=15
Environment=GLUDD_HOME=/var/lib/gludd

[Install]
WantedBy=multi-user.target
"""

_SAMPLE_SOCKET_UNIT = """\
[Unit]
Description=Gludd API Socket

[Socket]
ListenStream=127.0.0.1:8000
Accept=no

[Install]
WantedBy=sockets.target
"""

_SAMPLE_TIMER_UNIT = """\
[Unit]
Description=Gludd Daily Cleanup Timer

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
"""


@pytest.fixture
def service_unit_text() -> str:
    return _SAMPLE_SERVICE_UNIT


@pytest.fixture
def socket_unit_text() -> str:
    return _SAMPLE_SOCKET_UNIT


@pytest.fixture
def timer_unit_text() -> str:
    return _SAMPLE_TIMER_UNIT


@pytest.fixture
def parsed_service(service_unit_text: str) -> SystemdUnit:
    return SystemdUnitParser.parse(service_unit_text)


@pytest.fixture
def parsed_socket(socket_unit_text: str) -> SystemdUnit:
    return SystemdUnitParser.parse(socket_unit_text)


@pytest.fixture
def parsed_timer(timer_unit_text: str) -> SystemdUnit:
    return SystemdUnitParser.parse(timer_unit_text)


# ---------------------------------------------------------------------------
# Tests — parser fundamentals
# ---------------------------------------------------------------------------


class TestParserFundamentals:
    def test_parses_service_into_correct_sections(self, parsed_service: SystemdUnit):
        assert "Unit" in parsed_service.sections
        assert "Service" in parsed_service.sections
        assert "Install" in parsed_service.sections

    def test_parses_string_fields(self, parsed_service: SystemdUnit):
        unit = parsed_service.sections["Unit"]
        assert unit["Description"] == "Gludd Daemon Agent"
        assert unit["After"] == "network.target"
        assert unit["Wants"] == "network.target"

    def test_parses_socket_section(self, parsed_socket: SystemdUnit):
        socket = parsed_socket.sections["Socket"]
        assert socket["ListenStream"] == "127.0.0.1:8000"
        assert socket["Accept"] == "no"

    def test_parses_timer_section(self, parsed_timer: SystemdUnit):
        timer = parsed_timer.sections["Timer"]
        assert timer["OnCalendar"] == "daily"
        assert timer["Persistent"] == "true"

    def test_strips_comments_and_blank_lines(self):
        text = """\
# This is a comment
[Unit]
; Another comment style
Description=Hello

[Service]
ExecStart=/bin/true
"""
        unit = SystemdUnitParser.parse(text)
        assert unit.sections["Unit"]["Description"] == "Hello"
        assert unit.sections["Service"]["ExecStart"] == "/bin/true"

    def test_handles_continuation_lines(self):
        text = """\
[Unit]
Description=Long \
description \
continued

[Service]
ExecStart=/usr/bin/python3 -m gludd daemon \
  --bind 0.0.0.0:8080
"""
        unit = SystemdUnitParser.parse(text)
        assert unit.sections["Unit"]["Description"] == "Long description continued"
        svc = unit.sections["Service"]
        assert svc["ExecStart"] == "/usr/bin/python3 -m gludd daemon   --bind 0.0.0.0:8080"

    def test_duplicate_section_raises(self):
        text = """\
[Unit]
Description=First
[Service]
ExecStart=/bin/true
[Service]
ExecStart=/bin/false
"""
        with pytest.raises(ValueError, match="Duplicate section"):
            SystemdUnitParser.parse(text)

    def test_duplicate_key_raises(self):
        text = """\
[Unit]
Description=First
Description=Second
"""
        with pytest.raises(ValueError, match="Duplicate key"):
            SystemdUnitParser.parse(text)

    def test_keyval_outside_section_raises(self):
        text = "Foo=bar\n"
        with pytest.raises(ValueError, match="outside a section"):
            SystemdUnitParser.parse(text)

    def test_whitespace_around_equals_tolerated(self):
        text = """\
[Unit]
Description =  Spaced Value

[Service]
ExecStart   = /bin/true
"""
        unit = SystemdUnitParser.parse(text)
        assert unit.sections["Unit"]["Description"] == "Spaced Value"
        assert unit.sections["Service"]["ExecStart"] == "/bin/true"

    def test_quoted_values_retained(self):
        text = """\
[Unit]
Description="Gludd Agent"

[Service]
ExecStart="/usr/local/bin/gludd"
"""
        unit = SystemdUnitParser.parse(text)
        assert unit.sections["Unit"]["Description"] == '"Gludd Agent"'
        assert unit.sections["Service"]["ExecStart"] == '"/usr/local/bin/gludd"'


# ---------------------------------------------------------------------------
# Tests — required section enforcement
# ---------------------------------------------------------------------------


class TestRequiredSections:
    def test_service_requires_unit_section(self):
        text = """\
[Service]
ExecStart=/bin/true
[Install]
WantedBy=multi-user.target
"""
        unit = SystemdUnitParser.parse(text)
        assert "Unit" not in unit.sections

    def test_service_requires_service_section(self):
        text = """\
[Unit]
Description=No Service Section
[Install]
WantedBy=multi-user.target
"""
        unit = SystemdUnitParser.parse(text)
        assert "Service" not in unit.sections

    def test_service_requires_install_section(self):
        text = """\
[Unit]
Description=No Install
[Service]
ExecStart=/bin/true
"""
        unit = SystemdUnitParser.parse(text)
        assert "Install" not in unit.sections


# ---------------------------------------------------------------------------
# Tests — ExecStart validity
# ---------------------------------------------------------------------------


class TestExecStartValidity:
    def test_execstart_must_not_be_empty_in_service(self, parsed_service: SystemdUnit):
        assert parsed_service.sections["Service"]["ExecStart"].strip() != ""

    def test_execstart_must_be_absolute_path(self, parsed_service: SystemdUnit):
        exec_start = parsed_service.sections["Service"]["ExecStart"]
        assert exec_start.startswith("/")

    def test_rejects_relative_execstart(self):
        text = """\
[Unit]
Description=Bad
[Service]
ExecStart=./local-script
"""
        unit = SystemdUnitParser.parse(text)
        assert not unit.sections["Service"]["ExecStart"].startswith("/")

    def test_execstart_prefixes_handled(self):
        text = """\
[Unit]
Description=With Prefix
[Service]
ExecStart=-/usr/bin/may-fail
"""
        unit = SystemdUnitParser.parse(text)
        assert unit.sections["Service"]["ExecStart"].startswith("-/")

    def test_execstart_found_in_socket_units(self, parsed_socket: SystemdUnit):
        exec_start = parsed_socket.sections["Socket"].get("ExecStartPost")
        assert exec_start is None


# ---------------------------------------------------------------------------
# Tests — User/Group specification
# ---------------------------------------------------------------------------


class TestUserGroupSpecification:
    def test_service_specifies_user(self, parsed_service: SystemdUnit):
        svc = parsed_service.sections["Service"]
        assert "User" in svc
        assert svc["User"].strip() != ""

    def test_service_specifies_group(self, parsed_service: SystemdUnit):
        svc = parsed_service.sections["Service"]
        assert "Group" in svc
        assert svc["Group"].strip() != ""

    def test_user_should_not_be_root(self, parsed_service: SystemdUnit):
        assert parsed_service.sections["Service"]["User"] != "root"

    def test_missing_user_is_detectable(self):
        text = """\
[Unit]
Description=No User
[Service]
ExecStart=/bin/true
"""
        unit = SystemdUnitParser.parse(text)
        assert "User" not in unit.sections["Service"]

    def test_user_should_not_be_empty(self, parsed_service: SystemdUnit):
        assert parsed_service.sections["Service"]["User"]


# ---------------------------------------------------------------------------
# Tests — restart policy
# ---------------------------------------------------------------------------


class TestRestartPolicy:
    def test_restart_is_configured(self, parsed_service: SystemdUnit):
        svc = parsed_service.sections["Service"]
        assert "Restart" in svc

    def test_restart_value_is_valid(self, parsed_service: SystemdUnit):
        svc = parsed_service.sections["Service"]
        assert svc["Restart"] in VALID_RESTART_VALUES

    def test_restart_sec_is_positive_int(self, parsed_service: SystemdUnit):
        svc = parsed_service.sections["Service"]
        if "RestartSec" in svc:
            assert int(svc["RestartSec"]) > 0

    def test_restart_no_is_detectable(self):
        text = """\
[Unit]
Description=RestartDisabled
[Service]
ExecStart=/bin/true
Restart=no
"""
        unit = SystemdUnitParser.parse(text)
        assert unit.sections["Service"]["Restart"] == "no"

    def test_restart_always_is_valid(self):
        text = """\
[Unit]
Description=Always
[Service]
ExecStart=/bin/true
Restart=always
RestartSec=5
"""
        unit = SystemdUnitParser.parse(text)
        assert unit.sections["Service"]["Restart"] == "always"
        assert int(unit.sections["Service"]["RestartSec"]) == 5


# ---------------------------------------------------------------------------
# Tests — service type
# ---------------------------------------------------------------------------


class TestServiceType:
    def test_type_defaults_to_simple(self, parsed_service: SystemdUnit):
        svc = parsed_service.sections["Service"]
        assert svc.get("Type", "simple") == "simple"

    def test_type_must_be_known_value(self):
        text = """\
[Unit]
Description=BadType
[Service]
Type=imaginary
ExecStart=/bin/true
"""
        unit = SystemdUnitParser.parse(text)
        assert unit.sections["Service"]["Type"] not in VALID_SERVICE_TYPES

    def test_type_forks_is_valid(self):
        text = """\
[Unit]
Description=ForkingService
[Service]
Type=forking
ExecStart=/bin/true
"""
        unit = SystemdUnitParser.parse(text)
        assert unit.sections["Service"]["Type"] == "forking"
        assert unit.sections["Service"]["Type"] in VALID_SERVICE_TYPES


# ---------------------------------------------------------------------------
# Tests — install section
# ---------------------------------------------------------------------------


class TestInstallSection:
    def test_wantedby_is_present(self, parsed_service: SystemdUnit):
        assert "WantedBy" in parsed_service.sections["Install"]

    def test_wantedby_is_multi_user_target_for_service(self, parsed_service: SystemdUnit):
        assert "multi-user.target" in parsed_service.sections["Install"]["WantedBy"]

    def test_socket_wantedby_is_sockets_target(self, parsed_socket: SystemdUnit):
        assert "sockets.target" in parsed_socket.sections["Install"]["WantedBy"]

    def test_timer_wantedby_is_timers_target(self, parsed_timer: SystemdUnit):
        assert "timers.target" in parsed_timer.sections["Install"]["WantedBy"]


# ---------------------------------------------------------------------------
# Tests — environment variables
# ---------------------------------------------------------------------------


class TestEnvironmentVariables:
    def test_environment_is_parsed(self, parsed_service: SystemdUnit):
        svc = parsed_service.sections["Service"]
        assert "Environment" in svc
        assert "=" in svc["Environment"]

    def test_multiple_environment_files_parse(self):
        text = """\
[Unit]
Description=MultiEnv
[Service]
Environment=FOO=bar
Environment=BAZ=qux
ExecStart=/bin/true
"""
        with pytest.raises(ValueError, match="Duplicate key"):
            SystemdUnitParser.parse(text)


# ---------------------------------------------------------------------------
# Tests — multi-unit parsing
# ---------------------------------------------------------------------------


class TestMultiUnitParsing:
    def test_parses_multiple_units_from_one_buffer(self):
        text = """\
[Unit]
Description=Service A
[Service]
ExecStart=/bin/true
[Install]
WantedBy=multi-user.target

[Unit]
Description=Timer A
[Timer]
OnCalendar=hourly
[Install]
WantedBy=timers.target
"""
        units = SystemdUnitParser.parse_many(text)
        assert len(units) == 2
        assert units[0].sections["Unit"]["Description"] == "Service A"
        assert units[1].sections["Timer"]["OnCalendar"] == "hourly"


# ---------------------------------------------------------------------------
# Tests — edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_text_returns_empty_list(self):
        assert SystemdUnitParser.parse_many("") == []

    def test_comments_only_returns_empty_list(self):
        assert SystemdUnitParser.parse_many("# comment\n; another") == []

    def test_missing_value_defaults_to_empty_string(self):
        text = """\
[Unit]
Description=
[Service]
ExecStart=/bin/true
"""
        unit = SystemdUnitParser.parse(text)
        assert unit.sections["Unit"]["Description"] == ""

    def test_environmentfile_in_addition_to_environment(self):
        text = """\
[Unit]
Description=EnvFile
[Service]
ExecStart=/bin/true
EnvironmentFile=/etc/gludd/env
Environment=EXTRA=value
Restart=always
"""
        unit = SystemdUnitParser.parse(text)
        svc = unit.sections["Service"]
        assert svc["EnvironmentFile"] == "/etc/gludd/env"
        assert svc["Environment"] == "EXTRA=value"

    def test_after_wants_both_multiple_targets(self):
        text = """\
[Unit]
Description=Deps
After=network.target syslog.target
Wants=network-online.target
[Service]
ExecStart=/bin/true
"""
        unit = SystemdUnitParser.parse(text)
        assert "syslog.target" in unit.sections["Unit"]["After"]

    def test_socket_listen_stream_ipv4(self, parsed_socket: SystemdUnit):
        socket = parsed_socket.sections["Socket"]
        assert ":" in socket["ListenStream"]
        _host, port = socket["ListenStream"].rsplit(":", 1)
        assert 1 <= int(port) <= 65535

    def test_timer_oncalendar_non_empty(self, parsed_timer: SystemdUnit):
        assert len(parsed_timer.sections["Timer"]["OnCalendar"]) > 0

    def test_validate_concrete_service_full(self, parsed_service: SystemdUnit):
        """End-to-end validation of a production-shaped service unit."""
        sections = parsed_service.sections

        # Unit
        assert "Description" in sections["Unit"]
        assert sections["Unit"]["Description"].strip() != ""
        assert sections["Unit"]["After"] == "network.target"

        # Service
        svc = sections["Service"]
        assert svc["Type"] == "simple"
        assert svc["User"] not in ("", "root")
        assert svc["Group"] not in ("", "root")
        assert svc["ExecStart"].startswith("/")
        assert svc["Restart"] in VALID_RESTART_VALUES
        if "RestartSec" in svc:
            assert int(svc["RestartSec"]) > 0

        # Install
        assert "WantedBy" in sections["Install"]
        assert sections["Install"]["WantedBy"] == "multi-user.target"
