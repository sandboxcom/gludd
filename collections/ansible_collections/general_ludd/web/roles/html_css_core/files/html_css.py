#!/usr/bin/env python3
"""html_css_core — HTML5 validation, CSS syntax checking, responsive design audit."""
import argparse
import json
import re
import sys
from html.parser import HTMLParser

VALID_HTML5_SEMANTIC = {
    "header", "nav", "main", "article", "section", "aside", "footer",
    "figure", "figcaption", "details", "summary", "time", "mark", "data",
    "dialog", "template", "address", "h1", "h2", "h3", "h4", "h5", "h6",
}

ARIA_LANDMARKS = {
    "banner", "navigation", "main", "complementary", "contentinfo",
    "region", "search", "form",
}

CSS_PROPERTIES = {
    "display", "position", "top", "right", "bottom", "left", "width", "height",
    "min-width", "max-width", "min-height", "max-height", "margin", "padding",
    "border", "border-radius", "box-shadow", "opacity", "z-index", "overflow",
    "flex", "flex-direction", "flex-wrap", "flex-grow", "flex-shrink",
    "flex-basis", "justify-content", "align-items", "align-content",
    "align-self", "order", "grid", "grid-template-columns",
    "grid-template-rows", "grid-template-areas", "grid-column", "grid-row",
    "grid-area", "gap", "row-gap", "column-gap", "font-family", "font-size",
    "font-weight", "font-style", "line-height", "letter-spacing",
    "text-align", "text-decoration", "text-transform", "color",
    "background", "background-color", "background-image",
    "background-size", "background-position", "background-repeat",
    "transform", "transition", "animation", "filter", "backdrop-filter",
    "cursor", "pointer-events", "user-select", "visibility",
    "will-change", "contain", "container-type", "container-name",
    "aspect-ratio", "object-fit", "object-position", "isolation",
    "mix-blend-mode", "mask", "clip-path", "scroll-behavior",
    "scroll-snap-type", "scroll-snap-align", "overscroll-behavior",
    "accent-color", "caret-color", "color-scheme",
    "inset", "margin-block", "margin-inline", "padding-block",
    "padding-inline", "border-block", "border-inline",
    "font-variation-settings", "font-optical-sizing",
    "text-wrap", "text-wrap-mode", "text-wrap-style",
    "word-break", "overflow-wrap", "hyphens",
    "writing-mode", "direction", "text-orientation",
    "image-rendering", "shape-outside", "shape-margin",
}


class SemanticHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags_found = []
        self.aria_roles = []
        self.errors = []
        self.warnings = []
        self._current_tag_stack = []
        self._heading_levels = set()

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        self.tags_found.append(tag_lower)
        self._current_tag_stack.append(tag_lower)

        attr_dict = dict(attrs)

        if "role" in attr_dict:
            self.aria_roles.append(attr_dict["role"])

        if tag_lower == "main" and any(
            t in self._current_tag_stack[:-1] for t in ("article", "aside", "section")
        ):
            self.warnings.append(
                f"<main> nested inside <{self._current_tag_stack[-2]}> at position {self.getpos()}: main should be a top-level landmark"
            )

        if tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading_levels.add(int(tag_lower[1]))

    def handle_endtag(self, tag):
        if self._current_tag_stack and self._current_tag_stack[-1] == tag.lower():
            self._current_tag_stack.pop()


def validate_html(filepath):
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    parser = SemanticHTMLParser()
    errors = []
    warnings = []
    try:
        parser.feed(content)
    except Exception as e:
        errors.append(str(e))

    semantic_elements_found = [t for t in parser.tags_found if t in VALID_HTML5_SEMANTIC]
    semantic_missing = VALID_HTML5_SEMANTIC - set(parser.tags_found)

    errors.extend(parser.errors)
    warnings.extend(parser.warnings)

    if "main" not in parser.tags_found:
        warnings.append("No <main> element found: missing primary landmark")

    if 1 not in parser._heading_levels and ("h1" not in parser.tags_found):
        warnings.append("No <h1> found: consider adding a primary heading")

    heading_gap = parser._heading_levels and (
        max(parser._heading_levels) - min(parser._heading_levels) > 1
    )
    if heading_gap:
        warnings.append(
            f"Heading level skip detected: levels {sorted(parser._heading_levels)}"
        )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "tags_found": list(set(parser.tags_found)),
        "semantic_elements_found": semantic_elements_found,
        "semantic_missing": list(semantic_missing),
        "aria_roles": parser.aria_roles,
        "aria_landmarks_found": [r for r in parser.aria_roles if r in ARIA_LANDMARKS],
        "tag_count": len(parser.tags_found),
    }


