"""Tests for the bounded Playwright presentation runner."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import replace
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, TypedDict

import pytest

if TYPE_CHECKING:
    import run_presentation_browser_tests as runner
else:
    from scripts import run_presentation_browser_tests as runner


class _BrowserRoots(TypedDict):
    browser_root: Path
    output_root: Path


def _write_presentation_profile(root: Path, dependencies: list[str]) -> Path:
    profile = root / "requirements" / "profiles" / "presentation-test" / "pyproject.toml"
    profile.parent.mkdir(parents=True, exist_ok=True)
    rendered = ", ".join(f'"{dependency}"' for dependency in dependencies)
    profile.write_text(
        '[project]\nname = "presentation"\nversion = "0"\n'
        f"dependencies = [{rendered}]\n",
        encoding="utf-8",
    )
    return profile


def test_plan_is_serial_pinned_and_namespaced() -> None:
    """The mandatory browser proof cannot collide with another project run."""
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-unit"),
        output_root=Path("/tmp/gludd-presentation-unit"),
        timeout_seconds=120,
    )

    assert plan.browser == "chromium"
    assert plan.timeout_seconds == 120
    assert plan.command[0] == runner.sys.executable
    pairs = tuple(zip(plan.command, plan.command[1:], strict=False))
    assert ("-n", "0") in pairs
    assert ("-m", "presentation_browser") in pairs
    assert ("--tracing", "retain-on-failure") in pairs
    assert "--capture=tee-sys" in plan.command


def test_webkit_plan_is_a_first_class_browser_contract() -> None:
    """Safari-compatible WebKit must be runnable through the bounded harness."""
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-webkit-unit"),
        output_root=Path("/tmp/gludd-presentation-webkit-unit"),
        timeout_seconds=120,
    )

    assert plan.browser == "webkit"
    for run in plan.runs:
        pairs = tuple(zip(run.command, run.command[1:], strict=False))
        assert ("--browser", "webkit") in pairs


def test_plan_replays_only_layout_containment_at_a_second_aspect_ratio() -> None:
    """The viewport matrix must broaden geometry proof without rerunning the suite."""
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-viewport-unit"),
        output_root=Path("/tmp/gludd-presentation-viewport-unit"),
        timeout_seconds=120,
    )

    assert [
        (run.label, run.viewport_width, run.viewport_height, run.scope)
        for run in plan.runs
    ] == [
        ("desktop-landscape", 1280, 720, "full-suite"),
        ("compact-4x3", 1024, 768, "layout-containment"),
    ]
    desktop, compact = plan.runs
    assert "--device" not in desktop.command
    assert "--device" not in compact.command
    compact_pairs = tuple(zip(compact.command, compact.command[1:], strict=False))
    assert ("-p", "scripts.run_presentation_browser_tests") in compact_pairs
    assert runner.LAYOUT_CONTAINMENT_TEST in compact.command
    assert runner.TEST_FILE.relative_to(runner.ROOT).as_posix() in desktop.command
    assert runner.TEST_FILE.relative_to(runner.ROOT).as_posix() not in compact.command
    assert desktop.output_root != compact.output_root


def test_viewport_plugin_applies_exact_css_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The focused replay changes viewport only, without mobile-device emulation."""

    class Item:
        def __init__(self) -> None:
            self.markers: list[pytest.MarkDecorator] = []

        def add_marker(self, marker: object) -> None:
            assert isinstance(marker, pytest.MarkDecorator)
            self.markers.append(marker)

    item = Item()
    monkeypatch.setenv(runner.VIEWPORT_SIZE_ENV, "1024x768")

    runner.pytest_collection_modifyitems([item])  # type: ignore[list-item]

    assert len(item.markers) == 1
    marker = item.markers[0]
    assert marker.mark.name == "browser_context_args"
    assert marker.mark.kwargs == {"viewport": {"width": 1024, "height": 768}}


@pytest.mark.parametrize(
    "path",
    (Path("/tmp"), Path("/tmp/presentation"), Path("/var/tmp/gludd-presentation")),
)
def test_plan_rejects_unowned_output_roots(path: Path) -> None:
    """Artifacts and browsers may only use an explicit Gludd namespace."""
    with pytest.raises(ValueError, match="/tmp/gludd"):
        runner.build_plan(
            browser="chromium",
            browser_root=Path("/tmp/gludd-browser-unit"),
            output_root=path,
            timeout_seconds=120,
        )


def test_validate_plan_is_side_effect_free(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Validate-only reads contracts but never creates output or launches pytest."""
    root = tmp_path / "repo"
    test_file = root / "tests" / "browser" / "test_presentation.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_placeholder(): pass\n", encoding="utf-8")
    _write_presentation_profile(
        root,
        ["playwright==1.63.0", "pytest-playwright==0.9.0"],
    )
    monkeypatch.setattr(runner, "ROOT", root)
    monkeypatch.setattr(runner, "TEST_FILE", test_file)
    output = Path("/tmp/gludd-presentation-validate-only-test")
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-validate-only-test"),
        output_root=output,
        timeout_seconds=120,
    )

    runner.validate_plan(plan)

    assert not output.exists()


def test_validate_plan_reads_the_isolated_presentation_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The live runner validates the profile used by the Make target."""
    root = tmp_path / "repo"
    test_file = root / "tests" / "browser" / "test_presentation.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_placeholder(): pass\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "root-without-browser-dependencies"\nversion = "0"\n',
        encoding="utf-8",
    )
    _write_presentation_profile(
        root,
        ["playwright==1.63.0", "pytest-playwright==0.9.0"],
    )
    monkeypatch.setattr(runner, "ROOT", root)
    monkeypatch.setattr(runner, "TEST_FILE", test_file)
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-profile-test"),
        output_root=Path("/tmp/gludd-presentation-profile-test"),
        timeout_seconds=120,
    )

    runner.validate_plan(plan)


