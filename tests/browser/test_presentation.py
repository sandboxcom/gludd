"""Chromium and WebKit acceptance for the self-contained Reveal.js artifact."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import pytest

if TYPE_CHECKING:
    import build_deck
    from playwright.sync_api import Locator, Page, Route
else:
    from scripts import build_deck

pytest.importorskip("pytest_playwright", reason="presentation browser extra is not installed")
playwright_sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="presentation browser extra is not installed",
)
PlaywrightTimeoutError = playwright_sync_api.TimeoutError

pytestmark = pytest.mark.presentation_browser

FEATURE_SYNC_CONTRACTS = (
    "s83-157-158-166-177-admission-chain",
    "s83-101-104-concurrency-authority",
    "s83-103-canonical-worktree-paths",
    "s83-108-105-boundary-evidence",
    "s83-109-local-game-boundary",
    "s83-118-122-branch-reconciliation",
    "s83-169-capture-reuse-observability",
    "s83-179-180-shadow-reconciliation",
    "s91-3-enforcement-executable-modes",
    "decision-log-codification-v1",
)


def test_required_browser_matrix(browser_name: str) -> None:
    """The Pages acceptance is intentionally limited to both supported engines."""
    assert browser_name in {"chromium", "webkit"}


def test_direct_mermaid_render_is_geometry_clean(
    page: Any,
    presentation_url: str,
    browser_events: dict[str, list[str]],
) -> None:
    """The direct supported API must finish before a completed SVG is inserted."""
    page.route(
        "**/presentation.js*",
        lambda route: route.fulfill(status=200, content_type="application/javascript", body=""),
    )
    response = page.goto(presentation_url, wait_until="networkidle")
    assert response is not None and response.ok
    page.wait_for_function("typeof window.gluddMermaid?.render === 'function'")
    result = page.evaluate(
        """
        async () => {
          const stage = document.createElement('div');
          stage.className = 'gludd-mermaid-stage';
          document.body.append(stage);
          window.gluddMermaid.initialize({
            startOnLoad: false,
            htmlLabels: false,
            securityLevel: 'strict',
            flowchart: {htmlLabels: false},
          });
          const rendered = await window.gluddMermaid.render(
            'gludd-direct-api-probe',
            'flowchart TD\\n  start --> finish',
            stage,
          );
          stage.innerHTML = rendered.svg;
          const svg = stage.querySelector('svg');
          const rect = svg.getBoundingClientRect();
          const invalid = Array.from(svg.querySelectorAll('*')).flatMap((node) =>
            Array.from(node.attributes).filter((attribute) =>
              /(?:undefined|NaN|Infinity)/.test(attribute.value))
          );
          return {height: rect.height, invalid: invalid.length, width: rect.width};
        }
        """
    )
    assert result["invalid"] == 0
    assert result["width"] > 0 and result["height"] > 0
    assert browser_events == {
        "console_errors": [],
        "page_errors": [],
        "request_failures": [],
        "http_failures": [],
    }


def test_live_diagram_stays_in_reveal_as_a_decoded_svg_image(
    page: Any,
    presentation_url: str,
    browser_events: dict[str, list[str]],
) -> None:
    """Safari must not inherit an offscreen inline-SVG paint tree."""
    _load(page, presentation_url)

    result = page.evaluate(
        """
        async () => {
          const slide = Reveal.getCurrentSlide();
          const wrap = document.createElement('div');
          wrap.className = 'diagram-wrap';
          const diagram = document.createElement('div');
          diagram.className = 'mermaid';
          diagram.textContent = 'flowchart LR\\n  safari --> visible';
          wrap.append(diagram);
          slide.append(wrap);

          let leftRevealSlide = false;
          const observer = new MutationObserver(() => {
            if (!slide.contains(diagram)) leftRevealSlide = true;
          });
          observer.observe(document.body, {childList: true, subtree: true});
          await window.gluddPresentationRefresh();
          await new Promise((resolve) => requestAnimationFrame(
            () => requestAnimationFrame(resolve),
          ));
          observer.disconnect();

          const image = diagram.querySelector('img.mermaid-image');
          const rect = image?.getBoundingClientRect() || {width: 0, height: 0};
          return {
            complete: image?.complete || false,
            height: rect.height,
            heightAttribute: image?.getAttribute('height') || '',
            inlineSvgCount: diagram.querySelectorAll('svg').length,
            leftRevealSlide,
            naturalHeight: image?.naturalHeight || 0,
            naturalWidth: image?.naturalWidth || 0,
            sourceIsSvg: image?.src.startsWith('data:image/svg+xml;charset=utf-8,') || false,
            state: diagram.dataset.mermaidState,
            viewport: diagram.dataset.mermaidViewport || '',
            width: rect.width,
            widthAttribute: image?.getAttribute('width') || '',
          };
        }
        """
    )

    assert result == {
        "complete": True,
        "height": result["height"],
        "heightAttribute": result["heightAttribute"],
        "inlineSvgCount": 0,
        "leftRevealSlide": False,
        "naturalHeight": result["naturalHeight"],
        "naturalWidth": result["naturalWidth"],
        "sourceIsSvg": True,
        "state": "rendered",
        "viewport": "stable",
        "width": result["width"],
        "widthAttribute": result["widthAttribute"],
    }
    assert float(result["widthAttribute"]) > 0
    assert float(result["heightAttribute"]) > 0
    assert result["naturalWidth"] > 0 and result["naturalHeight"] > 0
    assert result["width"] > 0 and result["height"] > 0
    assert browser_events == {
        "console_errors": [],
        "page_errors": [],
        "request_failures": [],
        "http_failures": [],
    }


def test_script_blocking_csp_keeps_a_static_diagnostic_visible(
    page: Any,
    presentation_url: str,
) -> None:
    """A CSP or unsupported Safari parser may not leave a silent blank deck."""

    def enforce_script_blocking_csp(route: Any) -> None:
        response = route.fetch()
        headers = dict(response.headers)
        headers["content-security-policy"] = (
            "default-src 'self'; script-src 'none'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; object-src 'none'"
        )
        route.fulfill(response=response, headers=headers)

    page.route("**/gludd/", enforce_script_blocking_csp)
    response = page.goto(presentation_url, wait_until="domcontentloaded")

    assert response is not None and response.ok
    diagnostic = page.locator("#presentation-render-status")
    assert diagnostic.is_visible()
    assert "presentation scripts did not finish" in diagnostic.inner_text().lower()
    assert page.locator(".mermaid").count() > 0


def test_file_url_keeps_full_rendering_and_failure_visibility(
    page: Any,
    tmp_path: Path,
    browser_events: dict[str, list[str]],
) -> None:
    """The self-contained artifact must not depend on an HTTP origin."""
    deck = tmp_path / "file-presentation" / "deck"
    build_deck.build_preview_copy(
        deck,
        data={
            "version": "0.1.2-file-test",
            "git_sha": "c" * 7,
            "git_sha_full": "c" * 40,
            "test_count": 1,
            "role_count": 1,
            "features": [],
            "generated_at": "2026-10-06T00:00:00Z",
        },
    )

    response = page.goto((deck / "index.html").as_uri(), wait_until="networkidle")
    assert response is not None and response.ok
    page.wait_for_function("window.gluddPresentationReady === true", timeout=5_000)
    page.wait_for_function(
        """
        () => {
          const health = window.gluddPresentationHealth();
          return health.rendered > 0 && health.pending === 0 && health.rendering === 0 &&
            health.failed === 0 && health.unrendered === 0;
        }
        """,
        timeout=5_000,
    )
    first = page.evaluate(
        """
        () => {
          const slide = Reveal.getSlides().find((candidate) => candidate.querySelector('.mermaid'));
          const indices = Reveal.getIndices(slide);
          return [indices.h, indices.v];
        }
        """
    )
    _visit_slide(page, first[0], first[1])
    _assert_visible_diagrams(page)
    assert page.locator("#presentation-render-status").is_hidden()
    assert browser_events == {
        "console_errors": [],
        "page_errors": [],
        "request_failures": [],
        "http_failures": [],
    }


@pytest.fixture(scope="module")
def presentation_url(tmp_path_factory: pytest.TempPathFactory) -> Generator[str, None, None]:
    """Serve the resolved upload tree, or build an equivalent local preview."""
    serve_dir = build_deck.DECK_DIR
    allowlist_path = serve_dir / build_deck.SOURCE_ALLOWLIST
    resolved = allowlist_path.exists() and "{{GIT_SHA_FULL}}" not in (
        serve_dir / "index.html"
    ).read_text(encoding="utf-8")
    if not resolved:
        serve_dir = tmp_path_factory.mktemp("presentation-pages") / "deck"
        data = {
            "version": "0.1.2-test",
            "git_sha": "a" * 7,
            "git_sha_full": "a" * 40,
            "test_count": 1,
            "role_count": 1,
            "features": [],
            "generated_at": "2026-10-06T00:00:00Z",
        }
        build_deck.build_preview_copy(serve_dir, data=data)
        allowlist_path = serve_dir / build_deck.SOURCE_ALLOWLIST
    payload = json.loads(allowlist_path.read_text(encoding="utf-8"))
    handler = build_deck.source_request_handler(
        serve_dir=serve_dir,
        repo_root=build_deck.ROOT,
        allowlist=frozenset(payload["paths"]),
        url_prefix="/gludd/",
    )
    server = build_deck.DeckThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, name="gludd-presentation-browser", daemon=True)
    thread.start()
    host, port = cast(tuple[str, int], server.server_address)
    try:
        yield f"http://{host}:{port}/gludd/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _load(page: Any, url: str) -> None:
    """Load the deck and wait for Reveal plus the Gludd lifecycle controller."""
    response = page.goto(url, wait_until="networkidle")
    status = None if response is None else response.status
    assert response is not None and response.ok, f"top-level presentation response status={status}"
    page.wait_for_function("window.gluddPresentationReady === true")
    page.wait_for_function("typeof window.gluddPresentationHealth === 'function'")


def _visit_slide(page: Any, horizontal: int, vertical: int) -> None:
    """Navigate to one slide and await the Reveal transition plus rendering."""
    page.evaluate(
        """
        async ([horizontal, vertical]) => {
          const settled = new Promise((resolve) => {
            let finished = false;
            const finish = () => {
              if (finished) return;
              finished = true;
              Reveal.off('slidetransitionend', finish);
              resolve();
            };
            Reveal.on('slidetransitionend', finish);
            setTimeout(finish, 1200);
          });
          Reveal.slide(horizontal, vertical);
          await settled;
          await new Promise((resolve) => requestAnimationFrame(
            () => requestAnimationFrame(resolve),
          ));
          await window.gluddPresentationRenderVisible();
        }
        """,
        [horizontal, vertical],
    )
    page.wait_for_function(
        """
        () => Array.from(document.querySelectorAll('section.present .mermaid'))
          .every((node) => ['rendered', 'failed'].includes(node.dataset.mermaidState))
        """
    )


def _assert_visible_diagrams(page: Any) -> None:
    """Require a decoded positive vector image, not merely an inserted node."""
    results = page.eval_on_selector_all(
        "section.present .mermaid",
        """
        (nodes) => nodes.map((node) => {
          const images = node.querySelectorAll('img.mermaid-image');
          const image = images[0];
          const rect = image ? image.getBoundingClientRect() : {width: 0, height: 0};
          return {
            complete: image?.complete || false,
            imageCount: images.length,
            inlineSvgCount: node.querySelectorAll('svg').length,
            naturalHeight: image?.naturalHeight || 0,
            naturalWidth: image?.naturalWidth || 0,
            source: image?.src || '',
            state: node.dataset.mermaidState,
            width: rect.width,
            height: rect.height,
          };
        })
        """,
    )
    for result in results:
        assert result["state"] == "rendered", result
        assert result["complete"] is True
        assert result["imageCount"] == 1
        assert result["inlineSvgCount"] == 0
        assert result["naturalWidth"] > 0
        assert result["naturalHeight"] > 0
        assert result["source"].startswith("data:image/svg+xml;charset=utf-8,")
        assert result["width"] > 0
        assert result["height"] > 0


def test_cold_and_cached_load_prepare_every_chart_within_budget(
    page: Any,
    presentation_url: str,
    browser_events: dict[str, list[str]],
) -> None:
    """No chart may depend on visiting its slide, in either cache state."""
    for cache_state in ("cold", "cached"):
        started = time.monotonic()
        _load(page, presentation_url)
        try:
            page.wait_for_function(
                """
                () => {
                  const health = window.gluddPresentationHealth();
                  return health.rendered > 0 && health.pending === 0 &&
                    health.rendering === 0 && health.failed === 0 && health.unrendered === 0;
                }
                """,
                timeout=5_000,
            )
        except PlaywrightTimeoutError:
            diagnostics = page.eval_on_selector_all(
                ".mermaid",
                """
                (nodes) => ({
                  health: window.gluddPresentationHealth(),
                  diagrams: nodes.map((node, index) => ({
                    index,
                    error: node.dataset.mermaidError || '',
                    geometry: node.dataset.mermaidGeometry || '',
                    state: node.dataset.mermaidState || '',
                    text: node.textContent.slice(0, 80),
                  })),
                })
                """,
            )
            print(f"presentation-timeout diagnostics={json.dumps(diagnostics, sort_keys=True)}", flush=True)
            raise
        elapsed = time.monotonic() - started
        print(
            f"presentation-readiness cache={cache_state} seconds={elapsed:.3f}",
            flush=True,
        )
        assert elapsed < 5
        metadata = page.eval_on_selector_all(
            ".mermaid",
            """
            (nodes) => nodes.map((node) => ({
              complete: node.querySelector('img.mermaid-image')?.complete || false,
              imageCount: node.querySelectorAll('img.mermaid-image').length,
              inlineSvgCount: node.querySelectorAll('svg').length,
              naturalHeight: node.querySelector('img.mermaid-image')?.naturalHeight || 0,
              naturalWidth: node.querySelector('img.mermaid-image')?.naturalWidth || 0,
              source: node.querySelector('img.mermaid-image')?.src || '',
              state: node.dataset.mermaidState,
            }))
            """,
        )
        assert metadata
        assert all(
            item["state"] == "rendered"
            and item["complete"]
            and item["imageCount"] == 1
            and item["inlineSvgCount"] == 0
            and item["naturalWidth"] > 0
            and item["naturalHeight"] > 0
            and item["source"].startswith("data:image/svg+xml;charset=utf-8,")
            for item in metadata
        )
        invalid_attributes = page.eval_on_selector_all(
            ".mermaid svg *",
            """
            (nodes) => nodes.flatMap((node) => Array.from(node.attributes)
              .filter((attribute) => /(?:undefined|NaN)/.test(attribute.value))
              .map((attribute) => ({
                attribute: attribute.name,
                className: node.getAttribute('class') || '',
                tagName: node.tagName,
                value: attribute.value,
              })))
            """,
        )
        assert invalid_attributes == []
    if browser_events["console_errors"]:
        geometry_writes = page.evaluate(
            """
            () => ({
              directApi: typeof window.mermaid,
              writes: window.gluddInvalidGeometryWrites || [],
            })
            """
        )
        print(f"presentation-console diagnostics={json.dumps(geometry_writes, sort_keys=True)}", flush=True)
    assert browser_events == {
        "console_errors": [],
        "page_errors": [],
        "request_failures": [],
        "http_failures": [],
    }


def test_inline_svg_layout_collapse_cannot_hide_rendered_charts(
    page: Any,
    presentation_url: str,
    browser_events: dict[str, list[str]],
) -> None:
    """Simulate Safari's inline-SVG sizing failure at the live paint boundary."""
    _load(page, presentation_url)
    page.add_style_tag(
        content=".reveal .mermaid > svg { width: 0 !important; height: 0 !important; }"
    )
    horizontal, vertical = page.evaluate(
        """
        () => {
          const slide = Reveal.getSlides().find((candidate) => candidate.querySelector('.mermaid'));
          const indices = Reveal.getIndices(slide);
          return [indices.h, indices.v];
        }
        """
    )
    _visit_slide(page, horizontal, vertical)
    _assert_visible_diagrams(page)
    assert browser_events == {
        "console_errors": [],
        "page_errors": [],
        "request_failures": [],
        "http_failures": [],
    }


