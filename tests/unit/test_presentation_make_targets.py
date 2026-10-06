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
    assert "presentation-browser-test:" in makefile
    assert (
        "$(UV) run --extra presentation-test python "
        "scripts/run_presentation_browser_tests.py"
    ) in makefile
    for argument in ("--browser-root", "--output-root", "--timeout-seconds"):
        assert argument in makefile
    assert "--validate-only" in makefile
    assert "--run" in makefile
    assert "presentation-browser-test" in makefile
    assert (
        "presentation-browser-test   Validate/run bounded Chromium acceptance "
        "(PRESENTATION_BROWSER_VALIDATE_ONLY=0|1)"
    ) in makefile
    assert _contract("presentation-browser-test") == {
        "name": "presentation-browser-test",
        "make_variables": [
            "PRESENTATION_BROWSER_VALIDATE_ONLY",
            "PRESENTATION_BROWSER_ROOT",
            "PRESENTATION_BROWSER_OUTPUT",
            "PRESENTATION_BROWSER_TIMEOUT",
        ],
        "behavior": (
            "make presentation-browser-test "
            "PRESENTATION_BROWSER_VALIDATE_ONLY=1 "
            "PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers "
            "PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser "
            "PRESENTATION_BROWSER_TIMEOUT=300"
        ),
    }


def test_presentation_browser_install_is_read_only_by_default() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY ?= 1" in makefile
    assert "PRESENTATION_BROWSER_INSTALL_TIMEOUT ?= 600" in makefile
    assert "presentation-browser-install:" in makefile
    assert (
        "$(UV) run --extra presentation-test python "
        "scripts/run_presentation_browser_tests.py"
    ) in makefile
    for argument in (
        "--check-browser",
        "--install-browser",
        "--browser-root",
        "--output-root",
        "--timeout-seconds",
    ):
        assert argument in makefile
    assert "presentation-browser-install" in makefile
    assert (
        "presentation-browser-install Check/install pinned Chromium "
        "(PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY=0|1)"
    ) in makefile
    assert _contract("presentation-browser-install") == {
        "name": "presentation-browser-install",
        "make_variables": [
            "PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY",
            "PRESENTATION_BROWSER_ROOT",
            "PRESENTATION_BROWSER_OUTPUT",
            "PRESENTATION_BROWSER_INSTALL_TIMEOUT",
        ],
        "behavior": (
            "make presentation-browser-install "
            "PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY=1 "
            "PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers "
            "PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser "
            "PRESENTATION_BROWSER_INSTALL_TIMEOUT=600"
        ),
    }
