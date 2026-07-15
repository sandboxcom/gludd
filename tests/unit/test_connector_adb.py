"""Unit tests for AdbConnector using injectable runner pattern."""

from __future__ import annotations

import pytest

from general_ludd.connectors.adb import AdbConnector, RunResult


def _runner_factory(responses: dict[str, RunResult]):
    def _run(args: list[str], timeout: int | None = None) -> RunResult:
        key = " ".join(args)
        if key in responses:
            return responses[key]
        return RunResult(0, "", "")
    return _run


def _error_runner(*_args: object, **_kwargs: object) -> RunResult:
    raise FileNotFoundError("adb not found")


# ── kind / name / config ─────────────────────────────────────────────────


def test_name_defaults_to_adb() -> None:
    src = AdbConnector()
    assert src.name == "adb"


def test_name_from_config() -> None:
    src = AdbConnector(config={"name": "my-device"})
    assert src.name == "my-device"


def test_kind_is_logs() -> None:
    src = AdbConnector()
    assert src.KIND == "logs"


def test_serial_from_config() -> None:
    src = AdbConnector(config={"serial": "emulator-5554"})
    assert src.config["serial"] == "emulator-5554"


# ── health ───────────────────────────────────────────────────────────────


def test_health_ok_with_device() -> None:
    responses = {
        "adb version": RunResult(0, "Android Debug Bridge version 1.0.41\n", ""),
        "adb devices": RunResult(0, "List of devices attached\nemulator-5554\tdevice\n", ""),
    }
    src = AdbConnector(run=_runner_factory(responses))
    h = src.health()
    assert h["ok"] is True


def test_health_no_device() -> None:
    responses = {
        "adb version": RunResult(0, "Android Debug Bridge version 1.0.41\n", ""),
        "adb devices": RunResult(0, "List of devices attached\n", ""),
    }
    src = AdbConnector(run=_runner_factory(responses))
    h = src.health()
    assert h["ok"] is False
    assert "no device" in h["detail"].lower()


def test_health_adb_missing() -> None:
    src = AdbConnector(run=_error_runner)
    h = src.health()
    assert h["ok"] is False
    assert "not found" in h["detail"].lower()


def test_health_never_raises() -> None:
    src = AdbConnector(run=_error_runner)
    try:
        result = src.health()
    except Exception as exc:
        pytest.fail(f"health() raised {type(exc).__name__}: {exc}")
    assert "ok" in result


# ── shell ────────────────────────────────────────────────────────────────


def test_shell_returns_stdout() -> None:
    responses = {"adb shell echo hello": RunResult(0, "hello\n", "")}
    src = AdbConnector(run=_runner_factory(responses))
    assert src.shell("echo hello") == "hello\n"


def test_shell_error_returns_empty() -> None:
    src = AdbConnector(run=_error_runner)
    assert src.shell("echo hello") == ""


def test_shell_with_serial() -> None:
    responses = {"adb -s ABC shell whoami": RunResult(0, "root\n", "")}
    src = AdbConnector(config={"serial": "ABC"}, run=_runner_factory(responses))
    assert src.shell("whoami") == "root\n"


# ── list_packages ────────────────────────────────────────────────────────


def test_list_packages_parses_package_lines() -> None:
    responses = {
        "adb shell pm list packages -3": RunResult(0, "package:com.example.app\npackage:com.test.foo\n", ""),
    }
    src = AdbConnector(run=_runner_factory(responses))
    pkgs = src.list_packages()
    assert len(pkgs) == 2
    assert pkgs[0]["package"] == "com.example.app"
    assert pkgs[0]["flag"] == "-3"


def test_list_packages_empty() -> None:
    responses = {"adb shell pm list packages -3": RunResult(0, "", "")}
    src = AdbConnector(run=_runner_factory(responses))
    assert src.list_packages() == []


def test_list_packages_error_returns_empty() -> None:
    src = AdbConnector(run=_error_runner)
    assert src.list_packages() == []


# ── getprop ──────────────────────────────────────────────────────────────


def test_getprop_parses_properties() -> None:
    responses = {
        "adb shell getprop": RunResult(
            0,
            "[ro.build.version.sdk]: [34]\n[ro.product.model]: [Pixel 7]\n",
            "",
        ),
    }
    src = AdbConnector(run=_runner_factory(responses))
    props = src.getprop()
    assert props["ro.build.version.sdk"] == "34"
    assert props["ro.product.model"] == "Pixel 7"


def test_getprop_with_key() -> None:
    responses = {
        "adb shell getprop ro.debuggable": RunResult(0, "[ro.debuggable]: [1]\n", ""),
    }
    src = AdbConnector(run=_runner_factory(responses))
    props = src.getprop("ro.debuggable")
    assert props["ro.debuggable"] == "1"