def test_timeout_returns_observable_124(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A stuck browser is bounded and uses the conventional timeout status."""
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-timeout-test"),
        output_root=Path("/tmp/gludd-presentation-timeout-test"),
        timeout_seconds=30,
    )
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(runner, "_require_browser_executable", lambda _plan: None)

    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise subprocess.TimeoutExpired(plan.command, plan.timeout_seconds)

    monkeypatch.setattr(runner.subprocess, "run", timeout)

    assert runner.run_plan(plan) == 124


def test_run_plan_executes_both_viewports_with_each_process_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The targeted replay retains the process cap and isolated artifacts."""
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-viewport-run-test"),
        output_root=Path("/tmp/gludd-presentation-viewport-run-test"),
        timeout_seconds=30,
    )
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(runner, "_require_browser_executable", lambda _plan: None)
    captured: list[tuple[tuple[str, ...], float, str, str | None]] = []

    def complete(
        command: tuple[str, ...],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        timeout = kwargs["timeout"]
        assert isinstance(environment, dict)
        assert isinstance(timeout, (int, float))
        captured.append(
            (
                command,
                float(timeout),
                str(environment["GLUDD_PRESENTATION_BROWSER_OUTPUT"]),
                environment.get(runner.VIEWPORT_SIZE_ENV),
            )
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", complete)

    assert runner.run_plan(plan) == 0
    assert captured == [
        (plan.runs[0].command, 30.0, plan.runs[0].output_root, None),
        (plan.runs[1].command, 30.0, plan.runs[1].output_root, "1024x768"),
    ]


@pytest.mark.parametrize("browser_engine", ("chromium", "webkit"))
def test_browser_install_is_exact_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
    browser_engine: str,
) -> None:
    """Installation acquires one requested engine and verifies its binary."""
    plan = runner.build_plan(
        browser=browser_engine,
        browser_root=Path("/tmp/gludd-browser-install-test"),
        output_root=Path("/tmp/gludd-presentation-install-test"),
        timeout_seconds=120,
    )
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    verified: list[runner.BrowserPlan] = []
    monkeypatch.setattr(runner, "_require_browser_executable", verified.append)
    captured: list[tuple[str, ...]] = []

    def complete(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        captured.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", complete)

    assert runner.install_browser(plan) == 0
    assert captured == [
        (runner.sys.executable, "-m", "playwright", "install", browser_engine)
    ]
    assert verified == [plan]


def test_browser_install_with_dependencies_uses_the_locked_runtime_and_launch_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hosted WebKit uses Playwright's combined install and verifies it launches."""
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-deps-test"),
        output_root=Path("/tmp/gludd-presentation-deps-test"),
        timeout_seconds=120,
    )
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    binaries: list[runner.BrowserPlan] = []
    launches: list[runner.BrowserPlan] = []
    monkeypatch.setattr(runner, "_require_browser_executable", binaries.append)
    monkeypatch.setattr(runner, "_require_browser_launchable", launches.append)
    captured: list[tuple[tuple[str, ...], str]] = []

    def complete(
        command: tuple[str, ...],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        captured.append((command, str(environment["PLAYWRIGHT_BROWSERS_PATH"])))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", complete)

    assert runner.install_browser(plan, with_dependencies=True) == 0
    assert captured == [
        (
            (
                runner.sys.executable,
                "-m",
                "playwright",
                "install",
                "--with-deps",
                "webkit",
            ),
            plan.browser_root,
        )
    ]
    assert binaries == [plan]
    assert launches == [plan]


def test_plan_rejects_unbounded_timeout() -> None:
    """The make target cannot accidentally disable the process deadline."""
    with pytest.raises(ValueError, match="between 30 and 900"):
        runner.build_plan(
            browser="chromium",
            browser_root=Path("/tmp/gludd-browser-unit"),
            output_root=Path("/tmp/gludd-presentation-unit"),
            timeout_seconds=901,
        )


def test_plan_rejects_wrong_browser_and_short_timeout() -> None:
    """The runner cannot silently change engine or drop its lower bound."""
    common: _BrowserRoots = {
        "browser_root": Path("/tmp/gludd-browser-unit"),
        "output_root": Path("/tmp/gludd-presentation-unit"),
    }
    with pytest.raises(ValueError, match="Chromium or WebKit"):
        runner.build_plan(browser="firefox", timeout_seconds=120, **common)
    with pytest.raises(ValueError, match="between 30 and 900"):
        runner.build_plan(browser="chromium", timeout_seconds=29, **common)


def test_validate_plan_fails_closed_for_missing_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Static validation rejects every incomplete or indirect execution plan."""
    root = tmp_path / "repo"
    root.mkdir()
    test_file = root / "tests" / "browser" / "test_presentation.py"
    monkeypatch.setattr(runner, "ROOT", root)
    monkeypatch.setattr(runner, "TEST_FILE", test_file)
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-validation"),
        output_root=Path("/tmp/gludd-presentation-validation"),
        timeout_seconds=120,
    )
    with pytest.raises(RuntimeError, match="test file is missing"):
        runner.validate_plan(plan)

    test_file.parent.mkdir(parents=True)
    test_file.write_text("pass\n", encoding="utf-8")
    _write_presentation_profile(root, ["playwright==1.63.0"])
    with pytest.raises(RuntimeError, match="pytest-playwright"):
        runner.validate_plan(plan)

    _write_presentation_profile(
        root,
        ["playwright==1.63.0", "pytest-playwright==0.9.0"],
    )
    with pytest.raises(RuntimeError, match="direct bounded argv"):
        unsafe_run = replace(plan.runs[0], command=("shell",))
        runner.validate_plan(replace(plan, runs=(unsafe_run, *plan.runs[1:])))


def test_browser_executable_check_handles_available_and_missing_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The read-only check resolves Playwright's executable and fails visibly."""
    executable = tmp_path / "chromium"
    executable.write_text("binary", encoding="utf-8")

    class FakePlaywright:
        def __init__(self, path: Path) -> None:
            self.chromium = SimpleNamespace(executable_path=str(path))

        def __enter__(self) -> FakePlaywright:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: FakePlaywright(executable)  # type: ignore[attr-defined]
    playwright = ModuleType("playwright")
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-check"),
        output_root=Path("/tmp/gludd-presentation-check"),
        timeout_seconds=120,
    )
    assert runner._require_browser_executable(plan) == executable

    executable.unlink()
    with pytest.raises(RuntimeError, match="Chromium is not installed"):
        runner._require_browser_executable(plan)


def test_webkit_executable_check_uses_webkit_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """WebKit checks must not accidentally inspect the Chromium executable."""
    webkit_executable = tmp_path / "webkit"
    webkit_executable.write_text("binary", encoding="utf-8")

    class FakePlaywright:
        chromium = SimpleNamespace(executable_path=str(tmp_path / "missing-chromium"))
        webkit = SimpleNamespace(executable_path=str(webkit_executable))

        def __enter__(self) -> FakePlaywright:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: FakePlaywright()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-webkit-check"),
        output_root=Path("/tmp/gludd-presentation-webkit-check"),
        timeout_seconds=120,
    )

    assert runner._require_browser_executable(plan) == webkit_executable


def test_webkit_launch_probe_closes_runtime_and_reports_missing_host_libraries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dependency installation is accepted only after the selected engine launches."""
    closed: list[bool] = []

    class FakePlaywrightError(Exception):
        pass

    class FakeBrowser:
        def close(self) -> None:
            closed.append(True)

    class FakeBrowserType:
        def __init__(self, *, failure: bool) -> None:
            self.failure = failure

        def launch(self, *, headless: bool) -> FakeBrowser:
            assert headless is True
            if self.failure:
                raise FakePlaywrightError("missing libgtk-4.so.1")
            return FakeBrowser()

    class FakePlaywright:
        def __init__(self, *, failure: bool) -> None:
            self.webkit = FakeBrowserType(failure=failure)

        def __enter__(self) -> FakePlaywright:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    sync_api = ModuleType("playwright.sync_api")
    sync_api.Error = FakePlaywrightError  # type: ignore[attr-defined]
    sync_api.sync_playwright = lambda: FakePlaywright(failure=False)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-launch-check"),
        output_root=Path("/tmp/gludd-presentation-launch-check"),
        timeout_seconds=120,
    )

    runner._require_browser_launchable(plan)
    assert closed == [True]

    sync_api.sync_playwright = lambda: FakePlaywright(failure=True)  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="WebKit cannot launch"):
        runner._require_browser_launchable(plan)


def test_install_and_run_propagate_timeout_and_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both subprocess boundaries preserve failures and conventional timeouts."""
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-results"),
        output_root=Path("/tmp/gludd-presentation-results"),
        timeout_seconds=30,
    )
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(runner, "_require_browser_executable", lambda _plan: Path("/tmp/chromium"))

    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise subprocess.TimeoutExpired(("browser",), 30)

    monkeypatch.setattr(runner.subprocess, "run", timeout)
    assert runner.install_browser(plan) == 124

    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 7),
    )
    assert runner.install_browser(plan) == 7
    assert runner.run_plan(plan) == 7


