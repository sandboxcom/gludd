"""Pin the implementation contract for reliable Reveal.js diagrams and links."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SPEC = ROOT / "docs" / "design" / "specs" / "SPEC_REVEAL_RENDER_RESILIENCE.md"


def _spec_text() -> str:
    """Return the proposed resilience contract as lower-case searchable text."""
    return SPEC.read_text(encoding="utf-8").lower()


def test_spec_records_reproducible_mermaid_root_causes() -> None:
    """The design must retain the upstream evidence behind each mitigation."""
    text = _spec_text()

    for token in (
        "mermaid-js/mermaid#1846",
        "mermaid-js/mermaid#1824",
        "mermaid-js/mermaid#3577",
        "display: none",
        "without await",
        "slidetransitionend",
    ):
        assert token in text


def test_spec_selects_owned_assets_and_a_maintained_code_viewer() -> None:
    """Runtime dependencies and the local viewer must be deterministic."""
    text = _spec_text()

    for token in (
        "reveal.js-mermaid-plugin@11.15.0",
        "ace-builds@1.44.0",
        "vendored",
        "relative urls",
        "read only",
        "gotoline",
        "selection.setselectionrange",
    ):
        assert token in text


def test_spec_defines_fail_visible_serial_rendering() -> None:
    """A missing diagram must become observable instead of silently vanishing."""
    text = _spec_text()

    for token in (
        "one in-flight render",
        "current visible slide",
        "data-mermaid-state",
        "positive viewbox",
        "render failed",
        "original source",
        "zero unrendered diagrams",
        "console errors",
    ):
        assert token in text


def test_spec_defines_github_and_local_file_line_navigation() -> None:
    """Every source citation must retain an immutable public destination."""
    text = _spec_text()

    for token in (
        "github.com/sandboxcom/gludd/blob/{{git_sha_full}}/",
        "#l{start}-l{end}",
        "data-source-path",
        "data-source-lines",
        "/__gludd_source__",
        "path traversal",
        "symlink",
    ):
        assert token in text


def test_spec_requires_subpath_and_fault_injection_browser_proofs() -> None:
    """The browser suite must model Pages and the historical failure modes."""
    text = _spec_text()

    for token in (
        "/gludd/",
        "no 404",
        "non-zero bounding box",
        "navigate forward",
        "navigate backward",
        "invalid diagram",
        "blocked asset",
        "github #l",
        "85%",
        "75%",
    ):
        assert token in text