def check_css(css_filepath):
    with open(css_filepath, encoding="utf-8") as f:
        content = f.read()

    properties_found = []
    unknown_properties = []
    media_queries_found = []

    prop_pattern = re.compile(r"([a-z][a-z-]*[a-z])\s*:", re.MULTILINE)
    for match in prop_pattern.finditer(content):
        prop = match.group(1)
        if prop in CSS_PROPERTIES:
            properties_found.append(prop)
        else:
            if not prop.startswith("--") and not prop.startswith("-webkit-") and not prop.startswith("-moz-"):
                unknown_properties.append(prop)

    mq_pattern = re.compile(
        r"@media\s+([^{]+)\{", re.MULTILINE | re.IGNORECASE
    )
    for match in mq_pattern.finditer(content):
        media_queries_found.append(match.group(1).strip())

    custom_props = re.findall(r"(--[\w-]+)\s*:", content)
    container_queries = re.findall(r"@container\s+(\w*)", content, re.IGNORECASE)
    cascade_layers = re.findall(r"@layer\s+(\w+)", content, re.IGNORECASE)

    return {
        "css_valid": len(unknown_properties) == 0,
        "properties_found": list(set(properties_found)),
        "unknown_properties": list(set(unknown_properties)),
        "media_queries": media_queries_found,
        "custom_properties": list(set(custom_props)),
        "container_queries": list(set(container_queries)),
        "cascade_layers": list(set(cascade_layers)),
        "uses_grid": "grid" in content or "grid-template" in content,
        "uses_flexbox": "flex" in content,
        "uses_logical_properties": any(
            p in content for p in ("margin-inline", "padding-inline", "inset-inline", "border-inline")
        ),
        "uses_clamp": "clamp(" in content,
        "uses_viewport_units": any(u in content for u in ("vw", "vh", "dvh", "svh", "lvh")),
    }


def parse_media_queries(queries):
    breakpoints = set()
    mq_breakpoint = re.compile(r"(?:min-width|max-width)\s*:\s*(\d+)(?:px|em|rem)")
    for q in queries:
        for match in mq_breakpoint.finditer(q):
            breakpoints.add(int(match.group(1)))
    return sorted(breakpoints)


def check_responsive(css_result, target_breakpoints):
    mq_breakpoints = set(parse_media_queries(css_result["media_queries"]))
    uncovered = [bp for bp in target_breakpoints if bp not in mq_breakpoints]

    return {
        "responsive_pass": len(uncovered) == 0,
        "breakpoints_found": sorted(mq_breakpoints),
        "target_breakpoints": target_breakpoints,
        "uncovered_breakpoints": uncovered,
        "media_query_count": len(css_result["media_queries"]),
    }


def semantic_audit(html_result):
    return {
        "has_header": "header" in html_result["tags_found"],
        "has_nav": "nav" in html_result["tags_found"],
        "has_main": "main" in html_result["tags_found"],
        "has_aside": "aside" in html_result["tags_found"],
        "has_footer": "footer" in html_result["tags_found"],
        "semantic_coverage": len(html_result["semantic_elements_found"]),
        "semantic_missing": html_result["semantic_missing"],
        "aria_landmarks_present": html_result["aria_landmarks_found"],
        "aria_landmarks_missing": list(
            ARIA_LANDMARKS - set(html_result["aria_roles"])
        ),
    }


def main():
    parser = argparse.ArgumentParser(description="HTML/CSS core analysis")
    parser.add_argument("--html", required=True, help="HTML file to analyze")
    parser.add_argument("--operation", default="validate_html",
                        choices=["validate_html", "check_css", "responsive_boilerplate", "semantic_audit"])
    parser.add_argument("--output", default="/tmp/html_css_core.json")
    parser.add_argument("--css", default="")
    parser.add_argument("--validate-w3c", action="store_true")
    parser.add_argument("--responsive", action="store_true")
    parser.add_argument("--breakpoints", default="[768, 1024, 1440]")
    parser.add_argument("--semantic-audit", action="store_true")
    args = parser.parse_args()

    target_breakpoints = json.loads(args.breakpoints) if args.breakpoints else [768, 1024, 1440]
    result = {
        "role": "html_css_core",
        "operation": args.operation,
        "html_file": args.html,
    }

    html_result = validate_html(args.html)
    result.update(html_result)

    if args.css:
        css_result = check_css(args.css)
        result["css"] = css_result
        result["css_valid"] = css_result["css_valid"]

        if args.responsive:
            responsive_result = check_responsive(css_result, target_breakpoints)
            result["responsive"] = responsive_result
            result["responsive_pass"] = responsive_result["responsive_pass"]

    if args.semantic_audit or args.operation == "semantic_audit":
        audit_result = semantic_audit(html_result)
        result["semantic_tree"] = audit_result

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result))
    sys.exit(0 if result.get("valid", True) else 1)


if __name__ == "__main__":
    main()
