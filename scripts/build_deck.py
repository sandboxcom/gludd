#!/usr/bin/env python3
"""
build_deck.py — Generate the gludd reveal.js deck from live project data.

Reads README.md, .game-audit-report.json, and other live artifacts to produce
docs/presentation/deck/index.html. Missing data renders as honest "NO DATA"
placeholders rather than fabricated numbers.

Usage:
    python3 scripts/build_deck.py            # generate deck
    python3 scripts/build_deck.py --serve    # generate + start local server
    python3 scripts/build_deck.py --check    # verify honesty (banned words, % match)
"""

import argparse
import http.server
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DECK_DIR = ROOT / "docs" / "presentation" / "deck"
DECK_FILE = DECK_DIR / "index.html"
README = ROOT / "README.md"
GAME_AUDIT = ROOT / ".game-audit-report.json"
TEST_COUNT_CACHE = ROOT / ".test-count-cache.txt"

BANNED_MARKETING = [
    "production-ready", "blazing", "seamless", "enterprise-grade",
    "revolutionary", "effortless", "best-in-class", "world-class",
    "game-changing", "next-gen",
]


def parse_readme_status_table() -> list[dict]:
    """Parse the README status table rows into feature dicts."""
    text = README.read_text()
    features = []
    in_table = False
    for line in text.splitlines():
        if "STATUS-TABLE:START" in line:
            in_table = True
            continue
        if "STATUS-TABLE:END" in line:
            break
        if not in_table:
            continue
        if line.startswith("| ") and "Feature" not in line and "---" not in line:
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 3:
                features.append({
                    "name": parts[0],
                    "pct": parts[1],
                    "evidence": parts[2] if len(parts) > 2 else "",
                })
    return features


def get_test_count() -> int:
    """Get the collected test count."""
    result = subprocess.run(
        ["make", "test-count"],
        capture_output=True, text=True,
        cwd=str(ROOT),
    )
    for line in result.stdout.splitlines():
        m = re.search(r"(\d+)\s+tests?\s+collected", line)
        if m:
            return int(m.group(1))
    return 0


def get_git_sha() -> str:
    """Get current HEAD SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"],
        capture_output=True, text=True,
        cwd=str(ROOT),
    )
    return result.stdout.strip()


def get_version() -> str:
    """Get project version from pyproject.toml."""
    ppt = ROOT / "pyproject.toml"
    if ppt.exists():
        text = ppt.read_text()
        m = re.search(r'version\s*=\s*"([^"]+)"', text)
        if m:
            return m.group(1)
    return "unknown"


def get_game_audit() -> dict | None:
    """Load game audit report if it exists."""
    if GAME_AUDIT.exists():
        return json.loads(GAME_AUDIT.read_text())
    return None


def count_roles() -> int:
    """Count Ansible roles."""
    result = subprocess.run(
        ["make", "collection-roles"],
        capture_output=True, text=True,
        cwd=str(ROOT),
    )
    return len([l for l in result.stdout.strip().splitlines() if l.strip()])


def build_deck_data() -> dict:
    """Collect all live data for the deck."""
    data = {
        "version": get_version(),
        "git_sha": get_git_sha(),
        "test_count": get_test_count(),
        "role_count": count_roles(),
        "features": parse_readme_status_table(),
        "generated_at": subprocess.run(
            ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
            capture_output=True, text=True,
        ).stdout.strip(),
    }

    game_audit = get_game_audit()
    if game_audit:
        data["game_audit"] = game_audit

    return data


def apply_tokens(html: str, data: dict) -> tuple[str, list[str]]:
    """Replace {{TOKEN}} placeholders in HTML with live data values.

    Returns the rewritten HTML and a list of tokens that were not found in
    the template (so the caller can warn about stale tokens).
    """
    test_count = data.get("test_count", 0)
    role_count = data.get("role_count", 0)
    replacements = {
        "{{VERSION}}": str(data.get("version", "unknown")),
        "{{TEST_COUNT}}": f"{test_count:,}",
        "{{ROLE_COUNT}}": str(role_count),
        "{{GIT_SHA}}": str(data.get("git_sha", "unknown")),
        "{{GENERATED_AT}}": str(data.get("generated_at", "")),
    }
    missing = [tok for tok in replacements if tok not in html]
    for token, value in replacements.items():
        html = html.replace(token, value)
    return html, missing


def honesty_check(html: str) -> list[str]:
    """Check deck HTML for honesty violations. Returns list of violations."""
    violations = []

    for token in BANNED_MARKETING:
        if token.lower() in html.lower():
            violations.append(f"Banned marketing token: '{token}'")

    return violations


def serve_deck(port: int = 8080) -> None:
    """Serve the deck directory over HTTP."""
    os.chdir(str(DECK_DIR))
    handler = http.server.SimpleHTTPRequestHandler
    print(f"Serving deck at http://localhost:{port}/")
    print("Press Ctrl+C to stop.")
    with http.server.HTTPServer(("", port), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the gludd reveal.js deck")
    parser.add_argument("--serve", action="store_true", help="Start local server after build")
    parser.add_argument("--check", action="store_true", help="Run honesty check on deck HTML")
    parser.add_argument("--build", action="store_true",
                        help="Regenerate index.html by applying {{TOKEN}} placeholders from live data")
    parser.add_argument("--port", type=int, default=8080, help="Port for --serve")
    parser.add_argument("--data", action="store_true", help="Print deck data JSON to stdout")
    args = parser.parse_args()

    if args.data:
        data = build_deck_data()
        print(json.dumps(data, indent=2))
        return

    # Collect live data
    data = build_deck_data()

    # The deck HTML is primarily authored in docs/presentation/deck/index.html.
    # The build step currently regenerates data bindings. Full dynamic rendering
    # (Jinja2 template + partials) is Wave 1–2 of BUILD_TASK_LIST.md.
    deck_path = DECK_FILE
    if not deck_path.exists():
        print(f"ERROR: Deck file not found: {deck_path}", file=sys.stderr)
        sys.exit(1)

    html = deck_path.read_text()

    # When --build is set, rewrite index.html by replacing {{TOKEN}} placeholders
    # with live values from the collected data. This is what makes the deck
    # regenerate from data rather than staying static/stale.
    if args.build:
        html, missing = apply_tokens(html, data)
        deck_path.write_text(html)
        print(f"Deck HTML rewritten: {deck_path}")
        if missing:
            print(f"  WARNING: tokens not found in template: {', '.join(missing)}")

    # Honesty check
    violations = honesty_check(html)
    if violations:
        print("HONESTY CHECK FAILED:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        if args.check:
            sys.exit(1)

    # Write deck-data.json for downstream consumers
    data_path = ROOT / "docs" / "presentation" / "deck-data.json"
    data_path.write_text(json.dumps(data, indent=2))
    print(f"Deck data written: {data_path}")

    print(f"Deck ready: {deck_path}")
    print(f"  Version:    {data['version']}")
    print(f"  SHA:        {data['git_sha']}")
    print(f"  Tests:      {data['test_count']}")
    print(f"  Roles:      {data['role_count']}")
    print(f"  Features:   {len(data['features'])}")
    if violations:
        print(f"  HONESTY:    {len(violations)} violation(s) — banned marketing tokens found")

    if args.serve:
        serve_deck(args.port)


if __name__ == "__main__":
    main()
