"""Unit tests for IDeviceConnector using injectable runner pattern."""

from __future__ import annotations

import pytest

from general_ludd.connectors.libimobiledevice import IDeviceConnector, RunResult


def _runner_factory(responses: dict[str, RunResult]):
    def _run(args: list[str], timeout: int | None = None) -> RunResult:
        key = " ".join(args)
        if key in responses:
            return responses[key]
        return RunResult(0, "", "")
    return _run


def _error_runner(*_args: object, **_kwargs: object) -> RunResult:
    raise FileNotFoundError("idevice_id not found")


# ── kind / name / config ─────────────────────────────────────────────────


def test_name_defaults() -> None:
    src = IDeviceConnector()
    assert src.name == "libimobiledevice"


def test_name_from_config() -> None:
    src = IDeviceConnector(config={"name": "iphone-14"})
    assert src.name == "iphone-14"


def test_kind_is_logs() -> None:
    src = IDeviceConnector()
    assert src.KIND == "logs"


def test_udid_from_config() -> None:
    src = IDeviceConnector(config={"udid": "abc123def456"})
    assert src.config["udid"] == "abc123def456"


# ── health ───────────────────────────────────────────────────────────────


def test_health_ok_with_device() -> None:
    responses = {
        "idevice_id -l": RunResult(0, "abc123def456\n", ""),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    h = src.health()
    assert h["ok"] is True
    assert "abc123def456" in h["detail"]


def test_health_no_device() -> None:
    responses = {
        "idevice_id -l": RunResult(0, "", ""),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    h = src.health()
    assert h["ok"] is False
    assert "no device" in h["detail"].lower()


def test_health_tools_missing() -> None:
    responses = {
        "idevice_id -l": RunResult(1, "", "idevice_id not found"),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    h = src.health()
    assert h["ok"] is False


def test_health_never_raises() -> None:
    src = IDeviceConnector(run=_error_runner)
    try:
        result = src.health()
    except Exception as exc:
        pytest.fail(f"health() raised {type(exc).__name__}: {exc}")
    assert "ok" in result


# ── ideviceinfo ──────────────────────────────────────────────────────────


def test_ideviceinfo_parses_key_value() -> None:
    responses = {
        "ideviceinfo": RunResult(0, "ProductType: iPhone15,2\nProductVersion: 17.4\n", ""),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    info = src.ideviceinfo()
    assert info["ProductType"] == "iPhone15,2"
    assert info["ProductVersion"] == "17.4"


def test_ideviceinfo_with_domain() -> None:
    responses = {
        "ideviceinfo -q com.apple.mobile.iTunes": RunResult(0, "ModelNumber: A3102\n", ""),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    info = src.ideviceinfo(domain="com.apple.mobile.iTunes")
    assert info["ModelNumber"] == "A3102"


def test_ideviceinfo_with_udid() -> None:
    responses = {
        "ideviceinfo -u ABC123": RunResult(0, "DeviceName: My iPhone\n", ""),
    }
    src = IDeviceConnector(config={"udid": "ABC123"}, run=_runner_factory(responses))
    info = src.ideviceinfo()
    assert info["DeviceName"] == "My iPhone"


def test_ideviceinfo_error_returns_empty() -> None:
    src = IDeviceConnector(run=_error_runner)
    assert src.ideviceinfo() == {}


# ── idevicesyslog ────────────────────────────────────────────────────────


def test_idevicesyslog_parses_lines() -> None:
    responses = {
        "idevicesyslog": RunResult(
            0,
            "Jul 14 10:00:00 iPhone SpringBoard[123] <Notice>: Locked\n",
            "",
        ),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    entries = src.idevicesyslog(lines=10)
    assert len(entries) == 1
    assert entries[0]["process"] == "SpringBoard"
    assert entries[0]["message"] == "Locked"


def test_idevicesyslog_with_udid() -> None:
    responses = {
        "idevicesyslog -u ABC": RunResult(0, "Jul 14 10:00:00 iPhone kernel[0] <Notice>: boot\n", ""),
    }
    src = IDeviceConnector(config={"udid": "ABC"}, run=_runner_factory(responses))
    entries = src.idevicesyslog()
    assert len(entries) == 1
    assert entries[0]["process"] == "kernel"


def test_idevicesyslog_error_returns_empty() -> None:
    src = IDeviceConnector(run=_error_runner)
    assert src.idevicesyslog() == []


# ── idevicediagnostics ───────────────────────────────────────────────────


def test_idevicediagnostics_returns_output() -> None:
    responses = {
        "idevicediagnostics diagnostics All": RunResult(0, "<dict><key>Battery</key>...</dict>\n", ""),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    out = src.idevicediagnostics()
    assert "<dict>" in out


def test_idevicediagnostics_error_returns_empty() -> None:
    src = IDeviceConnector(run=_error_runner)
    assert src.idevicediagnostics() == ""


# ── idevice_id ───────────────────────────────────────────────────────────


def test_idevice_id_parses_list() -> None:
    responses = {
        "idevice_id -l": RunResult(0, "abc123def456\n789ghi012jkl\n", ""),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    udids = src.idevice_id()
    assert len(udids) == 2
    assert "abc123def456" in udids


def test_idevice_id_empty() -> None:
    responses = {"idevice_id -l": RunResult(0, "", "")}
    src = IDeviceConnector(run=_runner_factory(responses))
    assert src.idevice_id() == []


def test_idevice_id_error_returns_empty() -> None:
    src = IDeviceConnector(run=_error_runner)
    assert src.idevice_id() == []


# ── idevicepair ──────────────────────────────────────────────────────────


def test_idevicepair_validate_success() -> None:
    responses = {
        "idevicepair validate": RunResult(0, "SUCCESS: Valid pair record found\n", ""),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    result = src.idevicepair()
    assert result["success"] is True
    assert "SUCCESS" in result["output"]


def test_idevicepair_validate_failure() -> None:
    responses = {
        "idevicepair validate": RunResult(1, "ERROR: No pair record found\n", ""),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    result = src.idevicepair()
    assert result["success"] is False


def test_idevicepair_pair_action() -> None:
    responses = {
        "idevicepair pair": RunResult(0, "SUCCESS: Paired\n", ""),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    result = src.idevicepair(action="pair")
    assert result["success"] is True


def test_idevicepair_error_returns_dict() -> None:
    src = IDeviceConnector(run=_error_runner)
    result = src.idevicepair()
    assert result["success"] is False
    assert "action" in result


# ── oslog ────────────────────────────────────────────────────────────────


def test_oslog_returns_entries() -> None:
    responses = {
        "log stream --style compact": RunResult(0, "default 10:00:00.123 SpringBoard: Some log message\n", ""),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    entries = src.oslog()
    assert len(entries) >= 1


def test_oslog_with_predicate() -> None:
    responses = {
        "log stream --style compact --predicate process == 'SpringBoard'": RunResult(
            0, "default 10:00:00 SpringBoard: msg\n", "",
        ),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    entries = src.oslog(predicate="process == 'SpringBoard'")
    assert len(entries) >= 1


def test_oslog_error_returns_empty() -> None:
    src = IDeviceConnector(run=_error_runner)
    assert src.oslog() == []


# ── installed_profiles ───────────────────────────────────────────────────


def test_installed_profiles_parses() -> None:
    responses = {
        "ideviceprovision list": RunResult(0, "Profile - com.example.app\n", ""),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    profiles = src.installed_profiles()
    assert len(profiles) >= 1


def test_installed_profiles_error_returns_empty() -> None:
    src = IDeviceConnector(run=_error_runner)
    assert src.installed_profiles() == []


# ── disk_usage ───────────────────────────────────────────────────────────


def test_disk_usage_returns_dict() -> None:
    responses = {
        "ideviceinfo": RunResult(0, "TotalDiskCapacity: 128000000000\n", ""),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    usage = src.disk_usage()
    assert isinstance(usage, dict)


def test_disk_usage_error_returns_empty() -> None:
    src = IDeviceConnector(run=_error_runner)
    assert src.disk_usage() == {}


# ── query ────────────────────────────────────────────────────────────────


def test_query_ideviceinfo_action() -> None:
    responses = {
        "ideviceinfo": RunResult(0, "ProductType: iPhone15,2\n", ""),
    }
    src = IDeviceConnector(run=_runner_factory(responses))
    records = src.query({"action": "ideviceinfo"})
    assert len(records) == 1
    assert records[0]["data"]["info"]["ProductType"] == "iPhone15,2"


def test_query_device_id_action() -> None:
    responses = {"idevice_id -l": RunResult(0, "ABC123\n", "")}
    src = IDeviceConnector(run=_runner_factory(responses))
    records = src.query({"action": "device_id"})
    assert len(records) == 1
    assert "ABC123" in records[0]["data"]["udids"]


def test_query_pair_action() -> None:
    responses = {"idevicepair validate": RunResult(0, "SUCCESS\n", "")}
    src = IDeviceConnector(run=_runner_factory(responses))
    records = src.query({"action": "pair"})
    assert len(records) == 1
    assert records[0]["data"]["success"] is True


def test_query_disk_usage_action() -> None:
    responses = {"ideviceinfo": RunResult(0, "", "")}
    src = IDeviceConnector(run=_runner_factory(responses))
    records = src.query({"action": "disk_usage"})
    assert len(records) == 1
    assert isinstance(records[0]["data"]["usage"], dict)


def test_query_unknown_action_returns_empty() -> None:
    src = IDeviceConnector()
    records = src.query({"action": "nonexistent"})
    assert records == []


def test_query_never_raises_on_bad_spec() -> None:
    src = IDeviceConnector(run=_error_runner)
    records = src.query({})
    assert isinstance(records, list)
