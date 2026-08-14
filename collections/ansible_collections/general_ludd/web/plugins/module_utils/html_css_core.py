"""Bounded HTML5 and CSS authoring checks.

The web collection intentionally uses the standard-library HTML parser and
regular expressions for these lightweight structural checks. The functions
operate on in-memory strings and reject oversized input before parsing.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import ClassVar

MAX_INPUT_CHARS = 1_000_000

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
_EMPTY_DECLARATION = re.compile(r"([a-zA-Z-]+)\s*:\s*(?=;|})")
_DOUBLE_QUOTED = re.compile(r'"(?:\\.|[^"\\])*"', re.DOTALL)
_SINGLE_QUOTED = re.compile(r"'(?:\\.|[^'\\])*'", re.DOTALL)


def _require_bounded(text: str, label: str) -> None:
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(f"{label} exceeds input limit of {MAX_INPUT_CHARS} characters")


class _HtmlValidator(HTMLParser):
    """Collect structural and image-accessibility issues from HTML tokens."""

    void_elements: ClassVar[frozenset[str]] = VOID_ELEMENTS

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.issues: list[str] = []
        self.open_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower == "img":
            attr_map = {name.lower(): value for name, value in attrs}
            if "alt" not in attr_map or attr_map["alt"] is None:
                source = attr_map.get("src") or "<unknown>"
                self.issues.append(f"image is missing alt text: {source}")
        if tag_lower not in self.void_elements:
            self.open_tags.append(tag_lower)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        tag_lower = tag.lower()
        if tag_lower not in self.void_elements and self.open_tags:
            self.open_tags.pop()

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.void_elements:
            self.issues.append(f"stray end tag for void element </{tag_lower}>")
            return
        if not self.open_tags:
            self.issues.append(f"stray end tag </{tag_lower}>")
            return
        if self.open_tags[-1] == tag_lower:
            self.open_tags.pop()
            return
        if tag_lower not in self.open_tags:
            self.issues.append(f"stray end tag </{tag_lower}>")
            return
        while self.open_tags and self.open_tags[-1] != tag_lower:
            self.issues.append(f"unclosed tag <{self.open_tags.pop()}> before </{tag_lower}>")
        self.open_tags.pop()


def validate_html(html: str) -> list[str]:
    """Return structural/accessibility issues, or an empty list when valid."""
    _require_bounded(html, "HTML document")
    if not html.strip():
        return ["empty document"]

    issues: list[str] = []
    if not _DOCTYPE.search(html):
        issues.append("missing <!DOCTYPE html> declaration")

    parser = _HtmlValidator()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # HTMLParser extension hooks may raise on malformed tokens.
        issues.append(f"parse error: {type(exc).__name__}")
    issues.extend(parser.issues)
    issues.extend(f"unclosed tag <{tag}>" for tag in reversed(parser.open_tags))
    return issues


def _strip_css_noise(css: str) -> str:
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    no_double_strings = _DOUBLE_QUOTED.sub('""', no_comments)
    return _SINGLE_QUOTED.sub("''", no_double_strings)


def validate_css(css: str) -> list[str]:
    """Return bounded structural CSS issues, or an empty list when clean."""
    _require_bounded(css, "CSS stylesheet")
    if not css.strip():
        return ["empty stylesheet"]

    issues: list[str] = []
    if css.count("/*") != css.count("*/"):
        issues.append("unbalanced CSS comments")
    stripped = _strip_css_noise(css)
    opening = stripped.count("{")
    closing = stripped.count("}")
    if opening != closing:
        issues.append(f"unbalanced braces: {opening} opening vs {closing} closing")
    for property_name in _EMPTY_DECLARATION.findall(stripped):
        issues.append(f"empty value for property '{property_name}'")
    return issues


def _deduplicate(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def extract_design_tokens(css: str) -> dict[str, list[str]]:
    """Extract unique colors, font families, and spacing values from CSS."""
    _require_bounded(css, "CSS stylesheet")
    stripped = _strip_css_noise(css)
    colors = [match.lower() for match in _COLOR_HEX.findall(stripped)]
    colors.extend(match.lower() for match in _COLOR_FUNC.findall(stripped))
    fonts = [match.strip() for match in _FONT_FAMILY.findall(stripped)]
    spacing: list[str] = []
    for declaration in _SPACING_DECL.findall(stripped):
        spacing.extend(_SPACING_UNIT.findall(declaration))
    return {
        "colors": _deduplicate(colors),
        "fonts": _deduplicate(fonts),
        "spacing": _deduplicate(spacing),
    }


def check_responsive(css: str) -> bool:
    """Return whether the bounded stylesheet contains a real media rule."""
    _require_bounded(css, "CSS stylesheet")
    return bool(_MEDIA_QUERY.search(_strip_css_noise(css)))


__all__ = ["check_responsive", "extract_design_tokens", "validate_css", "validate_html"]
