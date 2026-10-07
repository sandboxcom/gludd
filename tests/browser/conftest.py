"""Failure diagnostics for the mandatory presentation browser lane."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest


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
        lambda response: events["http_failures"].append(f"{response.status} {response.url}")
        if response.status >= 400
        else None,
    )
    return events


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
