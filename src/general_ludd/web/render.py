"""render_page — OPTIONAL dynamic JS render (Playwright / Selenium).

ALL browser backends are LAZY-imported INSIDE :func:`render_page` under
try/except ImportError, returning a structured ``RENDERER_UNAVAILABLE`` fallback,
so this module imports (and the suite collects) with nothing extra installed.

Three render modes (both fully-headless-local AND remote/already-running):
  * ``headless_local``    (DEFAULT, no endpoint) — launch a local headless browser.
  * ``remote_cdp``        — Playwright ``connect_over_cdp`` to a remote-debug/CDP
                            endpoint (e.g. ``http://host:9222``).
  * ``webdriver_remote``  — Selenium ``webdriver.Remote`` to a Grid/standalone.

CRITICAL SSRF: a remote-debug/Grid endpoint is ITSELF an SSRF vector (it usually
points at localhost:9222).  Before any remote connect we run a relaxed-scheme
guard that (a) normalises ws->http, (b) allows http for the endpoint only, BUT
(c) STILL resolve-and-screens every resolved IP (so localhost:9222 is BLOCKED),
AND (d) requires the endpoint host on ``config.remote_host_allowlist``.  Both the
allowlist and the IP-reblock are mandatory so this relaxed path can never reopen
the hole the main client closes.  The render TARGET url is always guarded with
the full :func:`_guard_url`.

Every backend call has an explicit connect timeout and is wrapped so a failure
returns a structured :class:`RenderResult` — never a hang, never a raise.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from general_ludd.web.results import RenderResult, WebError
from general_ludd.web.ssrf_client import (
    OfflineResolveError,
    SSRFBlocked,
    _guard_url,
    resolve_and_screen,
)

DEFAULT_UA = "general-ludd-web/1.0 (+https://github.com/sandboxcom/gludd)"

_VALID_MODES = frozenset({"headless_local", "remote_cdp", "webdriver_remote"})

#: A render backend yields (html, text, screenshot_path, status, final_url).
_BackendResult = tuple[str | None, str | None, str | None, int | None, str]
_Backend = Callable[[str, "RenderConfig"], _BackendResult]


@dataclass
class RenderConfig:
    """Render backend configuration."""

    mode: str = "headless_local"
    endpoint: str | None = None
    viewport: tuple[int, int] = (1280, 800)
    user_agent: str = DEFAULT_UA
    timeout: float = 30.0
    connect_timeout: float = 10.0
    screenshot_path: str | None = None
    engine: str = "playwright"  # playwright | selenium
    remote_host_allowlist: list[str] = field(default_factory=list)


def _guard_remote_endpoint(endpoint: str, allowlist: list[str]) -> None:
    """Relaxed-scheme SSRF guard for a remote-debug / Grid endpoint.

    Allows http/ws (CDP/Grid are not https) BUT still resolve-screens every IP
    AND requires the host on the allowlist.  Raises :class:`SSRFBlocked` (or
    :class:`OfflineResolveError`) on any violation.
    """
    if not endpoint:
        raise SSRFBlocked("remote mode requires an endpoint", kind="string")
    norm = endpoint.strip()
    # Normalise ws/wss -> http/https for parsing + screening.
    low = norm.lower()
    if low.startswith("ws://"):
        norm = "http://" + norm[len("ws://"):]
    elif low.startswith("wss://"):
        norm = "https://" + norm[len("wss://"):]
    parts = urlsplit(norm)
    if parts.scheme not in ("http", "https"):
        raise SSRFBlocked(f"endpoint scheme not allowed: {parts.scheme!r}", kind="string")
    host = parts.hostname
    if not host:
        raise SSRFBlocked("endpoint has no host", kind="string")
    allow = {a.strip().lower() for a in (allowlist or [])}
    if host.lower() not in allow:
        raise SSRFBlocked(
            f"endpoint host {host!r} not on remote_host_allowlist", kind="allowlist",
        )
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    # Mandatory IP re-screen: localhost:9222 et al are BLOCKED even if allowlisted.
    resolve_and_screen(host, port)


def render_page(
    url: str,
    *,
    config: RenderConfig | None = None,
    _backend: _Backend | None = None,  # test seam: injectable fake backend
) -> RenderResult:
    """Render ``url`` with a JS-capable backend; return a structured result.

    Never raises, never hangs.  With nothing extra installed this returns
    ``RENDERER_UNAVAILABLE``.
    """
    cfg = config or RenderConfig()
    started = time.monotonic()

    if cfg.mode not in _VALID_MODES:
        return RenderResult(
            ok=False, error=WebError.RENDER_CONNECT_FAILED,
            detail=f"unknown render mode {cfg.mode!r}", mode=cfg.mode,
            elapsed_s=round(time.monotonic() - started, 4),
        )

    # 1. SSRF-guard the TARGET url (full string + DNS + IP screen) ALWAYS.
    try:
        _guard_url(url)
    except SSRFBlocked as exc:
        return _err(WebError.SSRF_BLOCKED, exc, cfg, started)
    except OfflineResolveError as exc:
        return _err(WebError.OFFLINE, exc, cfg, started)

    # 2. SSRF-guard the remote endpoint (relaxed scheme + allowlist + IP reblock).
    if cfg.mode in ("remote_cdp", "webdriver_remote"):
        try:
            _guard_remote_endpoint(cfg.endpoint or "", cfg.remote_host_allowlist)
        except SSRFBlocked as exc:
            return _err(WebError.SSRF_BLOCKED, exc, cfg, started)
        except OfflineResolveError as exc:
            return _err(WebError.OFFLINE, exc, cfg, started)

    # 3. Run the backend (injectable for tests; otherwise lazy-import the engine).
    try:
        if _backend is not None:
            html, text, screenshot, status, final_url = _backend(url, cfg)
        elif cfg.mode == "webdriver_remote" or cfg.engine == "selenium":
            html, text, screenshot, status, final_url = _render_selenium(url, cfg)
        else:
            html, text, screenshot, status, final_url = _render_playwright(url, cfg)
    except _RendererUnavailable as exc:
        return RenderResult(
            ok=False, error=WebError.RENDERER_UNAVAILABLE,
            detail=str(exc), mode=cfg.mode,
            elapsed_s=round(time.monotonic() - started, 4),
        )
    except SSRFBlocked as exc:
        return _err(WebError.SSRF_BLOCKED, exc, cfg, started)
    except TimeoutError as exc:
        return _err(WebError.TIMEOUT, exc, cfg, started)
    except Exception as exc:  # any backend failure -> structured, never raise
        return _err(WebError.RENDER_CONNECT_FAILED, exc, cfg, started)

    return RenderResult(
        ok=True, status=status, html=html, text=text,
        final_url=final_url or url, screenshot_path=screenshot,
        mode=cfg.mode, elapsed_s=round(time.monotonic() - started, 4),
    )


class _RendererUnavailable(Exception):
    """Lazy import of a browser backend failed (extra not installed)."""


def _render_playwright(url: str, cfg: RenderConfig) -> _BackendResult:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError as exc:
        raise _RendererUnavailable(
            "playwright not installed — install general-ludd-agent[web]"
        ) from exc
    timeout_ms = int(cfg.timeout * 1000)
    connect_ms = int(cfg.connect_timeout * 1000)
    with sync_playwright() as p:
        if cfg.mode == "remote_cdp":
            browser = p.chromium.connect_over_cdp(cfg.endpoint, timeout=connect_ms)
        else:
            browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                viewport={"width": cfg.viewport[0], "height": cfg.viewport[1]},
                user_agent=cfg.user_agent,
            )
            page = ctx.new_page()
            resp = page.goto(url, timeout=timeout_ms, wait_until="load")
            html = page.content()
            text = page.inner_text("body") if page.query_selector("body") else ""
            screenshot = None
            if cfg.screenshot_path:
                page.screenshot(path=cfg.screenshot_path)
                screenshot = cfg.screenshot_path
            status = resp.status if resp else None
            final_url = page.url
            return html, text, screenshot, status, final_url
        finally:
            browser.close()


def _render_selenium(url: str, cfg: RenderConfig) -> _BackendResult:
    try:
        from selenium import webdriver  # type: ignore[import-not-found]
        from selenium.webdriver.chrome.options import (  # type: ignore[import-not-found]
            Options,
        )
    except ImportError as exc:
        raise _RendererUnavailable(
            "selenium not installed — install general-ludd-agent[web]"
        ) from exc
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument(f"--window-size={cfg.viewport[0]},{cfg.viewport[1]}")
    options.add_argument(f"--user-agent={cfg.user_agent}")
    if cfg.mode == "webdriver_remote":
        driver = webdriver.Remote(command_executor=cfg.endpoint, options=options)
    else:
        driver = webdriver.Chrome(options=options)
    try:
        driver.set_page_load_timeout(cfg.timeout)
        driver.get(url)
        html = driver.page_source
        text = driver.find_element("tag name", "body").text
        screenshot = None
        if cfg.screenshot_path:
            driver.save_screenshot(cfg.screenshot_path)
            screenshot = cfg.screenshot_path
        return html, text, screenshot, None, driver.current_url
    finally:
        driver.quit()


def _err(error: WebError, exc: BaseException, cfg: RenderConfig, started: float) -> RenderResult:
    return RenderResult(
        ok=False, error=error,
        detail=f"{type(exc).__name__}: {str(exc)[:200]}",
        mode=cfg.mode, elapsed_s=round(time.monotonic() - started, 4),
    )
