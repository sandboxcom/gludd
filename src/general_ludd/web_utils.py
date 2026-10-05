"""Shared Python utility module for web design operations.

CSS parsing, HTML validation, JavaScript syntax checking, accessibility
analysis, design-token generation, and framework boilerplate.  Uses stdlib
only where possible (html.parser, json, re, colorsys).
"""

from __future__ import annotations

import colorsys
import html.parser
import json
import re
from typing import Any, cast

# ---------------------------------------------------------------------------
# HTML / CSS
# ---------------------------------------------------------------------------


def validate_html(html_content: str) -> list[str]:
    """Validate HTML content and return a list of issues found."""
    issues: list[str] = []

    class _Validator(html.parser.HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.open_tags: list[str] = []
            self.void_tags: frozenset[str] = frozenset({
                "area", "base", "br", "col", "embed", "hr", "img", "input",
                "link", "meta", "param", "source", "track", "wbr",
            })

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag not in self.void_tags:
                self.open_tags.append(tag)

        def handle_endtag(self, tag: str) -> None:
            if tag in self.void_tags:
                return
            if not self.open_tags:
                issues.append(f"Unexpected closing tag </{tag}>")
                return
            if tag != self.open_tags[-1]:
                issues.append(f"Mismatched tag: </{tag}> (expected </{self.open_tags[-1]}>")
            self.open_tags.pop()

        def handle_data(self, data: str) -> None:
            pass

        def handle_charref(self, name: str) -> None:
            pass

        def handle_entityref(self, name: str) -> None:
            pass

    parser = _Validator()
    try:
        parser.feed(html_content)
        parser.close()
    except Exception as exc:
        issues.append(f"Parse error: {exc}")
    for tag in reversed(parser.open_tags):
        issues.append(f"Unclosed tag: <{tag}>")
    return issues


def parse_css(css_content: str) -> dict[str, dict[str, str]]:
    """Parse CSS content into a dict of selector → declaration map."""
    result: dict[str, dict[str, str]] = {}
    cleaned = _strip_css_comments(css_content)
    for match in re.finditer(
        r"([^{]+)\{([^}]*)\}",
        cleaned,
        re.DOTALL,
    ):
        selector = match.group(1).strip()
        declarations: dict[str, str] = {}
        body = match.group(2)
        for decl in body.split(";"):
            decl = decl.strip()
            if ":" not in decl:
                continue
            prop, _, val = decl.partition(":")
            prop = prop.strip().lower()
            val = val.strip()
            if prop:
                declarations[prop] = val
        result[selector] = declarations
    return result


def extract_media_queries(css_content: str) -> list[dict[str, Any]]:
    """Extract media queries with their breakpoints and contained rules."""
    cleaned = _strip_css_comments(css_content)
    queries: list[dict[str, Any]] = []
    for match in re.finditer(
        r"@media\s+([^{}]+)\{(.*?)\}",
        cleaned,
        re.DOTALL,
    ):
        condition = match.group(1).strip()
        body = match.group(2)
        rules = parse_css(body)
        breakpoint_val = _extract_breakpoint_value(condition)
        queries.append({
            "condition": condition,
            "feature": condition,
            "breakpoint": breakpoint_val,
            "rules": rules,
        })
    return queries


def check_responsive_patterns(css_content: str) -> dict[str, bool]:
    """Check CSS for responsive design patterns (grid, flexbox, media queries)."""
    cleaned = _strip_css_comments(css_content).lower()
    return {
        "uses_grid": bool(re.search(r"display\s*:\s*grid", cleaned)),
        "uses_flexbox": bool(re.search(r"display\s*:\s*flex", cleaned)),
        "uses_media_queries": bool(re.search(r"@media\b", cleaned)),
        "uses_container_queries": bool(re.search(r"@container\b", cleaned)),
        "uses_clamp": bool(re.search(r"\bclamp\s*\(", cleaned)),
        "uses_viewport_units": bool(re.search(r"\d+\s*v[whm][abx]?\b", cleaned)),
        "uses_calc": bool(re.search(r"\bcalc\s*\(", cleaned)),
        "uses_min_max": bool(re.search(r"\bminmax\s*\(", cleaned)),
    }


def generate_boilerplate(page_type: str = "html5") -> str:
    """Generate a basic HTML boilerplate."""
    if page_type == "html5":
        return (
            '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
            '  <meta charset="UTF-8">\n'
            '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
            "  <title>Page</title>\n"
            "</head>\n<body>\n</body>\n</html>"
        )
    if page_type == "html4":
        return (
            '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN"\n'
            '  "http://www.w3.org/TR/html4/strict.dtd">\n'
            '<html>\n<head>\n'
            '  <meta http-equiv="Content-Type" content="text/html; charset=utf-8">\n'
            "  <title>Page</title>\n"
            "</head>\n<body>\n</body>\n</html>"
        )
    if page_type == "xhtml":
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"\n'
            '  "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">\n'
            "<head>\n"
            '  <meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>\n'
            "  <title>Page</title>\n"
            "</head>\n<body>\n</body>\n</html>"
        )
    return generate_boilerplate("html5")