def test_main_dispatches_each_explicit_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI modes delegate exactly once and retain downstream status codes."""
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-main"),
        output_root=Path("/tmp/gludd-presentation-main"),
        timeout_seconds=120,
    )
    monkeypatch.setattr(runner, "build_plan", lambda **kwargs: plan)
    validated: list[runner.BrowserPlan] = []
    monkeypatch.setattr(runner, "validate_plan", validated.append)
    monkeypatch.setattr(runner, "_require_browser_executable", lambda _plan: Path("/tmp/chromium"))

    monkeypatch.setattr(runner.sys, "argv", ["runner", "--validate-only"])
    runner.main()
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--check-browser"])
    runner.main()
    assert validated == [plan, plan]

    monkeypatch.setattr(runner, "install_browser", lambda _plan: 17)
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--install-browser"])
    with pytest.raises(SystemExit) as installed:
        runner.main()
    assert installed.value.code == 17

    monkeypatch.setattr(
        runner,
        "install_browser",
        lambda _plan, *, with_dependencies=False: 29 if with_dependencies else 17,
    )
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--install-browser-with-deps"])
    with pytest.raises(SystemExit) as installed_with_deps:
        runner.main()
    assert installed_with_deps.value.code == 29

    monkeypatch.setattr(runner, "run_plan", lambda _plan: 23)
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--run"])
    with pytest.raises(SystemExit) as run:
        runner.main()
    assert run.value.code == 23



def _javascript_source(tmp_path: Path) -> tuple[Path, str]:
    """Keep an entire Unicode source so V8 offsets cannot be treated as bytes."""
    source = tmp_path / "presentation.js"
    source.write_text(
        "(() => { const label = 'chart 🚦'; if (label) window.ready = true; })();\n",
        encoding="utf-8",
    )
    return source, source.read_text(encoding="utf-8")


def _javascript_record(
    source: str,
    *,
    url: str = "http://127.0.0.1:8123/gludd/presentation.js?v=synthetic",
    count: int = 1,
    detailed: bool = True,
) -> dict[str, object]:
    return {
        "scriptId": "41",
        "url": url,
        "functions": [{
            "functionName": "",
            "isBlockCoverage": detailed,
            "ranges": [{
                "startOffset": 0,
                "endOffset": len(source.encode("utf-16-le")) // 2,
                "count": count,
            }],
        }],
    }


def _source_digest(source: Path) -> str:
    import hashlib

    return hashlib.sha256(source.read_bytes()).hexdigest()


def test_javascript_coverage_is_opt_in_and_chromium_only() -> None:
    """Native WebKit behavior stays available without pretending to collect V8."""
    def plan(browser: str, *, covered: bool = False) -> runner.BrowserPlan:
        return runner.build_plan(
            browser=browser,
            browser_root=Path("/tmp/gludd-js-coverage-browser-unit"),
            output_root=Path("/tmp/gludd-js-coverage-plan-unit"),
            timeout_seconds=600,
            js_coverage=covered,
        )

    assert plan("webkit").js_coverage is False
    covered = plan("chromium", covered=True)
    assert covered.js_coverage is True
    assert covered.runs == plan("chromium").runs
    with pytest.raises(ValueError, match="Chromium"):
        plan("webkit", covered=True)


@pytest.mark.parametrize("flag", ("0", "1"))
def test_javascript_coverage_cli_passes_an_explicit_boolean(
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    """Make's optional flag reaches the plan rather than an ambient environment."""
    captured: list[dict[str, object]] = []
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-js-coverage-cli-browser"),
        output_root=Path("/tmp/gludd-js-coverage-cli-output"),
        timeout_seconds=600,
    )

    def build(**kwargs: object) -> runner.BrowserPlan:
        captured.append(kwargs)
        return plan

    monkeypatch.setattr(runner, "build_plan", build)
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(
        runner.sys, "argv", ["runner", "--validate-only", "--js-coverage", flag]
    )
    runner.main()
    assert captured[0]["js_coverage"] is (flag == "1")


