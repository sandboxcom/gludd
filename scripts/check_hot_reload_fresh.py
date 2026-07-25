#!/usr/bin/env python3
"""Verify hot-reload JS modules are fresh and valid.

Checks each /tmp/gludd-hot-<plugin>.js against its corresponding
.opencode/plugin/<plugin>.ts source:
  1. If a hot module exists, its mtime must be >= source .ts mtime (STALE otherwise)
  2. If a hot module exists, it must contain valid JS (no bare TS artifacts)

Hot modules that DON'T exist are SKIPPED (not a failure) — the plugin falls
back to compiled-in defaults safely (fail-open design in hot_reload.ts). Only
STALE modules (older than source) are failures, because they silently load old
code while the operator believes the edit took effect.

Exit 0: all existing hot modules are fresh + valid (or none exist).
Exit 1: at least one existing hot module is stale or broken.

Path overrides (for testing / isolated environments):
  GLUDD_PLUGIN_DIR  — source .ts directory (default: .opencode/plugin)
  GLUDD_HOT_OUT_DIR — hot-module output directory (default: /tmp)
"""

import os
import re
import sys
from pathlib import Path

PLUGIN_DIR = Path(
    os.environ.get(
        "GLUDD_PLUGIN_DIR",
        Path(__file__).resolve().parent.parent / ".opencode" / "plugin",
    )
)
OUT_DIR = Path(os.environ.get("GLUDD_HOT_OUT_DIR", "/tmp"))

DEFAULT_PLUGINS = [
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


def find_stale(
    plugin_dir: Path,
    out_dir: Path,
    plugins: list[str] | None = None,
) -> list[str]:
    """Return a list of problem strings for stale or broken hot modules.

    A hot module that does NOT exist is never a problem (the plugin falls back
    to compiled-in defaults). Only modules that exist but are older than their
    source, or contain stale TS artifacts, are reported.
    """
    if plugins is None:
        plugins = DEFAULT_PLUGINS

    problems: list[str] = []

    for name in plugins:
        src = plugin_dir / f"{name}.ts"
        hot = out_dir / f"gludd-hot-{name}.js"

        if not src.exists():
            continue

        if not has_default_impl(src) or name in ("enforce-multitask",):
            continue

        if not hot.exists():
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

    return problems


def main(argv: list[str] | None = None) -> int:
    del argv  # no argparse flags; path overrides come from env vars

    plugin_dir = PLUGIN_DIR
    out_dir = OUT_DIR
    plugins = DEFAULT_PLUGINS

    problems = find_stale(plugin_dir, out_dir, plugins)

    existing = 0
    for name in plugins:
        src = plugin_dir / f"{name}.ts"
        hot = out_dir / f"gludd-hot-{name}.js"
        if src.exists() and hot.exists() and has_default_impl(src):
            existing += 1

    fresh = existing - len(problems)

    if problems:
        print("=== HOT-RELOAD FRESHNESS FAILED ===\n")
        for p in problems:
            print(f"  FAIL  {p}")
        print(f"\n{fresh}/{existing} hot modules fresh, {len(problems)} problem(s)")
        print("\nFix: make hot-reload-plugins")
        return 1

    print(f"=== HOT-RELOAD FRESHNESS: {fresh}/{existing} modules fresh + valid ===")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
