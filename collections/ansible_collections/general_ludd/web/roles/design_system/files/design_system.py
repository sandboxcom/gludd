#!/usr/bin/env python3
"""design_system.py — Color extraction, font stack analysis, spacing detection, token generation."""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter


HEX_COLOR_RE = re.compile(r'#([0-9a-fA-F]{3,8})\b')
RGB_COLOR_RE = re.compile(r'rgb\(?\s*(\d+)\s*[,/\s]\s*(\d+)\s*[,/\s]\s*(\d+)[^)]*\)')
HSL_COLOR_RE = re.compile(r'hsl\(?\s*([\d.]+)\s*[,/\s]\s*([\d.]+)%?\s*[,/\s]\s*([\d.]+)%?[^)]*\)')
SPACING_RE = re.compile(r'(?:padding|margin|gap|inset|space|--space)\w*\s*:\s*(-?[\d.]+)(px|rem|em|ch|vw|vh|%)')
FONT_FAMILY_RE = re.compile(r'font-family\s*:\s*([^;{}]+)')
FONT_SIZE_RE = re.compile(r'(?:font-size|--text|--font-size)\S*\s*:\s*([\d.]+)(px|rem|em)')
LINE_HEIGHT_RE = re.compile(r'line-height\s*:\s*(-?[\d.]+)')
FONT_WEIGHT_RE = re.compile(r'font-weight\s*:\s*(\d{3})')
Z_INDEX_RE = re.compile(r'(?:--z-|z-index\s*:\s*)(\d+)')


