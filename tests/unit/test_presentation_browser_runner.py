"""Tests for the bounded Playwright presentation runner."""

from __future__ import annotations

import multiprocessing
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from scripts import run_presentation_browser_tests as runner


def _wedge_startup_probe(_plan: runner.BrowserPlan) -> None:
    """Remain blocked long enough for the owned-process deadline to fire."""
    threading.Event().wait(60.0)


def _wedge_viewport_command(
    _plan: runner.BrowserPlan,
    _run: runner.ViewportRun,
) -> int:
    """Model a wedged pytest/browser process beneath the owned supervisor."""
    threading.Event().wait(60.0)
    return 0


def _owned_probe_children(name: str) -> list[multiprocessing.Process]:
    return [child for child in multiprocessing.active_children() if child.name == name]


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


@pytest.mark.parametrize("rendered", ("wide", "0x768", "1024"))
def test_viewport_plugin_rejects_malformed_dimensions(
    monkeypatch: pytest.MonkeyPatch,
    rendered: str,
) -> None:
    """An invalid focused viewport cannot silently fall back to desktop size."""
    monkeypatch.setenv(runner.VIEWPORT_SIZE_ENV, rendered)

    with pytest.raises(RuntimeError, match=runner.VIEWPORT_SIZE_ENV):
        runner.pytest_collection_modifyitems([])


def test_viewport_plugin_is_a_noop_without_an_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full-suite viewport remains owned by pytest-playwright defaults."""
    monkeypatch.delenv(runner.VIEWPORT_SIZE_ENV, raising=False)

    runner.pytest_collection_modifyitems([])


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


def test_timeout_returns_observable_124(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stuck browser is bounded and uses the conventional timeout status."""
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-timeout-test"),
        output_root=Path("/tmp/gludd-presentation-timeout-test"),
        timeout_seconds=30,
    )
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(runner, "_require_browser_executable", lambda _plan: None)
    monkeypatch.setattr(runner, "probe_browser_runtime", lambda _plan: 0)

    def timeout(_plan: runner.BrowserPlan, _run: runner.ViewportRun) -> int:
        raise runner.OwnedProcessTimeout("owned process deadline exceeded")

    monkeypatch.setattr(runner, "_run_viewport_process", timeout)

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
    probed: list[runner.BrowserPlan] = []
    monkeypatch.setattr(
        runner,
        "probe_browser_runtime",
        lambda selected_plan: probed.append(selected_plan) or 0,
    )
    captured: list[tuple[runner.BrowserPlan, runner.ViewportRun]] = []

    def complete(
        selected_plan: runner.BrowserPlan,
        selected_run: runner.ViewportRun,
    ) -> int:
        captured.append((selected_plan, selected_run))
        return 0

    monkeypatch.setattr(runner, "_run_viewport_process", complete)

    assert runner.run_plan(plan) == 0
    assert probed == [plan]
    assert captured == [
        (plan, plan.runs[0]),
        (plan, plan.runs[1]),
    ]


def test_run_plan_can_focus_one_viewport_without_weakening_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators can replay compact containment through the same owned launcher."""
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-viewport-focus"),
        output_root=Path("/tmp/gludd-presentation-viewport-focus"),
        timeout_seconds=30,
    )
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(runner, "_require_browser_executable", lambda _plan: None)
    monkeypatch.setattr(runner, "probe_browser_runtime", lambda _plan: 0)
    captured: list[runner.ViewportRun] = []
    monkeypatch.setattr(
        runner,
        "_run_viewport_process",
        lambda _plan, selected_run: captured.append(selected_run) or 0,
    )

    assert runner.run_plan(plan, run_label="compact-4x3") == 0
    assert captured == [plan.runs[1]]

    with pytest.raises(ValueError, match="unknown presentation run label"):
        runner.run_plan(plan, run_label="not-a-run")


def test_viewport_command_sets_engine_artifacts_and_css_viewport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The supervised child retains exact commands and per-run environment."""
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-viewport-command"),
        output_root=Path("/tmp/gludd-presentation-viewport-command"),
        timeout_seconds=30,
    )
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

    assert [runner._execute_viewport_command(plan, run) for run in plan.runs] == [0, 0]
    assert captured == [
        (plan.runs[0].command, 40.0, plan.runs[0].output_root, None),
        (plan.runs[1].command, 40.0, plan.runs[1].output_root, "1024x768"),
    ]


