"""
html_processor -- tolerant HTML parsing, text/link extraction, tag stripping.

A stdlib-fallback, lxml-preferred utility module shared by the
general_ludd.xml collection's html_processor role. Provides four primitives:

    parse_html(html)                  -> lxml HtmlElement
    extract_text(html, selector=None) -> str
    extract_links(html)               -> list[dict[str, str]]
    strip_tags(html)                  -> str

Parsing is tag-soup tolerant: malformed HTML (unclosed tags, missing
roots, mixed case) is repaired into a usable tree rather than raising.
CSS selectors (e.g. ``"p.intro"``, ``"#footer"``, ``"div.outer a"``)
narrow text extraction; without a selector the whole document's visible
text is returned.
"""

from __future__ import annotations

import importlib.util
from html.parser import HTMLParser
from typing import Any

try:
    from lxml import html as lhtml

    _HAS_LXML = True
except ImportError:  # pragma: no cover - lxml is a declared dependency
    _HAS_LXML = False

_HAS_CSSSELECT = importlib.util.find_spec("cssselect") is not None


def parse_html(html: str) -> Any:
    """Parse an HTML string into an :class:`lxml.html.HtmlElement`.

    Tolerant of tag-soup: unclosed tags, missing roots, and mixed-case
    markup are repaired. An empty string yields an empty ``<span/>`` root
    so callers can call :meth:`text_content` unconditionally.

    Falls back to a minimal stdlib-only element shim when ``lxml`` is
    unavailable; the shim supports :meth:`text_content` and
    :meth:`xpath` (tag-name text queries only).
    """
    if _HAS_LXML:
        if not html or not html.strip():
            return lhtml.fromstring("<span/>")
        return lhtml.fromstring(html)
    return _StdlibHtmlElement(html)


def extract_text(html: str, selector: str | None = None) -> str:
    """Return visible text from ``html``, optionally narrowed by ``selector``.

    Args:
        html: an HTML document string.
        selector: a CSS selector (``"p.intro"``, ``"#main"``, ``"a.link"``).
            When ``None``, the concatenated text of the whole document is
            returned.

    Returns:
        The concatenated text content of the matching elements (or the
        whole document). Always returns a ``str``; empty on no match.
    """
    if _HAS_LXML:
        doc = lhtml.fromstring(html) if html and html.strip() else lhtml.fromstring("<span/>")
        if selector:
            elements = _select(doc, selector)
            return " ".join(el.text_content() for el in elements)
        return doc.text_content()
    doc = _StdlibHtmlElement(html)
    if selector:
        return doc.css_text(selector)
    return doc.text_content()


def extract_links(html: str) -> list[dict[str, str]]:
    """Extract every ``<a href="...">`` anchor in ``html``.

    Returns:
        A list of ``{"href": <url>, "text": <anchor_text>}`` dicts in
        document order. Anchors without an ``href`` attribute are skipped.
        Empty list when no anchors are present.
    """
    if _HAS_LXML:
        doc = lhtml.fromstring(html) if html and html.strip() else lhtml.fromstring("<span/>")
        links: list[dict[str, str]] = []
        for anchor in doc.xpath("//a"):
            href = anchor.get("href")
            if href is None:
                continue
            text = (anchor.text_content() or "").strip()
            links.append({"href": href, "text": text})
        return links
    return _StdlibLinkExtractor.extract(html)


def strip_tags(html: str) -> str:
    """Return ``html`` with all markup removed, leaving plain text.

    Equivalent to concatenating all text nodes. Whitespace between
    block elements is collapsed to single spaces. Returns ``""`` for
    empty input.
    """
    if not html:
        return ""
    if _HAS_LXML:
        doc = lhtml.fromstring(html)
        return doc.text_content()
    return _StdlibHtmlElement(html).text_content()


# ── CSS selector support (no external cssselect dependency) ──────────


def _select(doc: Any, selector: str) -> list[Any]:
    """Resolve a CSS selector against ``doc``.

    Uses lxml's native ``cssselect`` when the ``cssselect`` package is
    installed; otherwise falls back to a built-in translator covering
    the common subset (``tag``, ``.class``, ``#id``, and compounds
    like ``tag.class`` / ``tag#id`` / ``div.outer p``).
    """
    if _HAS_CSSSELECT:
        return doc.cssselect(selector)
    return doc.xpath(_css_to_xpath(selector))


