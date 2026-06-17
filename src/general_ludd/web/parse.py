"""Stdlib-only HTML parsing -> title / visible text / links / meta / lang.

Uses ``html.parser.HTMLParser`` (stdlib) so the toolkit adds NO new hard
dependency. Rich main-content extraction (boilerplate removal) is an OPTIONAL
lazy path: if ``trafilatura`` (the ``web`` extra) is installed it is used, else
the function degrades gracefully to the stdlib parser. The lazy import lives
INSIDE the function so this module imports with only core deps present.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

from general_ludd.web.types import Link, ParsedPage

#: Tags whose text content is not visible page text.
_SKIP_TEXT_TAGS = frozenset({"script", "style", "head", "noscript", "template"})


class HTMLTextParser(HTMLParser):
    """Extract title, visible text, links, meta and lang from an HTML document.

    Best-effort and forgiving: malformed markup yields whatever could be parsed
    rather than raising. Links are resolved to absolute http(s) URLs against the
    page's base; non-http(s) hrefs (``mailto:``, ``javascript:``) are dropped.
    """

    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self.title: str | None = None
        self.lang: str | None = None
        self.meta: dict[str, str] = {}
        self.links: list[Link] = []
        self._text_parts: list[str] = []
        self._link_text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._in_anchor = False
        self._current_href: str | None = None

    # -- tag handlers ------------------------------------------------------ #
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in _SKIP_TEXT_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "html" and "lang" in a and not self.lang:
            self.lang = a["lang"].strip() or None
        elif tag == "meta":
            self._handle_meta(a)
        elif tag == "link":
            rel = a.get("rel", "").lower()
            if "canonical" in rel and a.get("href"):
                self.meta["canonical"] = self._abs(a["href"])
        elif tag == "a":
            href = a.get("href", "").strip()
            if href:
                self._in_anchor = True
                self._current_href = href
                self._link_text_parts = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing tags (e.g. <meta .../>, <link .../>) only need start logic.
        tag = tag.lower()
        if tag in {"meta", "link"}:
            self.handle_starttag(tag, attrs)
            if tag in _SKIP_TEXT_TAGS:
                self._skip_depth = max(0, self._skip_depth - 1)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TEXT_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._in_anchor:
            text = " ".join("".join(self._link_text_parts).split())
            if self._current_href:
                abs_href = self._abs(self._current_href)
                if abs_href.startswith(("http://", "https://")):
                    self.links.append(Link(href=abs_href, text=text))
            self._in_anchor = False
            self._current_href = None
            self._link_text_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            cur = (self.title or "") + data
            self.title = cur
        if self._skip_depth > 0:
            return
        if self._in_anchor:
            self._link_text_parts.append(data)
        stripped = data.strip()
        if stripped:
            self._text_parts.append(stripped)

    # -- helpers ----------------------------------------------------------- #
    def _handle_meta(self, a: dict[str, str]) -> None:
        key = a.get("name") or a.get("property") or a.get("http-equiv")
        if key and "content" in a:
            self.meta[key.lower()] = a["content"]
        # <meta charset=...> and lang via http-equiv are ignored for text here.

    def _abs(self, href: str) -> str:
        if not self._base_url:
            return href
        try:
            return urljoin(self._base_url, href)
        except ValueError:
            return href

    def _flush_pending_anchor(self) -> None:
        """Emit a still-open anchor (malformed HTML with no closing </a>)."""
        if self._in_anchor and self._current_href:
            text = " ".join("".join(self._link_text_parts).split())
            abs_href = self._abs(self._current_href)
            if abs_href.startswith(("http://", "https://")):
                self.links.append(Link(href=abs_href, text=text))
        self._in_anchor = False
        self._current_href = None
        self._link_text_parts = []

    def to_parsed_page(self, status: int | None = None) -> ParsedPage:
        """Materialize the accumulated state into a frozen :class:`ParsedPage`."""
        self._flush_pending_anchor()
        title = self.title.strip() if self.title else None
        text = " ".join(self._text_parts)
        return ParsedPage(
            title=title or None,
            text=text,
            links=list(self.links),
            meta=dict(self.meta),
            lang=self.lang,
            status=status,
        )


def parse_html(
    body: str,
    *,
    base_url: str = "",
    status: int | None = None,
    rich: bool = False,
) -> ParsedPage:
    """Parse ``body`` into a :class:`ParsedPage` (stdlib; optional rich extract).

    ``rich=True`` tries ``trafilatura`` (the ``web`` extra) for boilerplate-free
    main-content text, falling back to the stdlib visible-text on ImportError or
    any extraction failure — so the function ALWAYS returns text. The lazy import
    keeps this module import-safe with only core deps installed.
    """
    parser = HTMLTextParser(base_url=base_url)
    parser.feed(body)
    parser.close()
    page = parser.to_parsed_page(status=status)

    if rich:
        extracted = _try_rich_extract(body)
        if extracted:
            page = page.model_copy(update={"text": extracted})
    return page


def _try_rich_extract(body: str) -> str | None:
    """Lazy-import trafilatura for main-content extraction; None on any failure."""
    try:
        import trafilatura  # lazy, optional [web] extra
    except ImportError:
        return None
    try:
        return trafilatura.extract(body) or None
    except Exception:
        return None
