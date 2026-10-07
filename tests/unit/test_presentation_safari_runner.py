"""Tests for the bounded native Safari presentation smoke."""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from scripts import run_presentation_safari_smoke as safari


def test_plan_is_native_bounded_and_namespaced() -> None:
    plan = safari.build_plan(
        driver_path=Path("/usr/bin/safaridriver"),
        output_root=Path("/tmp/gludd-presentation-safari-unit"),
        timeout_seconds=60,
    )

    assert plan.browser == "native-safari"
    assert plan.driver_path == "/usr/bin/safaridriver"
    assert plan.output_root == str(Path("/tmp/gludd-presentation-safari-unit").resolve())
    assert plan.timeout_seconds == 60


@pytest.mark.parametrize(
    "output_root",
    (Path("/tmp/safari"), Path("/var/tmp/gludd-safari"), Path("/tmp")),
)
def test_plan_rejects_unnamespaced_output(output_root: Path) -> None:
    with pytest.raises(ValueError, match="/tmp/gludd"):
        safari.build_plan(
            driver_path=Path("/usr/bin/safaridriver"),
            output_root=output_root,
            timeout_seconds=60,
        )


@pytest.mark.parametrize("timeout_seconds", (19, 301))
def test_plan_rejects_unbounded_timeout(timeout_seconds: int) -> None:
    with pytest.raises(ValueError, match="between 20 and 300"):
        safari.build_plan(
            driver_path=Path("/usr/bin/safaridriver"),
            output_root=Path("/tmp/gludd-presentation-safari-unit"),
            timeout_seconds=timeout_seconds,
        )


def test_plan_requires_absolute_driver() -> None:
    with pytest.raises(ValueError, match="absolute"):
        safari.build_plan(
            driver_path=Path("safaridriver"),
            output_root=Path("/tmp/gludd-presentation-safari-unit"),
            timeout_seconds=60,
        )


def test_validate_plan_fails_closed_for_wrong_platform_or_missing_driver(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    driver = tmp_path / "safaridriver"
    plan = safari.build_plan(
        driver_path=driver,
        output_root=Path("/tmp/gludd-presentation-safari-validation"),
        timeout_seconds=60,
    )
    monkeypatch.setattr(safari.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="macOS"):
        safari.validate_plan(plan)

    monkeypatch.setattr(safari.sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="not available"):
        safari.validate_plan(plan)

    driver.write_text("driver", encoding="utf-8")
    safari.validate_plan(plan)


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        ({"value": {"sessionId": "abc"}}, "abc"),
        ({"sessionId": "legacy"}, "legacy"),
    ),
)
def test_session_id_supports_w3c_and_legacy_payloads(
    payload: dict[str, object],
    expected: str,
) -> None:
    assert safari.session_id(payload) == expected


def test_session_id_rejects_incomplete_response() -> None:
    with pytest.raises(safari.WebDriverFailure, match="session id"):
        safari.session_id({"value": {}})


@pytest.mark.parametrize(
    "message",
    (
        "Safari's remote automation is disabled",
        "Please choose Develop > Allow Remote Automation",
    ),
)
def test_remote_automation_detection_is_fail_closed(message: str) -> None:
    assert safari.is_remote_automation_disabled(message)
    assert "Allow Remote Automation" in safari.REMOTE_AUTOMATION_GUIDANCE
    assert "did not enable" in safari.REMOTE_AUTOMATION_GUIDANCE


def test_smoke_script_checks_every_chart_navigation_and_source_viewer() -> None:
    script = safari.SAFARI_ACCEPTANCE_SCRIPT

    for token in (
        "gluddPresentationReady",
        "gluddPresentationHealth",
        "Reveal.getSlides()",
        "getBoundingClientRect",
        "img.mermaid-image",
        "naturalWidth",
        "data:image/svg+xml;charset=utf-8,",
        "querySelectorAll('svg').length !== 0",
        "source-viewer-dialog",
        "getReadOnly()",
    ):
        assert token in script


def test_write_report_is_content_free_and_atomic(tmp_path: Path) -> None:
    output = tmp_path / "gludd-safari"
    report = {
        "browser": "native-safari",
        "chart_count": 26,
        "cold_seconds": 1.25,
        "status": "passed",
    }

    path = safari.write_report(output, report)

    assert path == output / "last-report.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert not (output / ".last-report.json.tmp").exists()


