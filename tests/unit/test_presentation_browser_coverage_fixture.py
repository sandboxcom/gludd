"""Exercise the real browser fixtures without launching a browser or c8."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from collections.abc import Callable, Generator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Protocol, cast

import pytest

if TYPE_CHECKING:
    import run_presentation_browser_tests as runner
else:
    from scripts import run_presentation_browser_tests as runner

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def browser_fixture_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gludd_presentation_fixture_controls", ROOT / "tests/browser/conftest.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv(runner.JS_COVERAGE_DIR_ENV, raising=False)
    monkeypatch.delenv(runner.JS_COVERAGE_MANIFEST_ENV, raising=False)
    return module


class _WrappedFixture(Protocol):
    __wrapped__: Callable[..., object]


class _ReportHookModule(Protocol):
    def pytest_runtest_makereport(self, item: object) -> Generator[None, object, None]: ...


def _fixture(module: ModuleType, name: str) -> Callable[..., object]:
    return cast(_WrappedFixture, getattr(module, name)).__wrapped__


def _request(*, failed: bool | None = None) -> SimpleNamespace:
    node = SimpleNamespace(nodeid="tests/browser/test_fixture.py::diagnostic")
    if failed is not None:
        node.rep_call = SimpleNamespace(failed=failed)
    return SimpleNamespace(node=node)


class _FixtureSession:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def send(
        self, method: str, params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del params
        self.calls.append(method)
        if method == "Profiler.takePreciseCoverage":
            return {"result": [], "timestamp": 1.0}
        return {}


class _FixtureContext:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.browser: SimpleNamespace | None = SimpleNamespace(version="fake-browser-control")

    def new_cdp_session(self, _page: object) -> _FixtureSession:
        self.calls.append("attach-cdp")
        return _FixtureSession(self.calls)

    def close(self) -> None:
        self.calls.append("context.close")


class _FixturePage:
    def __init__(self, *, artifact_fault: str = "") -> None:
        self.calls: list[str] = []
        self.context = _FixtureContext(self.calls)
        self.handlers: dict[str, Callable[[object], None]] = {}
        self.scripts: list[str] = []
        self.artifact_fault = artifact_fault

    def add_init_script(self, script: str) -> None:
        self.scripts.append(script)

    def on(self, event: str, callback: Callable[[object], None]) -> None:
        self.handlers[event] = callback

    def evaluate(self, expression: str) -> object:
        if "gluddPresentationActiveDiagram" in expression:
            return "diagram42"
        if self.artifact_fault == "health":
            raise RuntimeError("closed health channel")
        return {"pending": 0, "rendered": 2}

    def content(self) -> str:
        if self.artifact_fault == "content":
            raise RuntimeError("closed content channel")
        return "<html>retained browser diagnostic</html>"

    def screenshot(self, *, path: str, full_page: bool) -> None:
        assert full_page
        if self.artifact_fault == "screenshot":
            raise RuntimeError("closed screenshot channel")
        Path(path).write_bytes(b"fake-browser-screenshot")

    def goto(self, url: str, *, wait_until: str) -> str:
        self.calls.append(f"goto:{url}:{wait_until}")
        return "response"

    def reload(self, *, wait_until: str) -> str:
        self.calls.append(f"reload:{wait_until}")
        return "reloaded"

    def close(self) -> None:
        self.calls.append("page.close")


def _coverage_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, dict[str, object]]:
    output = tmp_path / "javascript-coverage"
    output.mkdir()
    source = (runner.ROOT / runner.CONTROLLER_RELATIVE).resolve()
    payload: dict[str, object] = {
        "schema_version": 1, "browser": "chromium", "source_path": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    manifest = output / "source.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv(runner.JS_COVERAGE_DIR_ENV, str(output))
    monkeypatch.setenv(runner.JS_COVERAGE_MANIFEST_ENV, str(manifest))
    return output, manifest, payload


def _coverage_generator(module: ModuleType, page: _FixturePage) -> Generator[None, None, None]:
    return cast(Generator[None, None, None], _fixture(
        module, "_retain_presentation_javascript_coverage",
    )(page, "chromium", _request()))


def test_coverage_fixture_default_absent_never_attaches_cdp(
    browser_fixture_module: ModuleType,
) -> None:
    page = _FixturePage()
    generator = _coverage_generator(browser_fixture_module, page)
    assert next(generator) is None
    with pytest.raises(StopIteration):
        next(generator)
    assert page.calls == []


@pytest.mark.parametrize("coverage_mode", ("only-directory", "only-manifest", "webkit"))
def test_coverage_fixture_rejects_incomplete_carriers_or_unsupported_engine(
    browser_fixture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    coverage_mode: str,
) -> None:
    _output, _manifest, _payload = _coverage_manifest(tmp_path, monkeypatch)
    if coverage_mode == "only-directory":
        monkeypatch.delenv(runner.JS_COVERAGE_MANIFEST_ENV)
    elif coverage_mode == "only-manifest":
        monkeypatch.delenv(runner.JS_COVERAGE_DIR_ENV)
    page = _FixturePage()
    generator = cast(Generator[None, None, None], _fixture(
        browser_fixture_module, "_retain_presentation_javascript_coverage",
    )(page, "webkit" if coverage_mode == "webkit" else "chromium", _request()))
    with pytest.raises(RuntimeError, match="Chromium"):
        next(generator)
    assert page.calls == []


@pytest.mark.parametrize(
    "manifest_fault",
    ("outside-manifest", "missing-file", "schema", "engine", "source", "digest"),
)
def test_coverage_fixture_refuses_unbound_manifest_before_cdp(
    browser_fixture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    manifest_fault: str,
) -> None:
    output, manifest, payload = _coverage_manifest(tmp_path, monkeypatch)
    if manifest_fault == "outside-manifest":
        monkeypatch.setenv(runner.JS_COVERAGE_MANIFEST_ENV, str(output / "other.json"))
    elif manifest_fault == "missing-file":
        manifest.unlink()
    else:
        key, value = {
            "schema": ("schema_version", 2), "engine": ("browser", "webkit"),
            "source": ("source_path", str(output / "other.js")), "digest": ("source_sha256", None),
        }[manifest_fault]
        payload[key] = value
        manifest.write_text(json.dumps(payload), encoding="utf-8")
    page = _FixturePage()
    with pytest.raises(RuntimeError):
        next(_coverage_generator(browser_fixture_module, page))
    assert page.calls == []


def test_coverage_fixture_requires_actual_browser_identity(
    browser_fixture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _coverage_manifest(tmp_path, monkeypatch)
    page = _FixturePage()
    page.context.browser = None
    with pytest.raises(RuntimeError, match="browser identity"):
        next(_coverage_generator(browser_fixture_module, page))
    assert page.calls == ["attach-cdp"]


@pytest.mark.parametrize("body_fault", (False, True))
def test_coverage_fixture_starts_before_body_and_finishes_on_success_or_failure(
    browser_fixture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    body_fault: bool,
) -> None:
    output, manifest, payload = _coverage_manifest(tmp_path, monkeypatch)
    page = _FixturePage()
    original = page.reload
    generator = _coverage_generator(browser_fixture_module, page)
    assert next(generator) is None
    assert page.calls[-1] == "Profiler.startPreciseCoverage"
    assert page.reload(wait_until="networkidle") == "reloaded"
    assert page.calls[-2:] == ["Profiler.takePreciseCoverage", "reload:networkidle"]
    if body_fault:
        with pytest.raises(ValueError, match="browser assertion"):
            generator.throw(ValueError("browser assertion"))
    else:
        with pytest.raises(StopIteration):
            next(generator)
    assert page.calls[-1] == "Profiler.stopPreciseCoverage"
    assert page.reload == original
    runtime_files = list(output.glob("runtime-*.json"))
    assert len(runtime_files) == 1
    runtime = json.loads(runtime_files[0].read_text(encoding="utf-8"))
    assert runtime["browser_version"] == "fake-browser-control"
    assert runtime["source_sha256"] == payload["source_sha256"]
    assert runtime["source_manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert len(list((output / "raw").glob("*.json"))) == 2
    assert list((output / "normalized").glob("*.json")) == []


def test_browser_event_fixture_keeps_geometry_and_all_error_channels(
    browser_fixture_module: ModuleType,
) -> None:
    page = _FixturePage()
    events = _fixture(browser_fixture_module, "browser_events")(page)
    assert isinstance(events, dict)
    assert len(page.scripts) == 1 and "gluddInvalidGeometryWrites" in page.scripts[0]
    page.handlers["console"](SimpleNamespace(type="info", text="ordinary message"))
    assert events["console_errors"] == []
    page.handlers["console"](SimpleNamespace(type="error", text="render failure"))
    page.handlers["pageerror"](RuntimeError("page failure"))
    page.handlers["requestfailed"](SimpleNamespace(url="http://127.0.0.1/blocked.js"))
    page.handlers["response"](SimpleNamespace(status=200, url="http://127.0.0.1/ok"))
    page.handlers["response"](SimpleNamespace(status=503, url="http://127.0.0.1/unavailable"))
    assert events == {
        "console_errors": ["diagram=diagram42 render failure"],
        "page_errors": ["page failure"],
        "request_failures": ["http://127.0.0.1/blocked.js"],
        "http_failures": ["503 http://127.0.0.1/unavailable"],
    }


@pytest.mark.parametrize("report_phase", ("absent", "passed"))
def test_browser_failure_fixture_avoids_artifacts_without_failed_call(
    browser_fixture_module: ModuleType,
    tmp_path: Path,
    report_phase: str,
) -> None:
    page = _FixturePage()
    request = _request(failed=None if report_phase == "absent" else False)
    generator = cast(Generator[None, None, None], _fixture(
        browser_fixture_module, "_retain_browser_failure_artifacts",
    )(page, request))
    next(generator)
    with pytest.raises(StopIteration):
        next(generator)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("artifact_fault", ("none", "content", "screenshot", "health"))
def test_browser_failure_fixture_retains_diagnostics_or_capture_error(
    browser_fixture_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    artifact_fault: str,
) -> None:
    output = tmp_path / "diagnostics"
    monkeypatch.setenv("GLUDD_PRESENTATION_BROWSER_OUTPUT", str(output))
    page = _FixturePage(artifact_fault=artifact_fault)
    generator = cast(Generator[None, None, None], _fixture(
        browser_fixture_module, "_retain_browser_failure_artifacts",
    )(page, _request(failed=True)))
    next(generator)
    with pytest.raises(StopIteration):
        next(generator)
    if artifact_fault == "none":
        assert len(list(output.glob("*.html"))) == 1
        assert len(list(output.glob("*.png"))) == 1
        health = list(output.glob("*.health.json"))
        assert len(health) == 1
        assert json.loads(health[0].read_text(encoding="utf-8")) == {"pending": 0, "rendered": 2}
    else:
        failures = list(output.glob("*.capture-error.txt"))
        assert len(failures) == 1
        assert failures[0].read_text(encoding="utf-8") == "RuntimeError\n"


def test_browser_report_hook_exposes_native_report_on_item(
    browser_fixture_module: ModuleType,
) -> None:
    item = SimpleNamespace()
    report = SimpleNamespace(when="call", failed=True)
    hook = cast(_ReportHookModule, browser_fixture_module).pytest_runtest_makereport
    generator = hook(item)
    assert next(generator) is None
    outcome = SimpleNamespace(get_result=lambda: report)
    with pytest.raises(StopIteration):
        generator.send(outcome)
    assert item.rep_call is report