def test_every_mermaid_label_fits_inside_the_encoded_svg_viewport(
    page: Any,
    presentation_url: str,
) -> None:
    """Decoded SVGs must retain every label inside their own painted viewport."""
    _load(page, presentation_url)
    failures = page.evaluate(
        """
        async () => {
          await document.fonts?.ready;
          const stage = document.createElement('div');
          stage.className = 'gludd-mermaid-stage';
          document.body.append(stage);
          const failures = [];
          for (const [diagramIndex, image] of Array.from(
            document.querySelectorAll('img.mermaid-image')
          ).entries()) {
            const encoded = image.src.split(',', 2)[1] || '';
            const markup = decodeURIComponent(encoded);
            const parsed = new DOMParser().parseFromString(markup, 'image/svg+xml');
            const svg = document.importNode(parsed.documentElement, true);
            stage.append(svg);
            await new Promise((resolve) => requestAnimationFrame(
              () => requestAnimationFrame(resolve),
            ));
            const viewport = svg.getBoundingClientRect();
            for (const label of svg.querySelectorAll('text')) {
              const box = label.getBoundingClientRect();
              const tolerance = 0.5;
              const sides = [];
              if (box.left < viewport.left - tolerance) sides.push('left');
              if (box.top < viewport.top - tolerance) sides.push('top');
              if (box.right > viewport.right + tolerance) sides.push('right');
              if (box.bottom > viewport.bottom + tolerance) sides.push('bottom');
              if (sides.length) {
                failures.push({
                  diagramIndex,
                  kind: 'viewport',
                  markup: label.outerHTML.slice(0, 500),
                  sides,
                  text: (label.textContent || '').trim().slice(0, 100),
                });
              }
              const lines = Array.from(label.querySelectorAll(':scope > tspan.text-outer-tspan.row'));
              if (lines.length > 1) {
                const centers = lines.map((line) => {
                  const rect = line.getBoundingClientRect();
                  return rect.left + rect.width / 2;
                });
                const spread = Math.max(...centers) - Math.min(...centers);
                if (spread > 1) {
                  failures.push({
                    diagramIndex,
                    kind: 'multiline-alignment',
                    markup: label.outerHTML.slice(0, 500),
                    spread,
                    text: (label.textContent || '').trim().slice(0, 100),
                  });
                }
              }
            }
            svg.remove();
          }
          stage.remove();
          return failures;
        }
        """
    )
    if failures:
        print(f"presentation-mermaid-label diagnostics={json.dumps(failures[:5], sort_keys=True)}")
    assert failures == []