def test_getprop_error_returns_empty() -> None:
    src = AdbConnector(run=_error_runner)
    assert src.getprop() == {}


# ── dumpsys ──────────────────────────────────────────────────────────────


def test_dumpsys_returns_stdout() -> None:
    responses = {
        "adb shell dumpsys meminfo": RunResult(0, "Total RAM: 8,192,000K\n", ""),
    }
    src = AdbConnector(run=_runner_factory(responses))
    out = src.dumpsys("meminfo")
    assert "Total RAM" in out


def test_dumpsys_error_returns_empty() -> None:
    src = AdbConnector(run=_error_runner)
    assert src.dumpsys("meminfo") == ""


# ── logcat ───────────────────────────────────────────────────────────────


def test_logcat_parses_threadtime_format() -> None:
    responses = {
        "adb logcat -d -t 100 -b main -v threadtime": RunResult(
            0,
            "06-15 10:30:45.123  1234  5678 I ActivityManager: Start proc\n",
            "",
        ),
    }
    src = AdbConnector(run=_runner_factory(responses))
    entries = src.logcat(lines=100)
    assert len(entries) == 1
    assert entries[0]["level"] == "I"
    assert entries[0]["tag"] == "ActivityManager"
    assert entries[0]["pid"] == 1234
    assert entries[0]["tid"] == 5678


def test_logcat_error_returns_empty() -> None:
    src = AdbConnector(run=_error_runner)
    assert src.logcat() == []


# ── devices ──────────────────────────────────────────────────────────────


def test_devices_parses_device_list() -> None:
    responses = {
        "adb devices": RunResult(
            0,
            "List of devices attached\nemulator-5554\tdevice\nABCD1234\tunauthorized\n",
            "",
        ),
    }
    src = AdbConnector(run=_runner_factory(responses))
    devs = src.devices()
    assert len(devs) == 2
    assert devs[0]["serial"] == "emulator-5554"
    assert devs[0]["state"] == "device"
    assert devs[1]["state"] == "unauthorized"


def test_devices_none() -> None:
    responses = {"adb devices": RunResult(0, "List of devices attached\n", "")}
    src = AdbConnector(run=_runner_factory(responses))
    assert src.devices() == []


def test_devices_error_returns_empty() -> None:
    src = AdbConnector(run=_error_runner)
    assert src.devices() == []


# ── pm_list ──────────────────────────────────────────────────────────────


def test_pm_list_returns_packages() -> None:
    responses = {
        "adb shell pm list packages -s": RunResult(0, "package:com.android.settings\npackage:com.android.phone\n", ""),
    }
    src = AdbConnector(run=_runner_factory(responses))
    pkgs = src.pm_list(flag="-s")
    assert len(pkgs) == 2
    assert "com.android.settings" in pkgs


# ── am_start ─────────────────────────────────────────────────────────────


def test_am_start_returns_stdout() -> None:
    responses = {
        "adb shell am start -n com.example/.MainActivity": RunResult(0, "Starting: Intent\n", ""),
    }
    src = AdbConnector(run=_runner_factory(responses))
    out = src.am_start("com.example/.MainActivity")
    assert "Starting" in out


def test_am_start_error_returns_empty() -> None:
    src = AdbConnector(run=_error_runner)
    assert src.am_start("com.example/.MainActivity") == ""


# ── query ────────────────────────────────────────────────────────────────


def test_query_shell_action() -> None:
    responses = {"adb shell whoami": RunResult(0, "shell\n", "")}
    src = AdbConnector(run=_runner_factory(responses))
    records = src.query({"action": "shell", "command": "whoami"})
    assert len(records) == 1
    assert records[0]["action"] == "shell"
    assert records[0]["data"]["output"] == "shell\n"


def test_query_getprop_action() -> None:
    responses = {
        "adb shell getprop ro.build.version.sdk": RunResult(0, "[ro.build.version.sdk]: [34]\n", ""),
    }
    src = AdbConnector(run=_runner_factory(responses))
    records = src.query({"action": "getprop", "key": "ro.build.version.sdk"})
    assert len(records) == 1
    assert records[0]["data"]["properties"]["ro.build.version.sdk"] == "34"


def test_query_devices_action() -> None:
    responses = {"adb devices": RunResult(0, "List of devices attached\nABC\tdevice\n", "")}
    src = AdbConnector(run=_runner_factory(responses))
    records = src.query({"action": "devices"})
    assert len(records) == 1
    assert records[0]["data"]["devices"][0]["serial"] == "ABC"


def test_query_unknown_action_returns_empty() -> None:
    src = AdbConnector(run=_error_runner)
    records = src.query({"action": "nonexistent"})
    assert records == []


def test_query_never_raises_on_bad_spec() -> None:
    src = AdbConnector(run=_error_runner)
    records = src.query({})
    assert isinstance(records, list)
