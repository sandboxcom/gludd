"""
a11y_checker.py — Static HTML accessibility + visual-QA checker.

Parses an HTML file (or recursively scans a built site directory) with
stdlib html.parser only — no browser, no Playwright, no extra deps.

A11y checks:
  - lang attribute on <html>
  - <title> element present and non-empty
  - Semantic landmark elements (<main>, <nav>, <header>, <footer>)
  - Heading order (no skipped levels; h1 present)
  - Alt text on all <img> elements
  - ARIA roles / aria-label present somewhere (advisory)
  - Color-contrast heuristic: inline style="color:X;background:X" pairs

Visual / structural QA checks:
  - <meta name="viewport"> present
  - No obviously broken local asset src/href references (relative paths)
  - <link rel="stylesheet"> and <script src> references (existence check)
  - Potential overflow heuristic: style="overflow:hidden" on body/html

All findings carry:
  - check: str — identifier
  - severity: "error" | "warning" | "info"
  - message: str
  - element: str | None — the offending tag snippet (truncated)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    check: str
    severity: str  # "error" | "warning" | "info"
    message: str
    element: str | None = None


@dataclass
class A11yReport:
    file_path: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"{status} {self.file_path} "
            f"({len(self.errors)} errors, {len(self.warnings)} warnings, "
            f"{len(self.findings)} total findings)"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.file_path,
            "passed": self.passed,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "findings": [
                {
                    "check": f.check,
                    "severity": f.severity,
                    "message": f.message,
                    "element": f.element,
                }
                for f in self.findings
            ],
        }


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------

_TRUNC = 120


def _trunc(s: str) -> str:
    return s[:_TRUNC] + "..." if len(s) > _TRUNC else s


class _A11yParser(HTMLParser):
    """Single-pass HTML parser that collects structural information."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html_attrs: dict[str, str] = {}
        self.title_text: str = ""
        self._in_title: bool = False
        self.headings: list[tuple[int, str]] = []  # (level, text)
        self._heading_level: int = 0
        self._heading_buf: list[str] = []
        self.imgs: list[dict[str, str]] = []  # attrs dicts
        self.landmarks: set[str] = set()
        self.aria_present: bool = False
        self.viewport_meta: bool = False
        self.local_assets: list[str] = []  # relative href/src values
        self.inline_style_pairs: list[tuple[str, str, str]] = []  # (tag_snippet, color, bg)
        self.overflow_suppressed: bool = False

    # -- helpers -------------------------------------------------------------

    def _attrs_to_str(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        parts = [f"<{tag}"]
        for k, v in attrs:
            parts.append(f' {k}="{v}"' if v is not None else f" {k}")
        return _trunc("".join(parts) + ">")

    def _attr(self, attrs: list[tuple[str, str | None]], key: str) -> str:
        for k, v in attrs:
            if k == key:
                return v or ""
        return ""

    # -- tag handlers --------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        snippet = self._attrs_to_str(tag, attrs)

        def attr(k: str) -> str:
            return self._attr(attrs, k)

        # html lang
        if tag == "html":
            self.html_attrs = {k: (v or "") for k, v in attrs}

        # title
        elif tag == "title":
            self._in_title = True

        # headings
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_level = int(tag[1])
            self._heading_buf = []

        # landmarks (elements or role= overrides)
        elif tag in {"main", "nav", "header", "footer", "aside", "section", "article"}:
            self.landmarks.add(tag)

        # img
        elif tag == "img":
            d: dict[str, str] = {k: (v or "") for k, v in attrs}
            d["_snippet"] = snippet
            self.imgs.append(d)

        # viewport meta
        elif tag == "meta":
            name = attr("name").lower()
            if name == "viewport":
                self.viewport_meta = True

        # local assets (href / src that are relative, not http/https/data/# etc.)
        if tag in {"link", "script", "img", "source"}:
            for key in ("href", "src"):
                val = attr(key)
                if val and not re.match(r"^(https?://|data:|#|//|mailto:)", val):
                    self.local_assets.append(val)

        # ARIA
        for k, _v in attrs:
            if k.startswith("aria-") or k == "role":
                self.aria_present = True
                break

        # role= on any tag as landmark
        role = attr("role")
        if role in {"main", "navigation", "banner", "contentinfo", "complementary"}:
            self.landmarks.add(f"role={role}")

        # inline style color-contrast heuristic
        style = attr("style")
        if style:
            color = _extract_css_prop(style, "color")
            bg = _extract_css_prop(style, "background") or _extract_css_prop(
                style, "background-color"
            )
            if color and bg:
                self.inline_style_pairs.append((snippet, color, bg))

            # overflow heuristic on body/html
            if tag in {"body", "html"}:
                ov = _extract_css_prop(style, "overflow")
                if ov in {"hidden", "clip"}:
                    self.overflow_suppressed = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            text = "".join(self._heading_buf).strip()
            self.headings.append((self._heading_level, text))
            self._heading_level = 0
            self._heading_buf = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_text += data
        if self._heading_level:
            self._heading_buf.append(data)


# ---------------------------------------------------------------------------
# CSS helpers
# ---------------------------------------------------------------------------

def _extract_css_prop(style: str, prop: str) -> str:
    """Extract a CSS property value from an inline style string."""
    m = re.search(
        r"(?:^|;)\s*" + re.escape(prop) + r"\s*:\s*([^;]+)", style, re.IGNORECASE
    )
    return m.group(1).strip() if m else ""


_LOW_CONTRAST_PAIRS: set[tuple[str, str]] = {
    ("white", "white"),
    ("white", "#ffffff"),
    ("#ffffff", "white"),
    ("#ffffff", "#ffffff"),
    ("black", "black"),
    ("black", "#000000"),
    ("#000000", "black"),
    ("#000000", "#000000"),
    ("yellow", "white"),
    ("white", "yellow"),
    ("lightgray", "white"),
    ("white", "lightgray"),
    ("#cccccc", "white"),
    ("white", "#cccccc"),
}


def _low_contrast(color: str, bg: str) -> bool:
    """Heuristic: flag clearly-same or known-bad color/bg pairs."""
    c = color.lower().strip()
    b = bg.lower().strip()
    # same color = zero contrast
    if c == b:
        return True
    return (c, b) in _LOW_CONTRAST_PAIRS or (b, c) in _LOW_CONTRAST_PAIRS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_html(html_source: str, file_path: str = "<string>") -> A11yReport:
    """Run a11y + visual-QA checks on an HTML string.

    Args:
        html_source: Raw HTML content.
        file_path: Label used in the report (e.g. path to the file).

    Returns:
        A11yReport with all findings.
    """
    report = A11yReport(file_path=file_path)
    p = _A11yParser()
    p.feed(html_source)

    findings = report.findings

    # -- A11Y CHECKS ---------------------------------------------------------

    # 1. lang attribute on <html>
    lang = p.html_attrs.get("lang", "").strip()
    if not lang:
        findings.append(
            Finding(
                check="lang-attribute",
                severity="error",
                message="<html> element is missing a lang attribute",
            )
        )

    # 2. <title> present and non-empty
    if not p.title_text.strip():
        findings.append(
            Finding(
                check="title-element",
                severity="error",
                message="<title> element is missing or empty",
            )
        )

    # 3. Semantic landmarks
    required_landmarks = {"main", "nav", "header", "footer"}
    # Accept role= equivalents too
    resolved: set[str] = set()
    for lm in p.landmarks:
        if lm.startswith("role="):
            role_val = lm[5:]
            mapping = {
                "main": "main",
                "navigation": "nav",
                "banner": "header",
                "contentinfo": "footer",
            }
            resolved.add(mapping.get(role_val, lm))
        else:
            resolved.add(lm)
    for lm in sorted(required_landmarks):
        if lm not in resolved:
            findings.append(
                Finding(
                    check="landmark-missing",
                    severity="warning",
                    message=f"No <{lm}> landmark element found (or role= equivalent)",
                )
            )

    # 4. Heading order — no skipped levels, h1 must be present
    if not p.headings:
        findings.append(
            Finding(
                check="heading-order",
                severity="warning",
                message="No heading elements found",
            )
        )
    else:
        levels = [lvl for lvl, _ in p.headings]
        if 1 not in levels:
            findings.append(
                Finding(
                    check="heading-order",
                    severity="error",
                    message="No <h1> element found — every page should have exactly one",
                )
            )
        prev = 0
        for lvl, txt in p.headings:
            if lvl > prev + 1 and prev != 0:
                findings.append(
                    Finding(
                        check="heading-order",
                        severity="error",
                        message=(
                            f"Heading level skipped: h{prev} -> h{lvl} "
                            f'(text: "{txt[:60]}")'
                        ),
                    )
                )
            prev = lvl

    # 5. Alt text on images
    for img in p.imgs:
        alt = img.get("alt")
        if alt is None:
            findings.append(
                Finding(
                    check="img-alt",
                    severity="error",
                    message="<img> missing alt attribute",
                    element=img.get("_snippet"),
                )
            )
        # alt="" is valid (decorative image) — only flag if truly missing

    # 6. ARIA presence (advisory)
    if not p.aria_present:
        findings.append(
            Finding(
                check="aria-presence",
                severity="info",
                message=(
                    "No ARIA attributes (aria-*, role=) found. "
                    "Consider adding for enhanced screen-reader support."
                ),
            )
        )

    # 7. Color-contrast heuristic (inline styles)
    for snippet, color, bg in p.inline_style_pairs:
        if _low_contrast(color, bg):
            findings.append(
                Finding(
                    check="color-contrast",
                    severity="warning",
                    message=(
                        f"Potential low-contrast inline style: "
                        f"color={color!r} background={bg!r}"
                    ),
                    element=snippet,
                )
            )

    # -- VISUAL QA CHECKS ----------------------------------------------------

    # 8. viewport meta
    if not p.viewport_meta:
        findings.append(
            Finding(
                check="viewport-meta",
                severity="warning",
                message='Missing <meta name="viewport"> — page may not scale on mobile',
            )
        )

    # 9. Overflow heuristic
    if p.overflow_suppressed:
        findings.append(
            Finding(
                check="overflow-heuristic",
                severity="info",
                message=(
                    "overflow:hidden on <body> or <html> suppresses scrollbars "
                    "— verify this is intentional"
                ),
            )
        )

    # 10. Broken local asset references (static check — file must exist)
    base_dir = (
        os.path.dirname(os.path.abspath(file_path))
        if file_path != "<string>"
        else None
    )
    if base_dir:
        seen_assets: set[str] = set()
        for asset in p.local_assets:
            # Strip query/fragment
            clean = asset.split("?")[0].split("#")[0]
            if not clean or clean in seen_assets:
                continue
            seen_assets.add(clean)
            full = os.path.normpath(os.path.join(base_dir, clean))
            if not os.path.exists(full):
                findings.append(
                    Finding(
                        check="broken-asset",
                        severity="warning",
                        message=f"Referenced asset not found on disk: {asset!r}",
                        element=clean,
                    )
                )

    return report


def check_html_file(file_path: str) -> A11yReport:
    """Load an HTML file from disk and run checks."""
    path = Path(file_path)
    html_source = path.read_text(encoding="utf-8", errors="replace")
    return check_html(html_source, file_path=str(path))


def check_site_dir(site_dir: str) -> list[A11yReport]:
    """Recursively find all *.html files under site_dir and check each."""
    reports = []
    root = Path(site_dir)
    for html_file in sorted(root.rglob("*.html")):
        reports.append(check_html_file(str(html_file)))
    return reports
