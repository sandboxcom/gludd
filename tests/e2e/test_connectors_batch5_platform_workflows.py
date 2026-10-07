"""Tests split from :mod:`tests.e2e.test_connectors_batch5_workflows` by coherent behavior."""

from __future__ import annotations

import json as _json
import os

import pytest

from tests.e2e.test_connectors_batch5_workflows import (
    MockHttpResponse,
    MockHttpTransport,
)
from tests.e2e.test_connectors_batch5_workflows import TestPodmanConnector as _SplitBaseTestPodmanConnector


class TestSnmpConnector:
    def test_constructs_with_no_config(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource()
        assert source.KIND == "metrics"
        assert source.name == "snmp"

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource(
            {
                "host": "192.168.1.1",
                "community": "public",
                "oid": "1.3.6.1.2.1.1.3",
                "name": "router-uptime",
            }
        )
        assert source.name == "router-uptime"

    def test_health_ok_with_injected_getter(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource(getter=lambda host, community, oid: (None, None))
        result = source.health()
        assert isinstance(result, dict)

    def test_health_not_ok_when_getter_fails(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        def _fail(*_: object, **__: object) -> object:
            raise OSError("timeout")

        source = SnmpSource({"host": "192.168.1.1"}, getter=_fail)
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_records(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        def _getter(host: str, community: str, oid: str) -> tuple[object, object]:
            return ("1.3.6.1.2.1.1.3.0", "12345678")

        source = SnmpSource({"host": "192.168.1.1"}, getter=_getter)
        records = source.query({"oid": "1.3.6.1.2.1.1.3"})
        assert len(records) >= 1
        assert records[0]["kind"] == "metrics"


# ============================================================================
# 10. Windows Defender Connector
# ============================================================================


class TestWindowsDefenderConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        source = WindowsDefenderSource()
        assert source.KIND == "logs"

    def test_constructs_custom_name(self, monkeypatch):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        source = WindowsDefenderSource({"name": "defender-audit"})
        assert source.name == "defender-audit"

    def test_health_ok_with_runner(self, monkeypatch):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        source = WindowsDefenderSource(runner=lambda cmd: _json.dumps([{"ThreatName": "", "ActionSuccess": True}]))
        result = source.health()
        assert result["ok"] is True

    def test_query_returns_detections(self, monkeypatch):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        def _runner(cmd: str) -> str:
            return _json.dumps(
                [
                    {
                        "ThreatName": "Trojan:Win32/Test",
                        "Severity": "5",
                        "ActionSuccess": False,
                        "InitialDetectionTime": "2025-01-01T12:00:00Z",
                    }
                ]
            )

        source = WindowsDefenderSource(runner=_runner)
        records = source.query({})
        assert len(records) >= 1
        assert records[0]["kind"] == "logs"

    def test_query_empty_on_error(self, monkeypatch):
        from general_ludd.connectors.windows_defender import WindowsDefenderSource

        def _runner(cmd: str) -> str:
            raise RuntimeError("powershell not found")

        source = WindowsDefenderSource(runner=_runner)
        records = source.query({})
        assert records == []


# ============================================================================
# 11. Windows Event Log Connector
# ============================================================================


class TestWindowsEventLogConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        source = WindowsEventLogSource()
        assert source.KIND == "logs"

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        source = WindowsEventLogSource(
            {
                "log_name": "Security",
                "name": "sec-log",
                "max_events": 100,
            }
        )
        assert source.name == "sec-log"

    def test_health_ok_with_runner(self, monkeypatch):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        source = WindowsEventLogSource(runner=lambda cmd: _json.dumps([{"Id": "1"}]))
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_when_runner_unavailable(self, monkeypatch):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        source = WindowsEventLogSource()
        result = source.health()
        assert isinstance(result, dict)

    def test_query_returns_events(self, monkeypatch):
        from general_ludd.connectors.windows_event_log import WindowsEventLogSource

        def _runner(cmd: str) -> str:
            return _json.dumps(
                [
                    {
                        "Id": 4624,
                        "LevelDisplayName": "Information",
                        "TimeCreated": "2025-01-01T12:00:00.0000000Z",
                        "Message": "An account was successfully logged on",
                        "ProviderName": "Microsoft-Windows-Security-Auditing",
                    }
                ]
            )

        source = WindowsEventLogSource({"log_name": "Security"}, runner=_runner)
        records = source.query({})
        assert len(records) >= 1
        assert records[0]["kind"] == "logs"
        assert "4624" in str(records[0]["message"])


# ============================================================================
# 12. Windows WMI Connector
# ============================================================================


class TestWindowsWmiConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.windows_wmi import WindowsWmiSource

        source = WindowsWmiSource()
        assert source.KIND == "metrics"

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.windows_wmi import WindowsWmiSource

        source = WindowsWmiSource({"name": "wmi-cpu", "query": "SELECT * FROM Win32_Processor"})
        assert source.name == "wmi-cpu"

    def test_health_ok_with_runner(self, monkeypatch):
        from general_ludd.connectors.windows_wmi import WindowsWmiSource

        source = WindowsWmiSource(runner=lambda query: _json.dumps([{"Name": "CPU0"}]))
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_when_runner_unavailable(self, monkeypatch):
        from general_ludd.connectors.windows_wmi import WindowsWmiSource

        source = WindowsWmiSource()
        result = source.health()
        assert isinstance(result, dict)

    def test_query_returns_wmi_results(self, monkeypatch):
        from general_ludd.connectors.windows_wmi import WindowsWmiSource

        def _runner(query: str) -> str:
            if "Win32_Processor" in query:
                return _json.dumps([{"Name": "Intel Core i7", "NumberOfCores": 8, "MaxClockSpeed": 3600}])
            return _json.dumps([])

        source = WindowsWmiSource({"query": "SELECT * FROM Win32_Processor"}, runner=_runner)
        records = source.query({})
        assert len(records) >= 1
        assert records[0]["kind"] == "metrics"


# ============================================================================
# 13. macOS Log Connector
# ============================================================================


class TestMacOSLogConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.macos_log import MacOSLogSource

        source = MacOSLogSource()
        assert source.KIND == "logs"

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.macos_log import MacOSLogSource

        source = MacOSLogSource(
            {
                "predicate": 'process == "opendirectoryd"',
                "name": "od-log",
                "last": "10m",
            }
        )
        assert source.name == "od-log"

    def test_health_ok_with_runner(self, monkeypatch):
        from general_ludd.connectors.macos_log import MacOSLogSource

        source = MacOSLogSource(runner=lambda argv: _json.dumps([{"eventMessage": "test"}]))
        result = source.health()
        assert result["ok"] is True

    def test_query_returns_log_entries(self, monkeypatch):
        from general_ludd.connectors.macos_log import MacOSLogSource

        def _runner(argv: list[str]) -> str:
            return _json.dumps(
                [
                    {
                        "eventMessage": "Login attempt",
                        "processImagePath": "/usr/libexec/opendirectoryd",
                        "timestamp": "2025-01-01 12:00:00.000000-0500",
                    },
                    {
                        "eventMessage": "Authentication succeeded",
                        "processImagePath": "/usr/libexec/opendirectoryd",
                        "timestamp": "2025-01-01 12:00:01.000000-0500",
                    },
                ]
            )

        source = MacOSLogSource({"predicate": 'process == "opendirectoryd"'}, runner=_runner)
        records = source.query({})
        assert len(records) >= 2
        assert all(r["kind"] == "logs" for r in records)

    def test_query_empty_without_runner(self, monkeypatch):
        from general_ludd.connectors.macos_log import MacOSLogSource

        source = MacOSLogSource()
        records = source.query({})
        assert records == []


# ============================================================================
# 14. macOS Security Connector
# ============================================================================


class TestMacOSSecurityConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        source = MacOSSecuritySource()
        assert source.KIND == "logs"

    def test_constructs_custom_name(self, monkeypatch):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        source = MacOSSecuritySource({"name": "mac-sec-audit"})
        assert source.name == "mac-sec-audit"

    def test_health_ok_with_runner(self, monkeypatch):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        source = MacOSSecuritySource(runner=lambda argv: _json.dumps([{"event": "AUTHENTICATION_SUCCEEDED"}]))
        result = source.health()
        assert result["ok"] is True

    def test_query_returns_security_events(self, monkeypatch):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        def _runner(argv: list[str]) -> str:
            return _json.dumps(
                [
                    {"event": "AUTHENTICATION_SUCCEEDED", "user": "admin", "timestamp": "2025-01-01T12:00:00Z"},
                    {"event": "GATEKEEPER_OVERRIDE", "user": "admin", "timestamp": "2025-01-01T12:01:00Z"},
                ]
            )

        source = MacOSSecuritySource(runner=_runner)
        records = source.query({})
        assert len(records) >= 2
        assert all(r["kind"] == "logs" for r in records)

    def test_query_empty_without_runner(self, monkeypatch):
        from general_ludd.connectors.macos_security import MacOSSecuritySource

        source = MacOSSecuritySource()
        records = source.query({})
        assert records == []


# ============================================================================
# 15. StatsD Parsing Connector
# ============================================================================


class TestStatsdParseConnector:
    def test_constructs_with_defaults(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import StatsdParseSource

        source = StatsdParseSource()
        assert source.KIND == "metrics"

    def test_constructs_custom_config(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import StatsdParseSource

        source = StatsdParseSource({"name": "statsd-parser", "port": 9125})
        assert source.name == "statsd-parser"

    def test_dispatch_parses_counter(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.requests:1|c")
        assert len(records) >= 1
        assert records[0]["message"] == "app.requests"
        assert records[0]["value"] == 1
        assert records[0]["labels"]["metric_type"] == "counter"

    def test_dispatch_parses_gauge(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.memory:512.5|g")
        assert len(records) >= 1
        assert records[0]["value"] == 512.5
        assert records[0]["labels"]["metric_type"] == "gauge"

    def test_dispatch_parses_timer(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.latency:42.5|ms")
        assert len(records) >= 1
        assert records[0]["value"] == 42.5
        assert records[0]["labels"]["metric_type"] == "timer"

    def test_dispatch_parses_with_tags(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.errors:5|c|#host:web1,region:us-east")
        assert len(records) >= 1
        assert records[0]["labels"].get("host") == "web1"
        assert records[0]["labels"].get("region") == "us-east"

    def test_dispatch_parses_sampling_rate(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("app.hits:10|c|@0.1")
        assert len(records) >= 1
        assert records[0]["value"] == 10
        assert records[0]["labels"]["sample_rate"] == "0.1"

    def test_dispatch_returns_empty_for_invalid(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _dispatch

        records = _dispatch("invalid without colon")
        assert records == []

    def test_strip_name_drops_prefix(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import _strip_name

        result = _strip_name("stats.gauges.app.requests")
        assert "app" in result

    def test_query_parses_multiple_lines(self, monkeypatch):
        from general_ludd.connectors.statsd_parse import StatsdParseSource

        source = StatsdParseSource()
        records = source.query({"lines": ["app.hits:5|c", "app.mem:512|g"]})
        assert len(records) >= 2


# ============================================================================
# 16. Ingest Formats Connector
# ============================================================================


class TestIngestFormatsConnector:
    def test_detect_json_returns_true_for_json(self, monkeypatch):
        from general_ludd.connectors.ingest_formats import _detect_format

        fmt = _detect_format('{"key": "value"}')
        assert fmt == "json"

    def test_detect_plain_returns_true_for_plain(self, monkeypatch):
        from general_ludd.connectors.ingest_formats import _detect_format

        fmt = _detect_format("just plain text here")
        assert fmt == "plain"

    def test_detect_csv_returns_true_for_csv(self, monkeypatch):
        from general_ludd.connectors.ingest_formats import _detect_format

        fmt = _detect_format("col1,col2,col3\nval1,val2,val3")
        assert fmt == "csv"


# ============================================================================
# 17. Redfish Extended — power, events, SSRF, error isolation
# ============================================================================


class TestRedfishExtendedConnector:
    def test_query_power_returns_psu_and_voltage(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        power_body = {
            "@odata.id": "/redfish/v1/Chassis/1/Power",
            "Voltages": [
                {
                    "Name": "VRM1",
                    "ReadingVolts": 1.2,
                    "Status": {"Health": "OK"},
                }
            ],
            "PowerSupplies": [
                {
                    "Name": "PSU1",
                    "LastPowerOutputWatts": 450,
                    "Status": {"Health": "OK"},
                }
            ],
        }
        transport = MockHttpTransport(status_code=200, body=power_body)
        monkeypatch.setenv("RF_PWR_U", "admin")
        monkeypatch.setenv("RF_PWR_P", "pass")
        try:
            source = RedfishSource(
                {"base_url": "https://idrac.example.com", "username_env": "RF_PWR_U", "password_env": "RF_PWR_P"},
                transport=transport,
            )
            records = source.query({"what": "power"})
            assert len(records) >= 2
            metrics = [r["message"] for r in records]
            assert any("voltage" in m for m in metrics)
            assert any("power supply" in m for m in metrics)
        finally:
            del os.environ["RF_PWR_U"], os.environ["RF_PWR_P"]

    def test_query_events_returns_hardware_logs(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        events_body = {
            "Members": [
                {
                    "Id": "1",
                    "Name": "Log Entry 1",
                    "Message": "Power supply failure detected",
                    "Severity": "Critical",
                    "SensorType": "Power Supply",
                    "Created": "2025-01-01T12:00:00Z",
                }
            ]
        }
        transport = MockHttpTransport(status_code=200, body=events_body)
        monkeypatch.setenv("RF_EVT_U", "admin")
        monkeypatch.setenv("RF_EVT_P", "pass")
        try:
            source = RedfishSource(
                {"base_url": "https://idrac.example.com", "username_env": "RF_EVT_U", "password_env": "RF_EVT_P"},
                transport=transport,
            )
            records = source.query({"what": "events"})
            assert len(records) >= 1
            assert records[0]["kind"] == "events"
            assert "Critical" in str(records[0]["level_or_status"])
        finally:
            del os.environ["RF_EVT_U"], os.environ["RF_EVT_P"]

    def test_query_all_mode_invokes_all_resources(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        transport = MockHttpTransport(status_code=200, body={"Temperatures": [], "Fans": []})
        monkeypatch.setenv("RF_ALL_U", "admin")
        monkeypatch.setenv("RF_ALL_P", "pass")
        try:
            source = RedfishSource(
                {"base_url": "https://idrac.example.com", "username_env": "RF_ALL_U", "password_env": "RF_ALL_P"},
                transport=transport,
            )
            records = source.query({"what": "all"})
            assert isinstance(records, list)
        finally:
            del os.environ["RF_ALL_U"], os.environ["RF_ALL_P"]

    def test_ssrf_rejects_private_ip_during_health(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        source = RedfishSource(
            {"base_url": "https://10.0.0.1", "username_env": "NONEXISTENT_RF", "password_env": "NONEXISTENT_RF"}
        )
        result = source.health()
        assert result["ok"] is False

    def test_ssrf_rejects_loopback_during_query(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        source = RedfishSource(
            {"base_url": "https://127.0.0.1", "username_env": "NONEXISTENT_RF2", "password_env": "NONEXISTENT_RF2"}
        )
        with pytest.raises((ValueError, RuntimeError)):
            source.query({"what": "thermal"})

    def test_ssrf_allows_private_with_flag(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        monkeypatch.setenv("RF_PRIV_U", "admin")
        monkeypatch.setenv("RF_PRIV_P", "pass")
        try:
            source = RedfishSource(
                {
                    "base_url": "https://192.168.1.1",
                    "username_env": "RF_PRIV_U",
                    "password_env": "RF_PRIV_P",
                    "allow_private": True,
                }
            )
            assert source.KIND == "metrics"
        finally:
            del os.environ["RF_PRIV_U"], os.environ["RF_PRIV_P"]

    def test_health_reports_missing_credentials(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        source = RedfishSource(
            {
                "base_url": "https://idrac.example.com",
                "username_env": "NONEXISTENT_USR",
                "password_env": "NONEXISTENT_PWD",
            }
        )
        result = source.health()
        assert result["ok"] is False
        assert "missing credentials" in result["detail"]

    def test_health_ok_without_transport_but_valid_config(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        monkeypatch.setenv("RF_CFG_U", "admin")
        monkeypatch.setenv("RF_CFG_P", "pass")
        try:
            source = RedfishSource(
                {"base_url": "https://idrac.example.com", "username_env": "RF_CFG_U", "password_env": "RF_CFG_P"}
            )
            result = source.health()
            assert result["ok"] is True
            assert "no transport" in result["detail"]
        finally:
            del os.environ["RF_CFG_U"], os.environ["RF_CFG_P"]

    def test_safe_isolates_per_resource_errors(self, monkeypatch):
        from general_ludd.connectors.redfish import RedfishSource

        class _ThermalFailsTransport:
            def __call__(self, url: str, headers: dict[str, str], timeout: float) -> MockHttpResponse:
                if "Thermal" in url:
                    raise OSError("iDRAC unreachable")
                if "Power" in url:
                    return MockHttpResponse(
                        status_code=200,
                        body={
                            "Voltages": [{"Name": "VRM1", "ReadingVolts": 1.1, "Status": {"Health": "OK"}}],
                            "PowerSupplies": [],
                        },
                    )
                return MockHttpResponse(status_code=200, body={"Members": []})

        monkeypatch.setenv("RF_SAFE_U", "admin")
        monkeypatch.setenv("RF_SAFE_P", "pass")
        try:
            source = RedfishSource(
                {"base_url": "https://idrac.example.com", "username_env": "RF_SAFE_U", "password_env": "RF_SAFE_P"},
                transport=_ThermalFailsTransport(),
            )
            records = source.query({"what": "all"})
            has_error = any(r["message"] == "query error" for r in records)
            has_voltage = any("voltage" in r["message"] for r in records)
            assert has_error or has_voltage
        finally:
            del os.environ["RF_SAFE_U"], os.environ["RF_SAFE_P"]


# ============================================================================
# 18. SNMP Extended — exporter mode, community redaction, SSRF, numeric coercion
# ============================================================================


class TestSnmpExtendedConnector:
    def test_exporter_mode_health_requires_base_url(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource({"mode": "exporter"})
        result = source.health()
        assert result["ok"] is False
        assert "base_url" in result["detail"]

    def test_exporter_mode_health_requires_transport(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource({"mode": "exporter", "base_url": "https://snmp.example.com:9116"})
        result = source.health()
        assert isinstance(result, dict)

    def test_exporter_mode_health_ssrf_blocks_private(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource({"mode": "exporter", "base_url": "http://10.0.0.1:9116"})
        result = source.health()
        assert result["ok"] is False
        assert "ssrf-blocked" in result["detail"]

    def test_exporter_mode_query_parses_prometheus_metrics(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        metrics_body = (
            'snmp_scrape_duration_seconds{instance="10.1.1.1"} 2.5\n'
            'snmp_up{instance="10.1.1.2"} 1\n'
            "# HELP snmp_scrape_walk_duration_seconds SNMP walk duration\n"
            "# TYPE snmp_scrape_walk_duration_seconds gauge\n"
            'snmp_scrape_walk_duration_seconds{instance="10.1.1.1"} 1.2\n'
        )

        def _transport(url: str, timeout: float) -> tuple[int, str]:
            return (200, metrics_body)

        source = SnmpSource(
            {"mode": "exporter", "base_url": "https://snmp.example.com:9116", "allow_private": True},
            transport=_transport,
        )
        records = source.query({})
        assert len(records) >= 2
        assert records[0]["kind"] == "metrics"
        assert records[0]["labels"]["community"] == "***redacted***"

    def test_exporter_mode_query_empty_on_http_error(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        def _transport(url: str, timeout: float) -> tuple[int, str]:
            return (500, "")

        source = SnmpSource(
            {"mode": "exporter", "base_url": "https://snmp.example.com:9116", "allow_private": True},
            transport=_transport,
        )
        records = source.query({})
        assert records == []

    def test_exporter_mode_query_empty_without_transport(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource({"mode": "exporter", "base_url": "https://snmp.example.com:9116", "allow_private": True})
        records = source.query({})
        assert records == []

    def test_community_redacted_in_labels(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        monkeypatch.setenv("SNMP_COMM", "mysecret")
        try:
            source = SnmpSource(
                {"host": "192.168.1.1", "community_env": "SNMP_COMM", "oids": ["1.3.6.1.2.1.1.3"]},
                getter=lambda h, p, c, o, t: [("1.3.6.1.2.1.1.3", "12345")],
            )
            records = source.query({})
            assert len(records) >= 1
            assert records[0]["labels"]["community"] == "***redacted***"
            assert records[0]["raw"]["community"] == "***redacted***"
        finally:
            del os.environ["SNMP_COMM"]

    def test_coerce_numeric_handles_bool(self, monkeypatch):
        from general_ludd.connectors.snmp import _coerce_numeric

        value, status = _coerce_numeric(True)
        assert value == 1.0
        assert status == "ok"

    def test_coerce_numeric_handles_non_numeric_string(self, monkeypatch):
        from general_ludd.connectors.snmp import _coerce_numeric

        value, status = _coerce_numeric("Linux host 5.15.0")
        assert value is None
        assert status == "ok"

    def test_coerce_numeric_handles_numeric_string(self, monkeypatch):
        from general_ludd.connectors.snmp import _coerce_numeric

        value, status = _coerce_numeric("42.5")
        assert value == 42.5
        assert status == "ok"

    def test_call_getter_supports_legacy_3_arg_signature(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        legacy_calls: list[tuple] = []

        def _legacy_getter(host: str, community: str, oid: str) -> tuple[str, str]:
            legacy_calls.append((host, community, oid))
            return (oid, "42")

        monkeypatch.setenv("SNMP_LEGACY", "public")
        try:
            source = SnmpSource(
                {"host": "192.168.1.1", "community_env": "SNMP_LEGACY", "oids": ["1.3.6.1.2.1.1.3"]},
                getter=_legacy_getter,
            )
            records = source.query({})
            assert len(records) >= 1
            assert len(legacy_calls) >= 1
            assert legacy_calls[0][0] == "192.168.1.1"
            assert legacy_calls[0][2] == "1.3.6.1.2.1.1.3"
        finally:
            del os.environ["SNMP_LEGACY"]

    def test_query_empty_when_no_community_and_no_injected_getter(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource({"host": "192.168.1.1", "oids": ["1.3.6.1.2.1.1.3"]})
        records = source.query({})
        assert records == []

    def test_query_empty_when_no_host_and_no_injected_getter(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource({"oids": ["1.3.6.1.2.1.1.3"]})
        records = source.query({})
        assert records == []

    def test_scrub_redacts_secret_in_error_message(self, monkeypatch):
        from general_ludd.connectors.snmp import _scrub

        result = _scrub("timeout with community 'secret123'", "secret123")
        assert "secret123" not in result
        assert "***redacted***" in result

    def test_exporter_mode_query_empty_when_no_base_url(self, monkeypatch):
        from general_ludd.connectors.snmp import SnmpSource

        source = SnmpSource({"mode": "exporter"}, transport=lambda u, t: (200, "metrics 1"))
        records = source.query({})
        assert records == []


# ============================================================================
# 19. Dmesg Extended — arg validation edges, parse variants, timestamp shapes
# ============================================================================


class TestDmesgExtendedConnector:
    def test_validate_arg_rejects_empty_string(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        with pytest.raises(ValueError):
            DmesgSource._validate_arg("")

    def test_validate_arg_rejects_shell_pipe(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        with pytest.raises(ValueError):
            DmesgSource._validate_arg("kern|nc evil")

    def test_build_argv_includes_facility_and_level(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        source = DmesgSource()
        argv = source._build_argv({"facility": "kern", "level": "err"})
        assert "dmesg" in argv[0]
        assert "--json" in argv
        assert "--facility=kern" in argv
        assert "--level=err" in argv

    def test_parse_entries_handles_list_shape(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        entries = DmesgSource._parse_entries('[{"msg": "hello", "prio": 6}]')
        assert len(entries) == 1
        assert entries[0]["msg"] == "hello"

    def test_parse_entries_handles_dict_wrapper_shape(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        entries = DmesgSource._parse_entries('{"dmesg": [{"msg": "boot", "prio": 5}]}')
        assert len(entries) == 1
        assert entries[0]["msg"] == "boot"

    def test_parse_entries_raises_on_non_json(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        with pytest.raises(RuntimeError, match="non-JSON"):
            DmesgSource._parse_entries("not json at all")

    def test_normalize_entry_handles_dict_timestamp(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        source = DmesgSource()
        entry = {"msg": "test", "level": "err", "timestamp": {"usec": 123456, "sec": 123}}
        result = source._normalize_entry(entry)
        assert result["ts"] == 123456
        assert result["level_or_status"] == "err"

    def test_normalize_entry_defaults_level_to_info(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        source = DmesgSource()
        entry = {"msg": "test", "timestamp": None}
        result = source._normalize_entry(entry)
        assert result["level_or_status"] == "info"

    def test_health_not_ok_when_binary_not_found_and_no_runner(self, monkeypatch):
        from general_ludd.connectors.dmesg import DmesgSource

        source = DmesgSource({"binary": "/nonexistent/dmesg"})
        result = source.health()
        assert result["ok"] is False
        assert "not found" in result["detail"]


# ============================================================================
# 20. Podman Extended — logs mode, events mode
# ============================================================================


class TestPodmanExtendedConnector:
    def test_query_logs_mode_requires_container_id(self, monkeypatch):
        from general_ludd.connectors.podman import PodmanSource

        source = PodmanSource(transport=lambda *a, **kw: _SplitBaseTestPodmanConnector._MockPodmanResponse(status=200))
        with pytest.raises(ValueError):
            source.query({"mode": "logs"})

    def test_query_logs_mode_raises_on_http_error(self, monkeypatch):
        from general_ludd.connectors.podman import PodmanSource

        source = PodmanSource(transport=lambda *a, **kw: _SplitBaseTestPodmanConnector._MockPodmanResponse(status=500))
        with pytest.raises(RuntimeError):
            source.query({"mode": "logs", "container_id": "abc123"})

    def test_query_events_mode_parses_stream(self, monkeypatch):
        from general_ludd.connectors.podman import PodmanSource

        events_data = (
            b'{"Type":"container","Action":"start","time":1700000000,"timeNano":1700000000000000000,'
            b'"Actor":{"ID":"abc123","Attributes":{"image":"nginx:latest"}}}\n'
        )
        resp = _SplitBaseTestPodmanConnector._MockPodmanResponse(status=200, body=events_data)
        source = PodmanSource(transport=lambda *a, **kw: resp)
        records = source.query({"mode": "events"})
        assert len(records) >= 1
        assert records[0]["kind"] == "logs"
        assert "start" in str(records[0]["message"])

    def test_ssrf_rejects_tcp_to_internal_host(self, monkeypatch):
        from general_ludd.connectors.podman import PodmanSource

        source = PodmanSource({"base_url": "http://10.0.0.1:8080"})
        result = source.health()
        assert result["ok"] is False

    def test_query_unknown_mode_raises(self, monkeypatch):
        from general_ludd.connectors.podman import PodmanSource

        source = PodmanSource(transport=lambda *a, **kw: _SplitBaseTestPodmanConnector._MockPodmanResponse(status=200))
        with pytest.raises(ValueError):
            source.query({"mode": "nonexistent"})


# ============================================================================
# 21. Containerd Extended — stats query, pod_logs query
# ============================================================================


class TestContainerdExtendedConnector:
    def test_query_with_what_stats(self, monkeypatch):
        from general_ludd.connectors.containerd import ContainerdSource

        def _runner(argv: list[str]) -> str:
            cv = " ".join(str(a) for a in argv)
            if "stats" in cv:
                return _json.dumps(
                    {
                        "stats": [
                            {
                                "attributes": {
                                    "metadata": {"name": "web"},
                                    "labels": {"io.kubernetes.pod.name": "web-pod"},
                                },
                                "cpu": {"usageCoreNanoSeconds": {"value": 1000000}, "timestamp": 1700000000},
                                "memory": {"workingSetBytes": {"value": 67108864}, "timestamp": 1700000000},
                            }
                        ]
                    }
                )
            return _json.dumps({"items": []})

        source = ContainerdSource(runner=_runner)
        records = source.query({"what": "stats"})
        assert len(records) >= 1
        assert any(r["kind"] == "metrics" for r in records)

    def test_query_exited_container_state(self, monkeypatch):
        from general_ludd.connectors.containerd import ContainerdSource

        def _runner(argv: list[str]) -> str:
            return _json.dumps(
                {
                    "items": [
                        {
                            "id": "exited-c1",
                            "metadata": {"name": "cron-job", "namespace": "default"},
                            "state": "CONTAINER_EXITED",
                            "createdAt": "1700000000000000000",
                        }
                    ]
                }
            )

        source = ContainerdSource(runner=_runner)
        records = source.query({"what": "ps"})
        assert len(records) >= 1
        assert "CONTAINER_EXITED" in records[0]["level_or_status"]

    def test_runner_fallback_on_typeerror_timeout(self, monkeypatch):
        from general_ludd.connectors.containerd import ContainerdSource

        calls: list[tuple] = []

        def _runner(*args: object) -> str:
            calls.append(args)
            if len(args) == 2:
                raise TypeError("unexpected kwarg")
            return _json.dumps({"items": []})

        source = ContainerdSource(runner=_runner)
        result = source.health()
        assert result["ok"] is True
