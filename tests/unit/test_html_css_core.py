"""TDD tests for the html_css_core module_util.

Tests the module at:
``collections/ansible_collections/general_ludd/web/plugins/module_utils/html_css_core.py``

Imports directly via :mod:`importlib` from its file path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "web"
    / "plugins"
    / "module_utils"
    / "html_css_core.py"
)


def _load_module() -> Any:
    """Import html_css_core.py from disk, bypassing ansible collection path."""
    spec = importlib.util.spec_from_file_location("html_css_core", str(MODULE_PATH))
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["html_css_core"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
validate_html = _mod.validate_html
validate_css = _mod.validate_css
extract_design_tokens = _mod.extract_design_tokens
check_responsive = _mod.check_responsive
MAX_INPUT_CHARS = _mod.MAX_INPUT_CHARS


def test_module_utils_package_exports_public_api() -> None:
    """The additive package initializer exposes the reconciled public helpers."""
    package_path = MODULE_PATH.parent / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "html_css_module_utils",
        str(package_path),
        submodule_search_locations=[str(MODULE_PATH.parent)],
    )
    assert spec is not None
    assert spec.loader is not None
    package = importlib.util.module_from_spec(spec)
    sys.modules["html_css_module_utils"] = package
    spec.loader.exec_module(package)
    assert package.__all__ == [
        "check_responsive",
        "extract_design_tokens",
        "validate_css",
        "validate_html",
    ]


# ── validate_html ────────────────────────────────────────────────────

DOCTYPE = "<!DOCTYPE html>"


def test_validate_html_well_formed_returns_empty() -> None:
    """A valid HTML5 document with a main landmark produces no issues."""
    html = f"{DOCTYPE}<html><body><main><h1>Title</h1><p>ok</p></main></body></html>"
    assert validate_html(html) == []


def test_validate_html_missing_doctype() -> None:
    """A document without a DOCTYPE declaration is flagged."""
    issues = validate_html("<html><body><p>ok</p></body></html>")
    assert "missing <!DOCTYPE html> declaration" in issues


def test_validate_html_empty_string() -> None:
    """An empty document is flagged as empty."""
    assert validate_html("") == ["empty document"]


def test_validate_html_whitespace_only() -> None:
    """A whitespace-only document is flagged as empty."""
    assert validate_html("   \n  ") == ["empty document"]


def test_validate_html_unclosed_tag() -> None:
    """An inner tag left open when an outer tag closes is flagged."""
    issues = validate_html(f"{DOCTYPE}<div><p>hi</div>")
    assert any("unclosed tag <p>" in i for i in issues)


def test_validate_html_stray_end_tag() -> None:
    """An end tag with no matching open tag is flagged as stray."""
    issues = validate_html(f"{DOCTYPE}<div></div></span>")
    assert any("stray end tag" in i and "span" in i for i in issues)


def test_validate_html_void_element_end_tag() -> None:
    """An end tag for a void element (e.g. </br>) is flagged."""
    issues = validate_html(f"{DOCTYPE}<br></br>")
    assert any("void element" in i and "br" in i for i in issues)


def test_validate_html_self_closing_void_not_flagged() -> None:
    """Self-closing void elements (<img/>, <br/>) are never flagged as unclosed."""
    html = f"{DOCTYPE}<html><body><img src='x.png'/><br/></body></html>"
    assert all("unclosed" not in i for i in validate_html(html))


def test_validate_html_flags_image_without_alt_text() -> None:
    """The documented accessibility check flags missing image alt text."""
    issues = validate_html(f"{DOCTYPE}<html><body><img src='hero.png'></body></html>")
    assert any("alt" in issue and "hero.png" in issue for issue in issues)


def test_validate_html_accepts_explicit_empty_alt_text() -> None:
    """Decorative images may intentionally carry an empty alt attribute."""
    html = f"{DOCTYPE}<html><body><img src='divider.png' alt=''></body></html>"
    assert all("alt" not in issue for issue in validate_html(html))


# ── validate_css ─────────────────────────────────────────────────────


def test_validate_css_well_formed_returns_empty() -> None:
    """Valid CSS with balanced braces and non-empty values yields no issues."""
    assert validate_css("div { color: red; margin: 16px; }") == []


def test_validate_css_unbalanced_braces() -> None:
    """An unclosed brace is reported as an imbalance."""
    issues = validate_css("div { color: red;")
    assert any("unbalanced braces" in i for i in issues)


def test_validate_css_empty_value() -> None:
    """A declaration with an empty value is reported."""
    issues = validate_css("div { color: ; }")
    assert any("empty value for property 'color'" in i for i in issues)


def test_validate_css_empty_string() -> None:
    """An empty stylesheet is flagged as empty."""
    assert validate_css("") == ["empty stylesheet"]


def test_validate_css_ignores_strings_and_comments() -> None:
    """Braces inside strings and comments must not unbalance the count."""
    css = 'div { content: "{}"; /* { open */ color: red; }'
    assert validate_css(css) == []


# ── extract_design_tokens ────────────────────────────────────────────


def test_extract_design_tokens_hex_colors() -> None:
    """Hex colors are collected in first-seen order."""
    tokens = extract_design_tokens("a { color: #fff; background: #ff5733; }")
    assert "#fff" in tokens["colors"]
    assert "#ff5733" in tokens["colors"]


def test_extract_design_tokens_rgb_function() -> None:
    """rgb()/rgba()/hsl() color functions are collected."""
    tokens = extract_design_tokens("a { color: rgb(255, 0, 0); }")
    assert any("rgb" in c for c in tokens["colors"])


def test_extract_design_tokens_fonts() -> None:
    """font-family declarations are collected."""
    tokens = extract_design_tokens("a { font-family: Arial, sans-serif; }")
    assert any("Arial" in f for f in tokens["fonts"])


def test_extract_design_tokens_spacing() -> None:
    """Spacing values (px/rem) from margin/padding/gap are collected."""
    tokens = extract_design_tokens("a { margin: 16px; padding: 1rem 2rem; }")
    assert "16px" in tokens["spacing"]
    assert "1rem" in tokens["spacing"]
    assert "2rem" in tokens["spacing"]


def test_extract_design_tokens_empty_css() -> None:
    """An empty stylesheet yields empty token lists."""
    assert extract_design_tokens("") == {"colors": [], "fonts": [], "spacing": []}


def test_extract_design_tokens_dedups() -> None:
    """Repeated tokens appear only once."""
    tokens = extract_design_tokens("a { color: #fff; } b { border-color: #fff; }")
    assert tokens["colors"].count("#fff") == 1


def test_extract_design_tokens_ignores_comments_and_strings() -> None:
    """Examples in CSS comments and string content are not real design tokens."""
    css = '/* color: #bad; margin: 999px; */ a { content: "#abc"; color: #fff; }'
    tokens = extract_design_tokens(css)
    assert tokens["colors"] == ["#fff"]
    assert tokens["spacing"] == []


# ── check_responsive ─────────────────────────────────────────────────


def test_check_responsive_has_media_query() -> None:
    """A stylesheet containing an @media rule is responsive."""
    assert check_responsive("@media (max-width: 768px) { a { color: red; } }") is True


def test_check_responsive_no_media_query() -> None:
    """A stylesheet without @media is not responsive."""
    assert check_responsive("a { color: red; }") is False


def test_check_responsive_empty() -> None:
    """An empty stylesheet is not responsive."""
    assert check_responsive("") is False


def test_check_responsive_case_insensitive() -> None:
    """@MEDIA in any case is detected."""
    assert check_responsive("@MEDIA screen { a { } }") is True


def test_check_responsive_ignores_commented_media_query() -> None:
    """A commented example must not make a stylesheet appear responsive."""
    assert check_responsive("/* @media (min-width: 1px) {} */ a { color: red; }") is False


@pytest.mark.parametrize(
    "operation",
    [validate_html, validate_css, extract_design_tokens, check_responsive],
)
def test_public_operations_reject_oversized_input(operation: Any) -> None:
    """Every public parser rejects input beyond the documented resource bound."""
    with pytest.raises(ValueError, match="limit"):
        operation("x" * (MAX_INPUT_CHARS + 1))
