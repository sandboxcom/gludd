"""Headless Chromium acceptance for the self-contained Reveal.js artifact."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Generator
from http.server import ThreadingHTTPServer
from typing import Any

import pytest
from scripts import build_deck

pytestmark = pytest.mark.presentation_browser


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
    """Navigate to one slide and await its transition plus serialized render."""
    page.evaluate(
        """
        async ([horizontal, vertical]) => {
          await new Promise((resolve) => {
            let settled = false;
            const finish = () => {
              if (settled) return;
              settled = true;
              Reveal.off('slidetransitionend', finish);
              resolve();
            };
            Reveal.on('slidetransitionend', finish);
            Reveal.slide(horizontal, vertical);
            window.setTimeout(finish, 1600);
          });
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
        assert result["state"] == "rendered"
        assert result["svgCount"] == 1
        assert result["width"] > 0
        assert result["height"] > 0
        assert len(result["viewBox"]) == 4
        assert result["viewBox"][2] > 0 and result["viewBox"][3] > 0


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
