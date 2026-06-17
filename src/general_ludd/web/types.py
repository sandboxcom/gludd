"""Pure data models for the web toolkit — no I/O, no network, import-safe.

Every public web entrypoint (``fetch_raw`` / ``fetch_parsed`` / ``search_gather``
/ ``crawl_site`` / ``render_js``) returns a frozen :class:`WebResult` whose
``ok: bool`` is the contract the model sees. The model NEVER sees an ``httpx``
object and NEVER sees an exception — failures are mapped to a stable
:class:`WebError` enum and carried in the structured result.

Nothing in this module performs I/O, so it imports with only the core deps
installed (no playwright/lxml/trafilatura). ``WebResult.model_dump()`` produces
the JSON shape sent over ``/api/dispatch``.
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WebError(enum.StrEnum):
    """Stable, JSON-serialisable failure taxonomy for the web toolkit.

    A ``str`` enum so ``model_dump(mode="json")`` emits the bare string value and
    callers can compare against the literal without importing this module.
    """

    OFFLINE = "offline"
    TIMEOUT = "timeout"
    SSRF_BLOCKED = "ssrf_blocked"
    ROBOTS_DISALLOWED = "robots_disallowed"
    CAPTCHA_DETECTED = "captcha_detected"
    REDIRECT_LIMIT = "redirect_limit"
    HTTP_4XX = "http_4xx"
    HTTP_5XX = "http_5xx"
    CIRCUIT_OPEN = "circuit_open"
    RENDERER_UNAVAILABLE = "renderer_unavailable"
    #: DISTINCT from RENDERER_UNAVAILABLE: the renderer COULD run but the operator
    #: policy disabled it (allow_render=False). Lets a caller tell "flip the
    #: policy" apart from "install the [web] extra".
    RENDER_DISABLED = "render_disabled"
    PROVIDER_UNCONFIGURED = "provider_unconfigured"
    RETRY_EXHAUSTED = "retry_exhausted"
    PARSE_ERROR = "parse_error"


class Link(BaseModel):
    """One absolute hyperlink discovered in a parsed page."""

    model_config = ConfigDict(frozen=True)

    href: str
    text: str = ""


class ParsedPage(BaseModel):
    """Structured extraction from an HTML body via the stdlib parser.

    ``text`` is the visible text with ``<script>``/``<style>``/``<head>`` stripped;
    ``meta`` carries ``name``/``property`` -> ``content`` pairs (including
    ``canonical`` and ``og:*``). All best-effort: a malformed document degrades to
    whatever could be extracted rather than raising.
    """

    model_config = ConfigDict(frozen=True)

    title: str | None = None
    text: str = ""
    links: list[Link] = Field(default_factory=list)
    meta: dict[str, str] = Field(default_factory=dict)
    lang: str | None = None
    status: int | None = None


class GatheredPage(BaseModel):
    """One page result inside a multi-page gather/crawl aggregate.

    A per-page failure (timeout, SSRF block, captcha, robots-skip) is captured
    HERE — ``ok=False`` with an ``error`` — and the surrounding gather/crawl keeps
    going (the Observability.find resilience convention).
    """

    model_config = ConfigDict(frozen=True)

    url: str
    ok: bool
    status: int | None = None
    title: str | None = None
    text: str | None = None
    error: WebError | None = None
    detail: str | None = None


class BlockSignal(BaseModel):
    """A detected captcha / bot-block challenge (detection only, NO bypass)."""

    model_config = ConfigDict(frozen=True)

    vendor: str
    kind: str  # "captcha" | "rate_limited" | "bot_block"
    status: int
    evidence: str
    retry_after: float | None = None


class WebResult(BaseModel):
    """The single structured return type of every public web entrypoint.

    ``ok`` is the contract: ``True`` => the operation succeeded (the payload
    fields are populated); ``False`` => ``error`` (a :class:`WebError`) and
    ``detail`` explain why, and any partial data (``status``, ``final_url``) is
    still carried. The result is frozen and ``model_dump()``-able for transport.
    """

    model_config = ConfigDict(frozen=True)

    ok: bool
    url: str
    final_url: str | None = None
    status: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    parsed: ParsedPage | None = None
    results: list[GatheredPage] | None = None
    error: WebError | None = None
    detail: str | None = None
    elapsed_ms: float = 0.0
    meta: dict[str, Any] = Field(default_factory=dict)
