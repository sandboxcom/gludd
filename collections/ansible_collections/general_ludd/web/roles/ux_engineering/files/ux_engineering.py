#!/usr/bin/env python3
"""ux_engineering.py — Z-index analysis, contrast calculator, heading hierarchy, ARIA checker."""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser


class HeadingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.headings: list[dict] = []
        self._current_level: int | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        m = re.match(r'^h([1-6])$', tag)
        if m:
            self._current_level = int(m.group(1))
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if re.match(r'^h[1-6]$', tag) and self._current_level is not None:
            self.headings.append({
                "level": self._current_level,
                "text": "".join(self._current_text).strip()[:120],
            })
            self._current_level = None

    def handle_data(self, data: str) -> None:
        if self._current_level is not None:
            self._current_text.append(data)


class ZIndexParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.z_index_elements: list[dict] = []
        self._stacking_triggers: set[str] = set()
        self._current_tag: str = ""
        self._current_attrs: dict[str, str | None] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._current_tag = tag
        self._current_attrs = dict(attrs)
        style = self._current_attrs.get("style", "") or ""
        zi_match = re.search(r'z-index\s*:\s*(\d+)', style)
        if zi_match:
            self.z_index_elements.append({
                "tag": tag,
                "id": self._current_attrs.get("id", ""),
                "class": self._current_attrs.get("class", ""),
                "z_index": int(zi_match.group(1)),
            })
        stacking_props = [
            r'position\s*:\s*(?:relative|absolute|fixed|sticky)',
            r'opacity\s*:\s*(?:0?\.\d+|0)',
            r'transform\s*:',
            r'filter\s*:',
            r'will-change\s*:',
            r'isolation\s*:\s*isolate',
            r'perspective\s*:',
            r'clip-path\s*:',
            r'mask\s*:',
        ]
        for prop in stacking_props:
            if re.search(prop, style):
                self._stacking_triggers.add(re.search(r'\S+', prop).group(0).rstrip(':'))


