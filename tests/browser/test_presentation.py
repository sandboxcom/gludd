"""Chromium and WebKit acceptance for the self-contained Reveal.js artifact."""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Generator
from http.server import ThreadingHTTPServer
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
    assert response is not None and response.ok
    page.wait_for_function("window.gluddPresentationReady === true")
    page.wait_for_function("typeof window.gluddPresentationHealth === 'function'")


def _visit_slide(page: Any, horizontal: int, vertical: int) -> None:
    """Navigate to one slide and await two layout frames plus rendering."""
    page.evaluate(
        """
        async ([horizontal, vertical]) => {
          Reveal.slide(horizontal, vertical);
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
    """Require an actual positive SVG, not merely an inserted element."""
    results = page.eval_on_selector_all(
        "section.present .mermaid",
        """
        (nodes) => nodes.map((node) => {
          const svgs = node.querySelectorAll('svg');
          const svg = svgs[0];
          const rect = svg ? svg.getBoundingClientRect() : {width: 0, height: 0};
          const viewBox = svg ? (svg.getAttribute('viewBox') || '').trim().split(/[ ,]+/).map(Number) : [];
          return {
            state: node.dataset.mermaidState,
            svgCount: svgs.length,
            width: rect.width,
            height: rect.height,
            viewBox,
          };
        })
        """,
    )
    for result in results:
        assert result["state"] == "rendered", result
        assert result["svgCount"] == 1
        assert result["width"] > 0
        assert result["height"] > 0
        assert len(result["viewBox"]) == 4
        assert result["viewBox"][2] > 0 and result["viewBox"][3] > 0


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
              state: node.dataset.mermaidState,
              svgCount: node.querySelectorAll('svg').length,
              viewBox: (node.querySelector('svg')?.getAttribute('viewBox') || '')
                .trim().split(/[ ,]+/).map(Number),
            }))
            """,
        )
        assert metadata
        assert all(
            item["state"] == "rendered"
            and item["svgCount"] == 1
            and len(item["viewBox"]) == 4
            and item["viewBox"][2] > 0
            and item["viewBox"][3] > 0
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
