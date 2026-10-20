"""Failure diagnostics for the mandatory presentation browser lane."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    import run_presentation_browser_tests as browser_runner
    from playwright.sync_api import Page
else:
    from scripts import run_presentation_browser_tests as browser_runner


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item) -> Generator[None, Any, None]:
    """Expose each phase report to fixtures without a private pytest plugin."""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.fixture
def browser_events(page: Any) -> dict[str, list[str]]:
    """Capture console, page, request, and HTTP failures for assertions."""
    events: dict[str, list[str]] = {
        "console_errors": [],
        "page_errors": [],
        "request_failures": [],
        "http_failures": [],
    }
    page.add_init_script(
        """
        (() => {
          window.gluddInvalidGeometryWrites = [];
          const invalid = /(?:undefined|NaN|Infinity)/;
          for (const method of ['setAttribute', 'setAttributeNS']) {
            const original = Element.prototype[method];
            Element.prototype[method] = function(...args) {
              const value = String(args[args.length - 1]);
              if (invalid.test(value) && window.gluddInvalidGeometryWrites.length < 20) {
                window.gluddInvalidGeometryWrites.push({
                  method,
                  name: String(args[args.length - 2]),
                  stack: new Error('invalid geometry write').stack || '',
                  tag: this.tagName || '',
                  value,
                });
              }
              return original.apply(this, args);
            };
          }
        })();
        """
    )

    def record_console(message: Any) -> None:
        if message.type != "error":
            return
        active = page.evaluate("window.gluddPresentationActiveDiagram ?? null")
        events["console_errors"].append(f"diagram={active} {message.text}")

    page.on("console", record_console)
    page.on("pageerror", lambda error: events["page_errors"].append(str(error)))
    page.on(
        "requestfailed",
        lambda request: events["request_failures"].append(request.url),
    )
    page.on(
        "response",
        lambda response: (
            events["http_failures"].append(f"{response.status} {response.url}") if response.status >= 400 else None
        ),
    )
    return events


@pytest.fixture(autouse=True)
def _retain_presentation_javascript_coverage(
    page: Page,
    browser_name: str,
    request: pytest.FixtureRequest,
) -> Generator[None, None, None]:
    """Autouse: every browser test that runs under JS coverage must attach the
    collector before the page is used and flush it after the test, without each
    test declaring the fixture explicitly. This is a process-wide coverage
    boundary managed by the fixture lifecycle."""
    directory = os.environ.get(browser_runner.JS_COVERAGE_DIR_ENV)
    manifest_name = os.environ.get(browser_runner.JS_COVERAGE_MANIFEST_ENV)
    if directory is None and manifest_name is None:
        yield
        return
    if directory is None or manifest_name is None or browser_name != "chromium":
        raise RuntimeError("JavaScript coverage requires explicit Chromium runner carriers")
    output = browser_runner._safe_owned_tmp(Path(directory), label="JavaScript coverage output")
    manifest_path = Path(manifest_name).resolve()
    if manifest_path != output / "source.json":
        raise RuntimeError("JavaScript coverage manifest is outside its owned output")
    manifest = browser_runner._read_json_object(manifest_path)
    source_path = (browser_runner.ROOT / browser_runner.CONTROLLER_RELATIVE).resolve()
    digest = manifest.get("source_sha256")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("browser") != "chromium"
        or manifest.get("source_path") != str(source_path)
        or not isinstance(digest, str)
    ):
        raise RuntimeError("JavaScript coverage source manifest is invalid")
    session = cast(
        browser_runner.CoverageSession,
        page.context.new_cdp_session(page),
    )
    collector = browser_runner.JavaScriptCoverageCollector(
        session,
        source_path=source_path,
        expected_sha256=digest,
        output_root=output,
        test_id=request.node.nodeid,
    )
    browser = page.context.browser
    if browser is None:
        raise RuntimeError("JavaScript coverage browser identity is unavailable")
    browser_runner._write_json(
        output / f"runtime-{collector.identity}.json",
        {
            "browser": browser_name,
            "browser_version": browser.version,
            "source_sha256": digest,
            "test_id": request.node.nodeid,
            "source_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        },
    )
    collector.start()
    with browser_runner.coverage_boundaries(page, collector):
        try:
            yield
        finally:
            collector.finish()


@pytest.fixture(autouse=True)
def _retain_browser_failure_artifacts(
    page: Any,
    request: pytest.FixtureRequest,
) -> Generator[None, None, None]:
    """Ensure failed acceptance runs retain browser state for diagnosis."""
    yield
    report = getattr(request.node, "rep_call", None)
    if report is None or not report.failed:
        return
    output = Path(
        os.environ.get(
            "GLUDD_PRESENTATION_BROWSER_OUTPUT",
            str(Path(tempfile.gettempdir()) / "gludd-presentation-browser"),
        )
    )
    output.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", request.node.nodeid)[-160:]
    try:
        (output / f"{stem}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(output / f"{stem}.png"), full_page=True)
        health = page.evaluate(
            "typeof window.gluddPresentationHealth === 'function' ? window.gluddPresentationHealth() : null"
        )
        (output / f"{stem}.health.json").write_text(
            json.dumps(health, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:  # Browser may already have terminated; retain content-free status.
        (output / f"{stem}.capture-error.txt").write_text(type(exc).__name__ + "\n", encoding="utf-8")
