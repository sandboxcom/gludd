"""
html_css_core -- HTML5 authoring validation, CSS3 syntax checking, and
responsive design analysis.

A stdlib-only (html.parser + re) utility module shared by the
general_ludd.web collection's roles. Provides four primitives:

    validate_html(html)          -> list[str]   (issues; [] when valid)
    validate_css(css)            -> list[str]   (issues; [] when valid)
    extract_design_tokens(css)   -> dict        ({colors, fonts, spacing})
    check_responsive(css)        -> bool        (media queries present?)

All functions take the document as an in-memory string. File I/O is the
caller's responsibility; this keeps the primitives trivially testable and
composable with the collection's role scripts.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import ClassVar

# HTML5 void elements: never have closing tags or children.
VOID_ELEMENTS: frozenset[str] = frozenset(
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

_DOCTYPE = re.compile(r"<!doctype\s+html", re.IGNORECASE)
_COLOR_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_COLOR_FUNC = re.compile(r"\b(?:rgb|rgba|hsl|hsla)\s*\([^)]*\)", re.IGNORECASE)
_FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;{}]+)", re.IGNORECASE)
_SPACING_DECL = re.compile(
    r"(?:margin|padding|gap|row-gap|column-gap)\s*:\s*([^;{}]+)",
    re.IGNORECASE,
)
_SPACING_UNIT = re.compile(r"\b\d*\.?\d+(?:px|rem|em|vh|vw|vmin|vmax|%)\b", re.IGNORECASE)
_MEDIA_QUERY = re.compile(r"@media\b", re.IGNORECASE)
_DECLARATION = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;{}]+)")


class _HtmlValidator(HTMLParser):
    """Collect structural issues while streaming over an HTML document."""

    void_elements: ClassVar[frozenset[str]] = VOID_ELEMENTS

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.issues: list[str] = []
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower not in self.void_elements:
            self._stack.append(tag_lower)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing tag (e.g. <br/>, <img/>): never pushed onto the stack.
        return

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.void_elements:
            self.issues.append(f"stray end tag for void element </{tag_lower}>")
            return
        if not self._stack:
            self.issues.append(f"stray end tag </{tag_lower}>")
            return
        if self._stack[-1] == tag_lower:
            self._stack.pop()
            return
        if tag_lower in self._stack:
            while self._stack and self._stack[-1] != tag_lower:
                self.issues.append(f"unclosed tag <{self._stack[-1]}> before </{tag_lower}>")
                self._stack.pop()
            if self._stack:
                self._stack.pop()
        else:
            self.issues.append(f"stray end tag </{tag_lower}>")


def validate_html(html: str) -> list[str]:
    """Return a list of structural issues in ``html`` (``[]`` when valid).

    Detected issues: empty document, missing ``<!DOCTYPE html>``, parse
    errors, stray end tags, end tags for void elements, and tags left
    open at end-of-document.
    """
    if not html or not html.strip():
        return ["empty document"]
    issues: list[str] = []
    if not _DOCTYPE.search(html):
        issues.append("missing <!DOCTYPE html> declaration")
    parser = _HtmlValidator()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        issues.append(f"parse error: {exc}")
    issues.extend(parser.issues)
    for leftover in parser._stack:
        issues.append(f"unclosed tag <{leftover}>")
    return issues


def _strip_css_noise(css: str) -> str:
    """Remove comments and string literals so braces/semicolons inside
    them are not counted by the structural checks."""
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    no_strings = re.sub(r'"[^"]*"', '""', no_comments)
    no_strings = re.sub(r"'[^']*'", "''", no_strings)
    return no_strings


def validate_css(css: str) -> list[str]:
    """Return a list of syntax issues in ``css`` (``[]`` when valid).

    Detected issues: empty stylesheet, unbalanced braces, and
    declarations with empty values. Braces and semicolons inside
    comments or string literals are ignored.
    """
    if not css or not css.strip():
        return ["empty stylesheet"]
    issues: list[str] = []
    stripped = _strip_css_noise(css)
    opens = stripped.count("{")
    closes = stripped.count("}")
    if opens != closes:
        issues.append(f"unbalanced braces: {opens} opening vs {closes} closing")
    for prop, value in _DECLARATION.findall(stripped):
        if not value.strip():
            issues.append(f"empty value for property '{prop}'")
    return issues


def _dedup_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def extract_design_tokens(css: str) -> dict[str, list[str]]:
    """Extract color, font, and spacing tokens from ``css``.

    Returns:
        ``{"colors": [...], "fonts": [...], "spacing": [...]}`` where each
        list holds the unique tokens in first-seen order. Colors include
        hex (``#rgb`` / ``#rrggbb`` / ``#rrggbbaa``) and ``rgb()/rgba()/
        hsl()/hsla()`` functions. Fonts come from ``font-family``
        declarations. Spacing values are the unit-bearing tokens found in
        ``margin``/``padding``/``gap`` declarations.
    """
    colors: list[str] = []
    for match in _COLOR_HEX.findall(css):
        colors.append(match.lower())
    for match in _COLOR_FUNC.findall(css):
        colors.append(match.lower())

    fonts: list[str] = []
    for match in _FONT_FAMILY.findall(css):
        fonts.append(match.strip())

    spacing: list[str] = []
    for decl in _SPACING_DECL.findall(css):
        spacing.extend(_SPACING_UNIT.findall(decl))

    return {
        "colors": _dedup_preserve_order(colors),
        "fonts": _dedup_preserve_order(fonts),
        "spacing": _dedup_preserve_order(spacing),
    }


def check_responsive(css: str) -> bool:
    """Return ``True`` if ``css`` contains at least one ``@media`` rule.

    An empty stylesheet or one with no media queries returns ``False``.
    The check is case-insensitive (``@MEDIA`` is recognized).
    """
    return bool(_MEDIA_QUERY.search(css))