def fetch_html(source: str) -> str:
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers={"User-Agent": "gludd-ux-engineering/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    with open(source, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def analyze_headings(html: str) -> dict:
    parser = HeadingParser()
    parser.feed(html)
    headings = parser.headings
    violations: list[dict] = []
    skips: list[dict] = []
    prev_level = 0
    for h in headings:
        if prev_level > 0 and h["level"] > prev_level + 1:
            skips.append({"from": prev_level, "to": h["level"], "text": h["text"]})
        prev_level = h["level"]
    if not headings:
        violations.append({"issue": "No headings found", "severity": "high"})
    levels = {h["level"] for h in headings}
    if 1 not in levels:
        violations.append({"issue": "No h1 found — page should have exactly one h1", "severity": "high"})
    h1_count = sum(1 for h in headings if h["level"] == 1)
    if h1_count > 1:
        violations.append({"issue": f"Multiple h1 tags ({h1_count}) — use exactly one", "severity": "high"})
    return {
        "heading_count": len(headings),
        "levels_present": sorted(levels),
        "headings": headings,
        "skip_violations": skips,
        "skip_count": len(skips),
        "violations": violations,
        "violation_count": len(violations),
    }


def analyze_z_index(html: str) -> dict:
    parser = ZIndexParser()
    parser.feed(html)
    elements = parser.z_index_elements
    conflicts: list[dict] = []
    for i, el in enumerate(elements):
        for j, el2 in enumerate(elements):
            if i >= j:
                continue
            if el["z_index"] == el2["z_index"]:
                conflicts.append({
                    "z_index": el["z_index"],
                    "element_a": f'{el["tag"]}#{el["id"]}.{el["class"]}',
                    "element_b": f'{el2["tag"]}#{el2["id"]}.{el2["class"]}',
                })
    return {
        "z_index_count": len(elements),
        "elements": elements,
        "stacking_triggers": sorted(parser._stacking_triggers),
        "trigger_count": len(parser._stacking_triggers),
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
    }


def check_aria_attributes(html: str) -> dict:
    findings: list[dict] = []
    roles = re.findall(r'role\s*=\s*["\']([^"\']+)["\']', html)
    aria_labels = re.findall(r'aria-label\s*=\s*["\']([^"\']*)["\']', html)
    aria_labelledby = re.findall(r'aria-labelledby\s*=\s*["\']([^"\']*)["\']', html)
    empty_labels = [l for l in aria_labels if not l.strip()]
    if empty_labels:
        findings.append({"issue": "Empty aria-label attributes", "count": len(empty_labels), "severity": "medium"})
    alt_missing = len(re.findall(r'<img\s+(?![^>]*alt\s*=)', html))
    if alt_missing:
        findings.append({"issue": "Images missing alt attribute", "count": alt_missing, "severity": "high"})
    button_without_accessible_name = len(re.findall(r'<button\s+(?![^>]*aria-lab(?:el|elledby))(?![^>]*>.*\S)', html))
    if button_without_accessible_name:
        findings.append({"issue": "Buttons may lack accessible name", "count": button_without_accessible_name, "severity": "medium"})
    return {
        "roles_found": sorted(set(roles)),
        "role_count": len(roles),
        "aria_label_count": len(aria_labels),
        "aria_labelledby_count": len(aria_labelledby),
        "findings": findings,
        "finding_count": len(findings),
    }


def check_contrast_issues(html: str) -> dict:
    color_matches = re.findall(
        r'(?:color|background-color|background)\s*:\s*(#[0-9a-fA-F]{3,6}|rgb\(?\s*\d+\s*,\s*\d+\s*,\s*\d+)',
        html, re.IGNORECASE,
    )
    return {
        "color_declarations": len(color_matches),
        "note": "Full contrast ratio calculation requires rendering engine; this counts declarations for manual review.",
    }


def run_usability_checklist(html: str) -> dict:
    checks = [
        {"name": "Has h1", "pass": "<h1" in html, "heuristic": 4},
        {"name": "Has navigation landmark", "pass": bool(re.search(r'<nav\b', html)) or 'role="navigation"' in html, "heuristic": 4},
        {"name": "Has main landmark", "pass": bool(re.search(r'<main\b', html)) or 'role="main"' in html, "heuristic": 4},
        {"name": "Has form labels", "pass": '<label' in html or 'aria-label' in html or 'aria-labelledby' in html, "heuristic": 5},
        {"name": "Has unordered lists", "pass": '<ul' in html or '<ol' in html or '<dl' in html, "heuristic": 2},
        {"name": "Has visible focus style", "pass": ':focus' in html or 'outline' in html.lower(), "heuristic": 3},
        {"name": "Has meta description", "pass": bool(re.search(r'<meta[^>]+name\s*=\s*["\']description["\']', html)), "heuristic": 8},
    ]
    passed = sum(1 for c in checks if c["pass"])
    return {
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "score": f"{passed}/{len(checks)}",
        "heuristics_covered": sorted(set(c["heuristic"] for c in checks)),
    }


def main():
    parser = argparse.ArgumentParser(description="ux_engineering audit")
    parser.add_argument("--target", required=True)
    parser.add_argument("--accessibility-standard", default="wcag21_aa")
    parser.add_argument("--check-z-index", action="store_true")
    parser.add_argument("--check-visual-hierarchy", action="store_true")
    parser.add_argument("--usability-heuristics", action="store_true")
    parser.add_argument("--output-dir", default="/tmp/gludd-web/ux_engineering")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    result: dict = {
        "target": args.target,
        "accessibility_standard": args.accessibility_standard,
        "sections": {},
    }

    try:
        html = fetch_html(args.target)
        result["target_fetched"] = True
    except Exception as e:
        result["target_fetched"] = False
        result["error"] = str(e)
        output_path = os.path.join(args.output_dir, "ux_engineering.json")
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(json.dumps(result, indent=2))
        return

    if args.check_visual_hierarchy:
        result["sections"]["headings"] = analyze_headings(html)

    if args.check_z_index:
        result["sections"]["z_index"] = analyze_z_index(html)

    result["sections"]["aria"] = check_aria_attributes(html)
    result["sections"]["contrast_notes"] = check_contrast_issues(html)

    if args.usability_heuristics:
        result["sections"]["usability"] = run_usability_checklist(html)

    output_path = os.path.join(args.output_dir, "ux_engineering.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