def test_every_slide_keeps_visible_content_inside_the_reveal_canvas(
    page: Any,
    presentation_url: str,
) -> None:
    """Text and charts may scroll internally but may not paint off-slide."""
    _load(page, presentation_url)
    indices = page.evaluate(
        "Reveal.getSlides().map((slide) => { const i = Reveal.getIndices(slide); return [i.h, i.v]; })"
    )
    failures: list[dict[str, Any]] = []
    output = Path(
        os.environ.get("GLUDD_PRESENTATION_BROWSER_OUTPUT", "/tmp/gludd-presentation-browser")
    ) / "layout-overflow"
    capture_layout = os.environ.get("GLUDD_PRESENTATION_CAPTURE_LAYOUT") == "1"
    for horizontal, vertical in indices:
        _visit_slide(page, horizontal, vertical)
        overflow = page.evaluate(
            """
            () => {
              const slide = Reveal.getCurrentSlide();
              slide.querySelectorAll('.fragment').forEach((fragment) => {
                fragment.classList.add('visible');
              });
              Reveal.layout();
              const boundary = Reveal.getSlidesElement().getBoundingClientRect();
              const tolerance = 2;
              const selector = [
                'h1', 'h2', 'h3', 'p', 'li', 'pre', 'table', '.two-col', '.metrics-grid',
                '.diagram-wrap', '.mermaid-image'
              ].join(',');
              const nodes = [slide, ...slide.querySelectorAll(selector)];
              const boundsFailures = nodes.flatMap((node) => {
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                const hidden = style.display === 'none' || style.visibility === 'hidden';
                if (hidden || rect.width === 0 || rect.height === 0) {
                  return [];
                }
                const sides = [];
                if (rect.left < boundary.left - tolerance) sides.push('left');
                if (rect.right > boundary.right + tolerance) sides.push('right');
                if (rect.top < boundary.top - tolerance) sides.push('top');
                if (rect.bottom > boundary.bottom + tolerance) sides.push('bottom');
                return sides.length ? [{
                  className: node.className || '',
                  sides,
                  tagName: node.tagName,
                  text: (node.textContent || '').trim().slice(0, 100),
                }] : [];
              });
              const legibilityFailures = Array.from(
                slide.querySelectorAll('img.mermaid-image')
              ).flatMap((image) => {
                const imageRect = image.getBoundingClientRect();
                const encoded = image.src.split(',', 2)[1] || '';
                const parsed = new DOMParser().parseFromString(
                  decodeURIComponent(encoded),
                  'image/svg+xml',
                );
                const viewBox = parsed.documentElement.getAttribute('viewBox')
                  ?.trim().split(/[ ,]+/).map(Number) || [];
                if (viewBox.length !== 4 || viewBox[2] <= 0 || viewBox[3] <= 0) {
                  return [{className: image.className, kind: 'invalid-viewbox'}];
                }
                const effectiveFontPixels = 16 * Math.min(
                  imageRect.width / viewBox[2],
                  imageRect.height / viewBox[3],
                );
                return effectiveFontPixels < 8 ? [{
                  className: image.className,
                  effectiveFontPixels,
                  kind: 'illegible-chart-text',
                }] : [];
              });
              return [...boundsFailures, ...legibilityFailures];
            }
            """
        )
        if capture_layout and page.locator("section.present .mermaid-image").count():
            capture = output.parent / "layout-captures" / f"slide-{horizontal}-{vertical}.png"
            capture.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(capture), full_page=True)
        if overflow:
            output.mkdir(parents=True, exist_ok=True)
            image = output / f"slide-{horizontal}-{vertical}.png"
            page.screenshot(path=str(image), full_page=True)
            failures.append(
                {
                    "horizontal": horizontal,
                    "vertical": vertical,
                    "overflow": overflow,
                    "screenshot": str(image),
                }
            )
    if failures:
        print(f"presentation-layout-overflow diagnostics={json.dumps(failures, sort_keys=True)}")
    assert failures == []


def test_narrow_tall_canvas_keeps_prose_and_diagram_labels_readable(
    page: Any,
    presentation_url: str,
) -> None:
    """Portrait layouts must contain prose without shrinking chart labels away."""
    page.set_viewport_size({"width": 430, "height": 932})
    _load(page, presentation_url)
    failures = page.evaluate(
        """
        () => {
          Reveal.getSlides().forEach((slide) => {
            slide.querySelectorAll('.fragment').forEach((fragment) => {
              fragment.classList.add('visible');
            });
          });
          Reveal.layout();
          const boundary = document.querySelector('.reveal').getBoundingClientRect();
          const tolerance = 2;
          return Reveal.getSlides().flatMap((slide) => {
              const slideFailures = [];
              slide.querySelectorAll('.fragment').forEach((fragment) => {
                fragment.classList.add('visible');
              });
              const proseFailures = Array.from(
                slide.querySelectorAll('h1, h2, h3, p, li, pre, td, th')
              ).flatMap((node) => {
                const style = getComputedStyle(node);
                const range = document.createRange();
                range.selectNodeContents(node);
                const lines = Array.from(range.getClientRects());
                const clipped = lines.some((line) => (
                  line.left < boundary.left - tolerance ||
                  line.right > boundary.right + tolerance
                ));
                return clipped && style.display !== 'none' && style.visibility !== 'hidden'
                  ? [{kind: 'prose-horizontal-clip', tagName: node.tagName,
                      text: (node.textContent || '').trim().slice(0, 100)}]
                  : [];
              });
              const slideStyle = getComputedStyle(slide);
              if (
                slide.scrollHeight > slide.clientHeight + tolerance &&
                !['auto', 'scroll'].includes(slideStyle.overflowY)
              ) {
                proseFailures.push({kind: 'prose-vertical-inaccessible'});
              }
              const diagramFailures = Array.from(
                slide.querySelectorAll('img.mermaid-image')
              ).flatMap((image) => {
                const imageRect = image.getBoundingClientRect();
                const scroller = image.closest('.mermaid');
                const scrollerRect = scroller.getBoundingClientRect();
                const encoded = image.src.split(',', 2)[1] || '';
                const parsed = new DOMParser().parseFromString(
                  decodeURIComponent(encoded),
                  'image/svg+xml',
                );
                const viewBox = parsed.documentElement.getAttribute('viewBox')
                  ?.trim().split(/[ ,]+/).map(Number) || [];
                if (viewBox.length !== 4 || viewBox[2] <= 0 || viewBox[3] <= 0) {
                  return [{kind: 'invalid-viewbox'}];
                }
                const effectiveFontPixels = 16 * Math.min(
                  imageRect.width / viewBox[2],
                  imageRect.height / viewBox[3],
                );
                const horizontallyScrollable =
                  scroller.scrollWidth > scroller.clientWidth + tolerance &&
                  ['auto', 'scroll'].includes(getComputedStyle(scroller).overflowX);
                const centered = Math.abs(
                  (imageRect.left + imageRect.right) / 2 -
                  (scrollerRect.left + scrollerRect.right) / 2
                ) <= tolerance;
                const issues = [];
                if (
                  scrollerRect.left < boundary.left - tolerance ||
                  scrollerRect.right > boundary.right + tolerance
                ) {
                  issues.push('off-canvas');
                }
                if (!centered && !horizontallyScrollable) issues.push('unaligned');
                if (effectiveFontPixels < 8) issues.push('illegible-chart-text');
                return issues.length ? [{effectiveFontPixels, issues}] : [];
              });
              slideFailures.push(...proseFailures, ...diagramFailures);
              if (!slideFailures.length) return [];
              const indices = Reveal.getIndices(slide);
              return [{
                horizontal: indices.h,
                vertical: indices.v,
                failures: slideFailures,
              }];
          });
        }
        """
    )
    if failures:
        print(f"presentation-portrait-layout diagnostics={json.dumps(failures, sort_keys=True)}")
    assert failures == []


