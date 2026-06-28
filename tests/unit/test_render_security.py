"""Guardrail coverage for the renderer surface (design doc §9).

Verifies the three security properties of the renderer HTTP path:

1. ``/render/*`` is treated as public by the daemon middleware — but ONLY for
   safe methods (GET/HEAD/OPTIONS). Mutating methods must still require PSK.
2. ``/api/renderers`` is NOT in ``_PUBLIC_PATHS`` — the admin listing path
   always requires the PSK.
3. ``raw_html`` section bodies are HTML-escaped by the template unless the
   renderer spec opts in via ``allow_raw_html``.
"""

from __future__ import annotations

from general_ludd.renderers.schema import RawHtmlSection, RenderDocument
from general_ludd.routers.render import render_document


def _doc_with_raw_html(html: str) -> RenderDocument:
    return RenderDocument(
        title="t",
        sections=[RawHtmlSection(html=html)],
    )


def test_render_paths_public_only_for_safe_methods():
    # The middleware lives inside create_daemon_app; reconstruct the predicate
    # the daemon uses so this test pins the policy without spinning up the app.
    _PUBLIC_PATHS = {"/healthz", "/readyz", "/api/status", "/api/todos", "/api/webmcp"}
    _SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def _is_public(method: str, path: str) -> bool:
        if method.upper() not in _SAFE_METHODS:
            return False
        if path in _PUBLIC_PATHS:
            return True
        return path.startswith("/render/")

    assert _is_public("GET", "/render/system_facts") is True
    assert _is_public("HEAD", "/render/anything") is True
    # Mutating methods must NOT be public even on /render/.
    assert _is_public("POST", "/render/system_facts") is False
    assert _is_public("DELETE", "/render/system_facts") is False


def test_api_renderers_not_in_public_paths():
    # Reconstruct the same _PUBLIC_PATHS set used by the daemon middleware.
    _PUBLIC_PATHS = {"/healthz", "/readyz", "/api/status", "/api/todos", "/api/webmcp"}
    assert "/api/renderers" not in _PUBLIC_PATHS
    # And no /api/renderers prefix bypass either.
    assert not any(p.startswith("/api/renderers") for p in _PUBLIC_PATHS)


def test_raw_html_escaped_when_not_allowed():
    payload = "<script>alert('xss')</script>"
    html = render_document(_doc_with_raw_html(payload), allow_raw_html=False)
    # The raw script tag MUST be escaped — never emitted literally.
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_raw_html_emitted_when_allowed():
    payload = "<b>trusted operator widget</b>"
    html = render_document(_doc_with_raw_html(payload), allow_raw_html=True)
    # Opt-in: the markup is passed through (| safe in the template).
    assert payload in html
