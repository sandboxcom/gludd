#!/usr/bin/env python3
"""
deck_honesty_lint.py -- Lint the reveal.js deck source for honesty violations.

Rules enforced:
  1. BANNED_TOKENS: marketing adjectives must not appear in slide HTML.
  2. PERCENTAGE_DRIFT: percentage literals in slide HTML that are non-standard
     values must exist in the parsed README feature table.

For Wave 0 (static HTML), rules 1 and 2 are enforceable.

Exit 0 = clean. Exit 1 = violations found (printed to stdout).

Usage:
  python3 scripts/deck_honesty_lint.py [--path PATH] [--features JSON]

  --path     File or directory to lint (default: docs/presentation/build/)
  --features Path to parsed features JSON (default: auto-parse README.md)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Banned marketing tokens (case-insensitive whole-word)
# ---------------------------------------------------------------------------
BANNED_TOKENS: list[str] = [
    "production-ready",
    "blazing",
    "seamless",
    "enterprise-grade",
    "revolutionary",
    "effortless",
    "best-in-class",
    "world-class",
    "cutting-edge",
    "state-of-the-art",
    "industry-leading",
]

_BANNED_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in BANNED_TOKENS) + r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Percentage literal pattern: "75%" or "75 %" in HTML text nodes
# ---------------------------------------------------------------------------
_PCT_RE = re.compile(r"(\d{1,3})\s*%")

# HTML comment slots (these are OK with NO DATA placeholders)
_SLOT_RE = re.compile(r"<!--SLOT:[^-]+-->")

# "NO DATA" placeholder pattern -- acceptable in dynamic slides
_NO_DATA_RE = re.compile(r"NO\s+DATA", re.IGNORECASE)

# Standard coarse values that are always permitted without README backing
_STANDARD_PCTS: frozenset[int] = frozenset({0, 5, 10, 15, 20, 25, 30, 50, 60, 75, 100})


def _load_features(features_path: str | None) -> set[int]:
    """Return set of known % values from parsed README.

    Includes:
    - All feature pct values (e.g. 75, 20, 5)
    - All % literals found inside feature *titles* (e.g. "83%" in a title)
    - The computed overall-shipped % (derived from the counts — still README-grounded)
    """
    if features_path:
        data = json.loads(Path(features_path).read_text(encoding="utf-8"))
        features = data.get("features", [])
    else:
        root = Path(__file__).parent.parent
        readme = root / "README.md"
        if not readme.exists():
            return set()
        sys.path.insert(0, str(Path(__file__).parent))
        from parse_readme_status import parse_readme  # type: ignore[import]

        features = parse_readme(readme)

    known: set[int] = {int(f["pct"]) for f in features}

    # Also extract any % literals embedded in feature titles (e.g. "83% -> 100%")
    # and the computed overall-shipped percentage — both are README-grounded.
    title_pct_re = re.compile(r"(\d{1,3})%")
    for f in features:
        for m in title_pct_re.finditer(f.get("title", "")):
            known.add(int(m.group(1)))

    # Computed overall shipped % (full / total * 100, rounded) — honest summary stat
    total = len(features)
    full = sum(1 for f in features if f.get("bucket") == "full")
    if total:
        known.add(round(full / total * 100))

    return known


def lint_file(path: Path, readme_pcts: set[int]) -> list[str]:
    """Return list of violation strings for one file."""
    violations: list[str] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    for lineno, line in enumerate(lines, 1):
        loc = f"{path}:{lineno}"

        # Rule 1: banned tokens
        m = _BANNED_RE.search(line)
        if m:
            violations.append(
                f"{loc}: BANNED_TOKEN '{m.group(1)}' -- remove marketing adjective"
            )

        # Rule 2: percentage literals must correspond to known README values
        # Skip lines that are inside SLOT comments or NO DATA blocks
        if _SLOT_RE.search(line) or _NO_DATA_RE.search(line):
            continue
        # Skip HTML comments
        if re.match(r"^\s*<!--", line):
            continue

        for pct_m in _PCT_RE.finditer(line):
            pct_val = int(pct_m.group(1))
            # Allow standard coarse percentages and all README-known values
            if pct_val not in _STANDARD_PCTS and pct_val not in readme_pcts:
                violations.append(
                    f"{loc}: PERCENTAGE_DRIFT -- '{pct_val}%' not in README feature "
                    f"table (known %: {sorted(readme_pcts)})"
                )

    return violations


def lint_paths(target: Path, readme_pcts: set[int]) -> list[str]:
    """Recursively lint .html files under target."""
    if target.is_file():
        return lint_file(target, readme_pcts)

    violations: list[str] = []
    for html_file in sorted(target.rglob("*.html")):
        # Skip vendored reveal.js files
        if "vendor" in html_file.parts:
            continue
        violations.extend(lint_file(html_file, readme_pcts))
    return violations


def main() -> None:
    parser = argparse.ArgumentParser(description="Deck honesty lint")
    parser.add_argument(
        "--path",
        default="docs/presentation/build/",
        help="File or directory to lint",
    )
    parser.add_argument(
        "--features",
        default=None,
        help="Path to parsed features JSON (default: auto-parse README.md)",
    )
    args = parser.parse_args()

    readme_pcts = _load_features(args.features)

    target = Path(args.path)
    if not target.exists():
        print(f"SKIP: path not found: {target} (deck not built yet -- OK for Wave 0)")
        sys.exit(0)

    violations = lint_paths(target, readme_pcts)

    if violations:
        print(f"deck-honesty-lint: {len(violations)} violation(s) found:")
        for v in violations:
            print(f"  {v}")
        sys.exit(1)
    else:
        print(
            f"deck-honesty-lint: OK "
            f"(0 violations, {len(readme_pcts)} README % values loaded)"
        )


if __name__ == "__main__":
    main()