def test_short_landscape_canvas_keeps_rendered_text_readable_and_reachable(
    page: Any,
    presentation_url: str,
) -> None:
    """A Safari-like landscape canvas must not scale or center text out of reach."""
    page.set_viewport_size({"width": 932, "height": 430})
    _load(page, presentation_url)
    indices = page.evaluate(
        "Reveal.getSlides().map((slide) => { const i = Reveal.getIndices(slide); return [i.h, i.v]; })"
    )
    failures: list[dict[str, Any]] = []
    output = Path(
        os.environ.get("GLUDD_PRESENTATION_BROWSER_OUTPUT", "/tmp/gludd-presentation-browser")
    ) / "short-landscape-layout"
    for horizontal, vertical in indices:
        _visit_slide(page, horizontal, vertical)
        issues = page.evaluate(
            """
            () => {
              const slide = Reveal.getCurrentSlide();
              slide.querySelectorAll('.fragment').forEach((fragment) => {
                fragment.classList.add('visible');
              });
              Reveal.layout();
              slide.scrollTop = 0;
              const boundary = document.querySelector('.reveal').getBoundingClientRect();
              const tolerance = 2;
              const selector = [
                'h1', 'h2', 'h3', 'p', 'li', 'pre', 'td', 'th',
                '.stage-kicker', '.metric-box .number', '.metric-box .label',
                '.metric-box .cite',
              ].join(',');
              const visibleText = Array.from(slide.querySelectorAll(selector)).filter((node) => {
                const style = getComputedStyle(node);
                return style.display !== 'none' && style.visibility !== 'hidden' &&
                  (node.textContent || '').trim();
              });
              const textIssues = visibleText.flatMap((node) => {
                const range = document.createRange();
                range.selectNodeContents(node);
                const lines = Array.from(range.getClientRects());
                const effectiveFontPixels = lines.length
                  ? Math.min(...lines.map((line) => line.height))
                  : 0;
                const sides = [];
                if (lines.some((line) => line.left < boundary.left - tolerance)) sides.push('left');
                if (lines.some((line) => line.right > boundary.right + tolerance)) sides.push('right');
                if (effectiveFontPixels < 8) sides.push('illegible');
                return sides.length ? [{
                  effectiveFontPixels,
                  kind: 'prose',
                  sides,
                  tagName: node.tagName,
                  text: (node.textContent || '').trim().slice(0, 100),
                }] : [];
              });
              const diagramIssues = Array.from(
                slide.querySelectorAll('img.mermaid-image')
              ).flatMap((image) => {
                const imageRect = image.getBoundingClientRect();
                const scroller = image.closest('.mermaid');
                const scrollerRect = scroller.getBoundingClientRect();
                const encoded = image.src.split(',', 2)[1] || '';
                const parsed = new DOMParser().parseFromString(
                  decodeURIComponent(encoded),
                  'image/svg+xml',
                );
                const viewBox = parsed.documentElement.getAttribute('viewBox')
                  ?.trim().split(/[ ,]+/).map(Number) || [];
                if (viewBox.length !== 4 || viewBox[2] <= 0 || viewBox[3] <= 0) {
                  return [{kind: 'diagram-invalid-viewbox'}];
                }
                const effectiveFontPixels = 16 * Math.min(
                  imageRect.width / viewBox[2],
                  imageRect.height / viewBox[3],
                );
                const horizontallyScrollable =
                  scroller.scrollWidth > scroller.clientWidth + tolerance &&
                  ['auto', 'scroll'].includes(getComputedStyle(scroller).overflowX);
                const centered = Math.abs(
                  (imageRect.left + imageRect.right) / 2 -
                  (scrollerRect.left + scrollerRect.right) / 2
                ) <= tolerance;
                const reasons = [];
                if (
                  scrollerRect.left < boundary.left - tolerance ||
                  scrollerRect.right > boundary.right + tolerance
                ) reasons.push('off-canvas');
                if (!centered && !horizontallyScrollable) reasons.push('unaligned');
                if (effectiveFontPixels < 8) reasons.push('illegible');
                return reasons.length ? [{effectiveFontPixels, kind: 'diagram', reasons}] : [];
              });
              const firstText = visibleText[0];
              const firstRect = firstText?.getBoundingClientRect();
              const content = [
                ...visibleText,
                ...slide.querySelectorAll('img.mermaid-image'),
              ];
              const lastContent = content.reduce((lowest, node) => (
                !lowest || node.getBoundingClientRect().bottom > lowest.getBoundingClientRect().bottom
                  ? node
                  : lowest
              ), null);
              const initiallyReachable = !firstRect || (
                firstRect.bottom > boundary.top - tolerance &&
                firstRect.top < boundary.bottom + tolerance
              );
              const overflows = slide.scrollHeight > slide.clientHeight + tolerance;
              const overflowY = getComputedStyle(slide).overflowY;
              slide.scrollTop = slide.scrollHeight;
              const lastRect = lastContent?.getBoundingClientRect();
              const finallyReachable = !lastRect || (
                lastRect.bottom > boundary.top - tolerance &&
                lastRect.top < boundary.bottom + tolerance
              );
              const reachabilityIssues = [];
              if (!initiallyReachable) reachabilityIssues.push({kind: 'top-unreachable'});
              if (overflows && !['auto', 'scroll'].includes(overflowY)) {
                reachabilityIssues.push({kind: 'overflow-not-scrollable'});
              }
              if (overflows && slide.scrollTop <= 0) {
                reachabilityIssues.push({kind: 'scroll-position-unchanged'});
              }
              if (overflows && !finallyReachable) {
                reachabilityIssues.push({kind: 'bottom-unreachable'});
              }
              slide.scrollTop = 0;
              return [...textIssues, ...diagramIssues, ...reachabilityIssues];
            }
            """
        )
        if issues:
            output.mkdir(parents=True, exist_ok=True)
            image = output / f"slide-{horizontal}-{vertical}.png"
            page.screenshot(path=str(image), full_page=True)
            failures.append(
                {
                    "horizontal": horizontal,
                    "vertical": vertical,
                    "issues": issues,
                    "screenshot": str(image),
                }
            )
    if failures:
        print(f"presentation-landscape-layout diagnostics={json.dumps(failures, sort_keys=True)}")
    assert failures == []


def test_pages_subpath_navigation_renders_every_diagram(
    page: Any,
    presentation_url: str,
    browser_events: dict[str, list[str]],
) -> None:
    """Forward/back navigation must close both historical Mermaid races."""
    _load(page, presentation_url)
    inventory = page.locator(".mermaid").count()
    assert inventory > 0
    indices = page.evaluate(
        "Reveal.getSlides().map((slide) => { const i = Reveal.getIndices(slide); return [i.h, i.v]; })"
    )
    for horizontal, vertical in indices:
        _visit_slide(page, horizontal, vertical)
        _assert_visible_diagrams(page)
    for horizontal, vertical in reversed(indices):
        _visit_slide(page, horizontal, vertical)
        _assert_visible_diagrams(page)

    page.set_viewport_size({"width": 480, "height": 760})
    page.evaluate("window.dispatchEvent(new Event('resize'))")
    page.evaluate("window.gluddPresentationRenderVisible()")
    _assert_visible_diagrams(page)
    health = page.evaluate("window.gluddPresentationHealth()")
    assert health["rendered"] == inventory
    assert health["unrendered"] == 0
    assert health["failed"] == 0
    assert browser_events == {
        "console_errors": [],
        "page_errors": [],
        "request_failures": [],
        "http_failures": [],
    }