def test_execute_value_unwraps_w3c_response() -> None:
    assert safari.webdriver_value({"value": {"rendered": 26}}) == {"rendered": 26}
    with pytest.raises(safari.WebDriverFailure, match="missing value"):
        safari.webdriver_value({"status": 0})


class _UrlResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _UrlResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_webdriver_request_encodes_json_and_rejects_bad_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, bytes | None]] = []

    def success(request: urllib.request.Request, **_kwargs: object) -> _UrlResponse:
        observed.append((request.get_method(), request.data))
        return _UrlResponse({"value": {"ready": True}})

    monkeypatch.setattr(safari.urllib.request, "urlopen", success)
    assert safari._request(4444, "POST", "/session", payload={"x": 1}) == {
        "value": {"ready": True}
    }
    assert observed == [("POST", b'{"x": 1}')]

    monkeypatch.setattr(safari.urllib.request, "urlopen", lambda *_args, **_kwargs: _UrlResponse([]))
    with pytest.raises(safari.WebDriverFailure, match="non-object"):
        safari._request(4444, "GET", "/status")

    monkeypatch.setattr(
        safari.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    with pytest.raises(safari.WebDriverFailure, match="unavailable"):
        safari._request(4444, "GET", "/status")


@pytest.mark.parametrize(
    ("body", "message"),
    (
        (b'{"value":{"message":"remote automation disabled"}}', "remote automation"),
        (b'{"message":"driver failed"}', "driver failed"),
        (b"not-json", "request failed"),
    ),
)
def test_webdriver_request_extracts_safe_http_error(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    message: str,
) -> None:
    error = urllib.error.HTTPError("http://driver", 500, "failure", {}, BytesIO(body))
    monkeypatch.setattr(
        safari.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    with pytest.raises(safari.WebDriverFailure, match=message):
        safari._request(4444, "POST", "/session")


def test_driver_output_is_bounded() -> None:
    completed = type("Completed", (), {"communicate": lambda self, timeout: ("out", "err")})()
    assert safari._driver_output(completed) == "out\nerr"  # type: ignore[arg-type]

    class TimedOut:
        def communicate(self, timeout: int) -> tuple[str, str]:
            raise subprocess.TimeoutExpired(("safaridriver",), timeout)

    assert safari._driver_output(TimedOut()) == ""  # type: ignore[arg-type]


class _FakeProcess:
    def __init__(self, *, exit_early: bool = False, wait_timeout: bool = False) -> None:
        self.returncode: int | None = 1 if exit_early else None
        self.wait_timeout = wait_timeout
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def communicate(self, timeout: int) -> tuple[str, str]:
        del timeout
        return "", "Safari remote automation is disabled" if self.returncode else ""

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: int) -> int:
        if self.wait_timeout and not self.killed:
            self.wait_timeout = False
            raise subprocess.TimeoutExpired(("safaridriver",), timeout)
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.killed = True


def test_running_driver_retries_then_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(wait_timeout=True)
    attempts = iter((safari.WebDriverFailure("starting"), {"value": {"ready": True}}))
    monkeypatch.setattr(safari, "_free_port", lambda: 4321)
    monkeypatch.setattr(safari.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(safari.time, "sleep", lambda _seconds: None)

    def request(*_args: object, **_kwargs: object) -> dict[str, object]:
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(safari, "_request", request)
    with safari._running_driver(
        safari.SafariPlan("native-safari", "/usr/bin/safaridriver", "/tmp/gludd-x", 60),
        safari.time.monotonic() + 60,
    ) as port:
        assert port == 4321
    assert process.terminated and process.killed


def test_running_driver_reports_disabled_automation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(safari, "_free_port", lambda: 4321)
    monkeypatch.setattr(
        safari.subprocess,
        "Popen",
        lambda *args, **kwargs: _FakeProcess(exit_early=True),
    )
    with (
        pytest.raises(safari.RemoteAutomationUnavailable, match="Allow Remote Automation"),
        safari._running_driver(
            safari.SafariPlan("native-safari", "/usr/bin/safaridriver", "/tmp/gludd-x", 60),
            safari.time.monotonic() + 60,
        ),
    ):
        pass


def test_served_artifact_uses_exact_pages_subpath(tmp_path: Path) -> None:
    output = tmp_path / "native-safari"
    with safari._served_artifact(output) as url:
        assert url.endswith("/gludd/")
        with urllib.request.urlopen(url, timeout=2) as response:
            page = response.read().decode("utf-8")
        assert "presentation.js" in page
    assert list(output.iterdir()) == []


def _acceptance() -> dict[str, object]:
    return {
        "chartCount": 26,
        "clientErrorCount": 0,
        "failed": 0,
        "geometryFailureCount": 0,
        "ok": True,
        "readinessMs": 900.0,
        "rendered": 26,
        "slideCount": 30,
        "sourceViewer": True,
    }


def test_acceptance_validator_rejects_failure_and_incomplete_evidence() -> None:
    assert safari._validate_acceptance(_acceptance())["rendered"] == 26
    with pytest.raises(safari.WebDriverFailure, match="TimeoutError"):
        safari._validate_acceptance({"ok": False, "error": "TimeoutError"})
    with pytest.raises(safari.WebDriverFailure, match="invalid-result"):
        safari._validate_acceptance(None)
    with pytest.raises(safari.WebDriverFailure, match="incomplete"):
        safari._validate_acceptance({"ok": True})


def test_native_smoke_runs_cold_and_cached_and_deletes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def artifact(_output: Path) -> Iterator[str]:
        yield "http://127.0.0.1:8123/gludd/"

    @contextmanager
    def driver(_plan: safari.SafariPlan, _deadline: float) -> Iterator[int]:
        yield 4321

    calls: list[tuple[str, str]] = []

    def request(_port: int, method: str, path: str, **_kwargs: object) -> dict[str, Any]:
        calls.append((method, path))
        if path == "/session":
            return {
                "value": {
                    "sessionId": "session-1",
                    "capabilities": {"browserVersion": "99.1"},
                }
            }
        if path.endswith("/execute/async"):
            return {"value": _acceptance()}
        return {"value": None}

    monkeypatch.setattr(safari, "_served_artifact", artifact)
    monkeypatch.setattr(safari, "_running_driver", driver)
    monkeypatch.setattr(safari, "_request", request)
    plan = safari.SafariPlan("native-safari", "/usr/bin/safaridriver", "/tmp/gludd-x", 60)

    report = safari._run_native_smoke(plan)

    assert report["status"] == "passed"
    assert report["browser_version"] == "99.1"
    assert report["chart_count"] == 26
    assert sum(path.endswith("/execute/async") for _, path in calls) == 2
    assert ("DELETE", "/session/session-1") in calls


def test_native_smoke_promotes_remote_automation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def artifact(_output: Path) -> Iterator[str]:
        yield "http://127.0.0.1:8123/gludd/"

    @contextmanager
    def driver(_plan: safari.SafariPlan, _deadline: float) -> Iterator[int]:
        yield 4321

    monkeypatch.setattr(safari, "_served_artifact", artifact)
    monkeypatch.setattr(safari, "_running_driver", driver)
    monkeypatch.setattr(
        safari,
        "_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            safari.WebDriverFailure("Safari remote automation is disabled")
        ),
    )
    plan = safari.SafariPlan("native-safari", "/usr/bin/safaridriver", "/tmp/gludd-x", 60)
    with pytest.raises(safari.RemoteAutomationUnavailable, match="Allow Remote Automation"):
        safari._run_native_smoke(plan)


def test_run_plan_success_unavailable_and_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "reports"
    plan = safari.SafariPlan("native-safari", "/driver", str(output), 60)
    monkeypatch.setattr(safari, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(safari, "_run_native_smoke", lambda _plan: {"status": "passed"})
    assert safari.run_plan(plan) == 0
    assert json.loads((output / "last-report.json").read_text(encoding="utf-8"))["status"] == "passed"

    monkeypatch.setattr(
        safari,
        "_run_native_smoke",
        lambda _plan: (_ for _ in ()).throw(
            safari.RemoteAutomationUnavailable(safari.REMOTE_AUTOMATION_GUIDANCE)
        ),
    )
    assert safari.run_plan(plan) == 3
    blocked = json.loads((output / "last-report.json").read_text(encoding="utf-8"))
    assert blocked == {
        "browser": "native-safari",
        "operator_action": "enable-remote-automation",
        "status": "remote-automation-disabled",
    }

    monkeypatch.setattr(
        safari,
        "_run_native_smoke",
        lambda _plan: (_ for _ in ()).throw(safari.WebDriverFailure("failed")),
    )
    assert safari.run_plan(plan) == 1