@pytest.mark.parametrize("flag", ("true", "-1", "2"))
def test_javascript_coverage_cli_rejects_nonbinary_values(
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    monkeypatch.setattr(
        runner.sys, "argv", ["runner", "--validate-only", "--js-coverage", flag]
    )
    with pytest.raises(SystemExit) as rejected:
        runner.main()
    assert rejected.value.code == 2


def test_ordinary_browser_run_clears_inherited_coverage_carriers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default lane cannot accidentally inherit instrumented timing."""
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-js-coverage-default-browser"),
        output_root=Path("/tmp/gludd-js-coverage-default-output"),
        timeout_seconds=600,
    )
    monkeypatch.setenv("GLUDD_PRESENTATION_JS_COVERAGE_DIR", "/tmp/gludd-stale-js-coverage")
    monkeypatch.setenv(
        "GLUDD_PRESENTATION_JS_COVERAGE_MANIFEST", "/tmp/gludd-stale-js-source.json"
    )
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(runner, "_require_browser_executable", lambda _plan: None)
    environments: list[dict[str, str]] = []

    def complete(
        command: tuple[str, ...], **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        environments.append({str(key): str(value) for key, value in environment.items()})
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", complete)
    assert runner.run_plan(plan) == 0
    assert len(environments) == 2
    for environment in environments:
        assert "GLUDD_PRESENTATION_JS_COVERAGE_DIR" not in environment
        assert "GLUDD_PRESENTATION_JS_COVERAGE_MANIFEST" not in environment


@pytest.mark.parametrize(
    "url",
    (
        "http://127.0.0.1:8123/gludd/presentation.js?v=synthetic",
        "http://localhost:8123/presentation.js",
        "file:///tmp/gludd-preview/deck/presentation.js?v=synthetic",
    ),
)
def test_javascript_coverage_normalizes_only_complete_verified_controller(
    tmp_path: Path,
    url: str,
) -> None:
    """Query labels and preview copies require equal full source before merging."""
    source_path, source = _javascript_source(tmp_path)
    original = _javascript_record(source, url=url)
    normalized = runner.normalize_javascript_coverage(
        {"result": [original]},
        source_path=source_path,
        expected_sha256=_source_digest(source_path),
        sources={"41": source},
    )
    assert normalized == {
        "result": [{**original, "url": str(source_path.resolve())}],
    }
    assert original["url"] == url


@pytest.mark.parametrize(
    "fault",
    (
        "no-execution", "missing-result", "wrong-result", "missing-functions",
        "incomplete-blocks", "empty-ranges", "negative-count", "reverse-range",
        "past-source", "missing-source", "changed-body", "unknown-origin",
        "fragment-stdin", "changed-file",
    ),
)
def test_javascript_coverage_rejects_incomplete_or_untrusted_execution(
    tmp_path: Path,
    fault: str,
) -> None:
    """A nonempty browser result is insufficient without complete source identity."""
    source_path, source = _javascript_source(tmp_path)
    digest = _source_digest(source_path)
    record = _javascript_record(source)
    sources = {"41": source}
    payload: object = {"result": [record]}
    if fault == "no-execution":
        payload = {"result": []}
    elif fault == "missing-result":
        payload = {}
    elif fault == "wrong-result":
        payload = {"result": {}}
    elif fault == "missing-functions":
        record.pop("functions")
    elif fault == "incomplete-blocks":
        record["functions"] = [{"isBlockCoverage": False, "ranges": []}]
    elif fault == "empty-ranges":
        record["functions"] = [{"isBlockCoverage": True, "ranges": []}]
    elif fault in {"negative-count", "reverse-range", "past-source"}:
        ranges = {
            "negative-count": {"startOffset": 0, "endOffset": 1, "count": -1},
            "reverse-range": {"startOffset": 2, "endOffset": 1, "count": 1},
            "past-source": {"startOffset": 0, "endOffset": 10000, "count": 1},
        }
        record["functions"] = [{"isBlockCoverage": True, "ranges": [ranges[fault]]}]
    elif fault == "missing-source":
        sources = {}
    elif fault == "changed-body":
        sources["41"] = source + "// another source\n"
    elif fault == "unknown-origin":
        record["url"] = "https://unowned.example/presentation.js"
    elif fault == "fragment-stdin":
        record["url"] = "[stdin]"
    elif fault == "changed-file":
        source_path.write_text(source + "// changed candidate\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        runner.normalize_javascript_coverage(
            payload,
            source_path=source_path,
            expected_sha256=digest,
            sources=sources,
        )


class _BoundaryContext:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("context.close")


class _BoundaryPage:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.context = _BoundaryContext(events)

    def goto(self, url: str, *, wait_until: str) -> str:
        self.events.append(f"goto:{url}:{wait_until}")
        return "response"

    def reload(self, *, wait_until: str) -> str:
        self.events.append(f"reload:{wait_until}")
        return "reloaded"

    def close(self) -> None:
        self.events.append("page.close")


class _BoundaryCollector:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def snapshot(self, reason: str) -> None:
        self.events.append(f"snapshot:{reason}")


def test_javascript_coverage_snapshots_before_real_navigation_reload_and_close() -> None:
    """Old-isolate data must be retained before the public method destroys it."""
    events: list[str] = []
    page = _BoundaryPage(events)
    originals = (page.goto, page.reload, page.close, page.context.close)
    with runner.coverage_boundaries(page, _BoundaryCollector(events)):
        assert page.goto("http://127.0.0.1/deck/", wait_until="networkidle") == "response"
        assert page.reload(wait_until="networkidle") == "reloaded"
        page.close()
        page.context.close()
    assert events == [
        "snapshot:goto", "goto:http://127.0.0.1/deck/:networkidle",
        "snapshot:reload", "reload:networkidle",
        "snapshot:page.close", "page.close",
        "snapshot:context.close", "context.close",
    ]
    assert (page.goto, page.reload, page.close, page.context.close) == originals


def test_javascript_coverage_boundary_restores_after_failure_and_propagates() -> None:
    events: list[str] = []
    page = _BoundaryPage(events)
    original = page.goto
    with (
        pytest.raises(RuntimeError, match="test failure"),
        runner.coverage_boundaries(page, _BoundaryCollector(events)),
    ):
        raise RuntimeError("test failure")
    assert page.goto == original
    assert page.goto("about:blank", wait_until="load") == "response"
    assert events == ["goto:about:blank:load"]


def test_javascript_coverage_snapshot_failure_prevents_navigation() -> None:
    events: list[str] = []
    page = _BoundaryPage(events)

    class FailedCollector:
        def snapshot(self, reason: str) -> None:
            raise RuntimeError(f"coverage unavailable before {reason}")

    with (
        runner.coverage_boundaries(page, FailedCollector()),
        pytest.raises(RuntimeError, match="coverage unavailable"),
    ):
        page.goto("about:blank", wait_until="load")
    assert events == []


class _CoverageSession:
    def __init__(
        self, source: str, events: list[str], counts: tuple[int, ...] = (1,),
    ) -> None:
        self.source = source
        self.events = events
        self.samples = 0
        self.counts = counts

    def send(
        self, method: str, params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.events.append(method)
        if method == "Profiler.startPreciseCoverage":
            assert params is not None
            assert params["callCount"] is True
            assert params["detailed"] is True
        if method == "Profiler.takePreciseCoverage":
            count = self.counts[min(self.samples, len(self.counts) - 1)]
            self.samples += 1
            return {"result": [_javascript_record(self.source, count=count)], "timestamp": self.samples}
        if method == "Debugger.getScriptSource":
            assert params == {"scriptId": "41"}
            return {"scriptSource": self.source}
        return {}


def test_javascript_coverage_collector_retains_raw_and_normalized_each_snapshot(
    tmp_path: Path,
) -> None:
    """Sequential reset-on-sample records reach c8 separately, without manual sums."""
    import json

    source_path, source = _javascript_source(tmp_path)
    events: list[str] = []
    output = tmp_path / "coverage"
    collector = runner.JavaScriptCoverageCollector(
        _CoverageSession(source, events),
        source_path=source_path,
        expected_sha256=_source_digest(source_path),
        output_root=output,
        test_id="browser::cold-and-cached",
    )
    collector.start()
    collector.snapshot("reload")
    collector.finish()
    assert events.index("Profiler.startPreciseCoverage") < events.index("Profiler.takePreciseCoverage")
    assert events[-1] == "Profiler.stopPreciseCoverage"
    raw_files = sorted((output / "raw").glob("*.json"))
    normalized_files = sorted((output / "normalized").glob("*.json"))
    assert len(raw_files) == len(normalized_files) == 2
    for raw_path, normalized_path in zip(raw_files, normalized_files, strict=True):
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
        assert raw["result"][0]["url"].startswith("http://127.0.0.1:")
        assert normalized["result"][0]["url"] == str(source_path.resolve())
        assert normalized["result"][0]["functions"] == raw["result"][0]["functions"]


@pytest.mark.parametrize("failure_at", ("report", "aggregate", "per-file", "none"))
def test_javascript_coverage_native_report_or_floor_failure_is_not_green(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_at: str,
) -> None:
    """The locked c8 process owns each independent metric/floor verdict."""
    commands: list[tuple[str, ...]] = []
    phases = ("report", "aggregate", "per-file")

    def complete(
        command: tuple[str, ...], **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 600
        commands.append(command)
        failed = phases[len(commands) - 1] == failure_at
        return subprocess.CompletedProcess(command, 19 if failed else 0)

    monkeypatch.setattr(runner.subprocess, "run", complete)
    expected_status = 0 if failure_at == "none" else 19
    assert runner.report_javascript_coverage(tmp_path, timeout_seconds=600) == expected_status
    assert len(commands) == (3 if failure_at == "none" else phases.index(failure_at) + 1)
    assert all(command[0].endswith("/.opencode/node_modules/.bin/c8") for command in commands)
    assert all("--config" in command and "--temp-directory" in command for command in commands)
    assert all(str(tmp_path / "normalized") in command for command in commands)
    if len(commands) >= 2:
        pairs = tuple(zip(commands[1], commands[1][1:], strict=False))
        assert ("--lines", "85") in pairs
        assert ("--branches", "85") in pairs
        assert "--per-file" not in commands[1]
    if len(commands) == 3:
        pairs = tuple(zip(commands[2], commands[2][1:], strict=False))
        assert ("--lines", "75") in pairs
        assert ("--branches", "75") in pairs
        assert "--per-file" in commands[2]


def test_javascript_coverage_native_timeout_is_observable_124(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise subprocess.TimeoutExpired(("c8",), 600)

    monkeypatch.setattr(runner.subprocess, "run", timeout)
    assert runner.report_javascript_coverage(tmp_path, timeout_seconds=600) == 124


@pytest.mark.parametrize(
    "fault",
    ("absent", "missing-controller", "missing-lines", "missing-branches", "malformed"),
)
def test_javascript_coverage_report_must_include_complete_controller_metrics(
    tmp_path: Path,
    fault: str,
) -> None:
    """A successful native command cannot hide an empty or unrelated report."""
    import json

    source_path, _source = _javascript_source(tmp_path)
    report = tmp_path / "coverage-summary.json"
    metric = {"total": 10, "covered": 9, "skipped": 0, "pct": 90}
    file_metrics = {"lines": metric, "branches": metric}
    payload: dict[str, object] = {
        "total": dict(file_metrics),
        str(source_path.resolve()): dict(file_metrics),
    }
    if fault == "missing-controller":
        payload.pop(str(source_path.resolve()))
    elif fault == "missing-lines":
        payload[str(source_path.resolve())] = {"branches": metric}
    elif fault == "missing-branches":
        payload["total"] = {"lines": metric}
    if fault == "malformed":
        report.write_text("not JSON", encoding="utf-8")
    elif fault != "absent":
        report.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError):
        runner.require_javascript_coverage_report(report, source_path=source_path)



def test_javascript_coverage_keeps_unexecuted_native_function_ranges(
    tmp_path: Path,
) -> None:
    """V8 legitimately has no detailed blocks for a function never called."""
    source_path, source = _javascript_source(tmp_path)
    record = _javascript_record(source)
    functions = record["functions"]
    assert isinstance(functions, list)
    functions.append({
        "functionName": "neverCalled",
        "isBlockCoverage": False,
        "ranges": [{"startOffset": 10, "endOffset": 20, "count": 0}],
    })
    normalized = runner.normalize_javascript_coverage(
        {"result": [record]}, source_path=source_path,
        expected_sha256=_source_digest(source_path), sources={"41": source},
    )
    assert normalized == {
        "result": [{**record, "url": str(source_path.resolve())}],
    }


@pytest.mark.parametrize("browser_status", (0, 7))
def test_javascript_coverage_run_routes_child_carriers_and_preserves_test_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    browser_status: int,
) -> None:
    """The existing whole matrix precedes reporting; failed acceptance stops it."""
    import json

    source_path, _source = _javascript_source(tmp_path)
    manifest_path = tmp_path / "source.json"
    manifest_path.write_text(
        json.dumps({"source_sha256": _source_digest(source_path)}), encoding="utf-8",
    )
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-js-coverage-route-browser"),
        output_root=Path("/tmp/gludd-js-coverage-route-output"),
        timeout_seconds=600,
        js_coverage=True,
    )
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(runner, "_require_browser_executable", lambda _plan: None)
    monkeypatch.setattr(
        runner, "_prepare_javascript_coverage", lambda _plan: (tmp_path, manifest_path)
    )
    phases: list[str] = []

    def complete(
        command: tuple[str, ...], **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert environment["GLUDD_PRESENTATION_JS_COVERAGE_DIR"] == str(tmp_path)
        assert environment["GLUDD_PRESENTATION_JS_COVERAGE_MANIFEST"] == str(manifest_path)
        assert kwargs["timeout"] == 600
        phases.append("pytest")
        return subprocess.CompletedProcess(command, browser_status)

    def require_execution(_output: Path, _manifest: Path) -> Path:
        phases.append("execution")
        return source_path

    def report(_output: Path, *, timeout_seconds: int) -> int:
        assert timeout_seconds == 600
        phases.append("c8")
        return 0

    def require_report(_report: Path, *, source_path: Path) -> None:
        assert source_path.is_file()
        phases.append("metrics")

    monkeypatch.setattr(runner.subprocess, "run", complete)
    monkeypatch.setattr(runner, "_require_executed_javascript_coverage", require_execution)
    monkeypatch.setattr(runner, "report_javascript_coverage", report)
    monkeypatch.setattr(runner, "require_javascript_coverage_report", require_report)
    monkeypatch.setattr(runner, "_require_javascript_source_unchanged", lambda _path: None)
    assert runner.run_plan(plan) == browser_status
    assert phases == (
        ["pytest", "pytest", "execution", "c8", "metrics"]
        if browser_status == 0 else ["pytest"]
    )



def test_javascript_coverage_retains_idle_deltas_through_reload_and_close(
    tmp_path: Path,
) -> None:
    """Sampling resets counters; an idle delta does not erase earlier execution."""
    import json

    source_path, source = _javascript_source(tmp_path)
    events: list[str] = []
    collector = runner.JavaScriptCoverageCollector(
        _CoverageSession(source, events, counts=(1, 0, 0, 1)),
        source_path=source_path, expected_sha256=_source_digest(source_path),
        output_root=tmp_path / "coverage", test_id="executed-idle-reload-close",
    )
    collector.start()
    page = _BoundaryPage(events)
    with runner.coverage_boundaries(page, collector):
        collector.snapshot("rendered")
        collector.snapshot("idle")
        page.reload(wait_until="networkidle")
        page.close()
    collector.finish()
    normalized = sorted((tmp_path / "coverage" / "normalized").glob("*.json"))
    counts = [
        json.loads(path.read_text(encoding="utf-8"))["result"][0]["functions"][0]["ranges"][0]["count"]
        for path in normalized
    ]
    assert counts == [1, 0, 0, 1]
    assert events.index("Profiler.takePreciseCoverage") < events.index("reload:networkidle")
    assert events[-1] == "page.close"


@pytest.mark.parametrize("detailed", (True, False))
def test_javascript_coverage_idle_native_script_remains_zero(
    tmp_path: Path,
    detailed: bool,
) -> None:
    """Zero native ranges stay zero; invocation-level execution is checked later."""
    source_path, source = _javascript_source(tmp_path)
    record = _javascript_record(source, count=0, detailed=detailed)
    normalized = runner.normalize_javascript_coverage(
        {"result": [record]}, source_path=source_path,
        expected_sha256=_source_digest(source_path), sources={"41": source},
    )
    assert normalized == {
        "result": [{**record, "url": str(source_path.resolve())}],
    }


def test_javascript_coverage_rejects_degraded_executed_function_only_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, manifest = _retained_controller_invocation(tmp_path, monkeypatch)
    source_path = tmp_path / "presentation.js"
    source = source_path.read_text(encoding="utf-8")
    for raw_path in (output / "raw").glob("*.json"):
        _replace_retained_script_covs(
            output, raw_path, source_path, [_javascript_record(source, detailed=False)],
        )
    with pytest.raises(RuntimeError, match="detailed"):
        runner._require_executed_javascript_coverage(output, manifest)
    assert not (output / "runtime.json").exists()


@pytest.mark.parametrize("count", (0, 1))
def test_javascript_coverage_whole_invocation_requires_real_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    count: int,
) -> None:
    """A fresh source-bound invocation of zero-only snapshots cannot report green."""
    import json

    source_path, source = _javascript_source(tmp_path)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "CONTROLLER_RELATIVE", "presentation.js")
    output = tmp_path / "coverage"
    output.mkdir()
    manifest = output / "source.json"
    digest = _source_digest(source_path)
    manifest.write_text(json.dumps({
        "source_path": str(source_path.resolve()), "source_sha256": digest,
    }), encoding="utf-8")
    collector = runner.JavaScriptCoverageCollector(
        _CoverageSession(source, [], counts=(count, 0)),
        source_path=source_path, expected_sha256=digest, output_root=output,
        test_id="whole-invocation",
    )
    runtime = {
        "browser": "chromium", "browser_version": "unit-browser-version",
        "source_sha256": digest, "source_manifest_sha256": _source_digest(manifest),
    }
    (output / f"runtime-{collector.identity}.json").write_text(
        json.dumps(runtime), encoding="utf-8",
    )
    collector.start()
    collector.snapshot("rendered")
    collector.finish()
    if count:
        assert runner._require_executed_javascript_coverage(output, manifest) == source_path
    else:
        with pytest.raises(RuntimeError, match="whole fresh"):
            runner._require_executed_javascript_coverage(output, manifest)



def test_javascript_coverage_source_ref_reuses_public_native_git_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ref identity comes from bounded native Git, not a cache label or env SHA."""
    calls: list[tuple[str, ...]] = []
    oid = "a" * 40
    tree = "b" * 40

    def git(_root: Path, *args: str, optional: bool = False) -> str:
        calls.append(args)
        if args == ("rev-parse", "--verify", "HEAD^{commit}"):
            assert not optional
            return oid + "\n"
        if args == ("rev-parse", "--verify", oid + "^{tree}"):
            assert not optional
            return tree + "\n"
        if args[0] == "symbolic-ref":
            assert optional
            return "development\n"
        assert args == ("status", "--porcelain=v1", "-z", "--untracked-files=no")
        return " M tracked.py\0"

    def owner(name: str) -> SimpleNamespace:
        assert name == "scripts.owned_git_checkpoint"
        return SimpleNamespace(git=git)

    monkeypatch.setattr(runner, "import_module", owner)
    assert runner._native_source_ref() == {
        "commit": oid, "tree": tree, "branch": "development",
        "tracked_status": " M tracked.py\0",
    }
    assert len(calls) == 4


@pytest.mark.parametrize("changed", ("ref", "tools", "installed-c8"))
def test_javascript_coverage_entry_exit_identity_changes_refuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    changed: str,
) -> None:
    import json

    manifest = tmp_path / "source.json"
    manifest.write_text(json.dumps({
        "source_ref": {"commit": "a" * 40},
        "tool_versions": {"node_version": "v26.0.0"}, "c8_version": "12.0.0",
    }), encoding="utf-8")
    monkeypatch.setattr(
        runner, "_native_source_ref",
        lambda: {"commit": ("b" if changed == "ref" else "a") * 40},
    )
    monkeypatch.setattr(
        runner, "_javascript_tool_versions",
        lambda: {"node_version": "v26.1.0" if changed == "tools" else "v26.0.0"},
    )
    monkeypatch.setattr(
        runner, "_validate_javascript_coverage_tool",
        lambda **_kwargs: "12.0.1" if changed == "installed-c8" else "12.0.0",
    )
    with pytest.raises(RuntimeError, match="changed"):
        runner._require_javascript_source_unchanged(manifest)