def test_direct_hash_navigation_and_reload_repaint_the_chart(
    page: Any,
    presentation_url: str,
    browser_events: dict[str, list[str]],
) -> None:
    """A copied SVG must paint on direct Reveal navigation and cached reload."""
    _load(page, presentation_url)
    horizontal, vertical = page.evaluate(
        """
        () => {
          const slide = Reveal.getSlides().find((candidate) => candidate.querySelector('.mermaid'));
          const indices = Reveal.getIndices(slide);
          return [indices.h, indices.v];
        }
        """
    )
    fragment = f"#/{horizontal}" + (f"/{vertical}" if vertical else "")

    page.goto("about:blank")
    _load(page, f"{presentation_url}{fragment}")
    _visit_slide(page, horizontal, vertical)
    _assert_visible_diagrams(page)
    response = page.reload(wait_until="networkidle")
    assert response is not None and response.ok
    page.wait_for_function("window.gluddPresentationReady === true")
    _visit_slide(page, horizontal, vertical)
    _assert_visible_diagrams(page)
    assert browser_events == {
        "console_errors": [],
        "page_errors": [],
        "request_failures": [],
        "http_failures": [],
    }


def test_local_source_link_opens_read_only_ace_at_range(page: Any, presentation_url: str) -> None:
    """A typed citation keeps its immutable href while selecting local lines."""
    _load(page, presentation_url)
    anchor = page.locator("a.source-link[data-source-lines]").first
    details = anchor.evaluate(
        "node => ({href: node.href, path: node.dataset.sourcePath, lines: node.dataset.sourceLines})"
    )
    assert re.match(
        r"^https://github\.com/sandboxcom/gludd/blob/[0-9a-f]{40}/",
        details["href"],
    )
    start_text, end_text = details["lines"].split("-")
    anchor.evaluate("node => node.click()")
    page.wait_for_function("document.getElementById('source-viewer-dialog').open")
    state = page.evaluate(
        """
        () => {
          const editor = ace.edit('source-viewer-editor');
          const range = editor.selection.getRange();
          return {
            readOnly: editor.getReadOnly(),
            path: document.getElementById('source-viewer-path').textContent,
            github: document.getElementById('source-viewer-github').href,
            start: range.start.row + 1,
            end: range.end.row + 1,
            firstVisible: editor.renderer.getFirstVisibleRow() + 1,
          };
        }
        """
    )
    assert state["readOnly"] is True
    assert state["path"] == details["path"]
    assert state["github"] == details["href"]
    assert state["start"] == int(start_text)
    assert state["end"] == int(end_text)
    assert state["firstVisible"] <= int(start_text)


def test_feature_sync_slides_are_source_linked_compact_and_mermaid_ready(
    page: Any,
    presentation_url: str,
    browser_events: dict[str, list[str]],
) -> None:
    """Synced feature slides must stay usable in desktop and short landscape views."""
    page.set_viewport_size({"width": 1024, "height": 768})
    _load(page, presentation_url)

    for contract in FEATURE_SYNC_CONTRACTS:
        slide = page.locator(f'section[data-contract="{contract}"]')
        assert slide.count() == 1
        anchors = slide.locator(
            "a.source-link[data-source-path][data-source-lines]"
        )
        assert anchors.count() > 0
        link_state = anchors.first.evaluate(
            """
            node => ({
              href: node.href,
              lines: node.dataset.sourceLines,
              path: node.dataset.sourcePath,
            })
            """
        )
        assert re.match(
            r"^https://github\.com/sandboxcom/gludd/blob/[0-9a-f]{40}/.+#L[0-9]+(?:-L[0-9]+)?$",
            link_state["href"],
        )
        assert re.match(r"^[1-9][0-9]*-[1-9][0-9]*$", link_state["lines"])
        assert link_state["path"]

    branch = page.locator(
        'section[data-contract="s83-118-122-branch-reconciliation"]'
    )
    branch_indices = branch.evaluate(
        "node => { const index = Reveal.getIndices(node); return [index.h, index.v]; }"
    )
    _visit_slide(page, branch_indices[0], branch_indices[1])
    _assert_visible_diagrams(page)
    readiness = branch.locator(".mermaid").evaluate(
        """
        node => ({
          complete: node.querySelector('img.mermaid-image')?.complete || false,
          state: node.dataset.mermaidState,
          viewport: node.dataset.mermaidViewport,
        })
        """
    )
    assert readiness == {"complete": True, "state": "rendered", "viewport": "stable"}

    for contract in FEATURE_SYNC_CONTRACTS:
        slide = page.locator(f'section[data-contract="{contract}"]')
        diagrams = slide.locator(".mermaid")
        if diagrams.count() == 0:
            continue
        indices = slide.evaluate(
            "node => { const index = Reveal.getIndices(node); return [index.h, index.v]; }"
        )
        _visit_slide(page, indices[0], indices[1])
        _assert_visible_diagrams(page)
        effective_font_pixels = diagrams.first.evaluate(
            """
            node => {
              const image = node.querySelector('img.mermaid-image');
              const rect = image.getBoundingClientRect();
              const encoded = image.src.split(',', 2)[1] || '';
              const parsed = new DOMParser().parseFromString(
                decodeURIComponent(encoded),
                'image/svg+xml',
              );
              const viewBox = parsed.documentElement.getAttribute('viewBox')
                ?.trim().split(/[ ,]+/).map(Number) || [];
              return 16 * Math.min(rect.width / viewBox[2], rect.height / viewBox[3]);
            }
            """
        )
        assert effective_font_pixels >= 8, contract

    for viewport in ({"width": 1024, "height": 768}, {"width": 932, "height": 430}):
        page.set_viewport_size(viewport)
        page.evaluate("window.dispatchEvent(new Event('resize'))")
        for contract in FEATURE_SYNC_CONTRACTS:
            slide = page.locator(f'section[data-contract="{contract}"]')
            indices = slide.evaluate(
                "node => { const index = Reveal.getIndices(node); return [index.h, index.v]; }"
            )
            _visit_slide(page, indices[0], indices[1])
            issues = page.evaluate(
                """
                () => {
                  const slide = Reveal.getCurrentSlide();
                  Reveal.layout();
                  const boundary = document.querySelector('.reveal').getBoundingClientRect();
                  const tolerance = 2;
                  const slideRect = slide.getBoundingClientRect();
                  const scale = slide.offsetWidth > 0 ? slideRect.width / slide.offsetWidth : 1;
                  const textNodes = Array.from(
                    slide.querySelectorAll('h2, h3, p, li, .stage-kicker')
                  ).filter((node) => {
                    const style = getComputedStyle(node);
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                      (node.textContent || '').trim();
                  });
                  const textIssues = textNodes.flatMap((node) => {
                    const range = document.createRange();
                    range.selectNodeContents(node);
                    const lines = Array.from(range.getClientRects());
                    const sides = [];
                    if (lines.some((line) => line.left < boundary.left - tolerance)) sides.push('left');
                    if (lines.some((line) => line.right > boundary.right + tolerance)) sides.push('right');
                    if (lines.some((line) => line.top < boundary.top - tolerance)) sides.push('top');
                    if (lines.some((line) => line.bottom > boundary.bottom + tolerance)) sides.push('bottom');
                    const effectiveFontPixels = parseFloat(getComputedStyle(node).fontSize) * scale;
                    if (effectiveFontPixels < 8) sides.push('illegible');
                    return sides.length ? [{
                      effectiveFontPixels,
                      sides,
                      text: (node.textContent || '').trim().slice(0, 100),
                    }] : [];
                  });
                  const containment = [];
                  if (slide.scrollHeight > slide.clientHeight + tolerance) {
                    containment.push({
                      clientHeight: slide.clientHeight,
                      kind: 'vertical-overflow',
                      scrollHeight: slide.scrollHeight,
                    });
                  }
                  return [...textIssues, ...containment];
                }
                """
            )
            assert issues == [], {"contract": contract, "viewport": viewport, "issues": issues}

    first = page.locator(
        'section[data-contract="s83-109-local-game-boundary"] '
        'a.source-link[data-source-lines]'
    ).first
    details = first.evaluate(
        "node => ({href: node.href, path: node.dataset.sourcePath, lines: node.dataset.sourceLines})"
    )
    first.evaluate("node => node.click()")
    page.wait_for_function("document.getElementById('source-viewer-dialog').open")
    viewer = page.evaluate(
        """
        () => {
          const editor = ace.edit('source-viewer-editor');
          const range = editor.selection.getRange();
          return {
            github: document.getElementById('source-viewer-github').href,
            path: document.getElementById('source-viewer-path').textContent,
            start: range.start.row + 1,
            end: range.end.row + 1,
          };
        }
        """
    )
    start_text, end_text = details["lines"].split("-")
    assert viewer == {
        "github": details["href"],
        "path": details["path"],
        "start": int(start_text),
        "end": int(end_text),
    }
    assert browser_events == {
        "console_errors": [],
        "page_errors": [],
        "request_failures": [],
        "http_failures": [],
    }