def test_viewport_process_uses_the_shared_owned_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pytest and its Playwright descendants inherit one killable process group."""
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-viewport-owned"),
        output_root=Path("/tmp/gludd-presentation-viewport-owned"),
        timeout_seconds=30,
    )
    viewport_run = plan.runs[1]
    captured: dict[str, object] = {}

    def owned_process(
        operation: object,
        args: tuple[object, ...],
        **kwargs: object,
    ) -> int:
        captured.update(operation=operation, args=args, **kwargs)
        return 0

    monkeypatch.setattr(runner, "run_owned_process", owned_process)

    assert runner._run_viewport_process(plan, viewport_run) == 0
    assert captured["operation"] is runner._execute_viewport_command
    assert captured["args"] == (plan, viewport_run)
    assert captured["process_name"] == "gludd-presentation-webkit-compact-4x3"
    policy = captured["policy"]
    assert isinstance(policy, runner.OwnedProcessPolicy)
    assert policy.timeout_seconds == 30
    assert policy.shutdown_grace_seconds == 5


def test_viewport_timeout_reaps_the_exact_owned_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wedged acceptance child is gone before the timeout returns."""
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-viewport-wedge"),
        output_root=Path("/tmp/gludd-presentation-viewport-wedge"),
        timeout_seconds=30,
    )
    viewport_run = plan.runs[0]
    process_name = "gludd-presentation-webkit-desktop-landscape"
    monkeypatch.setattr(
        runner,
        "_execute_viewport_command",
        _wedge_viewport_command,
    )
    monkeypatch.setattr(
        runner,
        "_viewport_process_policy",
        lambda _plan: runner.OwnedProcessPolicy(
            timeout_seconds=0.5,
            shutdown_grace_seconds=0.25,
            poll_interval_seconds=0.05,
            heartbeat_interval_seconds=0.1,
        ),
    )
    started = time.monotonic()

    with pytest.raises(runner.OwnedProcessTimeout, match="deadline exceeded"):
        runner._run_viewport_process(plan, viewport_run)

    assert time.monotonic() - started < 3.0
    assert _owned_probe_children(process_name) == []


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
    monkeypatch.setattr(
        runner,
        "probe_browser_runtime",
        lambda selected_plan: launches.append(selected_plan) or 0,
    )
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


def test_browser_install_with_dependencies_propagates_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installing WebKit cannot turn a failed page handshake into success."""
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-deps-probe-failure"),
        output_root=Path("/tmp/gludd-presentation-deps-probe-failure"),
        timeout_seconds=120,
    )
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(runner, "_require_browser_executable", lambda _plan: None)
    monkeypatch.setattr(runner, "probe_browser_runtime", lambda _plan: 124)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0),
    )

    assert runner.install_browser(plan, with_dependencies=True) == 124


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
    common = {
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
        def new_page(self) -> FakePage:
            return FakePage()

        def close(self) -> None:
            closed.append(True)

    class FakePage:
        def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            assert url == runner.PROBE_URL
            assert wait_until == "domcontentloaded"
            assert timeout == runner.PROBE_ACTION_TIMEOUT_MILLISECONDS

        def title(self) -> str:
            return runner.PROBE_TITLE

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


def test_webkit_probe_timeout_emits_one_typed_macos_root_cause(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unresponsive WebKit page handshake becomes one actionable failure."""
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-webkit-probe-timeout"),
        output_root=Path("/tmp/gludd-presentation-webkit-probe-timeout"),
        timeout_seconds=120,
    )
    monkeypatch.setattr(runner.sys, "platform", "darwin")

    def timeout(_plan: runner.BrowserPlan) -> None:
        raise runner.OwnedProcessTimeout("owned process deadline exceeded")

    monkeypatch.setattr(runner, "_run_probe_process", timeout)

    assert runner.probe_browser_runtime(plan) == 124

    diagnostics = [
        line
        for line in capsys.readouterr().err.splitlines()
        if '"event": "presentation-browser-root-cause"' in line
    ]
    assert len(diagnostics) == 1
    payload = runner.json.loads(diagnostics[0])
    assert payload == {
        "action": (
            "Keep the WebKit acceptance red; compare system Safari with "
            "make presentation-safari-test PRESENTATION_SAFARI_VALIDATE_ONLY=0 "
            "PRESENTATION_SAFARI_DRIVER=/usr/bin/safaridriver "
            "PRESENTATION_SAFARI_OUTPUT=/tmp/gludd-presentation-safari "
            "PRESENTATION_SAFARI_TIMEOUT=60; "
            "then rerun after the macOS or pinned Playwright runtime changes."
        ),
        "browser": "webkit",
        "detail": None,
        "event": "presentation-browser-root-cause",
        "failure_kind": "timeout",
        "phase": "launch-page-probe",
        "platform": "darwin",
        "reference": "https://github.com/microsoft/playwright/issues/41870",
        "root_cause": (
            "Playwright WebKit did not complete first-page creation and navigation "
            "before the external deadline on macOS; this matches the "
            "NetworkProcess deadlock signature tracked in issue #41870."
        ),
        "schema_version": 1,
        "timeout_seconds": runner.PROBE_TIMEOUT_SECONDS,
    }


