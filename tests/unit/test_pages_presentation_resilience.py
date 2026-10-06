"""Structural contracts for presentation validation and operator guidance."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"
DESIGN = ROOT / "docs" / "presentation" / "DESIGN_revealjs_deck.md"
BROWSER_TEST = ROOT / "tests" / "browser" / "test_presentation.py"


def test_pages_validates_development_and_pull_requests_before_upload() -> None:
    """The exact Pages tree must pass the browser lane before upload."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "branches: [master, development]" in workflow
    assert "make vendor-presentation-assets PRESENTATION_VENDOR_VALIDATE_ONLY=1" in workflow
    assert "make presentation-browser-install" in workflow
    assert 'PRESENTATION_BROWSER_ENGINES="chromium webkit"' in workflow
    build = workflow.index("make deck-build")
    browser = workflow.index("make presentation-browser-test")
    upload = workflow.index("actions/upload-artifact@")
    assert build < browser < upload
    assert "path: docs/presentation/deck" in workflow
    assert "continue-on-error" not in workflow


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
        "bugs.webkit.org/show_bug.cgi?id=198609",
        "github.com/orgs/community/discussions/12523",
    ):
        assert issue in design
    assert "reveal.js-mermaid-plugin@11.15.0" in design
    assert "ace-builds@1.44.0" in design
    assert "make presentation-browser-test" in design
    assert "presentation-pages-probe" in design
    assert "Chromium and WebKit" in design
    assert "/__gludd_source__" in design
