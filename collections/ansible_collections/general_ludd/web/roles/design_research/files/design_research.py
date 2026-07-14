#!/usr/bin/env python3
"""design_research — Extract design tokens from websites."""
import argparse
import json
import re
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser


HEX_COLOR = re.compile(r"#([0-9a-fA-F]{3,8})\b")
RGB_COLOR = re.compile(
    r"rgb\s*\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([\d.]+))?\s*\)"
)
HSL_COLOR = re.compile(
    r"hsl\s*\(\s*(\d{1,3})\s*,\s*(\d{1,3})%\s*,\s*(\d{1,3})%\s*(?:,\s*([\d.]+))?\s*\)"
)
CSS_VAR = re.compile(r"var\s*\(\s*(--[\w-]+)[^)]*\)")
FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;{}]+)")
FONT_SIZE = re.compile(r"font-size\s*:\s*([^;{}]+)")
MARGIN_PROP = re.compile(r"margin(?:-top|-right|-bottom|-left)?\s*:\s*([^;{}]+)")
PADDING_PROP = re.compile(r"padding(?:-top|-right|-bottom|-left)?\s*:\s*([^;{}]+)")
DISPLAY_PROP = re.compile(r"display\s*:\s*([^;{}]+)")
POSITION_PROP = re.compile(r"position\s*:\s*([^;{}]+)")
GRID_TEMPLATE = re.compile(r"grid-template-(?:columns|rows|areas)\s*:\s*([^;{}]+)")
FLEX_DIRECTION = re.compile(r"flex-direction\s*:\s*([^;{}]+)")
GAP_PROP = re.compile(r"(?:row-)?gap\s*:\s*([^;{}]+)")

FRAMEWORK_PATTERNS = {
    "tailwind": re.compile(r"tailwind|(?:[a-z]+-){2,}[a-z]+", re.IGNORECASE),
    "bootstrap": re.compile(r"bootstrap|col-(?:xs|sm|md|lg|xl)|row|container-fluid", re.IGNORECASE),
    "material": re.compile(r"material|mdc-|mat-|md-", re.IGNORECASE),
    "bulma": re.compile(r"bulma|is-(?:primary|link|info|success|warning|danger)", re.IGNORECASE),
    "foundation": re.compile(r"foundation|(?:small|medium|large)-\d+", re.IGNORECASE),
}

SPACING_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s*(px|rem|em|ch|vh|vw|%|pt)")


class CSSExtractor(HTMLParser):
    def __init__(self, base_url):
        super().__init__()
        self.base_url = base_url
        self.css_links = []
        self.inline_styles = []
        self.classes = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "link" and attr_dict.get("rel") == "stylesheet":
            href = attr_dict.get("href", "")
            if href:
                self.css_links.append(href)
        if "style" in attr_dict:
            self.inline_styles.append(attr_dict["style"])
        if "class" in attr_dict:
            self.classes.extend(attr_dict["class"].split())
        if "id" in attr_dict:
            self.ids.append(attr_dict["id"])