def test_invalid_diagram_fails_visible_without_breaking_navigation(
    page: Any,
    presentation_url: str,
    browser_events: dict[str, list[str]],
) -> None:
    """Malformed authored content must retain source and an accessible category."""
    _load(page, presentation_url)
    page.evaluate(
        """
        async () => {
          const slide = Reveal.getCurrentSlide();
          const wrap = document.createElement('div');
          wrap.className = 'diagram-wrap';
          const diagram = document.createElement('div');
          diagram.className = 'mermaid';
          diagram.textContent = 'graph TD\\nA -->';
          wrap.append(diagram);
          slide.append(wrap);
          await window.gluddPresentationRefresh();
        }
        """
    )
    page.wait_for_function(
        "document.querySelector('section.present .mermaid:last-of-type')?.dataset.mermaidState === 'failed'"
    )
    fault = page.locator("section.present .mermaid-host").last
    assert fault.get_attribute("data-mermaid-state") == "failed"
    assert fault.locator(".mermaid-source").is_visible()
    assert "Diagram render failed" in fault.locator(".mermaid-failure").inner_text()
    assert page.evaluate("window.gluddPresentationHealth().errors['invalid-source']") == 1
    _visit_slide(page, 1, 0)
    assert page.evaluate("Reveal.getIndices().h") == 1
    assert any("Diagram render failed: invalid-source" in message for message in browser_events["console_errors"])
    assert browser_events["page_errors"] == []


def test_blocked_mermaid_asset_keeps_all_source_readable(
    page: Any,
    presentation_url: str,
    browser_events: dict[str, list[str]],
) -> None:
    """A renderer outage must not leave an empty chart or break Reveal."""
    page.route("**/vendor/mermaid/mermaid.js", lambda route: route.abort("failed"))
    _load(page, presentation_url)
    page.wait_for_function("window.gluddPresentationHealth().pending === 0")
    health = page.evaluate("window.gluddPresentationHealth()")
    assert health["failed"] > 0
    assert health["errors"] == {"renderer-unavailable": health["failed"]}
    assert page.locator(
        ".mermaid-host[data-mermaid-state='failed'] .mermaid-source"
    ).count() == health["failed"]
    indices = page.evaluate(
        "Reveal.getSlides().map((slide) => { const i = Reveal.getIndices(slide); return [i.h, i.v]; })"
    )
    for horizontal, vertical in indices:
        _visit_slide(page, horizontal, vertical)
        hosts = page.locator("section.present .mermaid-host[data-mermaid-state='failed']")
        for index in range(hosts.count()):
            assert hosts.nth(index).locator(".mermaid-source").is_visible()
    assert browser_events["page_errors"] == []
    assert browser_events["http_failures"] == []
    assert all("/vendor/mermaid/mermaid.js" in url for url in browser_events["request_failures"])


class _SourceViewerState(TypedDict):
    text: str
    mode: str
    read_only: bool
    path: str
    href: str
    start: int
    end: int
    end_column: int
    reused: bool


def _source_viewer_state(page: Page) -> _SourceViewerState:
    return cast(_SourceViewerState, page.evaluate(
        """() => {
          const editor = window.ace.edit('source-viewer-editor');
          const range = editor.selection.getRange();
          return {
            text: editor.getValue(), mode: editor.session.getMode().$id,
            read_only: editor.getReadOnly(),
            path: document.getElementById('source-viewer-path').textContent,
            href: document.getElementById('source-viewer-github').href,
            start: range.start.row + 1, end: range.end.row + 1,
            end_column: range.end.column,
            reused: !window.__gluddR3Editor || window.__gluddR3Editor === editor,
          };
        }""",
    ))


def _citation_fixture(
    page: Page, identifier: str, path: str, lines: str | None,
) -> tuple[Locator, str]:
    """Author a local citation/real named-slide fallback without modifying immutable citations."""
    href = cast(str, page.evaluate(
        """({identifier, path, lines}) => {
          const fallback = document.createElement('section');
          fallback.id = identifier + '-fallback';
          fallback.innerHTML = '<h2>Authored source fallback</h2>';
          document.querySelector('.reveal .slides').append(fallback);
          const anchor = document.createElement('a');
          anchor.id = identifier; anchor.className = 'source-link';
          anchor.dataset.sourcePath = path;
          if (lines !== null) anchor.dataset.sourceLines = lines;
          // Absolute same-document href avoids Reveal consuming a hash-link click first.
          anchor.href = new URL('#/' + fallback.id, location.href).href;
          anchor.textContent = identifier;
          Reveal.getCurrentSlide().append(anchor);
          Reveal.sync();
          return anchor.href;
        }""",
        {"identifier": identifier, "path": path, "lines": lines},
    ))
    return page.locator(f"#{identifier}"), href


def _source_endpoint(base: str, path: str) -> str:
    parsed = urlsplit(base)
    return urlunsplit((parsed.scheme, parsed.netloc, "/__gludd_source__", urlencode({"path": path}), ""))


def _close_viewer(page: Page, anchor: Locator, *, escape: bool) -> None:
    if escape:
        page.keyboard.press("Escape")
    else:
        page.locator("#source-viewer-close").click()
    page.wait_for_function("!document.getElementById('source-viewer-dialog').open")
    assert anchor.evaluate("node => node === document.activeElement") is True


def _console_texts(events: dict[str, list[str]]) -> list[str]:
    return [re.sub(r"^diagram=(?:None|\d+) ", "", message) for message in events["console_errors"]]


def _assert_controller_faults(
    page: Page, events: dict[str, list[str]], categories: list[str],
) -> None:
    assert _console_texts(events) == [
        f"[gludd-presentation] Diagram render failed: {category}" for category in categories
    ]
    assert events["page_errors"] == []
    assert events["request_failures"] == []
    assert events["http_failures"] == []
    assert page.evaluate("window.gluddPresentationClientErrors()") == []


def _load_ready_for_fault(page: Page, url: str) -> None:
    _load(page, url)
    page.wait_for_function(
        """() => {
          const h = window.gluddPresentationHealth();
          return h.rendered > 0 && h.pending === 0 && h.rendering === 0 &&
            h.failed === 0 && h.unrendered === 0;
        }""",
        timeout=5_000,
    )


def _install_render_response_fault(
    page: Page, source: str, kind: str, markup: str = "",
) -> None:
    """Fault one designated new source through the real captured Mermaid object's public API."""
    assert page.evaluate(
        """({source, kind, markup}) => {
          const api = window.gluddMermaid;
          if (window.__gluddR3RenderFault) throw new Error('render fixture already installed');
          const original = api.render;
          window.__gluddR3RenderFault = {api, original};
          api.render = function(...args) {
            if (args[1] !== source) return Reflect.apply(original, api, args);
            if (kind === 'parse') {
              const error = new Error('designated dependency rejection');
              error.name = 'ParseError';
              return Promise.reject(error);
            }
            if (kind === 'syntax') return Promise.reject(new Error('syntax in designated response'));
            if (kind === 'null') return Promise.reject(null);
            if (kind === 'svg') return Promise.resolve({svg: markup});
            throw new Error('unknown render fixture kind');
          };
          return api.render !== original;
        }""",
        {"source": source, "kind": kind, "markup": markup},
    ) is True


def _restore_render_response(page: Page) -> None:
    page.evaluate(
        """() => {
          const saved = window.__gluddR3RenderFault;
          if (saved) {
            saved.api.render = saved.original;
            delete window.__gluddR3RenderFault;
          }
        }""",
    )


def _append_behavior_chart(page: Page, identifier: str, source: str) -> float:
    return cast(float, page.evaluate(
        """async ({identifier, source}) => {
          const wrap = document.createElement('div');
          wrap.id = identifier; wrap.className = 'diagram-wrap';
          const chart = document.createElement('div');
          chart.className = 'mermaid'; chart.textContent = source;
          wrap.append(chart); Reveal.getCurrentSlide().append(wrap);
          const started = performance.now();
          await window.gluddPresentationRefresh();
          return performance.now() - started;
        }""",
        {"identifier": identifier, "source": source},
    ))


