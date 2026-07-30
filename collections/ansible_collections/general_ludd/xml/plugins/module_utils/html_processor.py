"""html_processor -- HTML parsing and extraction utilities.

A stdlib-only (html.parser + xml.etree.ElementTree) module for the
general_ludd.xml collection. Provides:

* :func:`parse_html`     -- build an Element tree from an HTML string,
                            repairing malformed markup via the lenient
                            html.parser.HTMLParser.
* :func:`extract_text`   -- return visible text, optionally filtered to
                            a single tag selector.
* :func:`extract_links`  -- collect ``{"href", "text"}`` dicts for every
                            ``<a>`` element.
* :func:`strip_tags`     -- return plain text with all markup removed.

Sibling module to :mod:`xml_core` and :mod:`xsd_generator`.
"""

from __future__ import annotations

from html.parser import HTMLParser
from xml.etree import ElementTree as ET

# HTML void elements: no closing tag, never pushed onto the open-element stack.
_VOID_ELEMENTS: frozenset[str] = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class _TreeBuildingHTMLParser(HTMLParser):
    """Build an ``xml.etree.ElementTree.Element`` tree from an HTML stream.

    The parser is deliberately lenient: unclosed tags are auto-closed when a
    sibling or ancestor end-tag is seen, void elements (br, img, ...) never
    nest, and the result is always a single synthetic root element whose
    ``.tag == "_root"``. Callers should use ``iter()`` / ``findall()`` to
    locate real content rather than relying on the root tag.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: ET.Element = ET.Element("_root")
        self._stack: list[ET.Element] = [self.root]

    # -- start / end handling --------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        el = ET.Element(tag, {k: (v if v is not None else "") for k, v in attrs})
        self._append_to_current(el)
        if tag not in _VOID_ELEMENTS:
            self._stack.append(el)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing form (<br/>) — append but never push.
        el = ET.Element(tag, {k: (v if v is not None else "") for k, v in attrs})
        self._append_to_current(el)

    def handle_endtag(self, tag: str) -> None:
        # Close the nearest matching open element; ignore stray end-tags.
        if tag in _VOID_ELEMENTS:
            return
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return
        # No match: drop silently (lenient).

    # -- text -----------------------------------------------------------

    def handle_data(self, data: str) -> None:
        current = self._stack[-1]
        if len(current):
            # Text immediately after a child element attaches as .tail
            last_child = current[-1]
            last_child.tail = (last_child.tail or "") + data
        else:
            current.text = (current.text or "") + data

    # -- helpers --------------------------------------------------------

    def _append_to_current(self, el: ET.Element) -> None:
        self._stack[-1].append(el)


def parse_html(html: str) -> ET.Element:
    """Parse an HTML string into an ``xml.etree.ElementTree.Element`` tree.

    Uses the stdlib ``html.parser.HTMLParser`` so no third-party dependency
    (lxml, beautifulsoup) is required. Malformed input — unclosed tags,
    stray end-tags, missing quotes — is repaired silently rather than
    raising.

    The returned tree always has a synthetic ``"_root"`` element at its
    base to accommodate HTML fragments that have multiple top-level
    nodes (``<h1>...</h1><p>...</p>``).

    Args:
        html: An HTML document or fragment. Empty string is permitted and
            yields an empty root with no children.

    Returns:
        An :class:`xml.etree.ElementTree.Element` whose descendants
        reflect the parsed HTML.
    """
    parser = _TreeBuildingHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.root


def extract_text(html: str, selector: str | None = None) -> str:
    """Return the visible text content of ``html``.

    Args:
        html: An HTML document or fragment.
        selector: Optional tag name (e.g. ``"p"``, ``"h1"``). When given,
            only the text inside matching elements is concatenated.
            ``None`` returns text from the entire document.

    Returns:
        The concatenated text content. For empty input, returns ``""``.
    """
    if not html:
        return ""
    root = parse_html(html)
    if selector:
        chunks: list[str] = []
        for el in root.iter(selector):
            chunks.append("".join(el.itertext()))
        return "".join(chunks)
    return "".join(root.itertext())


def extract_links(html: str) -> list[dict[str, str]]:
    """Return ``{"href", "text"}`` dicts for every ``<a>`` element in ``html``.

    An anchor with no ``href`` attribute yields ``href=""``. Anchor text
    is the concatenated visible text of the element's descendants.

    Args:
        html: An HTML document or fragment.

    Returns:
        A list of dicts in document order. Empty list for input with no
        anchors.
    """
    if not html:
        return []
    root = parse_html(html)
    links: list[dict[str, str]] = []
    for anchor in root.iter("a"):
        href = anchor.attrib.get("href", "")
        text = "".join(anchor.itertext())
        links.append({"href": href, "text": text})
    return links


def strip_tags(html: str) -> str:
    """Return ``html`` with all markup removed, preserving visible text.

    Equivalent to :func:`extract_text` with no selector — both produce
    the plain-text representation of the document. Provided as a separate
    function for callers that conceptually distinguish "give me text"
    from "give me text and let me filter".

    Args:
        html: An HTML document or fragment.

    Returns:
        The plain-text content. For empty input, returns ``""``.
    """
    if not html:
        return ""
    return "".join(parse_html(html).itertext())
