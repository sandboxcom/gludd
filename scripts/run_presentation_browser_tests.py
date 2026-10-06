#!/usr/bin/env python3
"""Run the mandatory presentation browser acceptance in a bounded process."""

from __future__ import annotations

import argparse
import json
import os
import subprocess as subprocess
import sys as sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_FILE = ROOT / "tests" / "browser" / "test_presentation.py"
DEFAULT_OUTPUT_ROOT = Path("/tmp/gludd-presentation-browser")
DEFAULT_BROWSER_ROOT = Path("/tmp/gludd-playwright-browsers")
SUPPORTED_BROWSERS = frozenset({"chromium", "webkit"})
VIEWPORT_SIZE_ENV = "GLUDD_PRESENTATION_VIEWPORT_SIZE"
LAYOUT_CONTAINMENT_TEST = (
    "tests/browser/test_presentation.py::"
    "test_every_slide_keeps_visible_content_inside_the_reveal_canvas"
)
PINNED_PRESENTATION_DEPENDENCIES = frozenset(
    {"playwright==1.63.0", "pytest-playwright==0.9.0"}
)


@dataclass(frozen=True)
class ViewportRun:
    """One auditable viewport and its intentionally bounded pytest scope."""

    label: str
    viewport_width: int
    viewport_height: int
    scope: str
    output_root: str
    command: tuple[str, ...]
    enforce_viewport: bool


@dataclass(frozen=True)
class BrowserPlan:
    """Auditable, serializable description of one browser acceptance run."""

    browser: str
    browser_root: str
    output_root: str
    test_file: str
    timeout_seconds: int
    runs: tuple[ViewportRun, ...]

    @property
    def command(self) -> tuple[str, ...]:
        """Retain the historical primary-command view for callers and diagnostics."""
        return self.runs[0].command


def _safe_owned_tmp(path: Path, *, label: str) -> Path:
    """Accept only explicit project-namespaced temporary output roots."""
    resolved = path.resolve()
    prefixes = ("/tmp/gludd-", "/private/tmp/gludd-")
    if not resolved.as_posix().startswith(prefixes):
        raise ValueError(f"{label} must be below an explicit /tmp/gludd-* path")
    return resolved


def build_plan(
    *,
    browser: str,
    browser_root: Path,
    output_root: Path,
    timeout_seconds: int,
) -> BrowserPlan:
    """Build a bounded pytest-playwright invocation without executing it."""
    if browser not in SUPPORTED_BROWSERS:
        raise ValueError("the presentation contract requires Chromium or WebKit")
    if not 30 <= timeout_seconds <= 900:
        raise ValueError("browser timeout must be between 30 and 900 seconds")
    safe_browser_root = _safe_owned_tmp(browser_root, label="browser root")
    safe_output_root = _safe_owned_tmp(output_root, label="output root")

    def viewport_run(
        *,
        label: str,
        viewport_width: int,
        viewport_height: int,
        scope: str,
        test_selector: str,
        enforce_viewport: bool = False,
    ) -> ViewportRun:
        run_output = safe_output_root / label
        plugin_arguments = (
            ("-p", "scripts.run_presentation_browser_tests")
            if enforce_viewport
            else ()
        )
        command = (
            sys.executable,
            "-m",
            "pytest",
            *plugin_arguments,
            test_selector,
            "-m",
            "presentation_browser",
            "-n",
            "0",
            "--browser",
            browser,
            "--tracing",
            "retain-on-failure",
            "--output",
            str(run_output),
            "--capture=tee-sys",
            "-W",
            "error",
        )
        return ViewportRun(
            label=label,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            scope=scope,
            output_root=str(run_output),
            command=command,
            enforce_viewport=enforce_viewport,
        )

    runs = (
        viewport_run(
            label="desktop-landscape",
            viewport_width=1280,
            viewport_height=720,
            scope="full-suite",
            test_selector=str(TEST_FILE.relative_to(ROOT)),
        ),
        viewport_run(
            label="compact-4x3",
            viewport_width=1024,
            viewport_height=768,
            scope="layout-containment",
            test_selector=LAYOUT_CONTAINMENT_TEST,
            enforce_viewport=True,
        ),
    )
    return BrowserPlan(
        browser=browser,
        browser_root=str(safe_browser_root),
        output_root=str(safe_output_root),
        test_file=str(TEST_FILE.relative_to(ROOT)),
        timeout_seconds=timeout_seconds,
        runs=runs,
    )


