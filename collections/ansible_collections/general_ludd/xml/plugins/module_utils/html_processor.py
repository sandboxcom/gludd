"""Bounded HTML tag-soup parsing and visible-content extraction.

``lxml.html`` supplies the mature parser. This module only adds project policy:
explicit resource limits, fail-closed selector validation, and visible-text
semantics that omit script and style payloads.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import html as lhtml

MAX_INPUT_CHARS = 1_000_000
MAX_SELECTOR_CHARS = 256
MAX_SELECTOR_PARTS = 8

_SIMPLE_SELECTOR = re.compile(
    r"(?P<tag>[A-Za-z][A-Za-z0-9_-]*)?(?P<qualifiers>(?:[.#][A-Za-z_][A-Za-z0-9_-]*)*)\Z"
)
_QUALIFIER = re.compile(r"([.#])([A-Za-z_][A-Za-z0-9_-]*)")


def _require_bounded(html: str) -> None:
    if len(html) > MAX_INPUT_CHARS:
        raise ValueError(f"HTML input exceeds limit of {MAX_INPUT_CHARS} characters")


def _new_parser() -> Any:
    return lhtml.HTMLParser(no_network=True, recover=True, huge_tree=False)


def parse_html(html: str) -> Any:
    """Parse a bounded HTML document or fragment into an lxml element."""
    _require_bounded(html)
    source = html if html.strip() else "<span/>"
    return lhtml.fromstring(source, parser=_new_parser())


def _simple_to_xpath(fragment: str) -> str:
    match = _SIMPLE_SELECTOR.fullmatch(fragment)
    if match is None or not fragment:
        raise ValueError(f"unsupported selector fragment: {fragment!r}")
    tag = match.group("tag") or "*"
    qualifiers = match.group("qualifiers")
    predicates: list[str] = []
    for marker, value in _QUALIFIER.findall(qualifiers):
        if marker == "#":
            predicates.append(f"@id='{value}'")
        else:
            predicates.append(f"contains(concat(' ', normalize-space(@class), ' '), ' {value} ')")
    return f"{tag}[{' and '.join(predicates)}]" if predicates else tag


def _selector_to_xpath(selector: str) -> str:
    if not selector or len(selector) > MAX_SELECTOR_CHARS:
        raise ValueError("selector is empty or exceeds the selector limit")
    tokens = selector.replace(">", " > ").split()
    simple_count = sum(token != ">" for token in tokens)
    if simple_count == 0 or simple_count > MAX_SELECTOR_PARTS:
        raise ValueError("selector has an unsupported number of parts")

    expression = ""
    axis = "//"
    expecting_selector = True
    for token in tokens:
        if token == ">":
            if expecting_selector or not expression:
                raise ValueError("selector contains an invalid child combinator")
            axis = "/"
            expecting_selector = True
            continue
        if not expecting_selector:
            axis = "//"
        expression += axis + _simple_to_xpath(token)
        axis = "//"
        expecting_selector = False
    if expecting_selector:
        raise ValueError("selector cannot end with a combinator")
    return expression


def _visible_text(element: Any) -> str:
    nodes = element.xpath(".//text()[not(ancestor::script) and not(ancestor::style)]")
    pieces = [" ".join(str(node).split()) for node in nodes]
    return " ".join(piece for piece in pieces if piece)


def extract_text(html: str, selector: str | None = None) -> str:
    """Return normalized visible text, optionally narrowed by a safe selector."""
    _require_bounded(html)
    document = parse_html(html)
    elements = [document] if selector is None else document.xpath(_selector_to_xpath(selector))
    return " ".join(text for element in elements if (text := _visible_text(element)))


def extract_links(html: str) -> list[dict[str, str]]:
    """Return navigable anchors as ``href``/visible ``text`` dictionaries."""
    _require_bounded(html)
    document = parse_html(html)
    links: list[dict[str, str]] = []
    for anchor in document.xpath("self::a | .//a"):
        href = anchor.get("href")
        if href is not None:
            links.append({"href": href, "text": _visible_text(anchor)})
    return links


def strip_tags(html: str) -> str:
    """Return normalized visible text with markup removed."""
    return extract_text(html)


__all__ = ["extract_links", "extract_text", "parse_html", "strip_tags"]
