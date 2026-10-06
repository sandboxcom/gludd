#!/usr/bin/env python3
"""Run the mandatory presentation browser acceptance in a bounded process."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_FILE = ROOT / "tests" / "browser" / "test_presentation.py"
DEFAULT_OUTPUT_ROOT = Path("/tmp/gludd-presentation-browser")
DEFAULT_BROWSER_ROOT = Path("/tmp/gludd-playwright-browsers")
SUPPORTED_BROWSERS = frozenset({"chromium", "webkit"})


@dataclass(frozen=True)
class BrowserPlan:
    """Auditable, serializable description of one browser acceptance run."""

    browser: str
    browser_root: str
    output_root: str
    test_file: str
    timeout_seconds: int
    command: tuple[str, ...]


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
    command = (
        sys.executable,
        "-m",
        "pytest",
        str(TEST_FILE.relative_to(ROOT)),
        "-m",
        "presentation_browser",
        "-n",
        "0",
        "--browser",
        browser,
        "--tracing",
        "retain-on-failure",
        "--output",
        str(safe_output_root),
        "-W",
        "error",
    )
    return BrowserPlan(
        browser=browser,
        browser_root=str(safe_browser_root),
        output_root=str(safe_output_root),
        test_file=str(TEST_FILE.relative_to(ROOT)),
        timeout_seconds=timeout_seconds,
        command=command,
    )


def validate_plan(plan: BrowserPlan) -> None:
    """Verify static prerequisites without importing or launching a browser."""
    if not TEST_FILE.is_file():
        raise RuntimeError("presentation browser test file is missing")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for requirement in ('"playwright==1.63.0"', '"pytest-playwright==0.9.0"'):
        if requirement not in pyproject:
            raise RuntimeError(f"missing pinned presentation dependency: {requirement}")
    if plan.command[0] != sys.executable or "shell" in plan.command:
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


def install_browser(plan: BrowserPlan) -> int:
    """Install one pinned engine into the explicit project-owned cache."""
    validate_plan(plan)
    Path(plan.browser_root).mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PLAYWRIGHT_BROWSERS_PATH"] = plan.browser_root
    command = (sys.executable, "-m", "playwright", "install", plan.browser)
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
    print(
        f"presentation-browser browser={plan.browser} phase=install "
        f"status=finished exit={completed.returncode}",
        flush=True,
    )
    return completed.returncode


def run_plan(plan: BrowserPlan) -> int:
    """Run browser acceptance once with owned caches and a hard timeout."""
    validate_plan(plan)
    _require_browser_executable(plan)
    Path(plan.output_root).mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PLAYWRIGHT_BROWSERS_PATH"] = plan.browser_root
    environment["GLUDD_PRESENTATION_BROWSER_OUTPUT"] = plan.output_root
    print(
        f"presentation-browser browser={plan.browser} phase=pytest status=starting",
        flush=True,
    )
    try:
        completed = subprocess.run(
            plan.command,
            cwd=ROOT,
            env=environment,
            timeout=plan.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(
            f"presentation-browser browser={plan.browser} phase=pytest status=timeout",
            flush=True,
        )
        return 124
    print(
        f"presentation-browser browser={plan.browser} phase=pytest "
        f"status=finished exit={completed.returncode}",
        flush=True,
    )
    return completed.returncode


def main() -> None:
    """Validate the execution plan or run it after explicit opt-in."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true", help="print and validate plan without side effects")
    mode.add_argument("--run", action="store_true", help="launch the pinned browser acceptance")
    mode.add_argument("--check-browser", action="store_true", help="verify the pinned browser without writing")
    mode.add_argument("--install-browser", action="store_true", help="install the pinned browser into the owned cache")
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
    raise SystemExit(run_plan(plan))


if __name__ == "__main__":
    main()
