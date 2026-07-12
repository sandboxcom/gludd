#!/usr/bin/env python3
"""Parse the Makefile dynamically and print available targets grouped by category.

Usage:
    uv run python scripts/list_make_targets.py              # print all targets categorized
    uv run python scripts/list_make_targets.py --count       # just count
    uv run python scripts/list_make_targets.py --json        # JSON output
    uv run python scripts/list_make_targets.py --category git  # filter by category prefix
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Optional

MAKEFILE_PATH = os.path.join(os.path.dirname(__file__), "..", "Makefile")

# Categories derived from AGENTS.md and target naming conventions.
# Patterns are regexes matched against the full target name.
CATEGORY_PATTERNS: Dict[str, str] = {
    "git": r"^(git-|repo-|untrack|submodule-)",
    "remote/push": r"^(git-(remote|push|pull|fetch|tag)|batch-push|ci-push|force-push|push-dev|verify-remote|deploy-and-forget)",
    "testing": r"^test(-|s$)",
    "quality/gate": r"^(lint|typecheck|yaml-lint|collect-check|gate|smoke|healthcheck|qa|validate|preflight|ruff-audit|check-(types|skills|test-env|readme)|scan-|sast|sbom|pip-|security|audit-|coverage-|gen-status|check-status|verify-(status|feature))",
    "state/ci": r"^(verify-state|ci-|pages-|repo-visibility|gha-|gh-|release-|require-ci)",
    "plugin/enforcement": r"^(write-plugin|check-plugin|write-gate-safe|check-all-guardrails|check-clean-tree|disengage-enforcement|restart-opencode)",
    "watchdog": r"^(watchdog-|task-watchdog-)",
    "agent/worktree": r"^(agent-|clean-stale-worktrees|clean-worktree-venvs|wt-(import|sync|apply|remove|reap|changed|prune)|branches-)",
    "branch/dev": r"^(feature-|development-)",
    "build/dist": r"^(build-executable|bundle-|dist|container-|podman-|ci-repro)",
    "setup": r"^(init$|sync$|relock|install-|setup-dirs|bootstrap|clean|disk-|version|check-(uv|pytest))",
    "ansible": r"^(ansible-|playbook-list|molecule-|collection-)",
    "misc/utility": r"^(grep|grepf|lsd|lsf|lsa|list-tests|script-count|plan$|task$|run-watched|gated-merge|ship-async|file-executable|delete-file|patch-test|skill-|bootstrap-skills|collect-prompts|dogfood|analyze-jsonl|bench-langgraph|game-audit|gen-mcp|mcp-docs|skeleton|scan-tool-usage|status-snapshot|fix-|search-coverage|verify-banana)",
    "search/db": r"^(db-|search-opencode)",
    "process": r"^(ps-|kill-|floor-)",
    "terraform": r"^tf-",
    "deck": r"^deck",
    "sdd": r"^sdd-",
    "searxng": r"^searx-",
}


def extract_targets(makefile_path: str) -> Dict[str, List[str]]:
    """Parse Makefile and return categorized targets."""
    with open(makefile_path) as f:
        content = f.read()

    # Find all target definitions — lines that start at column 0 with an identifier + colon
    # Exclude variable definitions like FOO := bar and include-target lines
    target_re = re.compile(r"^(\.PHONY:.*)?$", re.MULTILINE)
    target_def_re = re.compile(r"^([a-zA-Z][a-zA-Z0-9_-]*)\s*:", re.MULTILINE)

    raw_targets: List[str] = []
    seen: set = set()
    for match in target_def_re.finditer(content):
        name = match.group(1)
        # Skip internal/hidden targets (underbar prefix)
        if name.startswith("_"):
            continue
        # Skip Makefile variable defaults (not targets)
        if name.upper() == name:
            continue
        # Skip known non-targets
        if name in seen:
            continue
        seen.add(name)
        raw_targets.append(name)

    categorized: Dict[str, List[str]] = defaultdict(list)
    assigned: set = set()

    for cat, pattern in CATEGORY_PATTERNS.items():
        cat_re = re.compile(pattern)
        for target in raw_targets:
            if target in assigned:
                continue
            if cat_re.match(target):
                categorized[cat].append(target)
                assigned.add(target)

    # Remaining uncategorized
    uncat = [t for t in raw_targets if t not in assigned]
    if uncat:
        categorized["uncategorized"] = sorted(uncat)

    # Sort each category
    for cat in categorized:
        categorized[cat] = sorted(categorized[cat])

    return dict(sorted(categorized.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description="List available Makefile targets")
    parser.add_argument("--count", action="store_true", help="Print only count")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--category", type=str, help="Filter to category prefix match")
    parser.add_argument("--makefile", type=str, default=MAKEFILE_PATH, help="Path to Makefile")
    args = parser.parse_args()

    makefile_path = os.path.abspath(args.makefile)
    if not os.path.exists(makefile_path):
        print(f"ERROR: Makefile not found at {makefile_path}", file=sys.stderr)
        sys.exit(1)

    categorized = extract_targets(makefile_path)

    if args.category:
        filtered = {
            cat: targets
            for cat, targets in categorized.items()
            if args.category.lower() in cat.lower()
        }
        categorized = filtered if filtered else categorized

    total = sum(len(t) for t in categorized.values())

    if args.count:
        print(f"{total} targets in {len(categorized)} categories")
        for cat, targets in sorted(categorized.items()):
            print(f"  {cat:25s} {len(targets)}")
        return

    if args.json:
        output = {
            "total": total,
            "categories": {cat: sorted(targets) for cat, targets in categorized.items()},
            "source": makefile_path,
        }
        print(json.dumps(output, indent=2))
        return

    # Text output
    print(f"# gludd Makefile targets — {total} targets in {len(categorized)} categories")
    print(f"# Source: {makefile_path}")
    print()
    for cat, targets in sorted(categorized.items()):
        print(f"## {cat} ({len(targets)})")
        for t in targets:
            print(f"  {t}")
        print()


if __name__ == "__main__":
    main()
