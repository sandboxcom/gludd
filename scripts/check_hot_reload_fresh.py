#!/usr/bin/env python3
"""Verify hot-reload JS modules are fresh and valid.

Checks each /tmp/gludd-hot-<plugin>.js against its corresponding
.opencode/plugin/<plugin>.ts source:
  1. Hot module must exist
  2. Hot module mtime must be >= source .ts mtime
  3. Hot module must contain valid JS (no bare TS artifacts)

Exit 0: all hot modules are fresh + valid.
Exit 1: at least one hot module is stale or broken.
"""

import os
import re
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / ".opencode" / "plugin"
OUT_DIR = Path("/tmp")

PLUGINS = [
    "enforce-deadline",
    "enforce-enhancement-ratio",
    "enforce-floor",
    "enforce-delegate",
    "enforce-make",
    "enforce-multitask",
    "enforce-no-suppressions",
    "enforce-no-wait",
    "enforce-session-start",
    "enforce-stop",
    "enforce-verified-claims",
    "enforce-clean-tree",
    "enforce-deletion-gate",
]

STALE_ARTIFACT_PATTERNS = [
    re.compile(r"\bimport\s*\{[^}]*}\s*from\s*[\"']"),
    re.compile(r"\bexport\s+(default|const|function|interface)\b"),
    re.compile(r":\s*(string|number|boolean|any|void|never)\b"),
    re.compile(r"ReferenceError\s*(is\s+not|:)"),  # only actual runtime errors, not comments
]


COMMENT_LINE_RE = re.compile(r"(?m)^\s*//.*$")
TRAILING_COMMENT_RE = re.compile(r"(?m)(?<![:\"'])//(?![\"']).*$")


def strip_line_comments(content: str) -> str:
    """Remove JS line comments so inert comment text never trips the
    stale-artifact patterns (CI run 29449765249: 'ReferenceError' inside a
    comment in the generated enforce-stop hot module was flagged as stale).
    Protocol-relative markers like https:// are preserved by the negative
    lookbehind on ':'."""
    without_full_lines = COMMENT_LINE_RE.sub("", content)
    return TRAILING_COMMENT_RE.sub("", without_full_lines)


def is_stale_content(content: str) -> list[str]:
    issues = []
    scannable = strip_line_comments(content)
    for pat in STALE_ARTIFACT_PATTERNS:
        if pat.search(scannable):
            issues.append(f"  stale artifact: {pat.pattern}")
    return issues


def has_default_impl(src: Path) -> bool:
    try:
        return "defaultImpl" in src.read_text(encoding="utf-8")
    except OSError:
        return False


def main() -> int:
    problems: list[str] = []
    checked = 0
    skipped_no_proxy = 0

    for name in PLUGINS:
        src = PLUGIN_DIR / f"{name}.ts"
        hot = OUT_DIR / f"gludd-hot-{name}.js"

        if not src.exists():
            problems.append(f"{name}: source {src} not found")
            continue

        if not has_default_impl(src):
            skipped_no_proxy += 1
            continue

        if not hot.exists():
            problems.append(f"{name}: hot module {hot} missing — run make hot-reload-plugins")
            continue

        src_mtime = src.stat().st_mtime
        hot_mtime = hot.stat().st_mtime
        if hot_mtime < src_mtime:
            problems.append(
                f"{name}: hot module stale (source newer by {int(src_mtime - hot_mtime)}s)"
            )
            continue

        content = hot.read_text(encoding="utf-8")
        if len(content) < 50:
            problems.append(f"{name}: hot module too short ({len(content)} bytes)")
            continue

        stale_issues = is_stale_content(content)
        if stale_issues:
            for si in stale_issues:
                problems.append(f"{name}: {si}")
            continue

        checked += 1

    skipped_msg = f" ({skipped_no_proxy} skipped — no defaultImpl)" if skipped_no_proxy else ""

    if problems:
        print("=== HOT-RELOAD FRESHNESS FAILED ===\n")
        for p in problems:
            print(f"  FAIL  {p}")
        print(f"\n{checked}/{checked + len(problems)} hot modules fresh, {len(problems)} problem(s){skipped_msg}")
        print("\nFix: make hot-reload-plugins")
        return 1

    print(f"=== HOT-RELOAD FRESHNESS: {checked}/{checked} modules fresh + valid{skipped_msg} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