@pytest.mark.parametrize("browser_engine", ("chromium", "webkit"))
def test_probe_uses_shared_owned_process_policy_for_each_engine(
    monkeypatch: pytest.MonkeyPatch,
    browser_engine: str,
) -> None:
    """Each mandatory engine gets the same isolated launch/page contract."""
    plan = runner.build_plan(
        browser=browser_engine,
        browser_root=Path("/tmp/gludd-browser-probe-success"),
        output_root=Path("/tmp/gludd-presentation-probe-success"),
        timeout_seconds=120,
    )
    captured: dict[str, object] = {}

    def owned_process(
        operation: object,
        args: tuple[object, ...],
        **kwargs: object,
    ) -> None:
        captured.update(operation=operation, args=args, **kwargs)
        event_sink = kwargs["event_sink"]
        assert callable(event_sink)
        event_sink(f"OWNED_PROCESS_STARTED name={kwargs['process_name']}")

    monkeypatch.setattr(runner, "run_owned_process", owned_process)

    runner._run_probe_process(plan)

    assert captured["operation"] is runner._require_browser_launchable
    assert captured["args"] == (plan,)
    assert captured["policy"] == runner.PROBE_PROCESS_POLICY
    assert captured["process_name"] == f"gludd-presentation-{browser_engine}-probe"
    assert runner.PROBE_PROCESS_POLICY.timeout_seconds == runner.PROBE_TIMEOUT_SECONDS
    assert runner.PROBE_PROCESS_POLICY.shutdown_grace_seconds == 2


def test_probe_success_is_observable_without_a_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A healthy probe emits progress while reserving stderr for root cause."""
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-probe-observable"),
        output_root=Path("/tmp/gludd-presentation-probe-observable"),
        timeout_seconds=120,
    )
    monkeypatch.setattr(
        runner,
        "_run_probe_process",
        lambda _plan: None,
    )

    assert runner.probe_browser_runtime(plan) == 0

    captured = capsys.readouterr()
    assert "phase=launch-page-probe status=finished exit=0" in captured.out
    assert captured.err == ""


def test_probe_process_error_is_one_generic_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-timeout startup failures stay actionable without raw traceback noise."""
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-probe-exit"),
        output_root=Path("/tmp/gludd-presentation-probe-exit"),
        timeout_seconds=120,
    )
    def fail(_plan: runner.BrowserPlan) -> None:
        raise RuntimeError("driver failed")

    monkeypatch.setattr(runner, "_run_probe_process", fail)

    assert runner.probe_browser_runtime(plan) == 1

    diagnostics = capsys.readouterr().err.splitlines()
    assert len(diagnostics) == 1
    payload = runner.json.loads(diagnostics[0])
    assert payload["detail"] == "driver failed"
    assert payload["failure_kind"] == "process-error"
    assert payload["reference"] is None
    assert "probe raised an error" in payload["root_cause"]


