"""Optional JS render via Playwright — LAZY-imported with structured fallback.

Playwright is in the OPTIONAL ``[project.optional-dependencies].web`` extra and
is imported INSIDE :func:`render_js`, so ``import general_ludd.web.render``
SUCCEEDS with nothing extra installed. Only CALLING ``render_js`` without
playwright (or without a browser binary, or on a launch/runtime/timeout error)
returns a structured ``RENDERER_UNAVAILABLE`` :class:`WebResult` — never an
ImportError, never a crash, never a hang (the browser gets a fixed nav timeout).

SSRF NOTE: a headless browser is its own SSRF surface, so the target is vetted
(string + DNS-all-IPs) BEFORE anything is launched, and sub-resource requests are
re-checked by a route handler.
"""

from __future__ import annotations

import time

from general_ludd.web.parse import parse_html
from general_ludd.web.policy import DEFAULT_POLICY, WebPolicy
from general_ludd.web.safe_fetch import SafeFetcher
from general_ludd.web.types import WebError, WebResult


def render_js(
    url: str,
    *,
    wait_selector: str | None = None,
    policy: WebPolicy = DEFAULT_POLICY,
    fetcher: SafeFetcher | None = None,
) -> WebResult:
    """Render ``url`` with a headless browser; structured fallback on any failure."""
    t0 = time.monotonic()
    vetter = fetcher if fetcher is not None else SafeFetcher(policy=policy)

    # SSRF guard FIRST — vet string + all resolved IPs before launching anything.
    verdict = vetter.validate_target(url)
    if not verdict.ok:
        return WebResult(
            ok=False, url=url, error=verdict.error or WebError.SSRF_BLOCKED,
            detail=verdict.reason, elapsed_ms=(time.monotonic() - t0) * 1000.0,
        )

    if not policy.allow_render:
        return WebResult(
            ok=False, url=url, error=WebError.RENDERER_UNAVAILABLE,
            detail="render disabled by policy (policy.allow_render=False)",
            elapsed_ms=(time.monotonic() - t0) * 1000.0,
        )

    try:
        from playwright.sync_api import sync_playwright  # lazy, optional [web] extra
    except ImportError:
        return WebResult(
            ok=False, url=url, error=WebError.RENDERER_UNAVAILABLE,
            detail="JS render requires the 'web' extra: pip install general-ludd-agent[web]",
            elapsed_ms=(time.monotonic() - t0) * 1000.0,
        )

    try:
        html, status = _do_render(
            sync_playwright, url, wait_selector=wait_selector, policy=policy, vetter=vetter
        )
    except Exception as exc:  # launch/runtime/timeout/browser-missing -> fallback
        return WebResult(
            ok=False, url=url, error=WebError.RENDERER_UNAVAILABLE,
            detail=f"render failed: {exc}", elapsed_ms=(time.monotonic() - t0) * 1000.0,
        )

    parsed = parse_html(html, base_url=url, status=status, rich=True)
    return WebResult(
        ok=True, url=url, final_url=url, status=status, body=html, parsed=parsed,
        elapsed_ms=(time.monotonic() - t0) * 1000.0, meta={"rendered": True},
    )


def _do_render(
    sync_playwright: object,
    url: str,
    *,
    wait_selector: str | None,
    policy: WebPolicy,
    vetter: SafeFetcher,
) -> tuple[str, int | None]:
    """Drive playwright with a fixed nav timeout + SSRF-checked sub-resources."""
    timeout_ms = int(policy.render_timeout * 1000)
    with sync_playwright() as p:  # type: ignore[operator]
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=policy.user_agent)
            page = ctx.new_page()

            def _route(route: object) -> None:
                # Re-run the SSRF check on every sub-resource the page requests so
                # an internal sub-resource (img/script/xhr) is blocked too.
                req_url = route.request.url  # type: ignore[attr-defined]
                if vetter.validate_target(req_url).ok:
                    route.continue_()  # type: ignore[attr-defined]
                else:
                    route.abort()  # type: ignore[attr-defined]

            page.route("**/*", _route)
            response = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=timeout_ms)
            html = page.content()
            status = response.status if response is not None else None
            return html, status
        finally:
            browser.close()
