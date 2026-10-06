"""Tests for the bounded Playwright presentation runner."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from scripts import run_presentation_browser_tests as runner


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


@pytest.mark.parametrize("browser", ("chromium", "webkit"))
def test_browser_install_is_exact_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
    browser: str,
) -> None:
    """Installation acquires one requested engine and verifies its binary."""
    plan = runner.build_plan(
        browser=browser,
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
    assert captured == [(runner.sys.executable, "-m", "playwright", "install", browser)]
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
