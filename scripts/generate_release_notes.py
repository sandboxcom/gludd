#!/usr/bin/env python3
"""generate_release_notes.py — AC018: release-notes-automation.

Auto-generates release notes from conventional commits between two tags.
Categorizes by type (feat, fix, docs, refactor, test, chore).
"""

import os
import re
import subprocess
import sys


def run_git(args):
    result = subprocess.run(["git"] + args, capture_output=True, text=True)
    return result.stdout.strip()


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    if not tag:
        print("AC018: TAG required")
        sys.exit(2)

    tags = run_git(["tag", "--sort=-creatordate", "-l", "v*"]).split("\n")
    tags = [t for t in tags if t]

    if tag not in tags:
        print(f"AC018: TAG '{tag}' not found")
        sys.exit(2)

    try:
        idx = tags.index(tag)
        prev_tag = tags[idx + 1] if idx + 1 < len(tags) else None
    except ValueError:
        prev_tag = None

    if not prev_tag:
        print("AC018: WARN — no prior tag, generating notes from first commit")
        range_spec = tag
    else:
        range_spec = f"{prev_tag}..{tag}"

    commits = run_git(["log", "--oneline", range_spec]).split("\n")
    commits = [c for c in commits if c]

    categories = {"feat": [], "fix": [], "docs": [], "refactor": [], "test": [], "chore": [], "other": []}

    for commit in commits:
        match = re.match(r"^[a-f0-9]+\s+(\w+)(?:\([^)]*\))?[!:]\s*(.*)", commit)
        if match:
            ctype, desc = match.group(1), match.group(2)
            if ctype in categories:
                categories[ctype].append(desc)
            else:
                categories["other"].append(commit)
        else:
            categories["other"].append(commit)

    version = tag.lstrip("v")
    print(f"## What's Changed in {version}")
    print()

    label_map = {
        "feat": "Features",
        "fix": "Bug Fixes",
        "docs": "Documentation",
        "refactor": "Refactoring",
        "test": "Tests",
        "chore": "Chores",
        "other": "Other Changes",
    }

    for key, label in label_map.items():
        if categories[key]:
            print(f"### {label}")
            for item in categories[key]:
                print(f"- {item}")
            print()

    contributors = run_git(["shortlog", "-sn", range_spec])
    if contributors:
        print("### Contributors")
        for line in contributors.split("\n"):
            print(f"- {line.strip()}")

    print(f"\n**Full Changelog**: {prev_tag}...{tag}" if prev_tag else f"\n**Initial Release**: {tag}")
    sys.exit(0)


if __name__ == "__main__":
    main()
