"""Make contracts for the offline-first Reveal.js presentation tooling."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]


def _contract(name: str) -> dict[str, object]:
    payload = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    return next(item for item in payload["targets"] if item["name"] == name)


def test_presentation_asset_target_is_offline_and_read_only_by_default() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    presentation_fragment = (
        ROOT / "make" / "90-infrastructure-and-services.mk"
    ).read_text(encoding="utf-8")

    assert "PRESENTATION_VENDOR_VALIDATE_ONLY ?= 1" in presentation_fragment
    assert "vendor-presentation-assets:" in presentation_fragment
    assert "scripts/vendor_presentation_assets.py" in presentation_fragment
    assert "--validate-only" in presentation_fragment
    assert "--refresh" in presentation_fragment
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
    makefile = compose_makefile(ROOT / "Makefile")
    presentation_fragment = (
        ROOT / "make" / "90-infrastructure-and-services.mk"
    ).read_text(encoding="utf-8")
    pages_workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(
        encoding="utf-8"
    )

    assert "PRESENTATION_BROWSER_VALIDATE_ONLY ?= 1" in presentation_fragment
    assert "PRESENTATION_BROWSER_ROOT ?= /tmp/gludd-playwright-browsers" in presentation_fragment
    assert "PRESENTATION_BROWSER_OUTPUT ?= /tmp/gludd-presentation-browser" in presentation_fragment
    assert "PRESENTATION_BROWSER_TIMEOUT ?= 600" in presentation_fragment
    assert "PRESENTATION_BROWSER_ENGINES ?= chromium webkit" in presentation_fragment
    assert "presentation-browser-test:" in presentation_fragment
    assert (
        "$(UV) run --extra presentation-test python "
        "scripts/run_presentation_browser_tests.py"
    ) in presentation_fragment
    for argument in ("--browser", "--browser-root", "--output-root", "--timeout-seconds"):
        assert argument in presentation_fragment
    assert "--validate-only" in presentation_fragment
    assert "--run" in presentation_fragment
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
            "PRESENTATION_BROWSER_TIMEOUT=600"
        ),
    }
    assert "PRESENTATION_BROWSER_TIMEOUT=600" in pages_workflow
    assert "PRESENTATION_BROWSER_TIMEOUT=300" not in pages_workflow


def test_presentation_browser_install_is_read_only_by_default() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    presentation_fragment = (
        ROOT / "make" / "90-infrastructure-and-services.mk"
    ).read_text(encoding="utf-8")

    assert "PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY ?= 1" in presentation_fragment
    assert "PRESENTATION_BROWSER_INSTALL_TIMEOUT ?= 600" in presentation_fragment
    assert "PRESENTATION_BROWSER_ENGINES ?= chromium webkit" in presentation_fragment
    assert "presentation-browser-install:" in presentation_fragment
    assert (
        "$(UV) run --extra presentation-test python "
        "scripts/run_presentation_browser_tests.py"
    ) in presentation_fragment
    for argument in (
        "--check-browser",
        "--install-browser",
        "--browser",
        "--browser-root",
        "--output-root",
        "--timeout-seconds",
    ):
        assert argument in presentation_fragment
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


def test_presentation_browser_dependency_install_is_bounded_and_runtime_verified() -> None:
    """The hosted Linux lane installs and launch-probes the exact pinned engine."""
    makefile = compose_makefile(ROOT / "Makefile")
    presentation_fragment = (
        ROOT / "make" / "90-infrastructure-and-services.mk"
    ).read_text(encoding="utf-8")

    assert "presentation-browser-install-deps:" in presentation_fragment
    assert "scripts/run_presentation_browser_tests.py" in presentation_fragment
    assert "--install-browser-with-deps" in presentation_fragment
    assert 'playwright install-deps "$$browser"' not in presentation_fragment
    assert "presentation-browser-install-deps" in makefile
    assert _contract("presentation-browser-install-deps") == {
        "name": "presentation-browser-install-deps",
        "make_variables": [
            "PRESENTATION_BROWSER_DEPS_VALIDATE_ONLY",
            "PRESENTATION_BROWSER_ENGINES",
            "PRESENTATION_BROWSER_ROOT",
            "PRESENTATION_BROWSER_OUTPUT",
            "PRESENTATION_BROWSER_INSTALL_TIMEOUT",
        ],
        "behavior": (
            "make presentation-browser-install-deps "
            "PRESENTATION_BROWSER_DEPS_VALIDATE_ONLY=1 "
            "PRESENTATION_BROWSER_ENGINES='webkit' "
            "PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers "
            "PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser "
            "PRESENTATION_BROWSER_INSTALL_TIMEOUT=600"
        ),
    }


def test_native_safari_target_is_bounded_and_read_only_by_default() -> None:
    """Native Safari remains explicit and never enables Remote Automation."""
    makefile = compose_makefile(ROOT / "Makefile")
    presentation_fragment = (
        ROOT / "make" / "90-infrastructure-and-services.mk"
    ).read_text(encoding="utf-8")

    assert "PRESENTATION_SAFARI_VALIDATE_ONLY ?= 1" in presentation_fragment
    assert "PRESENTATION_SAFARI_DRIVER ?= /usr/bin/safaridriver" in presentation_fragment
    assert "PRESENTATION_SAFARI_OUTPUT ?= /tmp/gludd-presentation-safari" in presentation_fragment
    assert "PRESENTATION_SAFARI_TIMEOUT ?= 60" in presentation_fragment
    assert "presentation-safari-test:" in presentation_fragment
    assert "scripts.run_presentation_safari_smoke" in presentation_fragment
    assert "--enable" not in presentation_fragment
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
    makefile = compose_makefile(ROOT / "Makefile")
    presentation_fragment = (
        ROOT / "make" / "90-infrastructure-and-services.mk"
    ).read_text(encoding="utf-8")

    assert "PRESENTATION_PAGES_PROBE_VALIDATE_ONLY ?= 1" in presentation_fragment
    assert "PRESENTATION_PAGES_PROBE_TIMEOUT ?= 20" in presentation_fragment
    assert "presentation-pages-probe:" in presentation_fragment
    assert "scripts/probe_presentation_pages.py" in presentation_fragment
    for argument in ("--url", "--expected-sha", "--timeout-seconds"):
        assert argument in presentation_fragment
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
