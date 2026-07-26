"""Deterministic E2E coverage for batch-5 utility connectors.

The main batch-5 workflow suite covers platform collectors but intentionally
does not exercise ADB or Baseten.  These tests use injected runners/transports
so CI never needs an Android device, network access, or Baseten credentials.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest


def _adb_runner(responses: Mapping[tuple[str, ...], object]):
    """Return a deterministic runner keyed by the complete ADB argv tuple."""

    calls: list[list[str]] = []

    def run(args: list[str], timeout: int | None = None):
        from general_ludd.connectors.adb import RunResult

        del timeout
        calls.append(args)
        value = responses.get(tuple(args), RunResult(1, "", "missing fixture"))
        if isinstance(value, RunResult):
            return value
        return RunResult(0, str(value), "")

    run.calls = calls
    return run


class TestAdbConnectorE2E:
    def test_health_devices_and_serial_are_reported(self) -> None:
        from general_ludd.connectors.adb import AdbConnector, RunResult

        runner = _adb_runner(
            {
                ("adb", "-s", "pixel-7", "version"): RunResult(0, "Android Debug Bridge version 1", ""),
                ("adb", "-s", "pixel-7", "devices"): "List of devices attached\npixel-7\tdevice\n",
            }
        )
        source = AdbConnector({"serial": "pixel-7"}, run=runner)

        assert source.health() == {"ok": True, "detail": "1 device(s) connected"}
        assert source.devices() == [{"serial": "pixel-7", "state": "device"}]
        assert runner.calls[:2] == [
            ["adb", "-s", "pixel-7", "version"],
            ["adb", "-s", "pixel-7", "devices"],
        ]

    @pytest.mark.parametrize(
        ("version_result", "devices_result", "detail"),
        [
            ("missing", "List of devices attached\n", "no device connected"),
            ("missing", "", "no device connected"),
        ],
    )
    def test_health_reports_missing_devices(self, version_result, devices_result, detail) -> None:
        from general_ludd.connectors.adb import AdbConnector, RunResult

        runner = _adb_runner(
            {
                ("adb", "version"): RunResult(0, "ok", "") if version_result == "ok" else RunResult(1, "", "missing"),
                ("adb", "devices"): devices_result,
            }
        )
        source = AdbConnector(run=runner)
        assert source.health()["detail"] == ("adb not found" if version_result != "ok" else detail)

    def test_queries_parse_packages_properties_logs_and_actions(self) -> None:
        from general_ludd.connectors.adb import AdbConnector

        runner = _adb_runner(
            {
                ("adb", "shell", "pm", "list", "packages", "-3"): "package:com.example.app\nignored\n",
                ("adb", "shell", "pm", "list", "packages", "-s"): "package:com.example.app\n",
                ("adb", "shell", "getprop"): "[ro.product.model]: [Pixel 7]\n[ro.build.version.sdk]: [34]\n",
                ("adb", "shell", "getprop", "ro.product.model"): "[ro.product.model]: [Pixel 7]\n",
                ("adb", "logcat", "-d", "-t", "2", "-b", "main", "-v", "threadtime"):
                "01-01 12:00:00.000  12  13 I ActivityManager: started\nmalformed\n",
                ("adb", "shell", "dumpsys", "activity"): "ACTIVITY MANAGER\n",
                ("adb", "shell", "am", "start", "-n", "com.example/.MainActivity"): "Starting: Intent { }\n",
            }
        )
        source = AdbConnector(run=runner)

        assert source.list_packages() == [{"package": "com.example.app", "flag": "-3"}]
        assert source.pm_list() == ["com.example.app"]
        assert source.getprop() == {"ro.product.model": "Pixel 7", "ro.build.version.sdk": "34"}
        assert source.getprop("ro.product.model") == {"ro.product.model": "Pixel 7"}
        assert source.dumpsys("activity").startswith("ACTIVITY")
        assert source.am_start("com.example/.MainActivity").startswith("Starting")
        assert source.logcat(2)[0]["tag"] == "ActivityManager"
        assert source.query({"action": "shell", "command": "echo ok"})[0]["data"] == {"output": ""}
        assert source.query({"action": "getprop"})[0]["data"]["properties"]["ro.product.model"] == "Pixel 7"
        assert source.query({"action": "devices"})[0]["data"]["devices"] == []
        assert source.query({"action": "unsupported"}) == []

    def test_failures_are_empty_and_health_is_false(self) -> None:
        from general_ludd.connectors.adb import AdbConnector, RunResult

        def fail(_args: list[str], timeout: int | None = None) -> RunResult:
            del timeout
            raise OSError("adb unavailable")

        source = AdbConnector(run=fail)
        assert source.health()["ok"] is False
        assert source.shell("id") == ""
        assert source.list_packages() == []
        assert source.getprop() == {}
        assert source.dumpsys("activity") == ""
        assert source.logcat() == []
        assert source.devices() == []


def _baseten_transport(status: int, payload: dict[str, object]):
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def request(method: str, url: str, headers: Mapping[str, str], body: bytes | None):
        calls.append((method, url, dict(headers), body))
        return status, payload

    request.calls = calls
    return request


class TestBasetenConnectorE2E:
    @pytest.fixture
    def configured(self, monkeypatch):
        monkeypatch.setenv("B5_BASETEN_KEY", "test-key")  # pragma: allowlist secret
        return {"api_key_env": "B5_BASETEN_KEY", "name": "ci-baseten"}

    def test_health_maps_success_auth_and_outage(self, configured) -> None:
        from general_ludd.connectors.baseten import BasetenClient

        for status, expected in ((200, (True, True, True)), (401, (False, True, False)), (503, (False, False, False))):
            source = BasetenClient(configured, http_request=_baseten_transport(status, {}))
            result = source.health()
            assert (result["ok"], result["reachable"], result["api_key_valid"]) == expected
            assert result["source"] == ("ci-baseten" if status == 200 else "baseten")

    def test_deployments_are_flattened_and_invocation_body_is_preserved(self, configured) -> None:
        from general_ludd.connectors.baseten import BasetenClient

        payload = {
            "items": [
                {
                    "id": "model-1",
                    "name": "chat",
                    "deployments": [{"id": "dep-1", "status": "active", "environment": "prod"}],
                },
                {"id": "model-empty", "deployments": "invalid"},
            ]
        }
        transport = _baseten_transport(200, payload)
        source = BasetenClient(configured, http_request=transport)
        assert source.list_deployments() == [
            {"id": "dep-1", "model_id": "model-1", "name": "chat", "status": "active", "environment": "prod"}
        ]

        result = source.invoke("dep-1", {"messages": [{"role": "user", "content": "hello"}], "temperature": 0.1})
        assert result == payload
        method, url, headers, body = transport.calls[-1]
        assert method == "POST"
        assert url.endswith("/chat/completions")
        assert headers["Authorization"] == "Bearer test-key"
        assert json.loads(body or b"{}") == {
            "model": "dep-1",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.1,
        }

    def test_invalid_config_and_http_errors_are_explicit(self, configured) -> None:
        from general_ludd.connectors._errors import SSRFError
        from general_ludd.connectors.baseten import BasetenClient, BasetenConfigError, BasetenInvocationError

        with pytest.raises((BasetenConfigError, SSRFError)):
            BasetenClient({"api_key_env": "MISSING_B5_KEY"})
        with pytest.raises((BasetenConfigError, SSRFError)):
            BasetenClient({**configured, "base_url": "http://127.0.0.1:8080"})

        source = BasetenClient(configured, http_request=_baseten_transport(404, {}))
        with pytest.raises(BasetenInvocationError):
            source.list_deployments()
        with pytest.raises(BasetenConfigError):
            source.invoke("", {})
        with pytest.raises(BasetenInvocationError):
            source.invoke("dep-1", {})