# ---------------------------------------------------------------------------
# Design Research
# ---------------------------------------------------------------------------

_COLOR_HEX = re.compile(r"#([0-9a-fA-F]{3,8})\b")
_COLOR_RGBA = re.compile(
    r"rgba?\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)",
)
_COLOR_HSL = re.compile(
    r"hsla?\s*\(\s*(\d+)\s*,\s*(\d+)%\s*,\s*(\d+)%\s*(?:,\s*([\d.]+)\s*)?\)",
)
_COLOR_NAMED: dict[str, str] = {
    "aliceblue": "#f0f8ff", "antiquewhite": "#faebd7", "aqua": "#00ffff",
    "aquamarine": "#7fffd4", "azure": "#f0ffff", "beige": "#f5f5dc",
    "bisque": "#ffe4c4", "black": "#000000", "blanchedalmond": "#ffebcd",
    "blue": "#0000ff", "blueviolet": "#8a2be2", "brown": "#a52a2a",
    "burlywood": "#deb887", "cadetblue": "#5f9ea0", "chartreuse": "#7fff00",
    "chocolate": "#d2691e", "coral": "#ff7f50", "cornflowerblue": "#6495ed",
    "cornsilk": "#fff8dc", "crimson": "#dc143c", "cyan": "#00ffff",
    "darkblue": "#00008b", "darkcyan": "#008b8b", "darkgoldenrod": "#b8860b",
    "darkgray": "#a9a9a9", "darkgreen": "#006400", "darkkhaki": "#bdb76b",
    "darkmagenta": "#8b008b", "darkolivegreen": "#556b2f", "darkorange": "#ff8c00",
    "darkorchid": "#9932cc", "darkred": "#8b0000", "darksalmon": "#e9967a",
    "darkseagreen": "#8fbc8f", "darkslateblue": "#483d8b", "darkslategray": "#2f4f4f",
    "darkturquoise": "#00ced1", "darkviolet": "#9400d3", "deeppink": "#ff1493",
    "deepskyblue": "#00bfff", "dimgray": "#696969", "dodgerblue": "#1e90ff",
    "firebrick": "#b22222", "floralwhite": "#fffaf0", "forestgreen": "#228b22",
    "fuchsia": "#ff00ff", "gainsboro": "#dcdcdc", "ghostwhite": "#f8f8ff",
    "gold": "#ffd700", "goldenrod": "#daa520", "gray": "#808080",
    "green": "#008000", "greenyellow": "#adff2f", "honeydew": "#f0fff0",
    "hotpink": "#ff69b4", "indianred": "#cd5c5c", "indigo": "#4b0082",
    "ivory": "#fffff0", "khaki": "#f0e68c", "lavender": "#e6e6fa",
    "lavenderblush": "#fff0f5", "lawngreen": "#7cfc00", "lemonchiffon": "#fffacd",
    "lightblue": "#add8e6", "lightcoral": "#f08080", "lightcyan": "#e0ffff",
    "lightgoldenrodyellow": "#fafad2", "lightgray": "#d3d3d3", "lightgreen": "#90ee90",
    "lightpink": "#ffb6c1", "lightsalmon": "#ffa07a", "lightseagreen": "#20b2aa",
    "lightskyblue": "#87cefa", "lightslategray": "#778899", "lightsteelblue": "#b0c4de",
    "lightyellow": "#ffffe0", "lime": "#00ff00", "limegreen": "#32cd32",
    "linen": "#faf0e6", "magenta": "#ff00ff", "maroon": "#800000",
    "mediumaquamarine": "#66cdaa", "mediumblue": "#0000cd", "mediumorchid": "#ba55d3",
    "mediumpurple": "#9370db", "mediumseagreen": "#3cb371", "mediumslateblue": "#7b68ee",
    "mediumspringgreen": "#00fa9a", "mediumturquoise": "#48d1cc",
    "mediumvioletred": "#c71585", "midnightblue": "#191970", "mintcream": "#f5fffa",
    "mistyrose": "#ffe4e1", "moccasin": "#ffe4b5", "navajowhite": "#ffdead",
    "navy": "#000080", "oldlace": "#fdf5e6", "olive": "#808000",
    "olivedrab": "#6b8e23", "orange": "#ffa500", "orangered": "#ff4500",
    "orchid": "#da70d6", "palegoldenrod": "#eee8aa", "palegreen": "#98fb98",
    "paleturquoise": "#afeeee", "palevioletred": "#db7093", "papayawhip": "#ffefd5",
    "peachpuff": "#ffdab9", "peru": "#cd853f", "pink": "#ffc0cb",
    "plum": "#dda0dd", "powderblue": "#b0e0e6", "purple": "#800080",
    "rebeccapurple": "#663399", "red": "#ff0000", "rosybrown": "#bc8f8f",
    "royalblue": "#4169e1", "saddlebrown": "#8b4513", "salmon": "#fa8072",
    "sandybrown": "#f4a460", "seagreen": "#2e8b57", "seashell": "#fff5ee",
    "sienna": "#a0522d", "silver": "#c0c0c0", "skyblue": "#87ceeb",
    "slateblue": "#6a5acd", "slategray": "#708090", "snow": "#fffafa",
    "springgreen": "#00ff7f", "steelblue": "#4682b4", "tan": "#d2b48c",
    "teal": "#008080", "thistle": "#d8bfd8", "tomato": "#ff6347",
    "turquoise": "#40e0d0", "violet": "#ee82ee", "wheat": "#f5deb3",
    "white": "#ffffff", "whitesmoke": "#f5f5f5", "yellow": "#ffff00",
    "yellowgreen": "#9acd32",
}
_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;}]+)", re.IGNORECASE)
_FONT_WEIGHT_RE = re.compile(
    r"font-weight\s*:\s*(\d+|normal|bold|bolder|lighter)", re.IGNORECASE,
)
_FONT_SIZE_RE = re.compile(
    r"font-size\s*:\s*([\d.]+)\s*(px|em|rem|pt|%|vw|vh)", re.IGNORECASE,
)
_SPACING_RE = re.compile(
    r"(?:(?:margin|padding)(?:-(?:top|right|bottom|left))?"
    r"|gap|row-gap|column-gap)\s*:\s*([\d.]+)\s*(px|rem)",
    re.IGNORECASE,
)