def _requested_viewport() -> tuple[int, int] | None:
    """Parse the CSS-only viewport override used by the focused pytest replay."""
    rendered = os.environ.get(VIEWPORT_SIZE_ENV)
    if rendered is None:
        return None
    width_text, separator, height_text = rendered.partition("x")
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise RuntimeError(f"invalid {VIEWPORT_SIZE_ENV}: {rendered}") from exc
    if separator != "x" or width <= 0 or height <= 0:
        raise RuntimeError(f"invalid {VIEWPORT_SIZE_ENV}: {rendered}")
    return width, height


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply an exact viewport marker when this module is loaded as a plugin."""
    viewport = _requested_viewport()
    if viewport is None:
        return
    import pytest

    width, height = viewport
    marker = pytest.mark.browser_context_args(
        viewport={"width": width, "height": height}
    )
    for item in items:
        item.add_marker(marker)


def validate_plan(plan: BrowserPlan) -> None:
    """Verify static prerequisites without importing or launching a browser."""
    if not TEST_FILE.is_file():
        raise RuntimeError("presentation browser test file is missing")
    profile = ROOT / "requirements" / "profiles" / "presentation-test" / "pyproject.toml"
    if not profile.is_file():
        raise RuntimeError("presentation dependency profile is missing")
    try:
        profile_data = tomllib.loads(profile.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError("presentation dependency profile is invalid") from exc
    dependencies = profile_data.get("project", {}).get("dependencies")
    if not isinstance(dependencies, list) or not all(
        isinstance(dependency, str) for dependency in dependencies
    ):
        raise RuntimeError("presentation dependency profile has invalid dependencies")
    for requirement in sorted(PINNED_PRESENTATION_DEPENDENCIES):
        if requirement not in dependencies:
            raise RuntimeError(f"missing pinned presentation dependency: {requirement}")
    if not plan.runs:
        raise RuntimeError("presentation viewport matrix is empty")
    for run in plan.runs:
        if run.command[0] != sys.executable or "shell" in run.command:
            raise RuntimeError("presentation browser command is not a direct bounded argv")


def _browser_label(browser: str) -> str:
    """Return the user-facing name for one allowlisted engine."""
    return {"chromium": "Chromium", "webkit": "WebKit"}[browser]


def _require_browser_executable(plan: BrowserPlan) -> Path:
    """Fail with the owned install target when the requested engine is absent."""
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = plan.browser_root
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed; run make sync") from exc
    with sync_playwright() as playwright:
        executable = Path(getattr(playwright, plan.browser).executable_path)
    if not executable.is_file():
        label = _browser_label(plan.browser)
        raise RuntimeError(f"{label} is not installed; run make presentation-browser-install")
    return executable


def _require_browser_launchable(plan: BrowserPlan) -> None:
    """Launch and close one engine so missing host libraries fail immediately."""
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = plan.browser_root
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed; run make sync") from exc
    try:
        with sync_playwright() as playwright:
            runtime = getattr(playwright, plan.browser).launch(headless=True)
            runtime.close()
    except PlaywrightError as exc:
        label = _browser_label(plan.browser)
        raise RuntimeError(f"{label} cannot launch after dependency installation") from exc


def install_browser(plan: BrowserPlan, *, with_dependencies: bool = False) -> int:
    """Install one pinned engine and optionally its host-library contract."""
    validate_plan(plan)
    Path(plan.browser_root).mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PLAYWRIGHT_BROWSERS_PATH"] = plan.browser_root
    dependency_arguments = ("--with-deps",) if with_dependencies else ()
    command = (
        sys.executable,
        "-m",
        "playwright",
        "install",
        *dependency_arguments,
        plan.browser,
    )
    print(
        f"presentation-browser browser={plan.browser} phase=install status=starting",
        flush=True,
    )
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            timeout=plan.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(
            f"presentation-browser browser={plan.browser} phase=install status=timeout",
            flush=True,
        )
        return 124
    if completed.returncode == 0:
        _require_browser_executable(plan)
        if with_dependencies:
            _require_browser_launchable(plan)
    print(
        f"presentation-browser browser={plan.browser} phase=install "
        f"status=finished exit={completed.returncode}",
        flush=True,
    )
    return completed.returncode


def run_plan(plan: BrowserPlan) -> int:
    """Run the viewport matrix with each direct subprocess independently bounded."""
    validate_plan(plan)
    _require_browser_executable(plan)
    for run in plan.runs:
        Path(run.output_root).mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["PLAYWRIGHT_BROWSERS_PATH"] = plan.browser_root
        environment["GLUDD_PRESENTATION_BROWSER_OUTPUT"] = run.output_root
        if run.enforce_viewport:
            environment[VIEWPORT_SIZE_ENV] = (
                f"{run.viewport_width}x{run.viewport_height}"
            )
        else:
            environment.pop(VIEWPORT_SIZE_ENV, None)
        print(
            f"presentation-browser browser={plan.browser} run={run.label} "
            f"viewport={run.viewport_width}x{run.viewport_height} "
            f"scope={run.scope} phase=pytest status=starting",
            flush=True,
        )
        try:
            completed = subprocess.run(
                run.command,
                cwd=ROOT,
                env=environment,
                timeout=plan.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print(
                f"presentation-browser browser={plan.browser} run={run.label} "
                "phase=pytest status=timeout",
                flush=True,
            )
            return 124
        print(
            f"presentation-browser browser={plan.browser} run={run.label} "
            f"phase=pytest status=finished exit={completed.returncode}",
            flush=True,
        )
        if completed.returncode != 0:
            return completed.returncode
    return 0


def main() -> None:
    """Validate the execution plan or run it after explicit opt-in."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true", help="print and validate plan without side effects")
    mode.add_argument("--run", action="store_true", help="launch the pinned browser acceptance")
    mode.add_argument("--check-browser", action="store_true", help="verify the pinned browser without writing")
    mode.add_argument("--install-browser", action="store_true", help="install the pinned browser into the owned cache")
    mode.add_argument(
        "--install-browser-with-deps",
        action="store_true",
        help="install the pinned browser plus host libraries, then launch-probe it",
    )
    parser.add_argument("--browser", default="chromium")
    parser.add_argument("--browser-root", type=Path, default=DEFAULT_BROWSER_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    plan = build_plan(
        browser=args.browser,
        browser_root=args.browser_root,
        output_root=args.output_root,
        timeout_seconds=args.timeout_seconds,
    )
    if args.validate_only:
        validate_plan(plan)
        print(json.dumps(asdict(plan), indent=2, sort_keys=True))
        return
    if args.check_browser:
        validate_plan(plan)
        executable = _require_browser_executable(plan)
        print(json.dumps({"browser": plan.browser, "executable": str(executable), "status": "available"}))
        return
    if args.install_browser:
        raise SystemExit(install_browser(plan))
    if args.install_browser_with_deps:
        raise SystemExit(install_browser(plan, with_dependencies=True))
    raise SystemExit(run_plan(plan))


if __name__ == "__main__":
    main()
