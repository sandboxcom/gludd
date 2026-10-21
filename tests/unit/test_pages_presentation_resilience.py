"""Structural contracts for presentation validation and operator guidance."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"
MAKE_FRAGMENT = ROOT / "make" / "90-infrastructure-and-services.mk"
DESIGN = ROOT / "docs" / "presentation" / "DESIGN_revealjs_deck.md"
DECK = ROOT / "docs" / "presentation" / "deck" / "index.html"
BROWSER_TEST = ROOT / "tests" / "browser" / "test_presentation.py"
SETUP_PYTHON_V7_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"


def test_pages_browser_matrix_retains_every_engine_without_sibling_cancellation() -> None:
    """Independent hosted engines must retain both failure and success evidence."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    validate = workflow["jobs"]["validate"]
    assert validate["strategy"] == {
        "fail-fast": False,
        "max-parallel": 2,
        "matrix": {"browser": ["chromium", "webkit"]},
    }
    assert validate["timeout-minutes"] == 20
    assert "continue-on-error" not in validate
    for name in (
        "Install and launch-probe pinned browser system dependencies",
        "Install pinned browsers",
        "Test exact Pages directory below /gludd/",
    ):
        step = next(step for step in validate["steps"] if step["name"] == name)
        assert 'PRESENTATION_BROWSER_ENGINES="${{ matrix.browser }}"' in step["run"]
    assert "PRESENTATION_BROWSER_TIMEOUT=600" in next(
        step["run"] for step in validate["steps"]
        if step["name"] == "Test exact Pages directory below /gludd/"
    )


def test_pages_matrix_diagnostics_are_unique_and_all_engines_gate_one_artifact() -> None:
    """Both engines gate deployment while only one validated tree is uploaded."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    steps = jobs["validate"]["steps"]
    diagnostic = next(step for step in steps if step["name"] == "Retain presentation browser diagnostics")
    assert diagnostic["if"] == "failure() || cancelled()"
    assert diagnostic["with"]["name"] == (
        "presentation-browser-diagnostics-${{ matrix.browser }}-${{ github.run_attempt }}"
    )
    upload = next(step for step in steps if step["name"] == "Preserve validated Pages directory")
    assert upload["if"] == (
        "github.event_name == 'push' && github.ref == 'refs/heads/master' "
        "&& matrix.browser == 'chromium'"
    )
    assert upload["with"]["name"] == "gludd-pages-deck"
    assert upload["with"]["path"] == "docs/presentation/deck"
    assert jobs["deploy"]["needs"] == ["validate", "javascript-coverage"]
    assert "always()" not in jobs["deploy"]["if"]


def test_pages_coverage_has_one_original_bounded_pass_after_both_engines() -> None:
    """The verified outer-job timeout must not consume two browser passes."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    coverage = jobs["javascript-coverage"]
    assert coverage["needs"] == "validate"
    assert coverage["timeout-minutes"] == jobs["validate"]["timeout-minutes"] == 20
    assert not coverage.get("if")
    assert not coverage.get("strategy")
    assert not coverage.get("continue-on-error")
    steps = coverage["steps"]
    commands = [step["run"] for step in steps if "run" in step]
    owners = (
        "make node-deps-sync", "make sync", "make vendor-presentation-assets",
        "make deck-build", "make presentation-browser-install-deps",
        "make presentation-browser-install ", "make presentation-browser-test",
    )
    positions = [next(index for index, command in enumerate(commands) if owner in command) for owner in owners]
    assert positions == sorted(positions)
    sync = commands[positions[1]]
    for value in (
        "DEPENDENCY_PROFILE_SET=ci", "DEPENDENCY_PROFILE_ENVIRONMENT=.venv",
        "DEPENDENCY_PROFILE_PYTHON=3.11", "DEPENDENCY_PROFILE_VALIDATE_ONLY=0",
    ):
        assert value in sync
    assert "PRESENTATION_VENDOR_VALIDATE_ONLY=1" in commands[positions[2]]
    for index in positions[4:]:
        assert "PRESENTATION_BROWSER_ENGINES=chromium" in commands[index]
        assert "PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers" in commands[index]
        assert (
            "PRESENTATION_BROWSER_OUTPUT="
            "/tmp/gludd-presentation-js-coverage"
        ) in commands[index]
    for index in positions[4:6]:
        assert "PRESENTATION_BROWSER_INSTALL_TIMEOUT=600" in commands[index]
    assert "PRESENTATION_BROWSER_DEPS_VALIDATE_ONLY=0" in commands[positions[4]]
    assert "PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY=0" in commands[positions[5]]
    assert "PRESENTATION_BROWSER_VALIDATE_ONLY=0" in commands[positions[6]]
    assert "PRESENTATION_BROWSER_TIMEOUT=600" in commands[positions[6]]
    assert "PRESENTATION_BROWSER_JS_COVERAGE=1" in commands[positions[6]]
    assert not any(step.get("continue-on-error") for step in steps)


def test_pages_jobs_pin_the_measured_runner_instead_of_migrating_implicitly() -> None:
    """The hosted Ubuntu migration notice must not silently change native inputs."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        assert job["runs-on"] == "ubuntu-24.04"


def test_pages_validates_development_and_pull_requests_before_upload() -> None:
    """The exact Pages tree must pass the browser lane before upload."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "branches: [master, development]" in workflow
    assert "make vendor-presentation-assets PRESENTATION_VENDOR_VALIDATE_ONLY=1" in workflow
    assert "make presentation-browser-install" in workflow
    assert 'PRESENTATION_BROWSER_ENGINES="${{ matrix.browser }}"' in workflow
    assert "browser: [chromium, webkit]" in workflow
    build = workflow.index("make deck-build")
    browser = workflow.index("make presentation-browser-test")
    upload = workflow.index("actions/upload-artifact@")
    assert build < browser < upload
    assert "path: docs/presentation/deck" in workflow
    assert "continue-on-error" not in workflow


