"""Regression tests for the Reveal.js runtime, assets, and source viewer."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from scripts import build_deck

ROOT = Path(__file__).parent.parent.parent
DECK = ROOT / "docs" / "presentation" / "deck"


def test_deck_uses_only_owned_relative_runtime_assets() -> None:
    """The Pages artifact must remain useful when every CDN is unavailable."""
    html = (DECK / "index.html").read_text(encoding="utf-8")

    assert "cdn.jsdelivr.net" not in html
    assert "unpkg.com" not in html
    assert "@latest" not in html
    for asset in (
        "./vendor/reveal/reveal.css",
        "./vendor/reveal/reveal.js",
        "./vendor/reveal/plugin/highlight/highlight.js",
        "./vendor/reveal/plugin/notes/notes.js",
        "./vendor/mermaid/mermaid.js",
        "./vendor/ace/ace.js",
        "./presentation.js?v={{GIT_SHA_FULL}}",
        "./presentation.css?v={{GIT_SHA_FULL}}",
    ):
        assert asset in html
    assert "./vendor/reveal/theme/black.css" not in html


def test_vendor_manifest_is_exact_and_digest_bound() -> None:
    """Every vendored runtime file must be attributed and content addressed."""
    manifest = json.loads((DECK / "vendor" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "gludd-presentation-vendor/v1"
    assert {(item["package"], item["version"]) for item in manifest["assets"]} == {
        ("reveal.js", "5.1.0"),
        ("reveal.js-mermaid-plugin", "11.15.0"),
        ("ace-builds", "1.44.0"),
    }
    destinations = set()
    for item in manifest["assets"]:
        destination = item["destination"]
        destinations.add(destination)
        path = DECK / "vendor" / destination
        assert path.is_file(), destination
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        license_path = DECK / "vendor" / item["license"]
        assert license_path.is_file()
        assert hashlib.sha256(license_path.read_bytes()).hexdigest() == item["license_sha256"]
    vendor_root = DECK / "vendor"
    actual = {
        path.relative_to(vendor_root).as_posix()
        for path in vendor_root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    expected = destinations | {item["license"] for item in manifest["assets"]}
    assert actual == expected


def test_runtime_eagerly_renders_once_and_keeps_failures_visible() -> None:
    """Static charts use the awaited direct API without slide-visit retries."""
    runtime = (DECK / "presentation.js").read_text(encoding="utf-8")

    for token in (
        "overviewhidden",
        "requestAnimationFrame",
        "offsetParent",
        "getBoundingClientRect",
        "renderPendingDiagrams",
        "validateVisibleGeometry",
        "hasInvalidSvgAttributes",
        "gludd-mermaid-stage",
        "gludd-mermaid-deferred",
        "createRenderStage",
        "createRenderScratch",
        "stabilizeSvgViewport",
        "viewBoxDimensions",
        "mermaidViewport",
        "preserveAspectRatio",
        "BOOT_DIAGNOSTIC",
        "document.fonts.ready",
        "window.gluddMermaid",
        "await mermaidApi.render(",
        "htmlLabels: false",
        "data-mermaid-state",
        "renderer-unavailable",
        "invalid-source",
        "invalid-geometry",
        "Diagram render failed",
        "gluddPresentationHealth",
        "gluddPresentationClientErrors",
    ):
        assert token in runtime
    for legacy_retry in (
        "requestedDiagram",
        "awaitRenderOutcome",
        "RENDER_TIMEOUT_MS",
        "placeInStage",
    ):
        assert legacy_retry not in runtime
    assert "mermaid.run({" not in runtime
    assert "document.querySelectorAll('.mermaid')" not in runtime
    assert "await awaitStableSvg(scratch, deadline)" in runtime
    assert "revealMermaid.init" not in runtime
    assert "console.error =" not in runtime


def test_mermaid_staging_area_is_measurable_while_offscreen() -> None:
    """Mermaid may not measure charts inside hidden Reveal sections."""
    styles = (DECK / "presentation.css").read_text(encoding="utf-8")

    assert ".gludd-mermaid-stage" in styles
    assert "opacity: 0" in styles
    assert "visibility: hidden" not in styles
    assert "html.gludd-mermaid-prerender" not in styles


def test_live_mermaid_visual_avoids_inline_svg_percentage_sizing() -> None:
    """Safari receives a decoded replaced image, not fragile inline SVG layout."""
    runtime = (DECK / "presentation.js").read_text(encoding="utf-8")
    styles = (DECK / "presentation.css").read_text(encoding="utf-8")

    for token in (
        "createSvgImage",
        "awaitImageReady",
        "mermaid-image",
        "naturalWidth",
        "encodeURIComponent",
    ):
        assert token in runtime
    assert '.reveal .mermaid[data-mermaid-viewport="stable"] > svg' not in styles
    assert ".mermaid-image" in styles
    assert "height: auto" in styles


def test_mermaid_images_are_padded_centered_and_height_bounded() -> None:
    """Safari receives explicit text alignment and a bounded logical viewport."""
    runtime = (DECK / "presentation.js").read_text(encoding="utf-8")
    styles = (DECK / "presentation.css").read_text(encoding="utf-8")

    for token in (
        "SVG_VIEWPORT_PADDING",
        "normalizeSvgTextAlignment",
        'text-anchor", "middle"',
        "stabilizedSvg.outerHTML",
        "gluddViewportPadding",
    ):
        assert token in runtime
    assert "max-height: var(--gludd-diagram-max-height)" in styles
    assert "--gludd-diagram-max-height: 500px" in styles
    assert "width: auto" in styles


def test_slide_transition_does_not_fly_neighboring_content_through_the_canvas() -> None:
    """Navigation must not present adjacent slide text as off-screen content."""
    runtime = (DECK / "presentation.js").read_text(encoding="utf-8")

    assert 'transition: "fade"' in runtime
    assert 'transition: "slide"' not in runtime


def test_guardrail_overview_labels_do_not_depend_on_html_break_layout() -> None:
    """Critical chart labels stay readable when WebKit flattens Mermaid breaks."""
    html = (DECK / "index.html").read_text(encoding="utf-8")

    for label in (
        'C["Layer 1 — Config: permission + Make-only gate"]',
        'R["Layer 2 — Runtime: blocking hooks"]',
        'P["Layer 3 — Prompt: TDD + evidence policy"]',
    ):
        assert label in html
    for fragile_label in (
        'C["Layer 1 — Config<br/>permission + Make-only gate"]',
        'R["Layer 2 — Runtime<br/>13 blocking hooks"]',
        'P["Layer 3 — Prompt<br/>TDD + evidence policy"]',
    ):
        assert fragile_label not in html


def test_each_mermaid_diagram_gets_an_independent_render_deadline() -> None:
    """One slow Safari chart cannot spend the deadline for every later chart."""
    runtime = (DECK / "presentation.js").read_text(encoding="utf-8")

    assert "DIAGRAM_DEADLINE_MS" in runtime
    assert "BATCH_DEADLINE_MS" not in runtime
    loop = runtime.split("for (const [index, diagram] of candidates.entries())", 1)[1]
    assert "window.performance.now() + DIAGRAM_DEADLINE_MS" in loop


def test_boot_diagnostic_is_visible_until_runtime_proves_health() -> None:
    """A parser, file-origin, or CSP failure must be visible without JavaScript."""
    html = (DECK / "index.html").read_text(encoding="utf-8")

    assert (
        '<p id="presentation-render-status" role="status" aria-live="polite">'
        "Presentation scripts did not finish; diagram source remains available."
        "</p>"
    ) in html


def test_presentation_javascript_is_valid() -> None:
    """The vendored artifact must not ship a controller syntax error."""
    completed = subprocess.run(
        ["node", "--check", str(DECK / "presentation.js")],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_source_link_conversion_emits_immutable_valid_ranges(tmp_path: Path) -> None:
    """One-line, ranged, file, and directory citations get canonical links."""
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "demo.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    html = (
        '<p class="cite">Source: src/pkg/demo.py:2-3</p>'
        '<span class="path">src/pkg/demo.py</span>'
        '<code class="cite">src/pkg/</code>'
    )
    sha = "a" * 40

    linked, citations = build_deck.link_source_citations(html, sha, root=root)

    assert (
        f'https://github.com/sandboxcom/gludd/blob/{sha}/src/pkg/demo.py#L2-L3'
        in linked
    )
    assert 'data-source-path="src/pkg/demo.py"' in linked
    assert 'data-source-lines="2-3"' in linked
    assert f'https://github.com/sandboxcom/gludd/tree/{sha}/src/pkg' in linked
    assert citations == {"src/pkg/demo.py"}


def test_tracked_deck_builds_immutable_source_links() -> None:
    """Authored repository citations become exact commit links before upload."""
    sha = "b" * 40
    authored = (DECK / "index.html").read_text(encoding="utf-8")

    linked, citations = build_deck.link_source_citations(authored, sha)

    assert len(citations) >= 10
    assert f"https://github.com/sandboxcom/gludd/blob/{sha}/" in linked
    assert 'class="source-link"' in linked
    assert 'data-source-path="src/general_ludd/daemon.py"' in linked
    assert f"blob/{sha}/src/general_ludd/daemon.py" in linked
    rewritten, second_pass = build_deck.link_source_citations(linked, sha)
    assert rewritten == linked
    assert second_pass == set()


@pytest.mark.parametrize(
    "value",
    (
        "../secret",
        "%2e%2e/secret",
        "%252e%252e/secret",
        "/etc/passwd",
        "src/pkg",
        "src/pkg/missing.py",
        "src/pkg/unlisted.py",
        "src/pkg/binary.dat",
        "src/pkg/large.py",
    ),
)
def test_source_endpoint_rejects_non_allowlisted_or_unsafe_inputs(
    tmp_path: Path,
    value: str,
) -> None:
    """The loopback-only viewer cannot become an arbitrary repository reader."""
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "allowed.py").write_text("ok\n", encoding="utf-8")
    (root / "src" / "pkg" / "unlisted.py").write_text("no\n", encoding="utf-8")
    (root / "src" / "pkg" / "binary.dat").write_bytes(b"\x00not-text")
    (root / "src" / "pkg" / "large.py").write_text("x" * 33, encoding="utf-8")

    with pytest.raises(build_deck.SourceRequestError):
        build_deck.resolve_source_request(
            value,
            repo_root=root,
            allowlist={"src/pkg/allowed.py", "src/pkg/binary.dat", "src/pkg/large.py"},
            max_bytes=32,
        )


def test_source_endpoint_accepts_allowlisted_utf8_file(tmp_path: Path) -> None:
    """The viewer serves only the exact authored UTF-8 citation target."""
    root = tmp_path / "repo"
    path = root / "src" / "pkg" / "allowed.py"
    path.parent.mkdir(parents=True)
    path.write_text("snowman = '☃'\n", encoding="utf-8")

    resolved = build_deck.resolve_source_request(
        "src/pkg/allowed.py",
        repo_root=root,
        allowlist={"src/pkg/allowed.py"},
        max_bytes=1024,
    )

    assert resolved == path


def test_source_endpoint_rejects_symlink_escape(tmp_path: Path) -> None:
    """An allowlist entry does not authorize its symlink target outside root."""
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret\n", encoding="utf-8")
    link = root / "linked.py"
    link.symlink_to(outside)

    with pytest.raises(build_deck.SourceRequestError):
        build_deck.resolve_source_request(
            "linked.py",
            repo_root=root,
            allowlist={"linked.py"},
            max_bytes=1024,
        )


def test_full_sha_is_available_to_the_deck_template() -> None:
    """Public citation URLs may not use the seven-character display SHA."""
    html, missing = build_deck.apply_tokens(
        "{{GIT_SHA}} {{GIT_SHA_FULL}}",
        {
            "git_sha": "1234567",
            "git_sha_full": "1234567890abcdef1234567890abcdef12345678",
        },
    )

    assert missing == ["{{VERSION}}", "{{TEST_COUNT}}", "{{ROLE_COUNT}}", "{{GENERATED_AT}}"]
    assert html == "1234567 1234567890abcdef1234567890abcdef12345678"