def _coverage_package_fixture(root: Path) -> None:
    import json

    package_root = root / ".opencode"
    c8_root = package_root / "node_modules" / "c8"
    bin_root = package_root / "node_modules" / ".bin"
    c8_root.mkdir(parents=True)
    bin_root.mkdir()
    (root / "config").mkdir()
    config = json.loads((runner.ROOT / runner.JS_COVERAGE_CONFIG).read_text(encoding="utf-8"))
    (root / runner.JS_COVERAGE_CONFIG).write_text(json.dumps(config), encoding="utf-8")
    (package_root / "package.json").write_text(
        json.dumps({"devDependencies": {"c8": "12.0.0"}}), encoding="utf-8",
    )
    (package_root / "package-lock.json").write_text(json.dumps({"packages": {
        "": {"devDependencies": {"c8": "12.0.0"}},
        "node_modules/c8": {"version": "12.0.0", "integrity": "sha512-unit-fixture"},
    }}), encoding="utf-8")
    (c8_root / "package.json").write_text(
        json.dumps({"name": "c8", "version": "12.0.0"}), encoding="utf-8",
    )
    entry = c8_root / "c8.js"
    entry.write_text("unit transport fixture, never executed", encoding="utf-8")
    entry.chmod(0o755)
    (bin_root / "c8").symlink_to(entry)