def test_pages_syncs_the_ci_profile_that_owns_playwright() -> None:
    """The browser runner must execute inside the locked presentation environment."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    sync = workflow.index("make sync")
    deps = workflow.index("make presentation-browser-install-deps")

    assert "DEPENDENCY_PROFILE_SET=ci" in workflow
    assert "DEPENDENCY_PROFILE_ENVIRONMENT=.venv" in workflow
    assert "DEPENDENCY_PROFILE_PYTHON=3.11" in workflow
    assert "DEPENDENCY_PROFILE_VALIDATE_ONLY=0" in workflow
    assert sync < deps


def test_pages_uses_node24_setup_python() -> None:
    """The Pages lane must not emit GitHub's Node 20 deprecation warning."""
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count(f"actions/setup-python@{SETUP_PYTHON_V7_SHA}") == 3
    assert workflow.count("# v7.0.0 (node24)") == 3
    assert "a26af69be951a213d495a4c3e4e4022e16d87065" not in workflow


def test_pages_installs_linux_webkit_dependencies_before_launch() -> None:
    """The hosted WebKit lane must install its Linux shared libraries first."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    make_fragment = MAKE_FRAGMENT.read_text(encoding="utf-8")

    deps_target = "make presentation-browser-install-deps"
    browser_target = "make presentation-browser-install\n"
    test_target = "make presentation-browser-test"
    assert deps_target in workflow
    assert 'PRESENTATION_BROWSER_ENGINES="${{ matrix.browser }}"' in workflow
    assert "browser: [chromium, webkit]" in workflow
    assert workflow.index(deps_target) < workflow.index(browser_target)
    assert workflow.index(deps_target) < workflow.index(test_target)
    assert "presentation-browser-install-deps:" in make_fragment
    assert "--install-browser-with-deps" in make_fragment
    assert "scripts/run_presentation_browser_tests.py" in make_fragment
    assert "PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers" in workflow
    assert "PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser" in workflow
    assert "PRESENTATION_BROWSER_INSTALL_TIMEOUT=600" in workflow


def test_pages_deploys_only_the_validated_master_artifact() -> None:
    """Development validates continuously; only the release branch publishes."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "needs: validate" in workflow
    assert "github.ref == 'refs/heads/master'" in workflow
    assert "github.ref == 'refs/heads/development'" not in workflow
    assert "github.event_name == 'push'" in workflow
    assert "actions/download-artifact@" in workflow
    assert workflow.index("actions/download-artifact@") < workflow.index("actions/upload-pages-artifact@")
    assert workflow.index("actions/upload-pages-artifact@") < workflow.index("actions/deploy-pages@")
    assert workflow.index("actions/deploy-pages@") < workflow.index("make presentation-pages-probe")
    assert "PRESENTATION_PAGES_EXPECTED_SHA=${{ github.sha }}" in workflow


def test_browser_lane_can_serve_the_resolved_upload_tree() -> None:
    """A Pages build must be tested in place rather than reconstructed."""
    source = BROWSER_TEST.read_text(encoding="utf-8")
    assert "build_deck.DECK_DIR" in source
    assert "SOURCE_ALLOWLIST" in source
    assert "serve_dir=serve_dir" in source


def test_todo_state_diagram_keeps_canonical_review_id_behind_compact_label() -> None:
    """The viewport-safe label must not rename lifecycle transition endpoints."""
    deck = DECK.read_text(encoding="utf-8")
    diagram = deck.split("<h2>The todo state machine</h2>", 1)[1].split(
        "</section>", 1
    )[0]

    assert 'state "Review result" as REVIEWING_RETURN' in diagram
    for transition in (
        "AWAITING_RESULT --> REVIEWING_RETURN: result persisted",
        "REVIEWING_RETURN --> COMPLETE: approve",
        "REVIEWING_RETURN --> NEEDS_MORE_WORK: changes",
        "REVIEWING_RETURN --> FAILED: reject",
    ):
        assert transition in diagram


def test_implementation_guide_keeps_upstream_regressions_and_operations() -> None:
    """Long-lived practitioner reports remain next to the operating contract."""
    design = DESIGN.read_text(encoding="utf-8")
    for issue in (
        "mermaid-js/mermaid#1846",
        "mermaid-js/mermaid#1824",
        "mermaid-js/mermaid#3577",
        "mgaitan/sphinxcontrib-mermaid#126",
        "zjffun/reveal.js-mermaid-plugin#5",
        "mermaid-js/mermaid#5122",
        "mermaid-js/mermaid#6666",
        "mermaid-js/mermaid#7323",
        "gitlab-org/gitlab-docs#599",
        "mermaid-js/mermaid#8113",
        "mermaid-js/mermaid#4918",
        "bugs.webkit.org/show_bug.cgi?id=198609",
        "github.com/orgs/community/discussions/12523",
        "astral-sh/uv/issues/13319",
        "astral-sh/uv/issues/14645",
    ):
        assert issue in design
    assert "reveal.js-mermaid-plugin@11.15.0" in design
    assert "ace-builds@1.44.0" in design
    assert "make presentation-browser-test" in design
    assert "presentation-pages-probe" in design
    assert "Chromium and WebKit" in design
    assert "/__gludd_source__" in design