def _assert_behavior_chart_failed(
    page: Page, identifier: str, source: str, category: str, geometry: str | None = None,
) -> None:
    chart = page.locator(f"#{identifier} .mermaid")
    host = page.locator(f"#{identifier} .mermaid-host")
    assert chart.get_attribute("data-mermaid-state") == "failed"
    assert chart.get_attribute("data-mermaid-error") == category
    assert host.locator(".mermaid-source").is_visible()
    assert host.locator(".mermaid-source").text_content() == source.strip()
    notice = host.locator(".mermaid-failure")
    assert notice.is_visible()
    assert notice.get_attribute("role") == "status"
    assert notice.get_attribute("aria-live") == "polite"
    assert notice.inner_text() == f"Diagram render failed ({category}). Source remains available."
    assert chart.locator("img, svg").count() == 0
    if geometry is not None:
        assert chart.get_attribute("data-mermaid-geometry") == geometry
    assert page.locator(".gludd-mermaid-stage, .gludd-mermaid-scratch").count() == 0


def _remove_behavior_chart(page: Page, identifier: str) -> None:
    locator = page.locator(f"#{identifier}")
    if locator.count():
        locator.evaluate("node => node.remove()")


def _assert_native_recovery(page: Page, identifier: str) -> None:
    source = f"flowchart LR\n  {identifier.replace('-', '_')} --> real_renderer"
    try:
        _append_behavior_chart(page, identifier, source)
        chart = page.locator(f"#{identifier} .mermaid")
        assert chart.get_attribute("data-mermaid-state") == "rendered"
        assert chart.locator("img.mermaid-image").evaluate(
            "async image => { await image.decode(); return image.naturalWidth > 0 && image.naturalHeight > 0; }",
        ) is True
        _assert_visible_diagrams(page)
        assert page.evaluate("window.gluddPresentationHealth().failed") == 0
        assert page.locator("#presentation-render-status").is_hidden()
        current = page.evaluate("Reveal.getIndices().h")
        destination = 1 if current != 1 else 0
        _visit_slide(page, destination, 0)
        assert page.evaluate("Reveal.getIndices().h") == destination
    finally:
        _remove_behavior_chart(page, identifier)


def test_source_viewer_reopens_closes_and_validates_ranges(
    page: Page, presentation_url: str, browser_events: dict[str, list[str]],
) -> None:
    """Native clicks/focus and explicit source responses retain read-only ranges and editor reuse."""
    _load_ready_for_fault(page, presentation_url)
    original = page.locator("a.source-link[data-source-lines]").first
    indices = original.evaluate(
        "node => { const i = Reveal.getIndices(node.closest('section')); return [i.h, i.v]; }",
    )
    _visit_slide(page, indices[0], indices[1])
    original_href = original.get_attribute("href")
    original_lines = original.get_attribute("data-source-lines")
    assert original_lines is not None
    start, end = (int(part) for part in original_lines.split("-"))
    original.click()
    page.wait_for_function("document.getElementById('source-viewer-dialog').open")
    state = _source_viewer_state(page)
    assert state["read_only"] is True
    assert state["start"] == start and state["end"] == end
    assert state["text"]
    assert state["href"] == original.evaluate("node => node.href")
    page.evaluate("window.__gluddR3Editor = window.ace.edit('source-viewer-editor')")
    _close_viewer(page, original, escape=False)
    assert original.get_attribute("href") == original_href

    def source_response(route: Route) -> None:
        path = parse_qs(urlsplit(route.request.url).query).get("path", [""])[0]
        if not path.startswith("r3-viewer/"):
            route.fallback()
            return
        route.fulfill(status=200, content_type="text/plain", body="first\nsecond\n")

    page.route("**/__gludd_source__?*", source_response)
    cases = [
        ("r3-viewer-unknown", "r3-viewer/source.gludd", "2-2", "ace/mode/text", 2, 2, 6),
        ("r3-viewer-omitted", "r3-viewer/default.py", None, "ace/mode/python", 1, 1, 5),
        ("r3-viewer-malformed", "r3-viewer/default.py", "bad-range", "ace/mode/python", 1, 1, 5),
        ("r3-viewer-empty-final", "r3-viewer/final.py", "3-3", "ace/mode/python", 3, 3, 0),
    ]
    try:
        for identifier, path, lines, mode, expected_start, expected_end, column in cases:
            anchor, href = _citation_fixture(page, identifier, path, lines)
            anchor.click()
            page.wait_for_function("document.getElementById('source-viewer-dialog').open")
            state = _source_viewer_state(page)
            assert state == {
                "text": "first\nsecond\n", "mode": mode, "read_only": True,
                "path": path, "href": href, "start": expected_start, "end": expected_end,
                "end_column": column, "reused": True,
            }
            _close_viewer(page, anchor, escape=True)
            assert anchor.evaluate("node => node.href") == href
    finally:
        page.evaluate("delete window.__gluddR3Editor")
    assert browser_events == {
        "console_errors": [], "page_errors": [], "request_failures": [], "http_failures": [],
    }


def test_source_viewer_faults_follow_the_authored_fallback(
    page: Page, presentation_url: str, browser_events: dict[str, list[str]],
) -> None:
    """Dependency/response/metadata faults follow real same-document authored hrefs, never a stub."""
    _load_ready_for_fault(page, presentation_url)
    requests: list[str] = []

    def source_response(route: Route) -> None:
        path = parse_qs(urlsplit(route.request.url).query).get("path", [""])[0]
        if not path.startswith("r3-fault/"):
            route.fallback()
            return
        requests.append(path)
        if path == "r3-fault/abort.py":
            route.abort("failed")
        elif path == "r3-fault/status.py":
            route.fulfill(status=503, content_type="text/plain", body="injected unavailable source")
        else:
            route.fulfill(status=200, content_type="text/plain", body="first\nsecond\n")

    page.route("**/__gludd_source__?*", source_response)
    page.evaluate("window.__gluddR3Ace = window.ace; window.ace = undefined")
    try:
        anchor, href = _citation_fixture(page, "r3-ace-unavailable", "r3-fault/ace.py", "1-1")
        anchor.click()
        page.wait_for_function("href => location.href === href", arg=href)
        assert page.locator("#source-viewer-dialog").get_attribute("open") is None
        assert page.locator("#source-viewer-editor .ace_line").count() == 0
    finally:
        page.evaluate("window.ace = window.__gluddR3Ace; delete window.__gluddR3Ace")

    cases = [
        ("empty-path", "", "1-1", False),
        ("directory-path", "r3-fault/directory/", "1-1", False),
        ("endpoint-abort", "r3-fault/abort.py", "1-1", True),
        ("endpoint-status", "r3-fault/status.py", "1-1", True),
        ("zero-range", "r3-fault/range.py", "0-1", True),
        ("reversed-range", "r3-fault/range.py", "2-1", True),
        ("outside-range", "r3-fault/range.py", "1-8", True),
    ]
    for suffix, path, lines, fetches in cases:
        count_before = len(requests)
        anchor, href = _citation_fixture(page, "r3-fallback-" + suffix, path, lines)
        anchor.click()
        page.wait_for_function("href => location.href === href", arg=href)
        assert anchor.evaluate("node => node.href") == href
        assert page.locator("#source-viewer-dialog").get_attribute("open") is None
        assert page.locator("#source-viewer-editor .ace_line").count() == 0
        assert len(requests) == count_before + int(fetches)
    anchor, href = _citation_fixture(page, "r3-viewer-recovery", "r3-fault/recovered.py", "1-2")
    anchor.click()
    page.wait_for_function("document.getElementById('source-viewer-dialog').open")
    state = _source_viewer_state(page)
    assert state["read_only"] is True and state["text"] == "first\nsecond\n"
    assert state["path"] == "r3-fault/recovered.py" and state["href"] == href
    assert state["start"] == 1 and state["end"] == 2
    _close_viewer(page, anchor, escape=True)
    assert browser_events["page_errors"] == []
    assert browser_events["request_failures"] == [_source_endpoint(presentation_url, "r3-fault/abort.py")]
    assert browser_events["http_failures"] == ["503 " + _source_endpoint(presentation_url, "r3-fault/status.py")]
    resource_fault = re.compile(
        r"Failed to load resource: (?:net::ERR_FAILED|The network connection was lost\.|"
        r"An (?:unknown error occurred|internal error was encountered)\.?|"
        r"the server responded with a status of 503 \(Service Unavailable\))",
    )
    messages = _console_texts(browser_events)
    assert len(messages) <= 2
    assert all(resource_fault.fullmatch(message) for message in messages), messages
    assert page.evaluate("window.gluddPresentationClientErrors()") == []