def extract_colors_from_css(css_content: str) -> list[dict[str, str]]:
    """Extract color declarations from CSS with name, hex, and usage info."""
    cleaned = _strip_css_comments(css_content)
    seen: set[str] = set()
    results: list[dict[str, str]] = []

    for m in _COLOR_HEX.finditer(cleaned):
        raw = m.group(0).lower()
        if raw not in seen:
            seen.add(raw)
            hex_val = raw.strip("#")
            if len(hex_val) == 3:
                hex_val = "".join(c * 2 for c in hex_val)
            elif len(hex_val) == 4:
                hex_val = "".join(c * 2 for c in hex_val)
                hex_val = hex_val[:6]
            elif len(hex_val) > 6:
                hex_val = hex_val[:6]
            results.append({
                "name": _find_named_color(raw),
                "hex": f"#{hex_val[:6].upper()}",
                "value": raw,
            })

    for m in _COLOR_RGBA.finditer(cleaned):
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        m.group(4) or "1"
        raw = m.group(0).lower()
        if raw not in seen:
            seen.add(raw)
            hex_val = f"#{r:02X}{g:02X}{b:02X}"
            results.append({
                "name": _find_named_color(hex_val),
                "hex": hex_val,
                "value": raw,
            })
    for m in _COLOR_HSL.finditer(cleaned):
        h = (float(m.group(1)) % 360) / 360
        s = float(m.group(2)) / 100
        lightness = float(m.group(3)) / 100
        rf, gf, bf = colorsys.hls_to_rgb(h, lightness, s)
        raw = m.group(0).lower()
        if raw not in seen:
            seen.add(raw)
            hex_val = f"#{round(rf * 255):02X}{round(gf * 255):02X}{round(bf * 255):02X}"
            results.append({
                "name": _find_named_color(hex_val),
                "hex": hex_val,
                "value": raw,
            })

    return results
    return results


def extract_fonts_from_css(css_content: str) -> list[dict[str, Any]]:
    """Extract font-family, weight, and size declarations from CSS."""
    cleaned = _strip_css_comments(css_content)
    rules = parse_css(cleaned)
    results: list[dict[str, Any]] = []
    seen_families: set[str] = set()

    for selector, decls in rules.items():
        family_raw = decls.get("font-family", "")
        if family_raw:
            families = [
                f.strip().strip("'\"")
                for f in family_raw.split(",")
            ]
            for family in families:
                if family and family not in seen_families:
                    seen_families.add(family)
                    weight_match = _FONT_WEIGHT_RE.search(
                        "font-weight:" + decls.get("font-weight", "")
                    )
                    size_match = _FONT_SIZE_RE.search(
                        "font-size:" + decls.get("font-size", "")
                    )
                    results.append({
                        "family": family,
                        "selector": selector,
                        "weight": weight_match.group(1) if weight_match else None,
                        "size": size_match.group(1) + size_match.group(2) if size_match else None,
                    })

    return results


