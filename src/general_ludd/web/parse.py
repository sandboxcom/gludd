"""Bounded, dependency-free HTML extraction for web results."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from general_ludd.web.types import Link, ParsedPage

_SKIP_TEXT_TAGS = frozenset({"head", "script", "style", "noscript", "template"})
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_LINK_SCHEMES = frozenset({"http", "https"})


def _normalise_link(base_url: str, href: str) -> str | None:
    candidate = urljoin(base_url, href.strip())
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() not in _LINK_SCHEMES or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, ""))


class _HTMLExtractor(HTMLParser):
    """Forgiving extractor with a hard ceiling on retained links."""

    def __init__(self, *, base_url: str, max_links: int) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.max_links = max_links
        self.title_parts: list[str] = []
        self.lang: str | None = None
        self.links: list[Link] = []
        self.meta: dict[str, str] = {}
        self.headings: list[str] = []
        self.text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._heading_parts: list[str] | None = None
        self._seen_links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Capture bounded metadata and update visible-content state."""
        tag = tag.lower()
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if tag in _SKIP_TEXT_TAGS:
            self._skip_depth += 1
        if tag == "html" and self.lang is None:
            self.lang = attributes.get("lang", "").strip() or None
        if tag == "title":
            self._in_title = True
        if tag == "a" and len(self.links) < self.max_links:
            href = _normalise_link(self.base_url, attributes.get("href", ""))
            if href and href not in self._seen_links:
                self._seen_links.add(href)
                self.links.append(Link(href=href))
        if tag == "meta":
            key = attributes.get("name") or attributes.get("property")
            content = attributes.get("content")
            if key and content is not None:
                self.meta[key.strip().lower()] = content.strip()
            if attributes.get("http-equiv", "").lower() == "content-language" and self.lang is None:
                self.lang = attributes.get("content", "").strip() or None
        if tag == "link" and "canonical" in attributes.get("rel", "").lower().split():
            canonical = _normalise_link(self.base_url, attributes.get("href", ""))
            if canonical:
                self.meta["canonical"] = canonical
        if tag in _HEADING_TAGS:
            self._heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        """Close visible-content scopes without trusting balanced markup."""
        tag = tag.lower()
        if tag in _SKIP_TEXT_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in _HEADING_TAGS and self._heading_parts is not None:
            heading = " ".join("".join(self._heading_parts).split())
            if heading:
                self.headings.append(heading)
            self._heading_parts = None

    def handle_data(self, data: str) -> None:
        """Collect title, heading, and visible body text."""
        if self._in_title:
            self.title_parts.append(data)
        if self._heading_parts is not None:
            self._heading_parts.append(data)
        if self._skip_depth == 0:
            visible = data.strip()
            if visible:
                self.text_parts.append(visible)

    def page(self, *, status: int | None) -> ParsedPage:
        """Freeze the retained bounded state as a transport model."""
        title = " ".join("".join(self.title_parts).split()) or None
        return ParsedPage(
            url=self.base_url,
            title=title,
            text=" ".join(self.text_parts),
            links=self.links,
            meta=self.meta,
            headings=self.headings,
            lang=self.lang,
            status=status,
        )


def parse_html(
    body: str,
    *,
    base_url: str = "",
    status: int | None = None,
    rich: bool = False,
    max_links: int = 100,
) -> ParsedPage:
    """Extract visible HTML fields using a tolerant standard-library parser.

    ``rich`` is accepted for compatibility but never loads an optional parser;
    the bounded standard-library behavior is deterministic in every deployment.
    """
    del rich
    if not isinstance(body, str):
        raise TypeError("body must be a string")
    if isinstance(max_links, bool) or not isinstance(max_links, int) or not 1 <= max_links <= 500:
        raise ValueError("max_links must be an integer between 1 and 500")
    extractor = _HTMLExtractor(base_url=base_url, max_links=max_links)
    try:
        extractor.feed(body)
        extractor.close()
    except (AssertionError, ValueError):
        # HTMLParser is deliberately tolerant; preserve whatever it extracted if
        # a malformed token still reaches one of its defensive assertions.
        pass
    return extractor.page(status=status)


__all__ = ["parse_html"]