def fetch_url(url, timeout=10):
    req = Request(url, headers={"User-Agent": "gludd-design-research/0.1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        return None
    except URLError as e:
        return None
    except Exception as e:
        return None


def extract_colors(css_text, inline_styles):
    all_text = css_text + "\n" + "\n".join(inline_styles)
    palette = {
        "hex_colors": [],
        "rgb_colors": [],
        "hsl_colors": [],
        "css_variables": [],
    }

    for match in HEX_COLOR.finditer(all_text):
        palette["hex_colors"].append(match.group(0))

    for match in RGB_COLOR.finditer(all_text):
        palette["rgb_colors"].append(match.group(0))

    for match in HSL_COLOR.finditer(all_text):
        palette["hsl_colors"].append(match.group(0))

    for match in CSS_VAR.finditer(all_text):
        palette["css_variables"].append(match.group(1))

    return {
        "hex_colors": list(set(palette["hex_colors"])),
        "rgb_colors": list(set(palette["rgb_colors"])),
        "hsl_colors": list(set(palette["hsl_colors"])),
        "css_variables": list(set(palette["css_variables"])),
        "total_unique_colors": len(
            set(palette["hex_colors"])
            | set(palette["rgb_colors"])
            | set(palette["hsl_colors"])
        ),
    }


def extract_fonts(css_text):
    families = []
    sizes = []
    for match in FONT_FAMILY.finditer(css_text):
        val = match.group(1).strip().strip('"').strip("'")
        families.append(val)
    for match in FONT_SIZE.finditer(css_text):
        sizes.append(match.group(1).strip())

    return {
        "font_families": list(set(families)),
        "font_sizes": list(set(sizes)),
        "font_count": len(set(families)),
    }


def extract_spacing(css_text):
    margin_values = []
    padding_values = []
    gap_values = []

    for match in MARGIN_PROP.finditer(css_text):
        margin_values.append(match.group(1).strip())
    for match in PADDING_PROP.finditer(css_text):
        padding_values.append(match.group(1).strip())
    for match in GAP_PROP.finditer(css_text):
        gap_values.append(match.group(1).strip())

    spacing_units = {}
    for val in margin_values + padding_values + gap_values:
        for m in SPACING_UNIT.finditer(val):
            unit = m.group(2)
            num = float(m.group(1))
            if unit not in spacing_units:
                spacing_units[unit] = set()
            spacing_units[unit].add(num)

    spacing_scale = {
        k: sorted(v) for k, v in spacing_units.items()
    }

    return {
        "margin_values": list(set(margin_values)),
        "padding_values": list(set(padding_values)),
        "gap_values": list(set(gap_values)),
        "spacing_scale": spacing_scale,
        "uses_4px_grid": _check_grid(spacing_units.get("px", set())),
        "uses_8px_grid": _check_grid(spacing_units.get("px", set()), base=8),
    }


def _check_grid(px_values, base=4):
    if not px_values:
        return False
    return all(v % base == 0 for v in px_values if v > 0)


def extract_layout(css_text):
    display_values = set()
    for match in DISPLAY_PROP.finditer(css_text):
        display_values.add(match.group(1).strip())

    positions = set()
    for match in POSITION_PROP.finditer(css_text):
        positions.add(match.group(1).strip())

    grid_areas = []
    for match in GRID_TEMPLATE.finditer(css_text):
        grid_areas.append(match.group(1).strip())

    flex_dirs = set()
    for match in FLEX_DIRECTION.finditer(css_text):
        flex_dirs.add(match.group(1).strip())

    return {
        "display_values": sorted(display_values),
        "positions": sorted(positions),
        "uses_grid": any("grid" in d for d in display_values),
        "uses_flex": any("flex" in d for d in display_values),
        "grid_templates": grid_areas,
        "flex_directions": sorted(flex_dirs),
    }


def detect_framework(css_text, class_list):
    combined = css_text + " " + " ".join(class_list)
    for name, pattern in FRAMEWORK_PATTERNS.items():
        if pattern.search(combined):
            return name
    return "none"


def count_css_lines(css_text):
    return len(css_text.split("\n"))


def main():
    parser = argparse.ArgumentParser(description="Design research from websites")
    parser.add_argument("--url", required=True, help="Target URL to analyze")
    parser.add_argument("--operation", default="full_audit",
                        choices=["fetch", "extract_tokens", "analyze_layout", "full_audit"])
    parser.add_argument("--output", default="/tmp/design_research.json")
    parser.add_argument("--artifacts", default="/tmp/gludd-web/design_research")
    parser.add_argument("--extract-colors", action="store_true", default=True)
    parser.add_argument("--extract-fonts", action="store_true", default=True)
    parser.add_argument("--extract-spacing", action="store_true", default=True)
    parser.add_argument("--extract-layout", action="store_true", default=True)
    parser.add_argument("--max-depth", type=int, default=1)
    args = parser.parse_args()

    result = {
        "role": "design_research",
        "operation": args.operation,
        "target_url": args.url,
        "url_fetched": False,
    }

    html = fetch_url(args.url)
    if html is None:
        result["html"] = ""
        result["css"] = ""
        result["error"] = f"Could not fetch URL: {args.url}"
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(json.dumps(result))
        sys.exit(1)

    result["url_fetched"] = True
    result["html"] = html

    extractor = CSSExtractor(args.url)
    extractor.feed(html)

    all_css = "\n".join(extractor.inline_styles)
    for link in extractor.css_links:
        if link.startswith("http"):
            css_content = fetch_url(link)
        elif link.startswith("//"):
            css_content = fetch_url("https:" + link)
        elif link.startswith("/"):
            base = args.url.rstrip("/")
            css_content = fetch_url(base + link)
        else:
            base = args.url.rsplit("/", 1)[0] + "/"
            css_content = fetch_url(base + link)
        if css_content:
            all_css += "\n" + css_content

    result["css"] = all_css
    result["css_links_found"] = len(extractor.css_links)
    result["css_links_resolved"] = len([l for l in extractor.css_links if l])
    result["inline_style_count"] = len(extractor.inline_styles)
    result["classes_found"] = list(set(extractor.classes))
    result["ids_found"] = extractor.ids
    result["total_css_lines"] = count_css_lines(all_css)

    if args.extract_colors:
        result["colors"] = extract_colors(all_css, extractor.inline_styles)

    if args.extract_fonts:
        result["fonts"] = extract_fonts(all_css)

    if args.extract_spacing:
        result["spacing"] = extract_spacing(all_css)

    if args.extract_layout:
        result["layout"] = extract_layout(all_css)

    result["framework"] = detect_framework(all_css, extractor.classes)

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
