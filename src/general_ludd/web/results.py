"""Frozen pydantic result models for the web toolkit.

Every public entrypoint in :mod:`general_ludd.web` returns one of these frozen
models and NEVER raises and NEVER hangs.  They all share an ``ok: bool`` +
``error: WebError | None`` shape so a caller can branch uniformly on success or a
structured failure (the static fetch, the parsed page, a search gather, a render
and a crawl all carry the same failure vocabulary).  ``.model_dump()`` yields a
plain JSON-serialisable dict for the MCP tool surface.
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WebError(enum.StrEnum):
    """The structured failure vocabulary shared by every web result model."""

    SSRF_BLOCKED = "ssrf_blocked"
    OFFLINE = "offline"
    TIMEOUT = "timeout"
    REDIRECT_LIMIT = "redirect_limit"
    ROBOTS_DENIED = "robots_denied"
    CAPTCHA_DETECTED = "captcha_detected"
    CIRCUIT_OPEN = "circuit_open"
    RENDERER_UNAVAILABLE = "renderer_unavailable"
    HTTP_4XX = "http_4xx"
    HTTP_5XX = "http_5xx"
    RENDER_CONNECT_FAILED = "render_connect_failed"
    NO_PROVIDER = "no_provider"
    INVALID_URL = "invalid_url"


class _Frozen(BaseModel):
    """Base: frozen, JSON-friendly, enum-by-value on dump."""

    model_config = ConfigDict(frozen=True, use_enum_values=True)


class CaptchaSignal(_Frozen):
    """An ADVISORY captcha/bot-block detection signal (never ground truth).

    ``detected`` is heuristic; ``vendor`` names the matched challenge family when
    known.  The partial page body is ALWAYS retained by the caller so legitimate
    content is never silently dropped on a false positive.
    """

    detected: bool = False
    vendor: str | None = None
    status: int | None = None
    reason: str = ""


class RawFetchResult(_Frozen):
    """Outcome of a single hardened HTTP(S) fetch through :class:`SsrfSafeClient`."""

    ok: bool
    error: WebError | None = None
    detail: str = ""
    status: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    final_url: str = ""
    elapsed_s: float = 0.0
    redirect_chain: list[str] = Field(default_factory=list)
    resolved_ip: str | None = None
    captcha: CaptchaSignal | None = None


class ParsedPage(_Frozen):
    """A stdlib-parsed HTML page (title/text/links/meta/headings/lang)."""

    ok: bool
    error: WebError | None = None
    detail: str = ""
    status: int | None = None
    final_url: str = ""
    title: str | None = None
    text: str = ""
    links: list[str] = Field(default_factory=list)
    meta: dict[str, str] = Field(default_factory=dict)
    headings: list[str] = Field(default_factory=list)
    lang: str | None = None
    captcha: CaptchaSignal | None = None


class SearchHit(_Frozen):
    """A single search-provider result row."""

    url: str
    title: str = ""
    snippet: str = ""


class SearchResult(_Frozen):
    """Aggregate of a provider search + multi-page gather (partial-success-ok)."""

    ok: bool
    error: WebError | None = None
    detail: str = ""
    query: str = ""
    hits: list[SearchHit] = Field(default_factory=list)
    pages: list[ParsedPage] = Field(default_factory=list)
    gathered: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)


class RenderResult(_Frozen):
    """Outcome of a dynamic JS render — same shape as a static fetch."""

    ok: bool
    error: WebError | None = None
    detail: str = ""
    status: int | None = None
    html: str | None = None
    text: str | None = None
    final_url: str = ""
    screenshot_path: str | None = None
    mode: str = ""
    elapsed_s: float = 0.0


class CrawlResult(_Frozen):
    """Outcome of a polite BFS crawl."""

    ok: bool
    error: WebError | None = None
    detail: str = ""
    pages: list[ParsedPage] = Field(default_factory=list)
    visited: list[str] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