def fetch_css(source: str) -> str:
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers={"User-Agent": "gludd-design-system/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    with open(source, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def extract_colors(css: str) -> dict:
    hex_colors = [m.group(0).upper() for m in HEX_COLOR_RE.finditer(css)]
    rgb_colors = [f"rgb({r},{g},{b})" for r, g, b in RGB_COLOR_RE.findall(css)]
    hsl_colors = [f"hsl({h},{s}%,{l}%)" for h, s, l in HSL_COLOR_RE.findall(css)]
    unique_hex = sorted(set(hex_colors))
    return {
        "total": len(hex_colors) + len(rgb_colors) + len(hsl_colors),
        "hex": unique_hex,
        "hex_count": len(unique_hex),
        "rgb": rgb_colors,
        "rgb_count": len(rgb_colors),
        "hsl": hsl_colors,
        "hsl_count": len(hsl_colors),
    }


def extract_spacing(css: str) -> dict:
    matches = SPACING_RE.findall(css)
    values: dict[str, set[str]] = {"px": set(), "rem": set(), "em": set(), "other": set()}
    for val, unit in matches:
        if unit in values:
            values[unit].add(val)
        else:
            values["other"].add(val)
    numeric = sorted(set(float(v) for v, u in matches if v.replace(".", "").lstrip("-").isdigit()))
    return {
        "total": len(matches),
        "unique_values": {k: sorted(v, key=lambda x: float(x.replace(".", "").lstrip("-") or "0")) for k, v in values.items() if v},
        "numeric_sorted": numeric,
        "detected_scale": _detect_scale(numeric),
    }


def _detect_scale(values: list[float]) -> str | None:
    positive = [v for v in values if v > 0]
    if not positive:
        return None
    diffs: Counter[int] = Counter()
    for i in range(len(positive) - 1):
        d = positive[i + 1] - positive[i]
        if d > 0:
            diffs[int(round(d))] += 1
    if not diffs:
        return None
    most_common = diffs.most_common(1)[0]
    base = most_common[0]
    if base in (4, 8):
        return f"{base}px grid"
    if base == 12:
        return f"{base}px grid (divisible by 4)"
    return f"~{base}px increments"


def extract_typography(css: str) -> dict:
    font_families_raw = FONT_FAMILY_RE.findall(css)
    families: list[str] = []
    for f in font_families_raw:
        for part in f.split(","):
            cleaned = part.strip().strip("'\"")
            if cleaned and ":" not in cleaned:
                families.append(cleaned)
    font_sizes: list[tuple[str, str]] = FONT_SIZE_RE.findall(css)
    line_heights: list[str] = LINE_HEIGHT_RE.findall(css)
    font_weights: list[str] = FONT_WEIGHT_RE.findall(css)
    detected_type_scale: list[str] = sorted(
        set(f"{v}{u}" for v, u in font_sizes),
        key=lambda x: float(re.match(r'[\d.]+', x).group()),
    )
    return {
        "font_families": sorted(set(families)),
        "family_count": len(set(families)),
        "font_sizes": detected_type_scale,
        "font_size_count": len(detected_type_scale),
        "line_heights": sorted(set(lh for lh in line_heights if lh)),
        "line_height_count": len(set(line_heights)),
        "font_weights": sorted(set(font_weights)),
        "weight_count": len(set(font_weights)),
    }


def extract_z_indexes(css: str) -> dict:
    matches = Z_INDEX_RE.findall(css)
    values = sorted(set(int(m) for m in matches))
    return {
        "z_index_values": values,
        "count": len(values),
        "has_custom_scale": any(re.search(r'--z-[\w-]+', css) for _ in range(1)),
        "scale_tokens": list(set(re.findall(r'--z-[\w-]+', css))),
    }


def generate_css_tokens(tokens: dict) -> str:
    lines = [":root {"]
    for category, items in tokens.items():
        lines.append(f"  /* {category} */")
        for name, value in sorted(items.items()):
            lines.append(f"  --{name}: {value};")
    lines.append("}")
    return "\n".join(lines)


def generate_scss_tokens(tokens: dict) -> str:
    lines = []
    for category, items in tokens.items():
        lines.append(f"// {category}")
        for name, value in sorted(items.items()):
            lines.append(f"${name}: {value};")
        lines.append("")
    return "\n".join(lines)


def build_standard_tokens(colors: dict, typography: dict, spacing: dict, z_indexes: dict) -> dict:
    tokens: dict[str, dict[str, str]] = {}
    if colors.get("hex"):
        for i, hex_color in enumerate(colors["hex"][:12], 1):
            tokens.setdefault("colors", {})[f"color-{i:03d}"] = hex_color
    font_weights = typography.get("font_weights", [])
    for w in font_weights:
        tokens.setdefault("typography", {})[f"font-weight-{w}"] = w
    numeric = spacing.get("numeric_sorted", [])
    scale_map = {4: "xs", 8: "sm", 12: "md", 16: "md", 20: "lg", 24: "lg", 32: "xl", 40: "2xl", 48: "2xl", 64: "3xl", 80: "4xl", 96: "4xl", 128: "5xl"}
    for val in numeric:
        label = scale_map.get(int(val))
        if label:
            tokens.setdefault("spacing", {})[f"space-{label}"] = f"{val}px"
    for val in z_indexes.get("z_index_values", [])[:10]:
        label = next((k for k, v in {
            "base": 0, "dropdown": 100, "sticky": 200, "overlay": 300,
            "drawer": 400, "modal": 500, "popover": 600, "toast": 700,
            "tooltip": 800, "spinner": 900,
        }.items() if v == val), f"z-{val}")
        tokens.setdefault("z-index", {})[label] = f"{val}"
    return tokens


def main():
    parser = argparse.ArgumentParser(description="design_system token extraction")
    parser.add_argument("--css-source", required=True)
    parser.add_argument("--token-output-format", default="json", choices=["json", "css", "scss"])
    parser.add_argument("--extract-spacing", action="store_true")
    parser.add_argument("--extract-colors", action="store_true")
    parser.add_argument("--extract-typography", action="store_true")
    parser.add_argument("--generate-component-tokens", action="store_true")
    parser.add_argument("--output-dir", default="/tmp/gludd-web/design_system")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    try:
        css = fetch_css(args.css_source)
    except Exception as e:
        result = {"error": str(e), "css_source": args.css_source}
        output_path = os.path.join(args.output_dir, "design_system.json")
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(json.dumps(result, indent=2))
        return

    result: dict = {"css_source": args.css_source, "format": args.token_output_format, "sections": {}}

    colors = extract_colors(css) if args.extract_colors else {}
    typography = extract_typography(css) if args.extract_typography else {}
    spacing = extract_spacing(css) if args.extract_spacing else {}
    z_indexes = extract_z_indexes(css)

    result["sections"]["colors"] = colors
    result["sections"]["typography"] = typography
    result["sections"]["spacing"] = spacing
    result["sections"]["z_indexes"] = z_indexes

    if args.generate_component_tokens:
        tokens = build_standard_tokens(colors, typography, spacing, z_indexes)
        result["tokens"] = tokens
        if args.token_output_format == "css":
            token_output = generate_css_tokens(tokens)
        elif args.token_output_format == "scss":
            token_output = generate_scss_tokens(tokens)
        else:
            token_output = json.dumps(tokens, indent=2)
        ext = "." + args.token_output_format
        if ext == ".json":
            ext = "_tokens.json"
        elif ext == ".css":
            ext = "_tokens.css"
        elif ext == ".scss":
            ext = "_tokens.scss"
        token_path = os.path.join(args.output_dir, "design" + ext)
        with open(token_path, "w") as f:
            f.write(token_output)
        result["token_output_file"] = token_path

    output_path = os.path.join(args.output_dir, "design_system.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