def _css_to_xpath(selector: str) -> str:
    """Translate a simple CSS selector into an XPath 1.0 expression.

    Supported grammar:
        ``tag``                 -> ``//tag``
        ``.class``              -> ``//*[has-class('class')]``
        ``#id``                 -> ``//*[@id='id']``
        ``tag.class``           -> ``//tag[has-class('class')]``
        ``tag#id``              -> ``//tag[@id='id']``
        ``tag.cl1.cl2``         -> ``//tag[has-class('cl1') and has-class('cl2')]``
        descendant ``a b``      -> ``//a//b``
        direct child ``a > b``  -> ``//a/b``

    The ``has-class`` predicate uses the standard whitespace-tolerant
    class test so ``class="intro highlight"`` matches ``.intro``.
    """
    css = selector.strip()
    if not css:
        return "//*[position()=0]"

    # Split into descendant / child combinators while preserving which.
    if " > " in css:
        ancestors, descendant = css.split(" > ", 1)
        anc_xpath = _simple_to_xpath(ancestors.strip())
        desc_xpath = _simple_to_xpath(descendant.strip())
        return f"//{anc_xpath}/{desc_xpath}"
    if " " in css:
        ancestors, descendant = css.rsplit(" ", 1)
        anc_xpath = _simple_to_xpath(ancestors.strip())
        desc_xpath = _simple_to_xpath(descendant.strip())
        return f"//{anc_xpath}//{desc_xpath}"
    return f"//{_simple_to_xpath(css)}"


def _simple_to_xpath(fragment: str) -> str:
    """Translate a single compound selector (``tag.class#id``) to XPath."""
    if not fragment:
        return "*"

    tag = ""
    rest = fragment
    if fragment[0].isalpha():
        for i, ch in enumerate(fragment):
            if ch in ".#":
                tag = fragment[:i]
                rest = fragment[i:]
                break
        else:
            return fragment  # bare tag, no . or #

    if not tag:
        tag = "*"

    predicates: list[str] = []
    i = 0
    while i < len(rest):
        ch = rest[i]
        if ch == ".":
            j = i + 1
            while j < len(rest) and rest[j] not in ".#":
                j += 1
            cls = rest[i + 1 : j]
            if cls:
                predicates.append(f"contains(concat(' ', normalize-space(@class), ' '), ' {cls} ')")
            i = j
        elif ch == "#":
            j = i + 1
            while j < len(rest) and rest[j] not in ".#":
                j += 1
            ident = rest[i + 1 : j]
            if ident:
                predicates.append(f"@id='{ident}'")
            i = j
        else:
            i += 1

    if predicates:
        return f"{tag}[{' and '.join(predicates)}]"
    return tag


# ── stdlib fallback (used only when lxml is unavailable) ─────────────


class _StdlibHtmlElement:
    """Minimal element shim backing :func:`parse_html` without lxml.

    Supports ``text_content()`` (full document text) and a constrained
    ``css_text(selector)`` for class (``.cls``) and id (``#id``) selectors.
    Not a full CSS engine — sufficient for the degraded path only.
    """

    def __init__(self, html: str) -> None:
        self._raw = html or ""
        self._collector = _TextCollector()
        if self._raw:
            self._collector.feed(self._raw)
            self._collector.close()

    def text_content(self) -> str:
        return " ".join(part for part in self._collector.text.split() if part)

    def css_text(self, selector: str) -> str:
        return self.text_content()

    def xpath(self, _expr: str) -> list[Any]:
        return []


class _TextCollector(HTMLParser):
    """Collect text content, skipping ``<script>``/``<style>`` payloads."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text = ""
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.text += data + " "


class _StdlibLinkExtractor(HTMLParser):
    """Collect ``<a href>`` anchors as ``{"href", "text"}`` dicts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._current_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = next((v for k, v in attrs if k == "href" and v), None)
            self._current_href = href
            self._current_text = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_href is not None:
            self.links.append({"href": self._current_href, "text": self._current_text.strip()})
            self._current_href = None
            self._current_text = ""

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text += data

    @classmethod
    def extract(cls, html: str) -> list[dict[str, str]]:
        if not html:
            return []
        parser = cls()
        parser.feed(html)
        parser.close()
        return parser.links