def extract_spacing_scale(css_content: str) -> list[int]:
    """Detect spacing values used in margin, padding, and gap declarations."""
    cleaned = _strip_css_comments(css_content)
    values: set[int] = set()
    for m in _SPACING_RE.finditer(cleaned):
        val = float(m.group(1))
        unit = m.group(2)
        px = val * 16 if unit == "rem" else val
        values.add(int(px))
    return sorted(values)


def detect_css_framework(css_content: str) -> str | None:
    """Detect whether CSS content uses Tailwind, Bootstrap, etc."""
    cleaned = css_content.lower()
    if "tailwind" in cleaned or re.search(r"\b(tw-|sm:|md:|lg:|xl:|2xl:)", cleaned):
        return "tailwind"
    if "bootstrap" in cleaned or re.search(r"\b(col-sm-|col-md-|col-lg-|btn-primary)", cleaned):
        return "bootstrap"
    if re.search(r"\b(bulma|is-primary|is-large|is-info)\b", cleaned):
        return "bulma"
    if re.search(r"\b(foundation|small-|medium-|large-\d)", cleaned):
        return "foundation"
    if re.search(r"\b(uk-|uk-hidden|uk-visible)", cleaned):
        return "uikit"
    if re.search(r"\b(mui|muibutton|makestyles)\b", cleaned):
        return "mui"
    if re.search(r"\b(chakra|chakraprovider|usecolormode)\b", cleaned):
        return "chakra"
    if re.search(r"\b(ant-btn|ant-input|antdprovider)\b", cleaned):
        return "antd"
    return None


def analyze_layout(css_content: str) -> dict[str, Any]:
    """Analyze CSS for grid areas and flex directions."""
    cleaned = _strip_css_comments(css_content)
    grid_areas: list[str] = []
    for m in re.finditer(
        r"grid-template-areas\s*:\s*([^;}]*)",
        cleaned,
        re.IGNORECASE,
    ):
        area_val = m.group(1).strip()
        grid_areas.append(area_val)

    flex_directions: dict[str, str] = {}
    rules = parse_css(cleaned)
    for selector, decls in rules.items():
        fd = decls.get("flex-direction", "")
        if fd:
            flex_directions[selector] = fd

    return {
        "grid_areas": grid_areas,
        "flex_directions": flex_directions,
    }


# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------


_JS_ISSUE_PATTERNS: list[tuple[str, str, str]] = [
    (r"==\s*(?!\=)", "warning", "Use === instead of =="),
    (r"!=\s*(?!\=)", "warning", "Use !== instead of !="),
    (r"\bvar\b", "info", "var declaration (prefer let/const)"),
    (r"console\.(log|warn|error|debug)\s*\(", "info", "Console statement left in code"),
    (r"document\.write\s*\(", "warning", "document.write() is discouraged"),
    (r"eval\s*\(", "warning", "eval() usage detected (security risk)"),
    (r"with\s*\(", "error", "with statement is forbidden in strict mode"),
    (r"setTimeout\s*\(\s*[\"']", "warning", "setTimeout with string argument (use function)"),
    (r"setInterval\s*\(\s*[\"']", "warning", "setInterval with string argument (use function)"),
    (r"innerHTML\s*=?\s*(?!.*textContent)", "info", "innerHTML usage (consider alternatives)"),
]


def check_js_syntax(js_content: str) -> list[dict[str, Any]]:
    """Check JavaScript for common syntax issues and anti-patterns."""
    issues: list[dict[str, Any]] = []
    lines = js_content.split("\n")
    for i, line in enumerate(lines, start=1):
        for pattern, severity, message in _JS_ISSUE_PATTERNS:
            if re.search(pattern, line):
                issues.append({
                    "line": i,
                    "message": message,
                    "severity": severity,
                })
    brace_depth = 0
    for i, line in enumerate(lines, start=1):
        brace_depth += line.count("{") - line.count("}")
        if brace_depth < 0:
            issues.append({
                "line": i,
                "message": "Unmatched closing brace }",
                "severity": "error",
            })
            brace_depth = 0
    if brace_depth > 0:
        issues.append({
            "line": len(lines),
            "message": f"Unclosed braces: {brace_depth} unmatched {{",
            "severity": "error",
        })
    return issues


