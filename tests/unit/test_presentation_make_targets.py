"""Make contracts for the offline-first Reveal.js presentation tooling."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _contract(name: str) -> dict[str, object]:
    payload = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    return next(item for item in payload["targets"] if item["name"] == name)


def test_presentation_asset_target_is_offline_and_read_only_by_default() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "PRESENTATION_VENDOR_VALIDATE_ONLY ?= 1" in makefile
    assert "vendor-presentation-assets:" in makefile
    assert "scripts/vendor_presentation_assets.py" in makefile
    assert "--validate-only" in makefile
    assert "--refresh" in makefile
    assert "vendor-presentation-assets" in makefile
    assert _contract("vendor-presentation-assets") == {
        "name": "vendor-presentation-assets",
        "make_variables": ["PRESENTATION_VENDOR_VALIDATE_ONLY"],
        "behavior": (
            "make vendor-presentation-assets "
            "PRESENTATION_VENDOR_VALIDATE_ONLY=1"
        ),
    }


def test_presentation_browser_target_has_explicit_owned_bounds() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "PRESENTATION_BROWSER_VALIDATE_ONLY ?= 1" in makefile
    assert "PRESENTATION_BROWSER_ROOT ?= /tmp/gludd-playwright-browsers" in makefile
    assert "PRESENTATION_BROWSER_OUTPUT ?= /tmp/gludd-presentation-browser" in makefile
    assert "PRESENTATION_BROWSER_TIMEOUT ?= 300" in makefile
    assert "PRESENTATION_BROWSER_ENGINES ?= chromium webkit" in makefile
    assert "presentation-browser-test:" in makefile
    assert (
        "$(UV) run --extra presentation-test python "
        "scripts/run_presentation_browser_tests.py"
    ) in makefile
    for argument in ("--browser", "--browser-root", "--output-root", "--timeout-seconds"):
        assert argument in makefile
    assert "--validate-only" in makefile
    assert "--run" in makefile
    assert "presentation-browser-test" in makefile
    assert (
        "presentation-browser-test   Validate/run bounded Chromium + WebKit acceptance "
        "(PRESENTATION_BROWSER_VALIDATE_ONLY=0|1)"
    ) in makefile
    assert _contract("presentation-browser-test") == {
        "name": "presentation-browser-test",
        "make_variables": [
            "PRESENTATION_BROWSER_VALIDATE_ONLY",
            "PRESENTATION_BROWSER_ENGINES",
            "PRESENTATION_BROWSER_ROOT",
            "PRESENTATION_BROWSER_OUTPUT",
            "PRESENTATION_BROWSER_TIMEOUT",
        ],
        "behavior": (
            "make presentation-browser-test "
            "PRESENTATION_BROWSER_VALIDATE_ONLY=1 "
            "PRESENTATION_BROWSER_ENGINES='chromium webkit' "
            "PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers "
            "PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser "
            "PRESENTATION_BROWSER_TIMEOUT=300"
        ),
    }


def test_presentation_browser_install_is_read_only_by_default() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY ?= 1" in makefile
    assert "PRESENTATION_BROWSER_INSTALL_TIMEOUT ?= 600" in makefile
    assert "PRESENTATION_BROWSER_ENGINES ?= chromium webkit" in makefile
    assert "presentation-browser-install:" in makefile
    assert (
        "$(UV) run --extra presentation-test python "
        "scripts/run_presentation_browser_tests.py"
    ) in makefile
    for argument in (
        "--check-browser",
        "--install-browser",
        "--browser",
        "--browser-root",
        "--output-root",
        "--timeout-seconds",
    ):
        assert argument in makefile
    assert "presentation-browser-install" in makefile
    assert (
        "presentation-browser-install Check/install pinned Chromium + WebKit "
        "(PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY=0|1)"
    ) in makefile
    assert _contract("presentation-browser-install") == {
        "name": "presentation-browser-install",
        "make_variables": [
            "PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY",
            "PRESENTATION_BROWSER_ENGINES",
            "PRESENTATION_BROWSER_ROOT",
            "PRESENTATION_BROWSER_OUTPUT",
            "PRESENTATION_BROWSER_INSTALL_TIMEOUT",
        ],
        "behavior": (
            "make presentation-browser-install "
            "PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY=1 "
            "PRESENTATION_BROWSER_ENGINES='chromium webkit' "
            "PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers "
            "PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser "
            "PRESENTATION_BROWSER_INSTALL_TIMEOUT=600"
        ),
    }


def test_native_safari_target_is_bounded_and_read_only_by_default() -> None:
    """Native Safari remains explicit and never enables Remote Automation."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "PRESENTATION_SAFARI_VALIDATE_ONLY ?= 1" in makefile
    assert "PRESENTATION_SAFARI_DRIVER ?= /usr/bin/safaridriver" in makefile
    assert "PRESENTATION_SAFARI_OUTPUT ?= /tmp/gludd-presentation-safari" in makefile
    assert "PRESENTATION_SAFARI_TIMEOUT ?= 60" in makefile
    assert "presentation-safari-test:" in makefile
    assert "scripts.run_presentation_safari_smoke" in makefile
    assert "--enable" not in makefile
    assert "presentation-safari-test" in makefile
    assert _contract("presentation-safari-test") == {
        "name": "presentation-safari-test",
        "make_variables": [
            "PRESENTATION_SAFARI_VALIDATE_ONLY",
            "PRESENTATION_SAFARI_DRIVER",
            "PRESENTATION_SAFARI_OUTPUT",
            "PRESENTATION_SAFARI_TIMEOUT",
        ],
        "behavior": (
            "make presentation-safari-test "
            "PRESENTATION_SAFARI_VALIDATE_ONLY=1 "
            "PRESENTATION_SAFARI_DRIVER=/usr/bin/safaridriver "
            "PRESENTATION_SAFARI_OUTPUT=/tmp/gludd-presentation-safari "
            "PRESENTATION_SAFARI_TIMEOUT=60"
        ),
    }


def test_presentation_pages_probe_is_bounded_and_read_only_by_default() -> None:
    """Public revision checks must be explicit, bounded, and content-free."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "PRESENTATION_PAGES_PROBE_VALIDATE_ONLY ?= 1" in makefile
    assert "PRESENTATION_PAGES_PROBE_TIMEOUT ?= 20" in makefile
    assert "presentation-pages-probe:" in makefile
    assert "scripts/probe_presentation_pages.py" in makefile
    for argument in ("--url", "--expected-sha", "--timeout-seconds"):
        assert argument in makefile
    assert "presentation-pages-probe" in makefile
    assert _contract("presentation-pages-probe") == {
        "name": "presentation-pages-probe",
        "make_variables": [
            "PRESENTATION_PAGES_PROBE_VALIDATE_ONLY",
            "PRESENTATION_PAGES_URL",
            "PRESENTATION_PAGES_EXPECTED_SHA",
            "PRESENTATION_PAGES_PROBE_TIMEOUT",
        ],
        "behavior": (
            "make presentation-pages-probe "
            "PRESENTATION_PAGES_PROBE_VALIDATE_ONLY=1 "
            "PRESENTATION_PAGES_URL=https://sandboxcom.github.io/gludd/ "
            f"PRESENTATION_PAGES_EXPECTED_SHA={'a' * 40} "
            "PRESENTATION_PAGES_PROBE_TIMEOUT=20"
        ),
    }