def test_renderer_rejections_keep_source_and_allow_recovery(
    page: Page, presentation_url: str, browser_events: dict[str, list[str]],
) -> None:
    """Explicit public-renderer rejection fixtures preserve source, one notice and native recovery."""
    _load_ready_for_fault(page, presentation_url)
    categories: list[str] = []
    for kind, category in (("parse", "invalid-source"), ("syntax", "invalid-source"), ("null", "renderer-unavailable")):
        identifier = "r3-rejected-" + kind
        source = f"flowchart LR\n  r3_fault_{kind} --> retained"
        _install_render_response_fault(page, source, kind)
        try:
            _append_behavior_chart(page, identifier, source)
            _assert_behavior_chart_failed(page, identifier, source, category)
            page.evaluate("async () => { await gluddPresentationRefresh(); await gluddPresentationRenderVisible(); }")
            categories.append(category)
            _assert_controller_faults(page, browser_events, categories)
        finally:
            _restore_render_response(page)
            _remove_behavior_chart(page, identifier)
        _assert_native_recovery(page, "r3-recovered-" + kind)
    _assert_controller_faults(page, browser_events, categories)


def test_renderer_svg_faults_fail_visible_at_the_native_deadline(
    page: Page, presentation_url: str, browser_events: dict[str, list[str]],
) -> None:
    """Designated response fixtures use actual SVG layout and the unchanged native ten-second deadline."""
    _load_ready_for_fault(page, presentation_url)
    namespace = 'xmlns="http://www.w3.org/2000/svg"'
    cases = [
        ("no-svg", "<div>designated output has no svg</div>", "invalid-geometry", None),
        ("no-viewbox", f"<svg {namespace}><rect width='40' height='20'/></svg>", "invalid-geometry", None),
        ("bad-viewbox", f"<svg {namespace} viewBox='0 0 0 40'/>", "invalid-geometry", None),
        (
            "bad-attribute",
            f"<svg {namespace} viewBox='0 0 100 40'><g data-r3-invalid='NaN'/></svg>",
            "invalid-geometry", None,
        ),
        (
            "hidden-svg",
            f"<svg {namespace} viewBox='0 0 100 40' style='display:none'>"
            "<rect width='100' height='40'/></svg>",
            "render-timeout", "nonpositive-svg",
        ),
        (
            "zero-foreign",
            f"<svg {namespace} viewBox='0 0 100 40'><rect width='100' height='40'/>"
            "<foreignObject width='0' height='0'><div xmlns='http://www.w3.org/1999/xhtml'>"
            "zero-sized</div></foreignObject></svg>",
            "render-timeout", "invalid-foreign-object",
        ),
    ]
    categories: list[str] = []
    for kind, markup, category, geometry in cases:
        identifier = "r3-svg-" + kind
        source = f"flowchart LR\n  r3_fault_{kind.replace('-', '_')} --> retained"
        _install_render_response_fault(page, source, "svg", markup)
        try:
            elapsed = _append_behavior_chart(page, identifier, source)
            _assert_behavior_chart_failed(page, identifier, source, category, geometry)
            if geometry is not None:
                assert elapsed >= 10_000, {"kind": kind, "elapsed": elapsed}
            page.evaluate("async () => { await gluddPresentationRefresh(); await gluddPresentationRenderVisible(); }")
            categories.append(category)
            _assert_controller_faults(page, browser_events, categories)
        finally:
            _restore_render_response(page)
            _remove_behavior_chart(page, identifier)
        _assert_native_recovery(page, "r3-svg-recovery-" + kind)
    _assert_controller_faults(page, browser_events, categories)


def test_blocked_data_images_keep_source_visible(
    page: Page, presentation_url: str, browser_events: dict[str, list[str]],
) -> None:
    """A real response CSP blocks native data-image decoding while all scripts keep executing."""
    def image_csp(route: Route) -> None:
        response = route.fetch()
        headers = dict(response.headers)
        headers["content-security-policy"] = "img-src 'self'"
        route.fulfill(response=response, headers=headers)

    page.route("**/gludd/", image_csp)
    _load(page, presentation_url)
    page.wait_for_function(
        "() => { const h = gluddPresentationHealth(); return h.pending === 0 && h.rendering === 0; }",
    )
    health = page.evaluate("window.gluddPresentationHealth()")
    assert health["failed"] > 0
    assert health["errors"] == {"render-timeout": health["failed"]}
    assert page.locator("#presentation-render-status").is_visible()
    indices = page.evaluate(
        "() => { const s = Reveal.getSlides().find(s => s.querySelector('.mermaid')); "
        "const i = Reveal.getIndices(s); return [i.h, i.v]; }",
    )
    _visit_slide(page, indices[0], indices[1])
    for source in page.locator("section.present .mermaid-source").all():
        assert source.is_visible() and source.inner_text().strip()
    messages = _console_texts(browser_events)
    notices = [message for message in messages if message.startswith("[gludd-presentation]")]
    blocked = [message for message in messages if message not in notices]
    assert notices == ["[gludd-presentation] Diagram render failed: render-timeout"] * health["failed"]
    assert blocked
    assert all(
        "data:image/svg+xml" in message and "img-src" in message
        and ("policy" in message.lower() or "directive" in message.lower())
        for message in blocked
    ), blocked
    assert browser_events["page_errors"] == []
    assert browser_events["http_failures"] == []
    assert all(url.startswith("data:image/svg+xml;charset=utf-8,") for url in browser_events["request_failures"])
    assert page.evaluate("window.gluddPresentationClientErrors()") == []
    destination = 1 if indices[0] != 1 else 0
    _visit_slide(page, destination, 0)
    assert page.evaluate("Reveal.getIndices().h") == destination


def test_lost_visible_image_is_revalidated_on_render_request(
    page: Page, presentation_url: str, browser_events: dict[str, list[str]],
) -> None:
    """A narrowly routed real HTTP failure invalidates a previously decoded visible image."""
    _load_ready_for_fault(page, presentation_url)
    indices = page.evaluate(
        "() => { const s = Reveal.getSlides().find(s => s.querySelector('.mermaid')); "
        "const i = Reveal.getIndices(s); return [i.h, i.v]; }",
    )
    _visit_slide(page, indices[0], indices[1])
    _assert_visible_diagrams(page)
    original_source = cast(str, page.locator("section.present .mermaid-host .mermaid-source").first.text_content())
    parsed = urlsplit(presentation_url)
    failed_url = urlunsplit((parsed.scheme, parsed.netloc, "/__gludd_r3_broken_image__.svg", "", ""))

    def broken_image(route: Route) -> None:
        route.fulfill(status=503, content_type="image/svg+xml", body="injected image response failure")

    page.route(failed_url, broken_image)
    page.locator("section.present .mermaid").first.evaluate(
        """(diagram, url) => {
          diagram.closest('.diagram-wrap').id = 'r3-lost-image';
          diagram.querySelector('img.mermaid-image').src = url;
        }""",
        failed_url,
    )
    page.wait_for_function(
        "() => { const image = document.querySelector('#r3-lost-image img'); "
        "return image.complete && image.naturalWidth === 0; }",
        timeout=5_000,
    )
    assert page.locator("#r3-lost-image img").evaluate(
        "image => image.decode().then(() => false, () => true)",
    ) is True
    page.evaluate("async () => { await gluddPresentationRenderVisible(); await gluddPresentationRenderVisible(); }")
    _assert_behavior_chart_failed(page, "r3-lost-image", original_source, "invalid-geometry")
    assert browser_events["http_failures"] == ["503 " + failed_url]
    assert browser_events["request_failures"] == []
    assert browser_events["page_errors"] == []
    messages = _console_texts(browser_events)
    controller = "[gludd-presentation] Diagram render failed: invalid-geometry"
    resource = "Failed to load resource: the server responded with a status of 503 (Service Unavailable)"
    assert messages.count(controller) == 1
    assert all(message in {controller, resource} for message in messages), messages
    assert messages.count(resource) <= 1
    assert page.evaluate("window.gluddPresentationClientErrors()") == []
    _remove_behavior_chart(page, "r3-lost-image")
    _assert_native_recovery(page, "r3-image-recovery")