@pytest.mark.parametrize(
    "fault",
    ("none", "floating-pin", "lock-mismatch", "missing-integrity",
     "installed-mismatch", "outside-executable", "fragment-include", "coverage-ignore"),
)
def test_javascript_coverage_requires_exact_locked_installed_tool_and_whole_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fault: str,
) -> None:
    """Metadata fixtures prove refusal routing; they are not actual npm proof."""
    import json

    root = tmp_path / "repo"
    _coverage_package_fixture(root)
    paths = {
        "floating-pin": root / ".opencode" / "package.json",
        "lock-mismatch": root / ".opencode" / "package-lock.json",
        "missing-integrity": root / ".opencode" / "package-lock.json",
        "installed-mismatch": root / ".opencode" / "node_modules" / "c8" / "package.json",
        "fragment-include": root / runner.JS_COVERAGE_CONFIG,
        "coverage-ignore": root / runner.JS_COVERAGE_CONFIG,
    }
    if fault in paths:
        path = paths[fault]
        data = json.loads(path.read_text(encoding="utf-8"))
        if fault == "floating-pin":
            data["devDependencies"]["c8"] = "^12.0.0"
        elif fault == "lock-mismatch":
            data["packages"]["node_modules/c8"]["version"] = "12.0.1"
        elif fault == "missing-integrity":
            data["packages"]["node_modules/c8"].pop("integrity")
        elif fault == "installed-mismatch":
            data["version"] = "12.0.1"
        elif fault == "fragment-include":
            data["include"] = ["presentation-fragment.js"]
        elif fault == "coverage-ignore":
            data["ignoreClassMethods"] = ["render"]
        path.write_text(json.dumps(data), encoding="utf-8")
    elif fault == "outside-executable":
        executable = root / ".opencode" / "node_modules" / ".bin" / "c8"
        executable.unlink()
        outside = tmp_path / "outside.js"
        outside.write_text("not the locked c8 executable", encoding="utf-8")
        outside.chmod(0o755)
        executable.symlink_to(outside)
    monkeypatch.setattr(runner, "ROOT", root)
    if fault == "none":
        assert runner._validate_javascript_coverage_tool(require_installed=True) == "12.0.0"
    else:
        with pytest.raises(RuntimeError):
            runner._validate_javascript_coverage_tool(require_installed=True)



