"""Chromium and WebKit acceptance for the self-contained Reveal.js artifact."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Generator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from scripts import build_deck

pytestmark = pytest.mark.presentation_browser


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
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, name="gludd-presentation-browser", daemon=True)
    thread.start()
    host, port = server.server_address
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