def detect_error_patterns(js_content: str) -> list[str]:
    """Detect common JavaScript mistakes in code."""
    issues: list[str] = []
    if "typeof" in js_content and re.search(r"typeof\s+\w+\s*===\s*[\"']undefined[\"']", js_content):
        issues.append(
            "typeof checks for 'undefined': consider using optional chaining"
        )
    if re.search(r"for\s*\(\s*var\s+\w+\s+in\b", js_content):
        issues.append("for-in loop without hasOwnProperty check")
    if re.search(r"\.then\s*\(\s*function", js_content):
        issues.append("Plain .then() chains: consider async/await")
    if re.search(r"=\s*null\b", js_content) and "??" not in js_content:
        issues.append("null assignments without nullish coalescing fallback")
    return issues


def verify_source_map(source_map_path: str) -> bool:
    """Verify a .map file is valid JSON and has required source-map fields."""
    try:
        with open(source_map_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    required = {"version", "file", "mappings", "sources"}
    return required.issubset(data.keys())


# ---------------------------------------------------------------------------
# Framework
# ---------------------------------------------------------------------------


def generate_react_component(name: str, props: list[str] | None = None) -> str:
    """Generate a basic React functional component."""
    props_list = props or []
    destructure = ", ".join(props_list) if props_list else ""
    if destructure:
        destructure = f"{{ {destructure} }}"
    type_block = ""
    if props_list:
        type_lines = [f"  {p}: string;" for p in props_list]
        type_block = f"\ninterface {name}Props {{\n" + "\n".join(type_lines) + "\n}\n"
    return (
        f'import React from "react";{type_block}\n'
        f"export default function {name}({destructure}) {{\n"
        f"  return (\n"
        f'    <div className="{name.lower()}">\n'
        f"    </div>\n"
        f"  );\n"
        f"}}\n"
    )


def generate_nextjs_page(route: str, page_type: str = "ssr") -> str:
    """Generate a Next.js page component for a route."""
    component_name = "".join(
        part.capitalize() for part in route.strip("/").split("/")
    ) + "Page"
    if page_type == "static":
        return (
            f"export default function {component_name}() {{\n"
            f"  return (\n"
            f'    <div className="{component_name.lower()}">\n'
            f"      <h1>{route}</h1>\n"
            f"    </div>\n"
            f"  );\n"
            f"}}\n"
        )
    if page_type == "isr":
        return (
            f"export default async function {component_name}() {{\n"
            f"  const data = await fetch('https://api.example.com{route}').then(r => r.json());\n"
            f"  return (\n"
            f'    <div className="{component_name.lower()}">\n'
            f"      <h1>{route}</h1>\n"
            f"    </div>\n"
            f"  );\n"
            f"}}\n\n"
            f"export const revalidate = 60;\n"
        )
    return (
        f"export default async function {component_name}() {{\n"
        f"  const data = await fetch('https://api.example.com{route}').then(r => r.json());\n"
        f"  return (\n"
        f'    <div className="{component_name.lower()}">\n'
        f"      <h1>{route}</h1>\n"
        f"    </div>\n"
        f"  );\n"
        f"}}\n"
    )


def generate_htmx_template(endpoint: str, trigger: str = "click") -> str:
    """Generate an HTML template using htmx attributes."""
    return (
        f'<div hx-get="{endpoint}" hx-trigger="{trigger}" hx-swap="innerHTML">\n'
        f"  <p>Loading content from {endpoint}...</p>\n"
        f"</div>\n"
    )


def parse_graphql_schema(schema_sdl: str) -> dict[str, Any]:
    """Parse a GraphQL schema SDL into types, queries, and mutations."""
    result: dict[str, Any] = {"types": [], "queries": [], "mutations": [], "enums": [], "inputs": []}
    lines = schema_sdl.split("\n")
    current: dict[str, Any] | None = None
    current_category: str | None = None
    brace_depth = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if current is None:
            m = re.match(
                r"(type|interface|input|enum|union)\s+(\w+)\s*(.*)",
                stripped,
            )
            if m:
                kind = m.group(1)
                name = m.group(2)
                current = {"name": name, "fields": []}
                if kind == "type" and (name in ("Query", "Mutation")):
                    current_category = {
                        "Query": "queries",
                        "Mutation": "mutations",
                    }[name]
                elif kind == "enum":
                    current_category = "enums"
                elif kind == "input":
                    current_category = "inputs"
                elif kind == "union":
                    current_category = "types"
                else:
                    current_category = "types"
                brace_depth = stripped.count("{") - stripped.count("}")
                continue
            m = re.match(
                r"(schema|scalar|directive)\s+",
                stripped,
            )
            if m:
                continue
            m = re.match(r"extend\s+type\s+(\w+)", stripped)
            if m:
                current = {"name": m.group(1), "fields": []}
                current_category = (
                    "types"
                    if m.group(1) not in ("Query", "Mutation")
                    else {"Query": "queries", "Mutation": "mutations"}[m.group(1)]
                )
                brace_depth = stripped.count("{") - stripped.count("}")
                continue
        else:
            brace_depth += stripped.count("{") - stripped.count("}")
            if brace_depth <= 0:
                if current_category and current:
                    result[current_category].append(current)
                current = None
                current_category = None
                continue
            m = re.match(r"(\w+)\s*\(?([^)]*)\)?\s*:\s*(\S+)", stripped)
            if m:
                current["fields"].append({
                    "name": m.group(1),
                    "args": m.group(2).strip() if m.group(2) else "",
                    "type": m.group(3),
                })

    return result


def generate_graphql_query(entity: str, fields: list[str]) -> str:
    """Generate a GraphQL query string for an entity and its fields."""
    nested = _build_graphql_fields(fields[:5]) if len(fields) > 0 else "  id"
    return f"query {{\n  {entity} {{\n{nested}\n  }}\n}}"


def _build_graphql_fields(fields: list[str], depth: int = 0) -> str:
    indent = "    " * (depth + 1)
    result_lines: list[str] = []
    for f in fields:
        if isinstance(f, dict):
            for key, subfields in f.items():
                result_lines.append(f"{indent}{key} {{")
                result_lines.append(_build_graphql_fields(subfields, depth + 1))
                result_lines.append(f"{indent}}}")
        else:
            result_lines.append(f"{indent}{f}")
    return "\n".join(result_lines)


# ---------------------------------------------------------------------------
# UX / Accessibility
# ---------------------------------------------------------------------------


def check_color_contrast(fg_hex: str, bg_hex: str) -> dict[str, Any]:
    """Calculate WCAG contrast ratio between foreground and background colors."""
    fg_lum = _relative_luminance(fg_hex)
    bg_lum = _relative_luminance(bg_hex)
    lighter = max(fg_lum, bg_lum)
    darker = min(fg_lum, bg_lum)
    ratio = (lighter + 0.05) / (darker + 0.05)
    return {
        "ratio": round(ratio, 2),
        "aa_normal": ratio >= 4.5,
        "aa_large": ratio >= 3.0,
        "aaa_normal": ratio >= 7.0,
        "aaa_large": ratio >= 4.5,
    }


def extract_z_index_contexts(css_content: str) -> list[dict[str, Any]]:
    """Extract z-index declarations with their selectors and values."""
    cleaned = _strip_css_comments(css_content)
    rules = parse_css(cleaned)
    results: list[dict[str, Any]] = []
    for selector, decls in rules.items():
        zi = decls.get("z-index", "")
        if zi:
            try:
                val = int(zi) if zi.lstrip("-").isdigit() else zi
            except (ValueError, TypeError):
                val = zi
            pos = decls.get("position", "static")
            results.append({
                "element": selector,
                "z_index": val,
                "position": pos,
            })
    return results


def validate_heading_hierarchy(html_content: str) -> list[str]:
    """Validate heading hierarchy in HTML (h1-h6). Skips and missing h1 are flagged."""
    issues: list[str] = []
    headings = re.findall(r"<(h[1-6])[^a-zA-Z0-9][^>]*>", html_content, re.IGNORECASE)
    if not headings:
        return ["No headings found in content"]
    levels = [int(h[1]) for h in headings]
    if levels[0] != 1:
        issues.append(f"Document starts with <h{levels[0]}>, expected <h1>")
    if levels.count(1) > 1:
        issues.append("Multiple h1 headings found")
    for i in range(len(levels) - 1):
        if levels[i + 1] > levels[i] + 1:
            issues.append(
                f"Heading skip: h{levels[i]} -> h{levels[i + 1]} "
                f"(missing h{levels[i] + 1})"
            )
    return issues


_ARIA_REQUIRED: dict[str, list[str]] = {
    "combobox": ["aria-expanded", "aria-controls"],
    "slider": ["aria-valuenow", "aria-valuemin", "aria-valuemax"],
    "spinbutton": ["aria-valuenow", "aria-valuemin", "aria-valuemax"],
}
_ARIA_ATTR_RE = re.compile(r"\baria-(\w[\w-]*)\b", re.IGNORECASE)
_ARIA_ROLE_RE = re.compile(r'\brole\s*=\s*"([^"]+)"', re.IGNORECASE)


def check_aria_attributes(html_content: str) -> list[str]:
    """Check for missing labels, image alt text, and required ARIA role attrs."""
    issues: list[str] = []
    for button in re.finditer(r"<button([^>]*)>(.*?)</button>", html_content, re.IGNORECASE | re.DOTALL):
        attrs = button.group(1)
        label = re.sub(r"<[^>]+>", "", button.group(2)).strip()
        has_label_attr = re.search(r"(?:aria-label|aria-labelledby|title) *=", attrs, re.IGNORECASE)
        if not label and not has_label_attr:
            issues.append("button missing accessible label")
    for image in re.finditer(r"<img([^>]*)>", html_content, re.IGNORECASE):
        attrs = image.group(1)
        if not re.search(r"alt *=", attrs, re.IGNORECASE):
            issues.append("img missing alt attribute")
    for m_role in _ARIA_ROLE_RE.finditer(html_content):
        role = m_role.group(1).lower()
        if role not in _ARIA_REQUIRED:
            continue
        req_attrs = _ARIA_REQUIRED[role]
        pos = m_role.start()
        snippet = html_content[pos:pos + 500]
        present = {m.group(1).lower() for m in _ARIA_ATTR_RE.finditer(snippet)}
        missing = [a for a in req_attrs if a.replace("aria-", "") not in present]
        if missing:
            missing_text = ", ".join(missing)
            issues.append(f"role {role} missing ARIA attributes: {missing_text}")
    return issues


def calculate_readability(text: str) -> dict[str, Any]:
    """Calculate Flesch-Kincaid readability metrics for English text."""
    words = [w for w in re.findall(r"[a-zA-Z]+", text) if len(w) > 1]
    word_count = len(words)
    if word_count == 0:
        return {
            "grade_level": 0,
            "grade": 0,
            "level": 0,
            "reading_ease": 100,
            "score": 100,
            "words": 0,
            "sentences": 0,
        }

    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    sentence_count = max(len(sentences), 1)

    syllable_count = sum(_count_syllables(w) for w in words)
    words_per_sentence = word_count / sentence_count
    syllables_per_word = syllable_count / word_count
    flesch_ease = round(
        206.835 - 1.015 * words_per_sentence - 84.6 * syllables_per_word, 2,
    )
    grade_level = round(
        0.39 * words_per_sentence + 11.8 * syllables_per_word - 15.59, 2,
    )
    bounded_grade = max(grade_level, 0)
    bounded_ease = max(0, min(100, flesch_ease))

    return {
        "grade_level": bounded_grade,
        "grade": bounded_grade,
        "level": bounded_grade,
        "reading_ease": bounded_ease,
        "score": bounded_ease,
        "words": word_count,
        "sentences": sentence_count,
    }


# ---------------------------------------------------------------------------
# Design System
# ---------------------------------------------------------------------------


def generate_spacing_tokens(base: int = 4, steps: int = 12) -> dict[str, str]:
    """Generate spacing design tokens from zero through steps - 1."""
    tokens: dict[str, str] = {}
    for i in range(max(steps, 0)):
        val = i * base
        tokens[f"space-{i}"] = f"{val / 16}rem"
    return tokens


def generate_color_tokens(palette: dict[str, str]) -> dict[str, dict[str, str]]:
    """Generate color shade tokens from named base colors."""
    tokens: dict[str, dict[str, str]] = {}
    for name, base_hex in palette.items():
        base_rgb = _hex_to_rgb(base_hex)
        tokens[name] = {}
        colorsys.rgb_to_hls(*base_rgb)
        for i in range(1, 11):
            ratio = i / 10
            0.2 + (ratio * 1.6)
            if i <= 5:
                shade_rgb = tuple(
                    _clamp_01(c * (1 - 0.2 * (6 - i) / 5))
                    for c in base_rgb
                )
            else:
                shade_rgb = tuple(
                    _clamp_01(c + (1 - c) * 0.2 * (i - 5) / 5)
                    for c in base_rgb
                )
            tokens[name][str(i * 100)] = _rgb_to_hex(cast(tuple[float, float, float], shade_rgb))
    return tokens


def generate_type_scale(
    base_size: float = 16, ratio: float = 1.25, steps: int = 8,
) -> dict[str, dict[str, str]]:
    """Generate a modular type scale based on a ratio."""
    tokens: dict[str, dict[str, str]] = {}
    for i in range(-2, steps):
        size = round(base_size * (ratio ** i), 1)
        key = f"step-{i}" if i >= 0 else f"step-{abs(i)}"
        tokens[key] = {
            "font-size": f"{round(size / 16, 3)}rem",
            "line-height": str(round(1.1 + (0.3 / (i + 3)), 2)),
        }
    return tokens


def tokens_to_css(tokens: dict[str, Any], prefix: str = "") -> str:
    """Convert design tokens to CSS custom properties."""
    lines: list[str] = []

    def _walk(d: Any, current_path: list[str]) -> None:
        if isinstance(d, dict) and not _is_leaf_dict(d):
            for key, val in d.items():
                _walk(val, [*current_path, key])
        else:
            name = "-".join(current_path).replace(" ", "-").lower()
            full_name = f"--{prefix}{name}" if prefix else f"--{name}"
            value = str(d) if not isinstance(d, dict) else json.dumps(d)
            lines.append(f"  {full_name}: {value};")

    _walk(tokens, [])
    if not lines:
        return "  /* no tokens */"
    return "\n".join(lines)


def tokens_to_json(tokens: dict[str, Any]) -> str:
    """Convert design tokens to W3C DTCG-compatible JSON."""
    dtcg: dict[str, dict[str, Any]] = {}
    token_id = 0

    def _walk(d: dict[str, Any], path: str) -> None:
        nonlocal token_id
        for key, val in d.items():
            current_path = f"{path}.{key}" if path else key
            if isinstance(val, dict) and not _is_leaf_dict(val):
                token_id += 1
                dtcg[current_path] = {
                    "$type": "object",
                    "$value": json.dumps(val),
                    "$description": f"Design token group: {key}",
                }
                _walk(val, current_path)
            else:
                token_id += 1
                resolved_type = _dtcg_type(val) if isinstance(val, str) else "number"
                dtcg[current_path] = {
                    "$type": resolved_type,
                    "$value": val,
                    "$description": f"Design token: {key}",
                }

    _walk(tokens, "")
    return json.dumps(dtcg, indent=2)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_css_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _extract_breakpoint_value(condition: str) -> int | None:
    m = re.search(r"min-width\s*:\s*(\d+)\s*px", condition)
    if m:
        return int(m.group(1))
    m = re.search(r"max-width\s*:\s*(\d+)\s*px", condition)
    if m:
        return int(m.group(1))
    return None


def _find_named_color(hex_or_named: str) -> str:
    if hex_or_named.startswith("#"):
        lower = hex_or_named.lower()
        for name, val in _COLOR_NAMED.items():
            if val == lower:
                return name
        return ""
    return _COLOR_NAMED.get(hex_or_named, "")


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    r, g, b = (
        int(hex_color[0:2], 16) / 255.0,
        int(hex_color[2:4], 16) / 255.0,
        int(hex_color[4:6], 16) / 255.0,
    )

    def _linearize(c: float) -> float:
        if c <= 0.04045:
            return c / 12.92
        return cast(float, ((c + 0.055) / 1.055) ** 2.4)

    return 0.2126 * _linearize(r) + 0.7152 * _linearize(g) + 0.0722 * _linearize(b)


def _count_syllables(word: str) -> int:
    word = word.lower()
    if len(word) <= 2:
        return 1
    count = 0
    vowels = "aeiouy"
    if word[0] in vowels:
        count += 1
    for i in range(1, len(word)):
        if word[i] in vowels and word[i - 1] not in vowels:
            count += 1
    if word.endswith("e"):
        count -= 1
    if word.endswith("le") and len(word) > 2 and word[-3] not in vowels:
        count += 1
    return max(count, 1)


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    return (
        int(hex_color[0:2], 16) / 255.0,
        int(hex_color[2:4], 16) / 255.0,
        int(hex_color[4:6], 16) / 255.0,
    )


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return f"#{int(_clamp_01(rgb[0]) * 255):02X}{int(_clamp_01(rgb[1]) * 255):02X}{int(_clamp_01(rgb[2]) * 255):02X}"


def _clamp_01(val: float) -> float:
    return max(0.0, min(1.0, val))


def _is_leaf_dict(d: dict[str, Any]) -> bool:
    return all(
        not isinstance(v, dict) or (isinstance(v, dict) and _is_leaf_dict(v))
        for v in d.values()
    ) and len(d) > 0 and not isinstance(next(iter(d.values())), dict)


def _dtcg_type(value: str) -> str:
    if re.match(r"^\d+(\.\d+)?(rem|px|em|pt|%|vh|vw)$", value):
        return "dimension"
    if re.match(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$", value):
        return "color"
    if re.match(r"^\d+(\.\d+)?$", value):
        return "number"
    if value.lower() in ("true", "false"):
        return "boolean"
    return "string"
