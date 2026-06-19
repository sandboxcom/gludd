#!/usr/bin/env python3
"""
parse_readme_status.py — Parse the README "Feature & Task Completion Status" tables
into a structured features[] list for the reveal.js deck.

Output (stdout, JSON):
  {
    "features": [
      {
        "title": "...",
        "pct": 100,       # integer percentage (0-100)
        "pct_label": "100%",
        "evidence": "...",
        "bucket": "full|partial|none",
        "local_only": bool,
        "section": "..."  # section heading
      }, ...
    ],
    "parsed_at": "2026-06-18T...",
    "source": "README.md"
  }

Buckets:
  full    -- pct == 100
  partial -- 0 < pct < 100
  none    -- pct == 0

Usage:
  python3 scripts/parse_readme_status.py            # writes to stdout
  python3 scripts/parse_readme_status.py --out docs/presentation/deck-data.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

README = Path(__file__).parent.parent / "README.md"

# Matches table rows like: | Feature title | 75% | evidence text |
# Tolerates leading/trailing spaces inside cells.
_ROW_RE = re.compile(
    r"^\|\s*(?P<title>[^|]+?)\s*\|\s*(?P<pct_label>[0-9]+%[^|]*?)\s*\|\s*(?P<evidence>[^|]+?)\s*\|"
)

# Section headings above the table rows (### Foo)
_SECTION_RE = re.compile(r"^###\s+(.+)")

# "local_only" flag: row says "100% (local)" or "75% (local)"
_LOCAL_ONLY_RE = re.compile(r"\(local\)", re.IGNORECASE)


def _parse_pct(raw: str) -> int:
    """Extract integer percentage from a cell like '75%', '100% (local)', '20%'."""
    m = re.search(r"(\d+)%", raw)
    if not m:
        return -1
    return int(m.group(1))


def _bucket(pct: int) -> str:
    if pct == 100:
        return "full"
    if pct > 0:
        return "partial"
    return "none"


def parse_readme(path: Path) -> list[dict]:
    features: list[dict] = []
    current_section = ""
    in_status_block = False

    for line in path.read_text(encoding="utf-8").splitlines():
        # Track section heading
        sec_m = _SECTION_RE.match(line)
        if sec_m:
            current_section = sec_m.group(1).strip()
            continue

        # Detect entry into the status tables block.
        if "Feature & Task Completion Status" in line:
            in_status_block = True
            continue

        # Skip separator rows like |---|---|---|
        if re.match(r"^\|\s*[-:]+\s*\|", line):
            continue

        row_m = _ROW_RE.match(line)
        if row_m:
            in_status_block = True  # any matching table row confirms we're in
            title = row_m.group("title").strip()
            pct_label = row_m.group("pct_label").strip()
            evidence = row_m.group("evidence").strip()

            pct = _parse_pct(pct_label)
            if pct < 0:
                # Not a percentage row (e.g. header row "% | Evidence") -- skip
                continue

            local_only = bool(_LOCAL_ONLY_RE.search(pct_label))

            features.append(
                {
                    "title": title,
                    "pct": pct,
                    "pct_label": pct_label,
                    "evidence": evidence,
                    "bucket": _bucket(pct),
                    "local_only": local_only,
                    "section": current_section,
                }
            )

    return features


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse README status table -> JSON")
    parser.add_argument(
        "--out",
        default=None,
        help="Write output to this file (default: stdout)",
    )
    parser.add_argument(
        "--readme",
        default=str(README),
        help=f"Path to README.md (default: {README})",
    )
    args = parser.parse_args()

    readme_path = Path(args.readme)
    if not readme_path.exists():
        print(f"ERROR: README not found at {readme_path}", file=sys.stderr)
        sys.exit(1)

    features = parse_readme(readme_path)
    if not features:
        print("ERROR: No features parsed from README -- check table format", file=sys.stderr)
        sys.exit(1)

    result = {
        "features": features,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "source": str(readme_path),
        "counts": {
            "full": sum(1 for f in features if f["bucket"] == "full"),
            "partial": sum(1 for f in features if f["bucket"] == "partial"),
            "none": sum(1 for f in features if f["bucket"] == "none"),
            "total": len(features),
        },
    }

    out_json = json.dumps(result, indent=2)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_json + "\n", encoding="utf-8")
        print(f"Wrote {len(features)} features to {out_path}")
    else:
        print(out_json)


if __name__ == "__main__":
    main()