def test_startup_probe_only_emits_content_free_success_evidence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The operator mode returns one stable record without page or path content."""
    plan = runner.build_plan(
        browser="chromium",
        browser_root=Path("/tmp/gludd-browser-probe-only-success"),
        output_root=Path("/tmp/gludd-presentation-probe-only-success"),
        timeout_seconds=120,
    )
    validated: list[runner.BrowserPlan] = []
    monkeypatch.setattr(runner, "validate_plan", validated.append)
    monkeypatch.setattr(
        runner,
        "_require_browser_executable",
        lambda _plan: Path("/tmp/gludd-browser-probe-only-success/chromium"),
    )
    monkeypatch.setattr(runner, "_run_probe_process", lambda _plan: None)

    assert runner.run_startup_probe_only(plan) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert validated == [plan]
    assert runner.json.loads(captured.out) == {
        "browser": "chromium",
        "event": "presentation-browser-startup-probe",
        "exit_code": 0,
        "failure_kind": None,
        "phase": "launch-page-probe",
        "platform": runner.sys.platform,
        "reference": None,
        "schema_version": 1,
        "status": "passed",
        "timeout_seconds": runner.PROBE_TIMEOUT_SECONDS,
    }


@pytest.mark.parametrize(
    ("failure", "expected_kind", "expected_exit"),
    [
        (runner.OwnedProcessTimeout("private timeout content"), "timeout", 124),
        (RuntimeError("private driver content"), "process-error", 1),
    ],
)
def test_startup_probe_only_failure_evidence_never_echoes_content(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    expected_kind: str,
    expected_exit: int,
) -> None:
    """Probe-only failures are typed without transporting child output."""
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-probe-only-failure"),
        output_root=Path("/tmp/gludd-presentation-probe-only-failure"),
        timeout_seconds=120,
    )
    monkeypatch.setattr(runner.sys, "platform", "darwin")
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(
        runner,
        "_require_browser_executable",
        lambda _plan: Path("/tmp/gludd-browser-probe-only-failure/pw_run.sh"),
    )

    def fail(_plan: runner.BrowserPlan) -> None:
        raise failure

    monkeypatch.setattr(runner, "_run_probe_process", fail)

    assert runner.run_startup_probe_only(plan) == expected_exit

    captured = capsys.readouterr()
    rendered = captured.out + captured.err
    assert "private timeout content" not in rendered
    assert "private driver content" not in rendered
    assert runner.json.loads(captured.out) == {
        "browser": "webkit",
        "event": "presentation-browser-startup-probe",
        "exit_code": expected_exit,
        "failure_kind": expected_kind,
        "phase": "launch-page-probe",
        "platform": "darwin",
        "reference": (
            runner.WEBKIT_MACOS_DEADLOCK_REFERENCE
            if expected_kind == "timeout"
            else None
        ),
        "schema_version": 1,
        "status": "failed",
        "timeout_seconds": runner.PROBE_TIMEOUT_SECONDS,
    }


def test_startup_probe_timeout_reaps_the_exact_owned_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real wedged probe is killed and joined before control returns."""
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-probe-only-wedge"),
        output_root=Path("/tmp/gludd-presentation-probe-only-wedge"),
        timeout_seconds=120,
    )
    process_name = "gludd-presentation-webkit-probe"
    monkeypatch.setattr(runner, "_require_browser_launchable", _wedge_startup_probe)
    monkeypatch.setattr(
        runner,
        "PROBE_PROCESS_POLICY",
        runner.OwnedProcessPolicy(
            timeout_seconds=0.5,
            shutdown_grace_seconds=0.25,
            poll_interval_seconds=0.05,
            heartbeat_interval_seconds=0.1,
        ),
    )
    started = time.monotonic()

    with pytest.raises(runner.OwnedProcessTimeout, match="deadline exceeded"):
        runner._run_probe_process(plan)

    assert time.monotonic() - started < 3.0
    assert _owned_probe_children(process_name) == []


def test_run_plan_stops_before_pytest_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The launch/page root cause wins over a later broad pytest timeout."""
    plan = runner.build_plan(
        browser="webkit",
        browser_root=Path("/tmp/gludd-browser-probe-stop"),
        output_root=Path("/tmp/gludd-presentation-probe-stop"),
        timeout_seconds=120,
    )
    monkeypatch.setattr(runner, "validate_plan", lambda _plan: None)
    monkeypatch.setattr(runner, "_require_browser_executable", lambda _plan: None)
    monkeypatch.setattr(runner, "probe_browser_runtime", lambda _plan: 124)
    monkeypatch.setattr(
        runner,
        "_run_viewport_process",
        lambda *args, **kwargs: pytest.fail("pytest must not start after probe failure"),
    )

    assert runner.run_plan(plan) == 124


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
    monkeypatch.setattr(runner, "probe_browser_runtime", lambda _plan: 0)

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
    monkeypatch.setattr(runner, "_run_viewport_process", lambda _plan, _run: 7)
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

    selected_labels: list[str | None] = []
    monkeypatch.setattr(
        runner,
        "run_plan",
        lambda _plan, *, run_label=None: selected_labels.append(run_label) or 23,
    )
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--run"])
    with pytest.raises(SystemExit) as run:
        runner.main()
    assert run.value.code == 23

    monkeypatch.setattr(
        runner.sys,
        "argv",
        ["runner", "--run", "--run-label", "compact-4x3"],
    )
    with pytest.raises(SystemExit) as focused_run:
        runner.main()
    assert focused_run.value.code == 23
    assert selected_labels == [None, "compact-4x3"]

    monkeypatch.setattr(runner, "run_startup_probe_only", lambda _plan: 31)
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--startup-probe-only"])
    with pytest.raises(SystemExit) as startup_probe:
        runner.main()
    assert startup_probe.value.code == 31
