"""fetch_parsed + a stdlib ``html.parser`` extractor (NO new dependency).

Extracts title / visible text / absolute deduped links / meta / headings / lang
from an HTML body using only the standard library.  Rich extraction
(``trafilatura``) is the OPTIONAL ``[web]``-extra upgrade, lazy-imported with a
fallback to this stdlib extractor — so the module imports with nothing extra
installed.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from general_ludd.web.breaker import HostCircuitBreaker
from general_ludd.web.fetch import fetch_raw
from general_ludd.web.results import ParsedPage, RawFetchResult
from general_ludd.web.ssrf_client import DEFAULT_UA, SsrfSafeClient

#: Tags whose text content is NOT visible page content.
_SKIP_TEXT_TAGS = frozenset({"script", "style", "head", "noscript", "template"})
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


class _HtmlExtractor(HTMLParser):
    """A forgiving stdlib HTML extractor.  ``convert_charrefs`` decodes entities."""

    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title: str | None = None
        self.lang: str | None = None
        self.links: list[str] = []
        self.meta: dict[str, str] = {}
        self.headings: list[str] = []
        self._text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._cur_heading: list[str] | None = None
        self._seen_links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        adict = {k.lower(): (v or "") for k, v in attrs}
        if tag in _SKIP_TEXT_TAGS:
            self._skip_depth += 1
        if tag == "html" and "lang" in adict and not self.lang:
            self.lang = adict["lang"].strip() or None
        if tag == "title":
            self._in_title = True
        if tag == "a" and adict.get("href"):
            href = adict["href"].strip()
            if href and not href.lower().startswith(("javascript:", "mailto:", "#")):
                absolute = urljoin(self.base_url, href)
                if absolute not in self._seen_links:
                    self._seen_links.add(absolute)
                    self.links.append(absolute)
        if tag == "meta":
            key = adict.get("name") or adict.get("property")
            if key and "content" in adict:
                self.meta[key.strip().lower()] = adict["content"].strip()
            # <html lang> fallback via <meta http-equiv content-language>.
            if (
                adict.get("http-equiv", "").lower() == "content-language"
                and not self.lang
            ):
                self.lang = adict.get("content", "").strip() or None
        if tag in _HEADING_TAGS:
            self._cur_heading = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TEXT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in _HEADING_TAGS and self._cur_heading is not None:
            text = " ".join("".join(self._cur_heading).split())
            if text:
                self.headings.append(text)
            self._cur_heading = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            cur = (self.title or "") + data
            self.title = cur
        if self._cur_heading is not None:
            self._cur_heading.append(data)
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._text_parts.append(stripped)

    @property
    def text(self) -> str:
        return " ".join(self._text_parts)

    def normalized_title(self) -> str | None:
        if self.title is None:
            return None
        t = " ".join(self.title.split())
        return t or None


def _content_type(headers: dict[str, str]) -> str:
    return (headers.get("content-type") or headers.get("Content-Type") or "").lower()


def parse_html(body: str, *, base_url: str = "") -> _HtmlExtractor:
    """Parse ``body`` with the stdlib extractor (separated for unit testing)."""
    extractor = _HtmlExtractor(base_url=base_url)
    try:
        extractor.feed(body)
        extractor.close()
    except Exception:
        # html.parser is forgiving; guard anyway so parsing never raises out.
        pass
    return extractor


def _to_parsed(raw: RawFetchResult) -> ParsedPage:
    """Map a RawFetchResult into a ParsedPage, parsing only on an OK HTML body."""
    if not raw.ok:
        return ParsedPage(
            ok=False,
            error=raw.error,
            detail=raw.detail,
            status=raw.status,
            final_url=raw.final_url,
            captcha=raw.captcha,
        )
    ctype = _content_type(raw.headers)
    if ctype and "html" not in ctype and "xml" not in ctype:
        # Non-HTML: return the body as text, no link/meta extraction.
        return ParsedPage(
            ok=True,
            status=raw.status,
            final_url=raw.final_url,
            text=raw.body,
            captcha=raw.captcha,
        )
    ex = parse_html(raw.body, base_url=raw.final_url or "")
    return ParsedPage(
        ok=True,
        status=raw.status,
        final_url=raw.final_url,
        title=ex.normalized_title(),
        text=ex.text,
        links=ex.links,
        meta=ex.meta,
        headings=ex.headings,
        lang=ex.lang,
        captcha=raw.captcha,
    )


def fetch_parsed(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    transport: httpx.BaseTransport | None = None,
    client: SsrfSafeClient | None = None,
    breaker: HostCircuitBreaker | None = None,
    user_agent: str = DEFAULT_UA,
) -> ParsedPage:
    """Fetch + parse ``url`` into a structured :class:`ParsedPage`.

    Propagates a structured error (SSRF/offline/timeout/captcha/4xx) unchanged
    when the underlying fetch is not ok; never raises.
    """
    raw = fetch_raw(
        url,
        method=method,
        headers=headers,
        timeout=timeout,
        transport=transport,
        client=client,
        breaker=breaker,
        user_agent=user_agent,
    )
    return _to_parsed(raw)
