"""Frozen, JSON-safe result models for the bounded web toolkit."""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WebError(enum.StrEnum):
    """Stable failure vocabulary shared by all model-facing web operations."""

    OFFLINE = "offline"
    TIMEOUT = "timeout"
    SSRF_BLOCKED = "ssrf_blocked"
    RESPONSE_TOO_LARGE = "response_too_large"
    ROBOTS_DISALLOWED = "robots_disallowed"
    ROBOTS_DENIED = "robots_disallowed"
    CAPTCHA_DETECTED = "captcha_detected"
    REDIRECT_LIMIT = "redirect_limit"
    HTTP_4XX = "http_4xx"
    HTTP_5XX = "http_5xx"
    CIRCUIT_OPEN = "circuit_open"
    RENDERER_UNAVAILABLE = "renderer_unavailable"
    RENDER_DISABLED = "renderer_unavailable"
    RENDER_CONNECT_FAILED = "render_connect_failed"
    PROVIDER_UNCONFIGURED = "provider_unconfigured"
    NO_PROVIDER = "provider_unconfigured"
    RETRY_EXHAUSTED = "retry_exhausted"
    PARSE_ERROR = "parse_error"
    INVALID_URL = "invalid_url"
    INVALID_INPUT = "invalid_input"
    CRAWL_TIMEOUT = "crawl_timeout"


class _FrozenModel(BaseModel):
    """Common immutable Pydantic configuration for transport models."""

    model_config = ConfigDict(frozen=True)


class Link(_FrozenModel):
    """One absolute hyperlink discovered in a parsed page."""

    href: str
    text: str = ""

    def __str__(self) -> str:
        """Return the URL for compatibility with string-oriented callers."""
        return self.href

    def __contains__(self, value: str) -> bool:
        """Support bounded substring checks without discarding link metadata."""
        return value in self.href

    def __eq__(self, other: object) -> bool:
        """Compare directly with URLs while retaining normal model equality."""
        if isinstance(other, str):
            return self.href == other
        return super().__eq__(other)

    def __hash__(self) -> int:
        """Hash the immutable public fields."""
        return hash((self.href, self.text))


class ParsedPage(_FrozenModel):
    """Bounded visible content extracted with the standard-library parser."""

    url: str = ""
    title: str | None = None
    text: str = ""
    links: list[Link] = Field(default_factory=list)
    meta: dict[str, str] = Field(default_factory=dict)
    headings: list[str] = Field(default_factory=list)
    lang: str | None = None
    status: int | None = None

    def normalized_title(self) -> str | None:
        """Return a whitespace-normalized title for legacy parser callers."""
        if self.title is None:
            return None
        title = " ".join(self.title.split())
        return title or None


class SearchHit(_FrozenModel):
    """One operator-provider search result."""

    url: str
    title: str = ""
    snippet: str = ""


class GatheredPage(_FrozenModel):
    """One successful or failed page inside a search or crawl aggregate."""

    url: str
    ok: bool
    status: int | None = None
    title: str | None = None
    text: str | None = None
    error: WebError | None = None
    detail: str | None = None


class BlockSignal(_FrozenModel):
    """Advisory challenge signal; detection never authorizes a bypass."""

    vendor: str
    kind: str
    status: int
    evidence: str
    retry_after: float | None = None


class WebResult(_FrozenModel):
    """Unified structured result returned across the model-facing boundary."""

    ok: bool
    url: str = ""
    final_url: str | None = None
    status: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    parsed: ParsedPage | None = None
    results: list[GatheredPage] | None = None
    hits: list[SearchHit] = Field(default_factory=list)
    pages: list[ParsedPage] = Field(default_factory=list)
    visited: list[str] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)
    gathered: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)
    error: WebError | None = None
    detail: str | None = None
    html: str | None = None
    elapsed_ms: float = 0.0
    truncated: bool = False
    stats: dict[str, int] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class RawFetchResult(WebResult):
    """Compatibility name for a structured raw fetch result."""


class SearchResult(WebResult):
    """Compatibility name for a structured search-gather result."""


class RenderResult(WebResult):
    """Compatibility name for a structured offline render result."""


class CrawlResult(WebResult):
    """Compatibility name for a structured bounded crawl result."""


class CaptchaSignal(BlockSignal):
    """Compatibility name for an advisory challenge signal."""


__all__ = [
    "BlockSignal",
    "CaptchaSignal",
    "CrawlResult",
    "GatheredPage",
    "Link",
    "ParsedPage",
    "RawFetchResult",
    "RenderResult",
    "SearchHit",
    "SearchResult",
    "WebError",
    "WebResult",
]