@pytest.mark.parametrize(
    "runtime_fault",
    ("none", "missing-package", "wrong-playwright", "wrong-plugin", "missing-node",
     "node-error", "node-invalid", "node-timeout", "node-oserror"),
)
def test_javascript_runtime_identity_requires_selected_pinned_and_bounded_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime_fault: str,
) -> None:
    """Read actual interfaces, rejecting absent/stale/failed version producers."""
    def version(package: str) -> str:
        if runtime_fault == "missing-package":
            raise PackageNotFoundError(package)
        if package == "playwright":
            return "1.62.0" if runtime_fault == "wrong-playwright" else "1.63.0"
        assert package == "pytest-playwright"
        return "0.8.0" if runtime_fault == "wrong-plugin" else "0.9.0"

    node = str(tmp_path / "prepared-node")
    monkeypatch.setattr(runner, "package_version", version)
    monkeypatch.setattr(
        shutil, "which",
        lambda _name: None if runtime_fault == "missing-node" else node,
    )

    def complete(
        command: tuple[str, ...], **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        assert command == (node, "--version")
        assert kwargs["timeout"] == 30
        assert kwargs["capture_output"] is True and kwargs["text"] is True
        assert kwargs["check"] is False
        if runtime_fault == "node-timeout":
            raise subprocess.TimeoutExpired(command, 30)
        if runtime_fault == "node-oserror":
            raise OSError("prepared binary cannot execute")
        return subprocess.CompletedProcess(
            command, 7 if runtime_fault == "node-error" else 0,
            stdout="not-a-version\n" if runtime_fault == "node-invalid" else "v26.1.0\n",
        )

    monkeypatch.setattr(runner.subprocess, "run", complete)
    if runtime_fault == "none":
        identity = runner._javascript_tool_versions()
        assert identity == {
            "python_version": sys.version.split()[0],
            "playwright": "1.63.0", "pytest-playwright": "0.9.0",
            "node_executable": str(Path(node).resolve()), "node_version": "v26.1.0",
        }
    else:
        with pytest.raises(RuntimeError):
            runner._javascript_tool_versions()


def test_coverage_preparation_binds_working_source_and_refuses_stale_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Source hashes bind working bytes; ref metadata does not erase dirty state."""
    import json

    source, _body = _javascript_source(tmp_path)
    plan = runner.build_plan(
        browser="chromium", browser_root=Path("/tmp/gludd-coverage-source-browser"),
        output_root=tmp_path / "output", timeout_seconds=600, js_coverage=True,
    )
    reference = {"commit": "a" * 40, "tree": "b" * 40, "tracked_status": " M presentation.js\0"}
    tools = {"python_version": sys.version.split()[0], "node_version": "v26.1.0"}
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "CONTROLLER_RELATIVE", "presentation.js")
    monkeypatch.setattr(runner, "_validate_javascript_coverage_tool", lambda **_kwargs: "12.0.0")
    monkeypatch.setattr(runner, "_native_source_ref", lambda: dict(reference))
    monkeypatch.setattr(runner, "_javascript_tool_versions", lambda: dict(tools))
    output, manifest = runner._prepare_javascript_coverage(plan)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert output == tmp_path / "output" / "javascript-coverage"
    assert data["source_sha256"] == _source_digest(source)
    assert data["source_path"] == str(source.resolve())
    assert data["source_ref"] == reference
    assert data["tool_versions"] == tools
    assert data["browser"] == "chromium" and data["c8_version"] == "12.0.0"
    assert "separately" in data["source_authority"]
    runner._require_javascript_source_unchanged(manifest)
    unchanged = manifest.read_bytes()
    with pytest.raises(RuntimeError, match="stale"):
        runner._prepare_javascript_coverage(plan)
    assert manifest.read_bytes() == unchanged


def _retained_controller_invocation(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    """Real producer/validator code surrounds explicitly fake browser transport."""
    import json

    source_path, source = _javascript_source(root)
    monkeypatch.setattr(runner, "ROOT", root)
    monkeypatch.setattr(runner, "CONTROLLER_RELATIVE", "presentation.js")
    output = root / "coverage"
    output.mkdir()
    manifest = output / "source.json"
    digest = _source_digest(source_path)
    manifest.write_text(json.dumps({
        "source_path": str(source_path.resolve()), "source_sha256": digest,
    }), encoding="utf-8")
    for browser_version in ("unit-browser-a", "unit-browser-a"):
        collector = runner.JavaScriptCoverageCollector(
            _CoverageSession(source, []), source_path=source_path,
            expected_sha256=digest, output_root=output, test_id="retained-integrity",
        )
        (output / f"runtime-{collector.identity}.json").write_text(json.dumps({
            "browser": "chromium", "browser_version": browser_version,
            "source_sha256": digest, "source_manifest_sha256": _source_digest(manifest),
        }), encoding="utf-8")
        collector.start()
        collector.finish()
    return output, manifest


def _function_root(
    start: int, end: int, *, count: int = 1, detailed: bool = True, name: str = "handler",
) -> dict[str, object]:
    return {
        "functionName": name, "isBlockCoverage": detailed,
        "ranges": [{"startOffset": start, "endOffset": end, "count": count}],
    }


def _controller_with_functions(source: str, *functions: dict[str, object]) -> dict[str, object]:
    record = _javascript_record(source)
    original = record["functions"]
    assert isinstance(original, list)
    record["functions"] = [*original, *functions]
    return record


def _replace_retained_script_covs(
    output: Path, raw_path: Path, source_path: Path, records: list[dict[str, object]],
) -> None:
    """Build explicit raw/normalized fixtures without invoking a future normalizer."""
    raw = runner._read_json_object(raw_path)
    source = source_path.read_text(encoding="utf-8")
    bodies: dict[str, str] = {}
    for record in records:
        script_id = record["scriptId"]
        assert isinstance(script_id, str)
        bodies[script_id] = source
    raw["result"], raw["sources"] = records, bodies
    runner._write_json(raw_path, raw)
    runner._write_json(output / "normalized" / raw_path.name, {
        "result": [{**record, "url": str(source_path.resolve())} for record in records],
    })


def test_positive_function_only_native_spans_are_retained_without_invented_blocks(tmp_path: Path) -> None:
    """Native whole/IIFE/function spans retain their exact flags, roots and zeros."""
    import json

    source_path = tmp_path / "presentation.js"
    prefix = "// Unicode controller 🚦\n"
    source = prefix + " " * (24_314 - len(prefix.encode("utf-16-le")) // 2)
    source_path.write_text(source, encoding="utf-8")
    record = _javascript_record(source, detailed=False)
    record["functions"] = [
        _function_root(0, 24_314, detailed=False, name=""),
        _function_root(88, 24_309, detailed=False, name=""),
        _function_root(1010, 1101, count=28, detailed=False, name="diagramNodes"),
        _function_root(21_652, 22_504, detailed=False, name="installSourceViewer"),
        _function_root(22_505, 22_530, count=0, detailed=False, name="neverCalled"),
        {
            "functionName": "callback", "isBlockCoverage": True,
            "ranges": [
                {"startOffset": 23_000, "endOffset": 23_100, "count": 1},
                {"startOffset": 23_020, "endOffset": 23_030, "count": 0},
            ],
        },
    ]
    payload = {"result": [record]}
    before = json.dumps(payload, sort_keys=True)

    normalized = runner.normalize_javascript_coverage(
        payload, source_path=source_path, expected_sha256=_source_digest(source_path),
        sources={"41": source},
    )

    assert normalized == {"result": [{**record, "url": str(source_path.resolve())}]}
    assert json.dumps(payload, sort_keys=True) == before
    assert len(source.encode("utf-16-le")) // 2 == 24_314
    assert len(source) != 24_314


def test_positive_coarse_teardown_after_reload_preserves_native_idle_snapshots(tmp_path: Path) -> None:
    """The real collector preserves a positive coarse teardown instead of hiding it."""
    import json

    class TeardownSession(_CoverageSession):
        def send(
            self, method: str, params: dict[str, object] | None = None,
        ) -> dict[str, object]:
            payload = super().send(method, params)
            if method == "Profiler.takePreciseCoverage" and self.samples == 3:
                payload["result"] = [_javascript_record(self.source, detailed=False)]
            return payload

    source_path, source = _javascript_source(tmp_path)
    events: list[str] = []
    output = tmp_path / "coverage"
    collector = runner.JavaScriptCoverageCollector(
        TeardownSession(source, events, counts=(1, 0, 1)),
        source_path=source_path, expected_sha256=_source_digest(source_path),
        output_root=output, test_id="detailed-reload-coarse-teardown",
    )
    collector.start()
    page = _BoundaryPage(events)
    with runner.coverage_boundaries(page, collector):
        collector.snapshot("rendered")
        page.reload(wait_until="networkidle")
    collector.finish()

    raw_paths = sorted((output / "raw").glob("*.json"))
    assert len(raw_paths) == len(list((output / "normalized").glob("*.json"))) == 3
    raw = [json.loads(path.read_text(encoding="utf-8")) for path in raw_paths]
    assert [item["reason"] for item in raw] == ["rendered", "reload", "teardown"]
    assert [item["result"][0]["functions"][0]["ranges"][0]["count"] for item in raw] == [1, 0, 1]
    assert [item["result"][0]["functions"][0]["isBlockCoverage"] for item in raw] == [True, True, False]
    for path, native in zip(raw_paths, raw, strict=True):
        normalized = runner._read_json_object(output / "normalized" / path.name)
        assert normalized == {
            "result": [{**native["result"][0], "url": str(source_path.resolve())}],
        }
    assert events[-1] == "Profiler.stopPreciseCoverage"
    assert events.index("reload:networkidle") < len(events) - 1


@pytest.mark.parametrize("coarse_first", (True, False))
@pytest.mark.parametrize("coarse_records", (1, 2))
def test_positive_coarse_roots_require_order_independent_distinct_detailed_witnesses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, coarse_first: bool, coarse_records: int,
) -> None:
    """File order, repeated coarse records and names cannot replace exact native roots."""
    output, manifest = _retained_controller_invocation(tmp_path, monkeypatch)
    source_path = tmp_path / "presentation.js"
    source = source_path.read_text(encoding="utf-8")
    end = len(source.encode("utf-16-le")) // 2
    assert end != len(source)
    coarse = _javascript_record(source, detailed=False)
    coarse["functions"] = [
        _function_root(0, end, detailed=False, name=""),
        _function_root(1, end, count=7, detailed=False, name="coarseName"),
    ]
    detailed = _controller_with_functions(
        source, _function_root(1, end, count=2, name="differentDetailedName"),
    )
    raw_paths = sorted((output / "raw").glob("*.json"))
    groups = ([coarse] * coarse_records, [detailed])
    if not coarse_first:
        groups = groups[::-1]
    for path, records in zip(raw_paths, groups, strict=True):
        _replace_retained_script_covs(output, path, source_path, records)
    retained = {
        path: path.read_bytes()
        for directory in ("raw", "normalized") for path in (output / directory).glob("*.json")
    }

    assert runner._require_executed_javascript_coverage(output, manifest) == source_path
    assert all(path.read_bytes() == content for path, content in retained.items())


def test_detailed_witness_in_distinct_script_cov_ordinal_can_reuse_script_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One retained file may hold two ScriptCov records; scriptId is not record identity."""
    output, manifest = _retained_controller_invocation(tmp_path, monkeypatch)
    source_path = tmp_path / "presentation.js"
    source = source_path.read_text(encoding="utf-8")
    raw_path = sorted((output / "raw").glob("*.json"))[0]
    records = [
        _controller_with_functions(source, _function_root(10, 20, detailed=False)),
        _controller_with_functions(source, _function_root(10, 20)),
    ]
    assert records[0]["scriptId"] == records[1]["scriptId"] == "41"
    _replace_retained_script_covs(output, raw_path, source_path, records)

    assert runner._require_executed_javascript_coverage(output, manifest) == source_path


@pytest.mark.parametrize(
    "witness_fault",
    (
        "all-coarse", "partial", "same-record", "containing", "same-name-different-span",
        "zero-root", "zero-root-positive-child", "child-range-only", "malformed-coarse-child",
        "two-coarse", "unicode-offset",
        "foreign-invocation", "wrong-source", "stale-source", "tampered-normalized", "all-zero",
    ),
)
def test_unwitnessed_function_roots_refuse_before_any_native_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, witness_fault: str,
) -> None:
    """Invalid evidence cannot reach c8 or manufacture runtime/report success."""
    output, manifest = _retained_controller_invocation(tmp_path, monkeypatch)
    source_path = tmp_path / "presentation.js"
    source = source_path.read_text(encoding="utf-8")
    end = len(source.encode("utf-16-le")) // 2 - 1 if witness_fault == "unicode-offset" else 20
    coarse_function = _function_root(10, end, detailed=False)
    coarse = _controller_with_functions(source, coarse_function)
    detail_function = _function_root(10, end)
    detailed = _controller_with_functions(source, detail_function)
    expected_error = "witness"
    if witness_fault == "all-coarse":
        coarse = _javascript_record(source, detailed=False)
        detailed = _javascript_record(source, detailed=False)
    elif witness_fault == "partial":
        functions = coarse["functions"]
        assert isinstance(functions, list)
        functions.append(_function_root(30, 40, detailed=False))
    elif witness_fault == "same-record":
        functions = coarse["functions"]
        assert isinstance(functions, list)
        functions.append(detail_function)
        detailed = _javascript_record(source)
    elif witness_fault == "containing":
        detailed = _controller_with_functions(source, _function_root(9, 21))
    elif witness_fault == "same-name-different-span":
        detailed = _controller_with_functions(source, _function_root(11, end))
    elif witness_fault in {"zero-root", "zero-root-positive-child"}:
        detail_function = _function_root(10, end, count=0)
        if witness_fault == "zero-root-positive-child":
            ranges = detail_function["ranges"]
            assert isinstance(ranges, list)
            ranges.append({"startOffset": 12, "endOffset": 15, "count": 1})
        detailed = _controller_with_functions(source, detail_function)
    elif witness_fault == "child-range-only":
        detail_function = _function_root(9, 21)
        ranges = detail_function["ranges"]
        assert isinstance(ranges, list)
        ranges.append({"startOffset": 10, "endOffset": end, "count": 1})
        detailed = _controller_with_functions(source, detail_function)
    elif witness_fault == "malformed-coarse-child":
        malformed = _function_root(10, end, count=0, detailed=False)
        ranges = malformed["ranges"]
        assert isinstance(ranges, list)
        ranges.append({"startOffset": 12, "endOffset": 15, "count": 1})
        coarse = _controller_with_functions(source, malformed)
        expected_error = "detailed|coarse|function-only|non-block"
    elif witness_fault == "two-coarse":
        detailed = _controller_with_functions(source, _function_root(10, end, count=2, detailed=False))
    elif witness_fault == "unicode-offset":
        detailed = _controller_with_functions(source, _function_root(10, len(source) - 1))
    elif witness_fault == "wrong-source":
        expected_error = "complete source"
    elif witness_fault == "stale-source":
        expected_error = "stale source"
    elif witness_fault == "tampered-normalized":
        expected_error = "differs"
    elif witness_fault == "all-zero":
        coarse = _javascript_record(source, count=0, detailed=False)
        detailed = _javascript_record(source, count=0)
        expected_error = "whole fresh"

    raw_paths = sorted((output / "raw").glob("*.json"))
    for path, record in zip(raw_paths, (coarse, detailed), strict=True):
        _replace_retained_script_covs(output, path, source_path, [record])
    if witness_fault == "foreign-invocation":
        previous = tmp_path / "previous-invocation"
        previous.mkdir()
        raw_paths[1].rename(previous / "raw.json")
        (output / "normalized" / raw_paths[1].name).rename(previous / "normalized.json")
    elif witness_fault in {"wrong-source", "stale-source"}:
        raw = runner._read_json_object(raw_paths[1])
        if witness_fault == "wrong-source":
            raw["sources"] = {"41": source + "// foreign controller\n"}
        else:
            raw["source_sha256"] = "0" * 64
        runner._write_json(raw_paths[1], raw)
    elif witness_fault == "tampered-normalized":
        altered = _controller_with_functions(source, _function_root(10, end, count=99, detailed=False))
        runner._write_json(output / "normalized" / raw_paths[0].name, {
            "result": [{**altered, "url": str(source_path.resolve())}],
        })

    monkeypatch.setattr(runner, "TEST_FILE", tmp_path / "tests" / "browser" / "test_presentation.py")
    plan = runner.build_plan(
        browser="chromium", browser_root=Path("/tmp/gludd-witness-unit-browser"),
        output_root=tmp_path / "browser-output", timeout_seconds=600, js_coverage=True,
    )
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(runner, "_require_browser_executable", lambda _plan: None)
    monkeypatch.setattr(runner, "_prepare_javascript_coverage", lambda _plan: (output, manifest))
    browser_commands: list[tuple[str, ...]] = []

    def complete(
        command: tuple[str, ...], **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        assert command[0] == runner.sys.executable, "unsupported coverage must refuse before c8"
        browser_commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", complete)
    with pytest.raises(RuntimeError, match=expected_error) as failure:
        runner.run_plan(plan)
    assert len(browser_commands) == len(plan.runs)
    assert not (output / "runtime.json").exists()
    assert not list(output.glob("c8-*.json"))
    if witness_fault == "partial":
        assert "30" in str(failure.value) and "40" in str(failure.value)


def test_native_c8_report_and_both_floors_pin_batch_merge_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fake unit transport verifies all three public argv, never converter metrics."""
    commands: list[tuple[str, ...]] = []

    def complete(
        command: tuple[str, ...], **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == 600 and kwargs["check"] is False
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", complete)
    assert runner.report_javascript_coverage(tmp_path, timeout_seconds=600) == 0
    assert [command[1] for command in commands] == ["report", "check-coverage", "check-coverage"]
    for phase, command in zip(("report", "aggregate", "per-file"), commands, strict=True):
        assert command.count("--merge-async=false") == 1
        assert "--merge-async=true" not in command
        status = runner._read_json_object(tmp_path / f"c8-{phase}.json")
        assert status["command"] == list(command)
        assert status["exit_code"] == 0


@pytest.mark.parametrize(
    "snapshot_fault",
    ("manifest-path", "missing-normalized", "body-type", "stale-digest",
     "changed-normalized", "collector-id", "runtime-binding", "browser-switch"),
)
def test_retained_coverage_requires_native_range_and_runtime_integrity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    snapshot_fault: str,
) -> None:
    import json

    output, manifest = _retained_controller_invocation(tmp_path, monkeypatch)
    raw = sorted((output / "raw").glob("*.json"))
    normalized = sorted((output / "normalized").glob("*.json"))
    if snapshot_fault == "missing-normalized":
        for path in normalized:
            path.unlink()
    else:
        if snapshot_fault == "manifest-path":
            path = manifest
        elif snapshot_fault == "changed-normalized":
            path = normalized[0]
        elif snapshot_fault in {"runtime-binding", "browser-switch"}:
            path = sorted(output.glob("runtime-*.json"))[0]
        else:
            path = raw[0]
        data = json.loads(path.read_text(encoding="utf-8"))
        if snapshot_fault == "manifest-path":
            data["source_path"] = str(tmp_path / "different-source.js")
        elif snapshot_fault == "body-type":
            data["sources"]["41"] = 42
        elif snapshot_fault == "stale-digest":
            data["source_sha256"] = "c" * 64
        elif snapshot_fault == "changed-normalized":
            data["result"][0]["functions"][0]["ranges"][0]["count"] = 2
        elif snapshot_fault == "collector-id":
            data["collector_id"] = None
        elif snapshot_fault == "runtime-binding":
            data["source_manifest_sha256"] = "d" * 64
        elif snapshot_fault == "browser-switch":
            data["browser_version"] = "unit-browser-b"
        path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError):
        runner._require_executed_javascript_coverage(output, manifest)


@pytest.mark.parametrize("viewport_value", (None, "bad", "0x720", "1024x0"))
def test_viewport_override_is_optional_and_refuses_invalid_dimensions(
    monkeypatch: pytest.MonkeyPatch,
    viewport_value: str | None,
) -> None:
    """An invalid plan cannot masquerade as a different presentation viewport."""
    if viewport_value is None:
        monkeypatch.delenv(runner.VIEWPORT_SIZE_ENV, raising=False)
        assert runner._requested_viewport() is None
        runner.pytest_collection_modifyitems([])
    else:
        monkeypatch.setenv(runner.VIEWPORT_SIZE_ENV, viewport_value)
        with pytest.raises(RuntimeError, match="invalid"):
            runner._requested_viewport()


@pytest.mark.parametrize("native_ref_fault", ("commit", "tree"))
def test_native_source_identity_refuses_invalid_native_ref_output(
    monkeypatch: pytest.MonkeyPatch,
    native_ref_fault: str,
) -> None:
    def git(_root: Path, *args: str, optional: bool = False) -> str:
        del optional
        if args[-1] == "HEAD^{commit}":
            return "invalid" if native_ref_fault == "commit" else "a" * 40
        return "invalid" if native_ref_fault == "tree" else "b" * 40

    monkeypatch.setattr(runner, "import_module", lambda _name: SimpleNamespace(git=git))
    with pytest.raises(RuntimeError, match="invalid"):
        runner._native_source_ref()


def test_native_coverage_executable_error_is_retained_and_stops(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import json

    def failed(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("locked c8 entry disappeared")

    monkeypatch.setattr(runner.subprocess, "run", failed)
    assert runner.report_javascript_coverage(tmp_path, timeout_seconds=600) == 127
    status = json.loads((tmp_path / "c8-report.json").read_text(encoding="utf-8"))
    assert status["exit_code"] == 127 and "disappeared" in status["error"]
    assert not (tmp_path / "c8-aggregate.json").exists()
    assert not (tmp_path / "c8-per-file.json").exists()


@pytest.mark.parametrize(
    "profile_fault",
    ("missing-profile", "invalid-toml", "invalid-dependencies", "empty-matrix", "coverage-plan"),
)
def test_browser_plan_validates_profile_and_coverage_without_installing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    profile_fault: str,
) -> None:
    root = tmp_path / "repo"
    test_file = root / "tests/browser/test_presentation.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("pass\n", encoding="utf-8")
    profile = _write_presentation_profile(
        root, ["playwright==1.63.0", "pytest-playwright==0.9.0"],
    )
    monkeypatch.setattr(runner, "ROOT", root)
    monkeypatch.setattr(runner, "TEST_FILE", test_file)
    plan = runner.build_plan(
        browser="chromium", browser_root=Path("/tmp/gludd-profile-controls-browser"),
        output_root=Path("/tmp/gludd-profile-controls-output"), timeout_seconds=600,
        js_coverage=profile_fault == "coverage-plan",
    )
    if profile_fault == "missing-profile":
        profile.unlink()
    elif profile_fault == "invalid-toml":
        profile.write_text("not = [valid", encoding="utf-8")
    elif profile_fault == "invalid-dependencies":
        profile.write_text("[project]\ndependencies = [42]\n", encoding="utf-8")
    elif profile_fault == "empty-matrix":
        plan = replace(plan, runs=())
    static_checks: list[bool] = []

    def validate_tool(*, require_installed: bool) -> str:
        static_checks.append(require_installed)
        return "12.0.0"

    monkeypatch.setattr(runner, "_validate_javascript_coverage_tool", validate_tool)
    if profile_fault == "coverage-plan":
        runner.validate_plan(plan)
        assert static_checks == [False]
    else:
        with pytest.raises(RuntimeError):
            runner.validate_plan(plan)
        assert static_checks == []
